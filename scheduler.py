import streamlit as st
import pandas as pd
import re
import itertools
import requests
from bs4 import BeautifulSoup
import gc
import heapq
from datetime import datetime, timedelta, timezone
import io
import matplotlib.pyplot as plt
import urllib.parse
import unicodedata

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Generador de Horarios", layout="wide")
st.markdown(
    "<div style='font-size:0.85em; color:gray; margin-top:-8px; margin-bottom:10px;'>"
    "<a href='https://www.instagram.com/gaelprevaricare/' target='_blank' style='text-decoration:none;'>"
    "@gaelprevaricare</a></div>",
    unsafe_allow_html=True
)


# --- FUNCIONES AUXILIARES---
def hora_a_minutos(hora_str):
    try:
        h, m = map(int, hora_str.split(':'))
        return h * 60 + m
    except:
        return 0

def extraer_intervalos(horario_str, dias_lista):
    try:
        inicio_str, fin_str = horario_str.split(' a ')
        inicio_min = hora_a_minutos(inicio_str)
        fin_min = hora_a_minutos(fin_str)
        return [{'dia': d.strip(), 'inicio': inicio_min, 'fin': fin_min} for d in dias_lista]
    except:
        return []

def limpiar_nombre_profesor(nombre):
    if not nombre:
        return ""
    n = nombre.replace("(PRESENCIAL)", "").replace("\n", " ").strip()
    n = re.sub(r"\s+", " ", n)
    prefijos = [
        "M. EN I.", "M EN I.", "M.I.", "MI.", "M I.",
        "DR.", "DRA.", "MTRO.", "MTRA.", "LIC.", "ING.", "ISC.",
        "M.C.", "M C.", "M.A.", "M A.", "PROF.", "ARQ."
    ]
    upper_n = n.upper()
    for p in prefijos:
        if upper_n.startswith(p):
            n = n[len(p):].strip()
            break
    n = n.replace(".", " ")
    n = unicodedata.normalize("NFD", n)
    n = "".join(ch for ch in n if unicodedata.category(ch) != "Mn")
    n = re.sub(r"\s+", " ", n).strip()
    return n

def link_profesor_ingenieriatracker(nombre_profesor):
    """
    Genera el link directo al perfil del profesor en IngenieriaTracker.
    Ejemplo:
      'JOSE SALVADOR SALINAS TELESFORO'
      -> https://www.ingenieriatracker.com/#/profesores/JOSE-SALVADOR-SALINAS-TELESFORO
    """
    nombre_limpio = limpiar_nombre_profesor(nombre_profesor)
    if not nombre_limpio:
        return None

    slug = nombre_limpio.strip().upper().replace(" ", "-")
    slug = re.sub(r"-+", "-", slug)

    return f"https://www.ingenieriatracker.com/#/profesores/{slug}"

def consultar_ingenieria_tracker(nombre_profesor):
    """
    Retorna dict con:
      - promedio (float) o None
      - num_resenas (int) o None
      - nombre_api (str) o None
    """
    nombre_limpio = limpiar_nombre_profesor(nombre_profesor)
    if not nombre_limpio:
        return {"promedio": None, "num_resenas": None, "nombre_api": None}

    nombre_url = urllib.parse.quote(nombre_limpio)
    url = f"https://api.ingenieriatracker.com/searchProfesor?name={nombre_url}"

    try:
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            datos = response.json()
            if datos and len(datos) > 0:
                primero = datos[0]
                return {
                    "promedio": primero.get("promedio", None),
                    "num_resenas": primero.get("num_resenas", None),
                    "nombre_api": primero.get("nombre", None),
                }
        return {"promedio": None, "num_resenas": None, "nombre_api": None}
    except:
        return {"promedio": None, "num_resenas": None, "nombre_api": None}

def refrescar_vacantes():
    n_actualizados = 0
    with st.spinner("Actualizando cupos en tiempo real..."):
        for materia in st.session_state.materias_db:
            if materia.get("es_bloqueo", False):
                continue
            clave_raw = str(materia.get("materia", "")).split(" - ")[0].strip()
            if not clave_raw.isdigit():
                continue

            datos_nuevos_lista = obtener_datos_unam(clave_raw, materia.get("obligatoria", False))

            if datos_nuevos_lista:
                datos_nuevos = datos_nuevos_lista[0]

                for g_viejo in materia["grupos"]:
                    if g_viejo.get("gpo") == "N/A":
                        continue

                    for g_nuevo in datos_nuevos["grupos"]:
                        if g_nuevo.get("gpo") == g_viejo.get("gpo"):
                            g_viejo["vacantes"] = g_nuevo.get("vacantes", g_viejo.get("vacantes", 0))
                            n_actualizados += 1
                            break

    st.success(f"Se actualizaron {n_actualizados} grupos.")

