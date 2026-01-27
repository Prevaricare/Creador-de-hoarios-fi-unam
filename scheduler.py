import streamlit as st
import pandas as pd
import re
import itertools

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Generador de Horarios", layout="wide")

# --- LÓGICA DEL PARSER ---
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

def parsear_texto(texto_sucio, es_obligatoria):
    materias = []
    # Detecta claves de 2, 3 y 4 dígitos seguidas de un guion
    bloques = re.split(r'(\b\d{2,4}\s+-\s+.+)', texto_sucio)
    
    for i in range(1, len(bloques), 2):
        nombre_materia = bloques[i].split('\t')[0].strip()
        cuerpo = bloques[i+1].strip()
        datos_materia = {"materia": nombre_materia, "obligatoria": es_obligatoria, "grupos": []}
        
        # Regex ajustado para capturar correctamente la información
        patron_grupo = r'(\d+)\s+([A-ZÁÉÍÓÚÑ\.\s]+(?:\n\(.+?\))?)\s+(?:[A-Z]\s+)?(\d{2}:\d{2}\s+a\s+\d{2}:\d{2})\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ\.,\s]+)'
        
        grupos_encontrados = re.findall(patron_grupo, cuerpo)
        
        for g in grupos_encontrados:
            intervalos = extraer_intervalos(g[2], g[3].split(','))
            profesor_limpio = re.sub(r'\n\(.+?\)', '', g[1]).strip()
            
            datos_materia["grupos"].append({
                "gpo": g[0], 
                "profesor": profesor_limpio, 
                "horario": g[2],
                "dias": g[3].strip(), 
                "intervalos": intervalos, 
                "calificacion": 10, 
                "materia_nombre": nombre_materia
            })
        
        if not es_obligatoria and datos_materia["grupos"]:
            datos_materia["grupos"].append({
                "gpo": "N/A", "profesor": "N/A", "horario": "S/H",
                "dias": "N/A", "intervalos": [], "calificacion": 0,
                "materia_nombre": "VACIO"
            })
            
        materias.append(datos_materia)
    return materias

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
    
    # 1. HUECOS
    huecos = 0
    # Incluye "Sab" en la lista de días
    for dia in ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"]:
        clases = sorted([s for g in grupos_reales for s in g['intervalos'] if s['dia'] == dia], key=lambda x: x['inicio'])
        for i in range(len(clases)-1):
            huecos += (clases[i+1]['inicio'] - clases[i]['fin']) / 60
    score -= huecos * pesos['huecos']
    
    # 2. PROFESORES
    promedio_p = sum(g['calificacion'] for g in grupos_reales) / len(grupos_reales)
    score += promedio_p * pesos['profes']
    
    # 3. TURNO (MAÑANA / TARDE / MIXTO)
    start_times = [s['inicio'] for g in grupos_reales for s in g['intervalos']]
    end_times = [s['fin'] for g in grupos_reales for s in g['intervalos']]
    
    if start_times and end_times:
        primer_inicio = min(start_times)
        ultima_salida = max(end_times)
        
        if pesos['tipo_turno'] == "Mañana (Temprano)":
            score += ((1440 - ultima_salida) / 60) * pesos['peso_turno']
        elif pesos['tipo_turno'] == "Tarde / Noche":
            score += (primer_inicio / 60) * pesos['peso_turno']
        else: # Mixto
            pass

    # 4. CARGA ACADÉMICA
    score += len(grupos_reales) * pesos['carga']
    
    return score

# --- INTERFAZ DE USUARIO ---
st.title("Generador de Horarios")

if 'materias_db' not in st.session_state:
    st.session_state.materias_db = []