# --- CARGA DE CATÁLOGO DE MATERIAS ---
@st.cache_data
def cargar_nombres_materias():
    url = "https://www.ssa.ingenieria.unam.mx/cj/tmp/programacion_horarios/listaAsignatura.js"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            patron = r"asignatura\['(\d+)'\]\s*=\s*'([^']+)';"
            coincidencias = re.findall(patron, response.text)

            diccionario_nombres = {clave: nombre for clave, nombre in coincidencias}
            return diccionario_nombres
        else:
            return {}
    except requests.exceptions.Timeout:
        st.error("El servidor de la UNAM tardó demasiado en responder al cargar el catálogo.")
        return {}
    except Exception as e:
        st.error(f"No se pudo cargar el catálogo de materias: {e}")
        return {}

CATALOGO_MATERIAS = cargar_nombres_materias()

# --- LÓGICA DEL PARSER ---
def obtener_datos_unam(clave_materia, es_obligatoria):
    clave_materia = str(clave_materia)
    try:
        clave_int = str(int(clave_materia))
    except:
        return []


    nombre_limpio = CATALOGO_MATERIAS.get(clave_int, "MATERIA DESCONOCIDA")
    nombre_materia = f"{clave_int} - {nombre_limpio}"

    url = f"https://www.ssa.ingenieria.unam.mx/cj/tmp/programacion_horarios/{clave_int}.html"

    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200: return None

        soup = BeautifulSoup(response.text, 'html.parser')

        tablas = soup.find_all('table')
        if not tablas: return []

        datos_materia = {
            "materia": nombre_materia,
            "obligatoria": es_obligatoria,
            "grupos": []
        }

        for tabla in tablas:
            filas = tabla.find_all('tr')
            for fila in filas:
                celdas = fila.find_all('td')
                datos = [c.get_text(strip=True) for c in celdas]

                if len(datos) < 7: continue
                if datos[0] == "Clave": continue

                gpo = datos[1]
                # --- PROFESOR Y MODALIDAD---
                profesor_raw = datos[2].replace("\n", " ").strip()

                modalidad = None
                match_modalidad = re.search(r"\(([^)]+)\)", profesor_raw)
                if match_modalidad:
                    modalidad = match_modalidad.group(1).strip().upper()
                profesor_limpio = re.sub(r"\([^)]*\)", "", profesor_raw).strip()
                profesor_limpio = re.sub(
                    r"^(ING\.|DR\.|DRA\.|M\.I\.|M\. EN I\.|MC\.|MTRO\.|MTRA\.|LIC\.|ARQ\.)\s+",
                    "",
                    profesor_limpio,
                    flags=re.IGNORECASE
                ).strip()

                profesor = profesor_limpio

                horario = datos[4]
                dias_str = datos[5]
                try:
                    vacantes = int(datos[-1])
                except:
                    vacantes = 0

                intervalos = extraer_intervalos(horario, dias_str.split(','))

                datos_materia["grupos"].append({
                    "gpo": gpo,
                    "profesor": profesor,
                    "profesor_raw": profesor_raw,
                    "modalidad": modalidad,
                    "horario": horario,
                    "dias": dias_str,
                    "intervalos": intervalos,
                    "calificacion": 10,
                    "materia_nombre": nombre_materia,
                    "vacantes": vacantes,
                    "activo": vacantes > 0,
                    "api_consultado": False,
                    "sugerencia_api": None,
                    "api_num_resenas": None,
                    "api_nombre_match": None
                })

        if not datos_materia["grupos"]: return []
        return [datos_materia]

    except requests.exceptions.Timeout:
        st.error(f"Tiempo de espera agotado al buscar la clave {clave_int}. Intenta de nuevo.")
        return []
    except Exception as e:
        st.error(f"Error técnico: {e}")
        return []

# --- LÓGICA DE VALIDACIÓN Y SCORE ---
def hay_traslape(g1, g2):
    for s1 in g1['intervalos']:
        for s2 in g2['intervalos']:
            if s1['dia'] == s2['dia']:
                if s1['inicio'] < s2['fin'] and s1['fin'] > s2['inicio']:
                    return True
    return False

def es_horario_valido(combinacion):
    for i in range(len(combinacion)):
        for j in range(i + 1, len(combinacion)):
            if hay_traslape(combinacion[i], combinacion[j]):
                return False
    return True

def calcular_score(combinacion, pesos):
    grupos_reales = [g for g in combinacion if g['gpo'] != "N/A"]
    if not grupos_reales: return -1000

    score = 0

    huecos = 0
    for dia in ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"]:
        clases = sorted([s for g in grupos_reales for s in g['intervalos'] if s['dia'] == dia], key=lambda x: x['inicio'])
        for i in range(len(clases)-1):
            huecos += (clases[i+1]['inicio'] - clases[i]['fin']) / 60
    score -= huecos * pesos['huecos']

    promedio_p = sum(g['calificacion'] for g in grupos_reales) / len(grupos_reales)
    score += promedio_p * pesos['profes']

    start_times = [s['inicio'] for g in grupos_reales for s in g['intervalos']]
    end_times = [s['fin'] for g in grupos_reales for s in g['intervalos']]

    if start_times and end_times:
        primer_inicio = min(start_times)
        ultima_salida = max(end_times)

        if pesos['tipo_turno'] == "Mañana (Temprano)":
            score += ((1440 - ultima_salida) / 60) * pesos['peso_turno']
        elif pesos['tipo_turno'] == "Tarde / Noche":
            score += (primer_inicio / 60) * pesos['peso_turno']
        else:
            pass

    score += len(grupos_reales) * pesos['carga']

    return score

# EXPORTACIÓN A CALENDARIO (.ics)
def _proxima_fecha_para_dia(dia_str):
    """
    Regresa una fecha (datetime.date) para el próximo día de la semana indicado.
    No importa el semestre real: solo sirve como plantilla para el calendario.
    """
    mapa = {"Lun": 0, "Mar": 1, "Mie": 2, "Jue": 3, "Vie": 4, "Sab": 5}
    if dia_str not in mapa:
        return datetime.today().date()

    hoy = datetime.today().date()
    delta = (mapa[dia_str] - hoy.weekday()) % 7
    if delta == 0:
        delta = 7 
    return hoy + timedelta(days=delta)