# --- GUÍA DE USO DETALLADA ---
with st.expander("Instrucciones de uso", expanded=False):
    st.markdown("""
    ### Pasos rápidos:
    Para más detalles, consulta la guía en [GitHub](https://github.com/Prevaricare/Creador-de-hoarios-fi-unam/tree/main).

    **1. Copia:**
    Ve a [Horarios FI UNAM](https://www.ssa.ingenieria.unam.mx/horarios.html). Selecciona y copia todo el texto de la materia (desde el nombre hasta el último grupo).

    **2. Pega:**
    Pon el texto en el cuadro "Carga de Materias" y presiona **Procesar Materia**. Repite con todas tus asignaturas.

    **3. Personaliza:**
    * **Califica:** En la lista de "Materias Registradas", asigna un 10 a tus profesores favoritos y un 0 a los que quieras evitar.
    * **Bloquea:** Usa "Agregar Bloqueo" para reservar tiempo de trabajo, comida o transporte.

    **4. Configura:**
    En el menú de la izquierda, ajusta qué es prioridad para ti (Turno matutino/vespertino, evitar huecos, etc.).

    **5. Genera y Elige:**
    Presiona el botón **Generar combinaciones optimizadas**. Aparecerán 10 pestañas; revísalas y elige la que mejor se adapte a tu vida.
    """)
    
    st.markdown("**Ejemplo de cómo debe verse el texto copiado:**")
    st.code("""
1601 - COMPORTAMIENTO DE SUELOS
ASIGNATURA IMPARTIDA POR LA DICYG
http://escolar.ingenieria.unam.mx/asesoria/asesores/#DICYG
GRUPOS CON VACANTES
Clave   Gpo Profesor    Tipo    Horario Días    Cupo    Vacantes
1601    1   M.I. EDUARDO ALVAREZ CAZARES
(PRESENCIAL)    T   07:00 a 08:30   Lun, Mie, Vie   25  25
1601    2   ING. ARACELI ANGELICA SANCHEZ ENRIQUEZ
(PRESENCIAL)    T   08:30 a 10:00   Lun, Mie, Vie   25  25
    """, language="text")

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

# --- COLUMNAS PRINCIPALES ---
col_in, col_list = st.columns([1, 1.2])

with col_in:
    st.subheader("1. Carga de Materias")
    
    # SECCIÓN 1: PEGAR TEXTO OFICIAL
    tipo = st.radio("Categoría:", ["Obligatorio", "Opcional"], horizontal=True)
    raw_text = st.text_area("Pega el texto del portal aquí:", height=200, placeholder="Pega aquí el contenido copiado de la página de horarios...")
    
    if st.button("Procesar Materia", use_container_width=True):
        nuevas = parsear_texto(raw_text, tipo == "Obligatorio")
        if nuevas:
            st.session_state.materias_db.extend(nuevas)
            st.success(f"Se ha registrado correctamente: {len(nuevas)} materia(s).")
            st.rerun()
        else:
            st.error("No se detectaron grupos válidos. Verifica el formato.")

    # SECCIÓN 2: AGREGAR BLOQUEO MANUAL
    with st.expander("Agregar Actividad Manual / Bloqueo", expanded=False):
        st.write("Define un horario ocupado (Trabajo, Comida, etc.)")
        act_nombre = st.text_input("Nombre de la actividad", "Actividad Personal")
        act_dias = st.multiselect("Días", ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"])
        c_hora1, c_hora2 = st.columns(2)
        t_inicio = c_hora1.time_input("Inicio")
        t_fin = c_hora2.time_input("Fin")
        
        if st.button("Agregar Bloqueo"):
            if act_nombre and act_dias:
                # Convertimos la hora del input a formato "HH:MM a HH:MM"
                str_horario = f"{t_inicio.strftime('%H:%M')} a {t_fin.strftime('%H:%M')}"
                # Calculamos intervalos usando la función existente
                intervalos_manual = extraer_intervalos(str_horario, act_dias)
                
                # Creamos la estructura de materia ficticia
                materia_manual = {
                    "materia": act_nombre,
                    "obligatoria": True, # Obligatoria para que aparte el lugar sí o sí
                    "grupos": [{
                        "gpo": "Único",
                        "profesor": "Tú",
                        "horario": str_horario,
                        "dias": ", ".join(act_dias),
                        "intervalos": intervalos_manual,
                        "calificacion": 10, # Neutral/Alta para no afectar el score
                        "materia_nombre": act_nombre
                    }]
                }
                
                st.session_state.materias_db.append(materia_manual)
                st.success(f"Bloqueo '{act_nombre}' agregado.")
                st.rerun()
            else:
                st.error("Debes poner un nombre y seleccionar al menos un día.")


with col_list:
    st.subheader("2. Materias Registradas")
    if not st.session_state.materias_db:
        st.info("Tu lista está vacía. Comienza pegando una materia a la izquierda.")
    
    for i, m in enumerate(st.session_state.materias_db):
        status = " (Opcional)" if not m['obligatoria'] else ""
        
        with st.expander(f"{m['materia']}{status}"):
            if st.button(f"Eliminar materia", key=f"del_mat_{i}"):
                st.session_state.materias_db.pop(i)
                st.rerun()
            
            st.write("**Grupos detectados:**")
            
            for j, g in enumerate(m['groups' if 'groups' in m else 'grupos']):
                if g['gpo'] == "N/A": continue
                
                c1, c2, c3 = st.columns([1, 3, 1.5])
                c1.write(f"**Gpo {g['gpo']}**")
                c2.write(f"{g['profesor']}\n\n{g['dias']} ({g['horario']})")
                
                # Solo permitimos calificar si no es una actividad manual (Bloqueo)
                # Las actividades manuales tienen "Tú" como profesor por defecto
                if g['profesor'] != "Tú":
                    nueva_calif = c3.number_input(
                        "Calif:", 
                        min_value=0, max_value=10, 
                        value=g['calificacion'], 
                        key=f"cal_{i}_{j}",
                        help="10 = Excelente, 0 = Evitar"
                    )
                    st.session_state.materias_db[i]['grupos'][j]['calificacion'] = nueva_calif

st.divider()

# --- BOTÓN DE GENERACIÓN ---
if st.button("Generar combinaciones optimizadas", use_container_width=True):
    if not st.session_state.materias_db:
        st.error("No puedes generar horarios sin materias. Agrega al menos una.")
    else:
        grupos_input = [m['grupos'] for m in st.session_state.materias_db]
        
        posibles = []
        todas_comb = list(itertools.product(*grupos_input))
        progreso = st.progress(0)
        
        for idx, comb in enumerate(todas_comb):
            if es_horario_valido(comb):
                sc = calcular_score(comb, pesos)
                posibles.append({"materias": comb, "score": sc})
            if idx % 100 == 0:
                progreso.progress((idx+1)/len(todas_comb))
        
        posibles = sorted(posibles, key=lambda x: x['score'], reverse=True)[:10]
        
        if posibles:
            tabs = st.tabs([f"Opción {i+1}" for i in range(len(posibles))])
            for i, tab in enumerate(tabs):
                with tab:
                    st.write(f"**Puntaje de Excelencia:** {posibles[i]['score']:.2f}")
                    horas_labels = []
                    for h in range(7, 22):
                        horas_labels.append(f"{h:02d}:00")
                        horas_labels.append(f"{h:02d}:30")
                    
                    df_v = pd.DataFrame("", index=horas_labels, columns=["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"])
                    for m_g in posibles[i]['materias']:
                        if m_g['gpo'] == "N/A": continue 
                        for s in m_g['intervalos']:
                            h_i = f"{s['inicio']//60:02d}:{'30' if (s['inicio']%60 >= 30) else '00'}"
                            h_f = f"{s['fin']//60:02d}:{'30' if (s['fin']%60 >= 30) else '00'}"
                            if h_i in horas_labels and h_f in horas_labels:
                                start_idx = horas_labels.index(h_i)
                                end_idx = horas_labels.index(h_f)
                                for h_idx in range(start_idx, end_idx):
                                    if h_idx == start_idx:
                                        df_v.iloc[h_idx][s['dia']] = m_g['materia_nombre'][:20]
                                    elif h_idx == start_idx + 1:
                                        # Si es actividad personal, mostrar "Actividad", si no, el Profe
                                        texto_sec = "Personal" if m_g['profesor'] == "Tú" else m_g['profesor'][:18]
                                        df_v.iloc[h_idx][s['dia']] = texto_sec
                                    else:
                                        df_v.iloc[h_idx][s['dia']] = "|"
                    st.table(df_v)
        else:
            st.warning("No se encontraron combinaciones posibles sin traslapes.")

# --- PIE DE PÁGINA ---
st.markdown("---")
footer_col1, footer_col2, footer_col3 = st.columns([3, 2, 3])
with footer_col2:
    st.markdown("<div style='text-align: center; color: gray; font-size: 0.9em;'>Gael prevaricare</div>", unsafe_allow_html=True)
    st.link_button("Instagram", "https://www.instagram.com/gaelprevaricare/", use_container_width=True)