def generar_ics_desde_opcion(materias_combinadas, nombre_calendario="Horario FI UNAM"):
    """
    Convierte una combinación (lista de grupos) en texto ICS.
    """
    ics = []
    ics.append("BEGIN:VCALENDAR")
    ics.append("VERSION:2.0")
    ics.append("PRODID:-//FI UNAM Scheduler//Streamlit//ES")
    ics.append("CALSCALE:GREGORIAN")
    ics.append(f"X-WR-CALNAME:{nombre_calendario}")
    for g in materias_combinadas:
        if g.get("gpo") == "N/A":
            continue

        materia_nombre = g.get("materia_nombre", "Materia")
        profesor = g.get("profesor", "")
        modalidad = g.get("modalidad", "")
        horario = g.get("horario", "")
        dias = g.get("dias", "")
        vacantes = g.get("vacantes", "")
        summary = f"{materia_nombre} | GPO {g.get('gpo','')}"
        desc = f"Profesor: {profesor}"
        if modalidad:
            desc += f" ({modalidad})"
        desc += f"\\nHorario: {dias} {horario}"
        desc += f"\\nVacantes: {vacantes}"
        for s in g.get("intervalos", []):
            dia = s.get("dia")
            fecha = _proxima_fecha_para_dia(dia)

            inicio_min = s.get("inicio", 0)
            fin_min = s.get("fin", 0)

            dtstart = datetime.combine(fecha, datetime.min.time()) + timedelta(minutes=inicio_min)
            dtend = datetime.combine(fecha, datetime.min.time()) + timedelta(minutes=fin_min)

            # Formato ICS: YYYYMMDDTHHMMSS
            dtstart_str = dtstart.strftime("%Y%m%dT%H%M%S")
            dtend_str = dtend.strftime("%Y%m%dT%H%M%S")

            uid = f"{materia_nombre}-{g.get('gpo','')}-{dia}-{dtstart_str}@fiunam"

            ics.append("BEGIN:VEVENT")
            ics.append(f"UID:{uid}")
            ics.append(f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
            ics.append(f"DTSTART:{dtstart_str}")
            ics.append(f"DTEND:{dtend_str}")
            ics.append(f"SUMMARY:{summary}")
            ics.append(f"DESCRIPTION:{desc}")
            ics.append("END:VEVENT")

    ics.append("END:VCALENDAR")
    return "\n".join(ics)

# EXPORTACIÓN COMO IMAGEN (PNG)

def dataframe_a_png(df_text):
    """
    Renderiza un DataFrame como imagen PNG con matplotlib.
    Devuelve bytes PNG.
    """
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.axis("off")

    tabla = ax.table(
        cellText=df_text.values,
        colLabels=df_text.columns,
        rowLabels=df_text.index,
        loc="center",
        cellLoc="center"
    )

    tabla.auto_set_font_size(False)
    tabla.set_fontsize(8)
    tabla.scale(1.0, 1.2)

    buf = io.BytesIO()
    plt.tight_layout()
    fig.savefig(buf, format="png", dpi=200)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

# --- INTERFAZ DE USUARIO ---
st.title("Generador de Horarios")

if 'materias_db' not in st.session_state:
    st.session_state.materias_db = []
if "api_cache_profes" not in st.session_state:
    st.session_state.api_cache_profes = {}

# --- GUÍA DE USO DETALLADA ---
with st.expander("Instrucciones de uso (Actualizado)", expanded=False):
    st.markdown("""
    ### Pasos rápidos:
    Para más detalles, consulta la guía en [GitHub](https://github.com/Prevaricare/Creador-de-hoarios-fi-unam/tree/main).

    **1. Busca tu Clave:**
    Si no sabes la clave de tu materia (ej. 1120, 1601), consúltala en los [Mapas Curriculares Oficiales](http://escolar.ingenieria.unam.mx/mapas/).

    **2. Ingresa y Agrega:**
    Escribe las claves en el menú de la izquierda. Puedes ingresarlas **una por una** o **varias juntas separadas por comas** (ej. `1730` o `1120, 1601, 32`) y presiona **Buscar y Agregar Materias**.

    **3. Revisa Grupos y Cupos:**
    En la lista de la derecha verás los grupos disponibles con sus vacantes.
    * **Desmarca la casilla** ☑️ de los grupos que no te interesen para que el generador los ignore.
    * Usa **🔄 Refrescar Cupos** para actualizar vacantes sin borrar tus materias.

    **4. Consulta Promedios de Profesores (Nuevo):**
    Dentro de cada materia, presiona **🔍 Buscar sugerencias de calificación (IngenieriaTracker)** para mostrar una **sugerencia de promedio** por profesor.

    * Esta sugerencia **NO modifica** tu calificación manual.
    * Si no hay coincidencia, se mostrará **"No encontrado"**.
    * Puedes dar click en **(reseñas: #)** para abrir el perfil del profesor.

    **5. Personaliza:**
    * **Bloqueos:** Agrega tus horas de comida, trabajo o traslado en el panel izquierdo ("Actividad Manual").
    * **Pesos:** En la barra lateral, ajusta qué es más importante (evitar huecos, preferencia de turno, etc.).

    **6. Genera:**
    Presiona el botón al final para ver las mejores combinaciones posibles.

    ⚠️ **Aviso importante:** Esta app **NO es dueña** de IngenieriaTracker ni está afiliada.  
    Todo el crédito de la base de datos de las reseñas a **www.ingenieriatracker.com**.
    """)


# --- BARRA LATERAL (CONFIGURACIÓN) ---
with st.sidebar:
    st.header("Configuración de Pesos")
    st.info("Personaliza qué es lo más importante para ti.")

    st.markdown("---")

    tipo_turno = st.selectbox("Preferencia de Turno",
                              ["Mañana (Temprano)", "Tarde / Noche", "Mixto"],
                              help="Elige en qué momento del día prefieres tomar clases.")

    w_turno = st.slider("Importancia del Turno", 0, 100, 30,
                        help="Qué tanto debe esforzarse el sistema por respetar tu preferencia de mañana o tarde.")

    w_huecos = st.slider("Minimizar horas muertas", 0, 100, 50,
                         help="Busca juntar tus clases para que no tengas tiempos libres excesivos entre ellas.")

    w_profes = st.slider("Calificación de profesores", 0, 100, 70,
                         help="Da prioridad a los profesores con mayor calificación.")

    w_carga = st.slider("Cantidad de materias", 0, 100, 80,
                        help="Intenta inscribir el mayor número posible de materias de tu lista.")

    pesos = {
        "huecos": w_huecos,
        "profes": w_profes,
        "tipo_turno": tipo_turno,
        "peso_turno": w_turno,
        "carga": w_carga
    }

    st.markdown("---")

    # --- CARGA MASIVA DE CALIFICACIONES ---
    with st.expander(" Carga Masiva de Calificaciones desde Excel `experimental`", expanded=False):
        st.info("Pega aquí tus celdas de Excel. El sistema buscará el nombre del profesor y actualizará su nota.")

        st.markdown("ℹ **Para más información y ejemplos:** [Ver guía en GitHub](https://github.com/Prevaricare/Creador-de-hoarios-fi-unam/tree/main)")

        raw_data = st.text_area("Pegar datos de Excel:", height=150, placeholder="Clave\tGpo\tProfesor...\tCalificación")

        if st.button("Aplicar Calificaciones Masivas"):
            if not raw_data:
                st.warning("El cuadro está vacío.")
            else:
                lines = raw_data.split('\n')
                count_updates = 0

                califs_dict = {}
                for line in lines:
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        try:
                            nombre_profe = parts[2].replace("(PRESENCIAL)", "").replace("\n", " ").strip()
                            calif_str = parts[-1].strip()

                            valor_float = float(calif_str)
                            califs_dict[nombre_profe] = round(valor_float, 2)
                        except:
                            continue

                if not califs_dict:
                    st.error("No se detectó el formato correcto (Tabulaciones de Excel).")
                else:
                    for i, materia in enumerate(st.session_state.materias_db):
                        for j, grupo in enumerate(materia['grupos']):
                            profe_actual = grupo['profesor']
                            nueva_calif = None

                            if profe_actual in califs_dict:
                                nueva_calif = califs_dict[profe_actual]
                            else:
                                for k_profe, v_calif in califs_dict.items():
                                    if k_profe in profe_actual or profe_actual in k_profe:
                                        nueva_calif = v_calif
                                        break

                            if nueva_calif is not None:
                                st.session_state.materias_db[i]['grupos'][j]['calificacion'] = nueva_calif
                                count_updates += 1

                                widget_key = f"cal_{i}_{j}"
                                if widget_key in st.session_state:
                                    st.session_state[widget_key] = nueva_calif

                    if count_updates > 0:
                        st.success(f"✅ ¡Se actualizaron {count_updates} profesores!")
                        st.rerun()
                    else:
                        st.warning("No encontré coincidencias de nombres.")

# --- COLUMNAS PRINCIPALES ---
col_in, col_list = st.columns([1, 1.2])

# ==========================================
# COLUMNA IZQUIERDA: ENTRADA DE DATOS
# ==========================================
with col_in:
    st.subheader("1. Carga de Materias")
    tipo = st.radio("Categoría:", ["Obligatorio", "Opcional"], horizontal=True)

    clave_input = st.text_input(
        "Ingresa clave por clave o juntas separadas por comas:",
        placeholder="Ejemplo: 1730 ó 1120, 1601, 32"
    )

    if st.button("Buscar y Agregar Materias", width="stretch"):
        lista_claves = [c.strip() for c in clave_input.split(',') if c.strip()]

        if not lista_claves:
            st.warning("Por favor ingresa al menos una clave.")
        else:
            agregadas = []
            errores = []

            barra = st.progress(0)

            for i, clave_raw in enumerate(lista_claves):
                if clave_raw.isdigit():
                    clave_limpia = str(int(clave_raw))

                    nuevas = obtener_datos_unam(clave_limpia, tipo == "Obligatorio")

                    if nuevas:
                        nombre = nuevas[0]['materia']
                        st.session_state.materias_db.extend(nuevas)
                        agregadas.append(nombre)
                    else:
                        errores.append(f"Clave {clave_limpia}: No encontrada")
                else:
                    errores.append(f"'{clave_raw}' no es una clave válida")

                barra.progress((i + 1) / len(lista_claves))

            barra.empty()

            if agregadas:
                st.success(f"✅ Se agregaron {len(agregadas)} asignaturas correctamente.")
                st.caption(f"Agregadas: {', '.join([m.split(' - ')[0] for m in agregadas])}")

            if errores:
                for e in errores:
                    st.error(f"❌ {e}")

    # --- AGREGAR ACTIVIDAD MANUAL / BLOQUEO ---
    with st.expander("Agregar Actividad Manual / Bloqueo", expanded=False):
        st.info("Bloquea horarios para Trabajo, Comida, Transporte, etc.")
        act_nombre = st.text_input("Nombre de la actividad", "Actividad Personal")
        act_dias = st.multiselect("Días", ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"])

        c_hora1, c_hora2 = st.columns(2)
        t_inicio = c_hora1.time_input("Inicio")
        t_fin = c_hora2.time_input("Fin")

        if st.button("Agregar Bloqueo", width="stretch"):
            if act_nombre and act_dias:
                str_horario = f"{t_inicio.strftime('%H:%M')} a {t_fin.strftime('%H:%M')}"
                intervalos_manual = extraer_intervalos(str_horario, act_dias)

                materia_manual = {
                    "materia": act_nombre,
                    "obligatoria": True,
                    "es_bloqueo": True,

                    "grupos": [{
                        "gpo": "Único",
                        "profesor": "Tú",
                        "horario": str_horario,
                        "dias": ", ".join(act_dias),
                        "intervalos": intervalos_manual,
                        "calificacion": 10,
                        "materia_nombre": act_nombre,
                        "vacantes": 999,
                        "activo": True,

                        "sugerencia_api": None,
                        "api_num_resenas": None,
                        "api_nombre_match": None,
                        "api_consultado": False,
                        "modalidad": None,
                        "profesor_raw": "Tú",
                    }]
                }


                st.session_state.materias_db.append(materia_manual)
                st.success(f"Bloqueo '{act_nombre}' agregado.")
            else:
                st.error("Debes poner un nombre y seleccionar al menos un día.")

# ==========================================
# COLUMNA DERECHA: LISTA Y GESTIÓN
# ==========================================
with col_list:
    c_header_1, c_header_2 = st.columns([2, 1])
    c_header_1.subheader("2. Materias Registradas")
    if c_header_2.button("🔄 Refrescar Cupos"):
        refrescar_vacantes()
        st.rerun()

    if not st.session_state.materias_db:
        st.info("Tu lista está vacía. Comienza ingresando una clave a la izquierda.")

    for i, m in enumerate(st.session_state.materias_db):
        status = " (Opcional)" if not m['obligatoria'] else ""

        with st.expander(f"{m['materia']}{status}", expanded=True):
            c_api_1, c_api_2 = st.columns([1, 1])
            if c_api_1.button("🔍 Buscar sugerencias de Calificacion", key=f"api_mat_{i}", width="stretch"):
                grupos_actualizados = 0
                no_encontrados = 0

                with st.spinner("Consultando promedios de profesores..."):
                    for j, g in enumerate(m['grupos']):
                        if g.get("gpo") == "N/A":
                            continue
                        if g.get("profesor") == "Tú":
                            continue

                        nombre_original = g.get("profesor", "")
                        nombre_limpio = limpiar_nombre_profesor(nombre_original)
                        if nombre_limpio in st.session_state.api_cache_profes:
                            resultado = st.session_state.api_cache_profes[nombre_limpio]
                        else:
                            resultado = consultar_ingenieria_tracker(g.get("profesor", ""))
                            st.session_state.api_cache_profes[nombre_limpio] = resultado
                        st.session_state.materias_db[i]['grupos'][j]['api_consultado'] = True

                        promedio = resultado.get("promedio", None)

                        if promedio is not None:
                            st.session_state.materias_db[i]['grupos'][j]['sugerencia_api'] = promedio
                            st.session_state.materias_db[i]['grupos'][j]['api_num_resenas'] = resultado.get("num_resenas", None)
                            st.session_state.materias_db[i]['grupos'][j]['api_nombre_match'] = resultado.get("nombre_api", None)
                            grupos_actualizados += 1
                        else:
                            st.session_state.materias_db[i]['grupos'][j]['sugerencia_api'] = None
                            st.session_state.materias_db[i]['grupos'][j]['api_num_resenas'] = None
                            st.session_state.materias_db[i]['grupos'][j]['api_nombre_match'] = None
                            no_encontrados += 1

                c_api_2.success(f"✅ API: {grupos_actualizados} encontrados | ❌ {no_encontrados} no encontrados")
                st.rerun()

            if st.button(f"Eliminar asignatura", key=f"del_mat_{i}"):
                st.session_state.materias_db.pop(i)
                st.rerun()

            for j, g in enumerate(m['grupos']):
                if g['gpo'] == "N/A": continue

                c_check, c_info, c_calif = st.columns([0.22, 0.58, 0.20])

                activo = c_check.toggle(
                    "Incluir",
                    value=g.get('activo', True),
                    key=f"tgl_{i}_{j}",
                    label_visibility="collapsed"
                )

                st.session_state.materias_db[i]['grupos'][j]['activo'] = activo

                vacs = g.get('vacantes', 0)
                color_vac = "green" if vacs > 5 else ("orange" if vacs > 0 else "red")
                sug = g.get("sugerencia_api", None)
                num_res = g.get("api_num_resenas", None)
                consultado = g.get("api_consultado", False)

                sug_txt = ""

                if consultado:
                    if sug is not None:
                        sug_txt = f"⭐ Sugerencia Calificacion: <strong>{float(sug):.2f}</strong>"

                        if num_res is not None:
                            # Preferimos el nombre exacto que regresó la API (mejor match)
                            nombre_match = g.get("api_nombre_match") or g.get("profesor", "")
                            link = link_profesor_ingenieriatracker(nombre_match)

                            if link:
                                sug_txt += (
                                    f" <a href='{link}' target='_blank' style='color:gray; text-decoration:none;'>"
                                    f"(Ver reseñas: {num_res})</a>"
                                )
                            else:
                                sug_txt += f" <span style='color:gray'>(reseñas: {num_res})</span>"

                    else:
                        sug_txt = "<span style='color:gray;'>⭐ Sugerencia Calificacion: No encontrado</span>"

                vacs = g.get('vacantes', 0)
                color_vac = "green" if vacs > 5 else ("orange" if vacs > 0 else "red")
                modalidad = g.get("modalidad", None)
                modalidad_txt = f" <span style='color: #555;'>({modalidad})</span>" if modalidad else ""

                info_html = f"""
                <div style="font-size: 0.9em;">
                    <strong>Gpo {g['gpo']}</strong> - {g['profesor']}{modalidad_txt}<br>
                    📅 {g['dias']} ({g['horario']})<br>
                    Vacantes: <strong style='color: {color_vac}'>{vacs}</strong><br>
                    {sug_txt}
                </div>
                """
                c_info.markdown(info_html, unsafe_allow_html=True)

                if g['profesor'] != "Tú":
                    key_widget = f"cal_{i}_{j}"

                    if key_widget not in st.session_state:
                        st.session_state[key_widget] = float(g['calificacion'])

                    nueva_calif = c_calif.number_input(
                        "Calif.",
                        min_value=0.0,
                        max_value=10.0,
                        step=0.01,
                        format="%.2f",
                        key=key_widget,
                        label_visibility="collapsed"
                    )

                    nueva_calif = round(nueva_calif, 2)

                    st.session_state.materias_db[i]['grupos'][j]['calificacion'] = nueva_calif

                    st.markdown(
                        "<div style='height:6px; border-bottom: 1px solid rgba(200,200,200,0.25); margin: 6px 0;'></div>",
                        unsafe_allow_html=True
                    )

# --- BOTÓN DE GENERACIÓN ---
st.markdown("---")
if st.button("Generar combinaciones optimizadas", width="stretch"):
    if not st.session_state.materias_db:
        st.error("No puedes generar horarios sin materias. Agrega al menos una.")
    else:
        grupos_input = []

        for m in st.session_state.materias_db:
            grupos_validos = [g for g in m['grupos'] if g.get('activo', True)]

            if grupos_validos:
                grupos_input.append(grupos_validos)
            else:
                if m['obligatoria']:
                    st.error(f"Error: Has desactivado todos los grupos de {m['materia']}. Debes dejar al menos uno activo.")
                    st.stop()

        total_comb = 1
        for g in grupos_input:
            total_comb *= len(g)

        if total_comb > 5_000_000:
            st.warning(
                f"⚠️ Se detectaron {total_comb:,} combinaciones posibles. "
                "Esto podría saturar la memoria. Intenta desactivar algunos grupos o reducir materias."
            )

        generador_comb = itertools.product(*grupos_input)

        # ==========================================================
        # TOP-10 incremental (NO guarda todas las combinaciones)
        # ==========================================================
        TOP_K = 10
        top_heap = []

        MAX_COMBINACIONES_A_REVISAR = 1000000
        barra_progreso = st.progress(0)

        for idx, comb in enumerate(generador_comb):
            if idx >= MAX_COMBINACIONES_A_REVISAR:
                st.warning(
                    f"Se revisaron las primeras {MAX_COMBINACIONES_A_REVISAR} combinaciones "
                    "y se detuvo para no saturar."
                )
                break

            if es_horario_valido(comb):
                sc = calcular_score(comb, pesos)

                if len(top_heap) < TOP_K:
                    heapq.heappush(top_heap, (sc, idx, comb))
                else:
                    if sc > top_heap[0][0]:
                        heapq.heapreplace(top_heap, (sc, idx, comb))

            if idx % 5000 == 0:
                progreso_val = min(idx / min(total_comb, MAX_COMBINACIONES_A_REVISAR), 1.0)
                barra_progreso.progress(progreso_val)
        barra_progreso.progress(1.0)
        top_heap_sorted = sorted(top_heap, key=lambda x: x[0], reverse=True)
        posibles = [{"materias": comb, "score": sc} for (sc, _, comb) in top_heap_sorted]

        # ==========================================================
        # Mostrar resultados
        # ==========================================================
        if posibles:
            st.success("¡Horarios generados con éxito!")
            tabs = st.tabs([f"Opción {i+1}" for i in range(len(posibles))])

            colores = [
                "#FFCDD2", "#C5CAE9", "#B2DFDB", "#FFF9C4", "#E1BEE7",
                "#FFCCBC", "#D7CCC8", "#F0F4C3", "#B3E5FC", "#DCEDC8",
                "#F8BBD0", "#CFD8DC"
            ]

            for i, tab in enumerate(tabs):
                with tab:
                    opcion = posibles[i]

                    # ============================
                    # HEADER COMPACTO + EXPORT
                    # ============================
                    c_top1, c_top2, c_top3 = st.columns([1.2, 1, 1])
                    c_top1.markdown(
                        f"<div style='font-size: 0.95em; color: #444;'><strong>Score:</strong> {opcion['score']:.2f}</div>",
                        unsafe_allow_html=True
                    )
                    ics_text = generar_ics_desde_opcion(
                        opcion["materias"],
                        nombre_calendario=f"Horario - Opción {i+1}"
                    )

                    c_top2.download_button(
                        label="Exportar a Calendario (.ics)",
                        data=ics_text.encode("utf-8"),
                        file_name=f"horario_opcion_{i+1}.ics",
                        mime="text/calendar",
                        use_container_width=True
                    )

                    btn_png_slot = c_top3.empty()
                    horas_labels = []
                    for h in range(7, 22):
                        horas_labels.append(f"{h:02d}:00")
                        horas_labels.append(f"{h:02d}:30")
                    dias_cols = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"]
                    df_text = pd.DataFrame("", index=horas_labels, columns=dias_cols)
                    df_color = pd.DataFrame("", index=horas_labels, columns=dias_cols)
                    materia_color_map = {}
                    color_idx = 0
                    for m_g in opcion['materias']:
                        if m_g['gpo'] == "N/A":
                            continue
                        nombre_mat = m_g['materia_nombre']
                        if nombre_mat not in materia_color_map:
                            materia_color_map[nombre_mat] = colores[color_idx % len(colores)]
                            color_idx += 1
                        bg_color = materia_color_map[nombre_mat]
                        if " - " in nombre_mat:
                            partes = nombre_mat.split(' - ')
                            clave = partes[0]
                            nombre_limpio = partes[1]
                        else:
                            clave = ""
                            nombre_limpio = nombre_mat
                        nombre_limpio = (nombre_limpio[:20] + '..') if len(nombre_limpio) > 20 else nombre_limpio
                        profesor_corto = m_g['profesor'].split('\n')[0][:18]
                        vacs_grupo = m_g.get("vacantes", None)
                        sin_cupo = False
                        try:
                            if vacs_grupo is not None and int(vacs_grupo) <= 0:
                                sin_cupo = True
                        except:
                            sin_cupo = False
                        tag_cupo = " ⚠️SIN CUPO" if sin_cupo else ""
                        for s in m_g['intervalos']:
                            h_i = f"{s['inicio']//60:02d}:{'30' if (s['inicio']%60 >= 30) else '00'}"
                            h_f = f"{s['fin']//60:02d}:{'30' if (s['fin']%60 >= 30) else '00'}"
                            if h_i in horas_labels and h_f in horas_labels:
                                start_idx = horas_labels.index(h_i)
                                end_idx = horas_labels.index(h_f)
                                duracion_bloques = end_idx - start_idx
                                for counter, h_idx in enumerate(range(start_idx, end_idx)):
                                    dia = s['dia']
                                    if dia in dias_cols:
                                        if sin_cupo:
                                            estilo = f"background-color: {bg_color}; color: #000000; border: 2px solid #ff4d4d;"
                                        else:
                                            estilo = f"background-color: {bg_color}; color: #000000;"
                                        df_color.at[horas_labels[h_idx], dia] = estilo
                                        texto_celda = ""
                                        if duracion_bloques == 1:
                                            if counter == 0:
                                                texto_celda = f"GPO {m_g['gpo']} ({clave}) {nombre_limpio}{tag_cupo}"
                                        elif duracion_bloques == 2:
                                            if counter == 0:
                                                texto_celda = f"GPO {m_g['gpo']} ({clave}){tag_cupo}"
                                            if counter == 1:
                                                texto_celda = f"{nombre_limpio}"
                                        elif duracion_bloques >= 3:
                                            if counter == 0:
                                                texto_celda = f"GPO {m_g['gpo']} ({clave}){tag_cupo}"
                                            if counter == 1:
                                                texto_celda = f"{nombre_limpio}"
                                            if counter == 2:
                                                texto_celda = f"{profesor_corto}"
                                        df_text.at[horas_labels[h_idx], dia] = texto_celda

                    # ============================
                    # BOTÓN PNG COMPACTO (ARRIBA)
                    # ============================
                    try:
                        png_bytes = dataframe_a_png(df_text)
                        btn_png_slot.download_button(
                            label="Descargar imagen (.png)",
                            data=png_bytes,
                            file_name=f"horario_opcion_{i+1}.png",
                            mime="image/png",
                            use_container_width=True
                        )
                    except:
                        btn_png_slot.button("Descargar imagen (.png)", disabled=True, use_container_width=True)

                    st.dataframe(
                        df_text.style.apply(lambda x: df_color, axis=None),
                        height=900,
                        width="stretch"
                    )
        else:
            st.warning(
                "No se encontraron combinaciones válidas. "
                "Intenta relajar tus restricciones (ej. permitir huecos o más turnos)."
            )
        del posibles
        del top_heap
        del top_heap_sorted
        gc.collect()

# --- PIE DE PÁGINA ---
st.markdown("---")
footer_col1, footer_col2, footer_col3 = st.columns([3, 2, 3])
with footer_col2:
    st.markdown("<div style='text-align: center; color: gray; font-size: 0.9em;'>Gael prevaricare</div>", unsafe_allow_html=True)
    st.link_button("Instagram", "https://www.instagram.com/gaelprevaricare/", width="stretch")
