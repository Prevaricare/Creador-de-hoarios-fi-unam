import streamlit as st
import pandas as pd
import re
import itertools
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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
    """Convierte HH:MM a minutos; retorna None cuando el valor es inválido."""
    if hora_str is None:
        return None

    match = re.fullmatch(r"\s*(\d{1,2})\s*:\s*(\d{2})\s*", str(hora_str))
    if not match:
        return None

    h, m = map(int, match.groups())
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def normalizar_dia(dia):
    """Normaliza variantes comunes de los días usados por la FI."""
    valor = unicodedata.normalize("NFD", str(dia or "").strip())
    valor = "".join(ch for ch in valor if unicodedata.category(ch) != "Mn")
    valor = re.sub(r"[^A-Za-z]", "", valor).lower()
    mapa = {
        "lun": "Lun", "lunes": "Lun",
        "mar": "Mar", "martes": "Mar",
        "mie": "Mie", "miercoles": "Mie",
        "jue": "Jue", "jueves": "Jue",
        "vie": "Vie", "viernes": "Vie",
        "sab": "Sab", "sabado": "Sab",
    }
    return mapa.get(valor)


def extraer_intervalos(horario_str, dias_lista):
    """Extrae intervalos incluso si el sitio cambia espacios o usa guiones."""
    horario = str(horario_str or "").strip()
    match = re.search(
        r"(\d{1,2}\s*:\s*\d{2})\s*(?:a|al|[-–—])\s*(\d{1,2}\s*:\s*\d{2})",
        horario,
        flags=re.IGNORECASE,
    )
    if not match:
        return []

    inicio_min = hora_a_minutos(match.group(1))
    fin_min = hora_a_minutos(match.group(2))
    if inicio_min is None or fin_min is None or fin_min <= inicio_min:
        return []

    intervalos = []
    for dia_raw in dias_lista:
        dia = normalizar_dia(dia_raw)
        if dia:
            intervalos.append({"dia": dia, "inicio": inicio_min, "fin": fin_min})
    return intervalos

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
                            g_viejo["cupo"] = g_nuevo.get("cupo", g_viejo.get("cupo"))
                            g_viejo["vacantes"] = g_nuevo.get("vacantes", g_viejo.get("vacantes"))
                            g_viejo["horario"] = g_nuevo.get("horario", g_viejo.get("horario", ""))
                            g_viejo["dias"] = g_nuevo.get("dias", g_viejo.get("dias", ""))
                            g_viejo["intervalos"] = g_nuevo.get("intervalos", g_viejo.get("intervalos", []))
                            g_viejo["componentes"] = g_nuevo.get("componentes", g_viejo.get("componentes", []))
                            n_actualizados += 1
                            break

    st.success(f"Se actualizaron {n_actualizados} grupos.")

def cargar_grupos_actuales(texto_grupos, es_obligatorio=True):
    """
    Formato esperado (una línea por grupo):
        1601 2
        1120 3
        1730 1

    Agrega materias faltantes y deja activo solo el grupo indicado.
    """
    if not texto_grupos.strip():
        st.warning("No pegaste nada.")
        return

    lineas = [ln.strip() for ln in texto_grupos.split("\n") if ln.strip()]
    if not lineas:
        st.warning("No se detectaron líneas válidas.")
        return

    cargadas = 0
    actualizadas = 0
    errores = []

    for ln in lineas:
        # Acepta: "1601 2" o "1601-2" o "1601:2"
        ln_norm = ln.replace("-", " ").replace(":", " ")
        partes = [p for p in ln_norm.split() if p.strip()]

        if len(partes) < 2:
            errores.append(f"Formato inválido: '{ln}' (usa: 1601 2)")
            continue

        clave = partes[0].strip()
        gpo_obj = partes[1].strip()

        if not clave.isdigit():
            errores.append(f"Clave inválida: '{clave}' en '{ln}'")
            continue

        # Buscar si la materia ya existe
        idx_materia = None
        for i, mat in enumerate(st.session_state.materias_db):
            clave_mat = str(mat.get("materia", "")).split(" - ")[0].strip()
            if clave_mat == str(int(clave)):
                idx_materia = i
                break

        # Si no existe, cargarla desde UNAM
        if idx_materia is None:
            nuevas = obtener_datos_unam(str(int(clave)), es_obligatorio)
            if not nuevas:
                errores.append(f"No se pudo cargar la clave {clave}")
                continue
            st.session_state.materias_db.extend(nuevas)
            idx_materia = len(st.session_state.materias_db) - 1
            cargadas += 1

        # Activar solo el grupo indicado
        materia = st.session_state.materias_db[idx_materia]
        encontrado = False

        for j, g in enumerate(materia["grupos"]):
            if str(g.get("gpo", "")).strip() == str(gpo_obj):
                st.session_state.materias_db[idx_materia]["grupos"][j]["activo"] = True
                encontrado = True
            else:
                st.session_state.materias_db[idx_materia]["grupos"][j]["activo"] = False

        if encontrado:
            actualizadas += 1
        else:
            errores.append(f"En clave {clave} no existe el grupo {gpo_obj}")

    if cargadas > 0:
        st.success(f" Materias cargadas automáticamente: {cargadas}")

    if actualizadas > 0:
        st.success(f" Grupos activados correctamente: {actualizadas}")

    if errores:
        st.warning(" Algunos datos no se pudieron aplicar:")
        for e in errores:
            st.write(f"- {e}")
def calcular_penalizacion_por_dia(opcion, config_dias, w_dias=35):
    """
    Penaliza/bonifica una opción de horario según configuración avanzada por día.
    Retorna un número (negativo = peor, positivo = mejor).
    """
    if not config_dias or w_dias <= 0:
        return 0.0

    # Contar bloques de 30 min por día
    bloques_por_dia = {"Lun": 0, "Mar": 0, "Mie": 0, "Jue": 0, "Vie": 0, "Sab": 0}

    # También sacamos hora promedio por día para ver si fue temprano/tarde
    # guardamos minutos de inicio por bloque
    inicios_por_dia = {d: [] for d in bloques_por_dia.keys()}

    for m_g in opcion.get("materias", []):
        if m_g.get("gpo") == "N/A":
            continue
        if not m_g.get("intervalos"):
            continue

        for s in m_g["intervalos"]:
            dia = s.get("dia")
            if dia not in bloques_por_dia:
                continue

            inicio = int(s.get("inicio", 0))
            fin = int(s.get("fin", 0))

            duracion = max(0, fin - inicio)
            # bloques de 30 min
            bloques = int(duracion // 30)
            bloques_por_dia[dia] += bloques

            # guardamos el inicio para evaluar temprano/tarde
            if bloques > 0:
                inicios_por_dia[dia].append(inicio)

    score = 0.0

    for dia, usados in bloques_por_dia.items():
        cfg = config_dias.get(dia, {})
        evitar = cfg.get("evitar", False)
        max_bloques = int(cfg.get("max_bloques", 20))
        modo = cfg.get("modo", "Normal")
        pref = cfg.get("preferencia", "Mixto")

        # 1) Evitar día: penalización fuerte si hay cualquier clase
        if evitar and usados > 0:
            score -= 3.0 * usados  # castigo fuerte por cada bloque
            continue

        # 2) Max bloques deseados: penaliza exceso
        if usados > max_bloques:
            score -= 0.6 * (usados - max_bloques)

        # 3) Modo prioridad: premia tener carga en ese día (si no lo evitaste)
        if modo == "Prioridad":
            score += 0.15 * usados

        # 4) Preferencia temprano/tarde (usando hora promedio)
        if pref in ["Temprano", "Tarde"] and inicios_por_dia[dia]:
            prom_inicio = sum(inicios_por_dia[dia]) / len(inicios_por_dia[dia])

            # temprano = antes de 12:00 (720 min)
            if pref == "Temprano":
                if prom_inicio <= 720:
                    score += 1.0
                else:
                    score -= 1.0

            # tarde = después de 12:00
            if pref == "Tarde":
                if prom_inicio >= 720:
                    score += 1.0
                else:
                    score -= 1.0

        # Libre: intenta que esté vacío
        if pref == "Libre" and usados > 0:
            score -= 1.2 * usados

    # Escalado por peso global
    score = score * (w_dias / 35.0)
    return score


# --- CARGA DE CATÁLOGO Y HORARIOS UNAM ---
UNAM_BASE = "https://www.ssa.ingenieria.unam.mx/cj/tmp/programacion_horarios"
UNAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/javascript;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}


def crear_sesion_unam():
    """Sesión con reintentos para fallos temporales del servidor de la FI."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    sesion = requests.Session()
    adaptador = HTTPAdapter(max_retries=retry)
    sesion.mount("https://", adaptador)
    sesion.mount("http://", adaptador)
    return sesion


def solicitar_unam(url, timeout=(6, 20)):
    """Descarga una página de la UNAM y conserva un mensaje HTTP útil."""
    with crear_sesion_unam() as sesion:
        response = sesion.get(
            url,
            headers=UNAM_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )

    if response.status_code != 200:
        raise requests.HTTPError(
            f"El servidor respondió HTTP {response.status_code}",
            response=response,
        )

    # requests a veces interpreta estas páginas como ISO-8859-1.
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_nombres_materias():
    url = f"{UNAM_BASE}/listaAsignatura.js"
    try:
        response = solicitar_unam(url)

        # Acepta comillas simples o dobles y espacios opcionales.
        patron = re.compile(
            r"asignatura\s*\[\s*['\"](\d+)['\"]\s*\]\s*=\s*"
            r"(['\"])((?:\\.|(?!\2).)*)\2\s*;?",
            flags=re.DOTALL,
        )
        coincidencias = patron.findall(response.text)
        catalogo = {}
        for clave, _, nombre in coincidencias:
            nombre = nombre.replace("\\'", "'").replace('\\"', '"').strip()
            catalogo[str(int(clave))] = nombre

        if not catalogo:
            raise ValueError("El catálogo respondió, pero no se reconocieron asignaturas.")
        return catalogo

    except requests.Timeout:
        st.warning("El catálogo de la UNAM tardó demasiado. Las claves aún pueden cargarse por URL.")
    except requests.RequestException as e:
        st.warning(f"No se pudo descargar el catálogo de nombres: {e}")
    except Exception as e:
        st.warning(f"No se pudo interpretar el catálogo de nombres: {e}")
    return {}


CATALOGO_MATERIAS = cargar_nombres_materias()


def normalizar_encabezado(texto):
    valor = unicodedata.normalize("NFD", str(texto or "").strip())
    valor = "".join(ch for ch in valor if unicodedata.category(ch) != "Mn")
    valor = re.sub(r"[^a-zA-Z0-9]", "", valor).lower()
    equivalencias = {
        "grupo": "gpo",
        "gpo": "gpo",
        "gpo.": "gpo",
        "docente": "profesor",
        "profesor": "profesor",
        "dias": "dias",
        "dia": "dias",
        "vacan": "vacantes",
        "vacantes": "vacantes",
    }
    return equivalencias.get(valor, valor)


def expandir_tabla_con_rowspan(tabla):
    """Convierte una tabla HTML con rowspan/colspan en filas rectangulares."""
    filas_expandidas = []
    pendientes = {}  # columna -> [texto, filas_restantes]

    for fila_html in tabla.find_all("tr"):
        celdas = fila_html.find_all(["th", "td"], recursive=False)
        if not celdas:
            continue

        fila = []
        columna = 0

        def consumir_pendiente():
            nonlocal columna
            texto, restantes = pendientes[columna]
            fila.append(texto)
            if restantes <= 1:
                del pendientes[columna]
            else:
                pendientes[columna] = [texto, restantes - 1]
            columna += 1

        for celda in celdas:
            while columna in pendientes:
                consumir_pendiente()

            texto_celda = celda.get_text(" ", strip=True)
            try:
                rowspan = max(1, int(celda.get("rowspan", 1)))
            except (TypeError, ValueError):
                rowspan = 1
            try:
                colspan = max(1, int(celda.get("colspan", 1)))
            except (TypeError, ValueError):
                colspan = 1

            for _ in range(colspan):
                fila.append(texto_celda)
                if rowspan > 1:
                    pendientes[columna] = [texto_celda, rowspan - 1]
                columna += 1

        # Añade rowspans que aparecen al final de la fila.
        while columna in pendientes:
            consumir_pendiente()

        filas_expandidas.append(fila)

    return filas_expandidas


def _entero_desde_texto(valor):
    match = re.search(r"-?\d+", str(valor or ""))
    return int(match.group()) if match else None


def _datos_profesor(profesor_raw):
    profesor_raw = re.sub(r"\s+", " ", str(profesor_raw or "")).strip()
    match_modalidad = re.search(r"\(([^)]+)\)", profesor_raw)
    modalidad = match_modalidad.group(1).strip().upper() if match_modalidad else None
    profesor = limpiar_nombre_profesor(profesor_raw)
    return profesor, modalidad, profesor_raw


def _crear_grupo_base(gpo, profesor_raw, cupo, nombre_materia):
    profesor, modalidad, profesor_raw = _datos_profesor(profesor_raw)
    return {
        "gpo": str(gpo).strip(),
        "profesor": profesor or "SIN PROFESOR PUBLICADO",
        "profesor_raw": profesor_raw,
        "modalidad": modalidad,
        "salon": None,  # la tabla actual no publica salón
        "horario": "",
        "dias": "",
        "intervalos": [],
        "componentes": [],
        "calificacion": 10,
        "materia_nombre": nombre_materia,
        # La página actual publica Cupo, no número exacto de vacantes.
        "cupo": cupo,
        "vacantes": cupo,  # alias temporal para no romper versiones guardadas
        "dato_publicado": "cupo",
        "activo": True,
        "api_consultado": False,
        "sugerencia_api": None,
        "api_num_resenas": None,
        "api_nombre_match": None,
    }


def _agregar_componente(grupo, tipo, horario, dias_str):
    tipo = str(tipo or "Clase").strip() or "Clase"
    horario = re.sub(r"\s+", " ", str(horario or "")).strip()
    dias_str = re.sub(r"\s+", " ", str(dias_str or "")).strip()
    intervalos = extraer_intervalos(horario, re.split(r"[,;/]", dias_str))
    if not intervalos:
        return False

    firma = (tipo.upper(), horario, dias_str)
    firmas_existentes = {
        (c["tipo"].upper(), c["horario"], c["dias"])
        for c in grupo["componentes"]
    }
    if firma in firmas_existentes:
        return True

    grupo["componentes"].append({
        "tipo": tipo,
        "horario": horario,
        "dias": dias_str,
    })
    grupo["intervalos"].extend(intervalos)
    grupo["horario"] = "; ".join(
        f"{c['tipo']}: {c['horario']}" for c in grupo["componentes"]
    )
    grupo["dias"] = "; ".join(
        f"{c['tipo']}: {c['dias']}" for c in grupo["componentes"]
    )
    return True


def parsear_grupos_desde_tablas(soup, clave_int, nombre_materia):
    grupos = {}

    for tabla in soup.find_all("table"):
        filas = expandir_tabla_con_rowspan(tabla)
        indices = None

        for fila in filas:
            normalizados = [normalizar_encabezado(c) for c in fila]
            if {"clave", "gpo", "profesor", "horario", "dias"}.issubset(set(normalizados)):
                indices = {nombre: normalizados.index(nombre) for nombre in set(normalizados)}
                continue

            if not indices:
                continue

            max_indice = max(indices.values())
            if len(fila) <= max_indice:
                continue

            clave_fila = str(fila[indices["clave"]]).strip()
            if not clave_fila.isdigit() or str(int(clave_fila)) != clave_int:
                continue

            gpo = str(fila[indices["gpo"]]).strip()
            profesor_raw = str(fila[indices["profesor"]]).strip()
            horario = str(fila[indices["horario"]]).strip()
            dias_str = str(fila[indices["dias"]]).strip()
            tipo = fila[indices["tipo"]] if "tipo" in indices else "Clase"
            cupo = _entero_desde_texto(fila[indices["cupo"]]) if "cupo" in indices else None

            if not gpo or not horario or not dias_str:
                continue

            if gpo not in grupos:
                grupos[gpo] = _crear_grupo_base(gpo, profesor_raw, cupo, nombre_materia)
            else:
                if not grupos[gpo].get("profesor_raw") and profesor_raw:
                    profesor, modalidad, profesor_raw = _datos_profesor(profesor_raw)
                    grupos[gpo].update({
                        "profesor": profesor,
                        "modalidad": modalidad,
                        "profesor_raw": profesor_raw,
                    })
                if cupo is not None:
                    grupos[gpo]["cupo"] = cupo
                    grupos[gpo]["vacantes"] = cupo

            _agregar_componente(grupos[gpo], tipo, horario, dias_str)

    return grupos


def parsear_grupos_desde_texto(soup, nombre_materia):
    """Respaldo para la versión móvil si la tabla vuelve a cambiar."""
    texto = soup.get_text("\n", strip=True)
    grupos = {}
    patron_grupo = re.compile(
        r"Gpo\.?\s*:\s*([^,\n]+)\s*,\s*Cupo\s*:\s*(\d+)\s*(.*?)"
        r"(?=Gpo\.?\s*:|Horario\s+Actualizado|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for match in patron_grupo.finditer(texto):
        gpo, cupo_txt, bloque = match.groups()
        prof_match = re.search(
            r"Profesor\s*:\s*(.*?)(?=\n\s*Tipo\s*:)",
            bloque,
            flags=re.IGNORECASE | re.DOTALL,
        )
        profesor_raw = prof_match.group(1).strip() if prof_match else ""
        grupo = _crear_grupo_base(gpo, profesor_raw, int(cupo_txt), nombre_materia)

        patron_componente = re.compile(
            r"Tipo\s*:\s*([^\n]+).*?Horario\s*:\s*([^\n]+).*?D[ií]as\s*:\s*([^\n]+)",
            flags=re.IGNORECASE | re.DOTALL,
        )
        for tipo, horario, dias_str in patron_componente.findall(bloque):
            _agregar_componente(grupo, tipo, horario, dias_str)

        if grupo["intervalos"]:
            grupos[str(gpo).strip()] = grupo

    return grupos


def obtener_datos_unam(clave_materia, es_obligatoria):
    """Carga una materia y agrupa todas las sesiones T/L/A de cada grupo."""
    try:
        clave_int = str(int(str(clave_materia).strip()))
    except (TypeError, ValueError):
        st.error(f"La clave '{clave_materia}' no es numérica.")
        return []

    nombre_limpio = CATALOGO_MATERIAS.get(clave_int, "MATERIA SIN NOMBRE EN CATÁLOGO")
    nombre_materia = f"{clave_int} - {nombre_limpio}"
    url = f"{UNAM_BASE}/{clave_int}.html"

    try:
        response = solicitar_unam(url)
        soup = BeautifulSoup(response.text, "html.parser")

        grupos = parsear_grupos_desde_tablas(soup, clave_int, nombre_materia)
        if not grupos:
            grupos = parsear_grupos_desde_texto(soup, nombre_materia)

        grupos_validos = [g for g in grupos.values() if g.get("intervalos")]
        if not grupos_validos:
            titulo = soup.title.get_text(" ", strip=True) if soup.title else ""
            st.error(
                f"La página de la clave {clave_int} sí respondió, pero no se encontraron "
                f"grupos con horarios reconocibles. Título recibido: {titulo or 'sin título'}."
            )
            return []

        return [{
            "materia": nombre_materia,
            "obligatoria": es_obligatoria,
            "grupos": grupos_validos,
            "url_fuente": response.url,
        }]

    except requests.Timeout:
        st.error(f"La UNAM tardó demasiado al consultar la clave {clave_int}.")
    except requests.HTTPError as e:
        estado = e.response.status_code if e.response is not None else "desconocido"
        st.error(f"La página de la clave {clave_int} respondió con HTTP {estado}.")
    except requests.RequestException as e:
        st.error(f"No fue posible conectar con la UNAM para la clave {clave_int}: {e}")
    except Exception as e:
        st.error(f"No se pudo interpretar la clave {clave_int}: {type(e).__name__}: {e}")
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
        salon = g.get("salon", "SIN")
        horario = g.get("horario", "")
        dias = g.get("dias", "")
        vacantes = g.get("cupo", g.get("vacantes", ""))

        if salon and str(salon).strip().upper() != "SIN":
            summary = f"{materia_nombre} | GPO {g.get('gpo','')} | {salon}"
        else:
            summary = f"{materia_nombre} | GPO {g.get('gpo','')}"

        desc = f"Profesor: {profesor}"

        if modalidad:
            desc += f" ({modalidad})"

        if salon and str(salon).strip().upper() != "SIN":
            desc += f"\\nSalón: {salon}"
        else:
            desc += f"\\nSalón: SIN / En línea"

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

def dataframe_a_png(df_text, df_color=None):
    """
    Exporta un DataFrame a PNG. Si df_color viene, aplica background-color por celda.
    df_color debe contener strings tipo: "background-color: #FFCDD2; color: #000000;"
    """
    fig, ax = plt.subplots(figsize=(12, 18))
    ax.axis("off")

    tabla = ax.table(
        cellText=df_text.values,
        rowLabels=df_text.index,
        colLabels=df_text.columns,
        cellLoc="center",
        loc="center"
    )

    tabla.auto_set_font_size(False)
    tabla.set_fontsize(8)
    tabla.scale(1, 1.4)

    # Colorear celdas si viene df_color
    if df_color is not None:
        for r in range(df_text.shape[0]):
            for c in range(df_text.shape[1]):
                estilo = df_color.iat[r, c]
                if isinstance(estilo, str) and "background-color" in estilo:
                    try:
                        bg = estilo.split("background-color:")[1].split(";")[0].strip()
                        tabla[(r+1, c)].set_facecolor(bg)  # +1 por header row
                    except:
                        pass

                # borde rojo si está marcado en estilo
                if isinstance(estilo, str) and "border:" in estilo and "ff4d4d" in estilo.lower():
                    tabla[(r+1, c)].set_linewidth(2)

    # Guardar a bytes
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

# --- INTERFAZ DE USUARIO ---
st.title("Generador de Horarios FI")
st.caption("Versión corregida: parser UNAM con teoría/laboratorio agrupados")

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
    Escribe las claves en el menú de la izquierda. Puedes ingresarlas **una por una** o **varias juntas separadas por comas** (ej. `1730` o `1120, 1601, 32`) y presiona **Agregar Materias**.

    **3. Revisa Grupos y Cupos:**
    En la lista de la derecha verás los grupos publicados y su cupo total.
    * **Desmarca la casilla** ☑️ de los grupos que no te interesen para que el generador los ignore.
    * Usa **🔄 Refrescar Cupos** para actualizar el cupo publicado y los horarios sin borrar tus materias.

    **4. Consulta Promedios de Profesores:**
    Dentro de cada materia, presiona **🔍 Buscar sugerencias de calificación (IngenieriaTracker)** para mostrar una **sugerencia de promedio** por profesor.

    * Esta sugerencia **NO modifica** tu calificación manual.
    * Si no hay coincidencia, se mostrará **"No encontrado"**.
    * Puedes dar click en **(Ver reseñas: #)** para abrir el perfil del profesor.

    **5. Personaliza:**
    * **Bloqueos:** Agrega tus horas de comida, trabajo o traslado en el panel izquierdo ("Actividad Manual").
    * **Pesos:** Ahora se ajustan en la **columna izquierda**, en la sección **"⚙️ Configuración de Pesos"** (evitar huecos, preferencia de turno, etc.).
    * **Cargar grupos actuales (Experimental):** Puedes pegar tus grupos actuales en formato `CLAVE GRUPO` (uno por línea) para activar automáticamente solo esos grupos.

    **6. Genera:**
    Presiona el botón al final para ver las mejores combinaciones posibles.


    ⚠️ **Aviso importante:** Esta app **NO es dueña** de IngenieriaTracker ni está afiliada.  
    Todo el crédito de la base de datos de las reseñas a **www.ingenieriatracker.com**.
    """)



# --- BARRA LATERAL (CONFIGURACIÓN) ---

# --- COLUMNAS PRINCIPALES ---
col_in, col_list = st.columns([1, 1.2])

# ==========================================
# COLUMNA IZQUIERDA: ENTRADA DE DATOS
# ==========================================
with col_in:
    st.subheader("1. Carga de Materias")
    
    st.caption("Inicia aquí ingresando tus claves y presiona **Agregar Materias**.")
    
    clave_input = st.text_input(
        "## Claves:",
        placeholder="Ejemplo: 1730 ó 1120, 1601, 32"
    )

    if st.button("Agregar Materias", width="stretch"):
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

                    ya_existe = any(
                        str(m.get("materia", "")).split(" - ")[0].strip() == clave_limpia
                        for m in st.session_state.materias_db
                        if not m.get("es_bloqueo", False)
                    )
                    if ya_existe:
                        errores.append(f"Clave {clave_limpia}: ya estaba agregada")
                        barra.progress((i + 1) / len(lista_claves))
                        continue

                    nuevas = obtener_datos_unam(clave_limpia, True)

                    if nuevas:
                        nombre = nuevas[0]["materia"]
                        st.session_state.materias_db.extend(nuevas)
                        agregadas.append(nombre)
                    else:
                        errores.append(
                            f"Clave {clave_limpia}: no se pudieron cargar grupos publicados"
                        )
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

    # ==========================================================
    # --- AGREGAR ACTIVIDAD Personal ---
    # ==========================================================
    with st.expander("Agregar Actividad Personal", expanded=False):
        st.info("Bloquea horarios para Trabajo, Comida, Transporte, etc.")
        act_nombre = st.text_input("Nombre de la actividad", "Actividad Personal")
        act_dias = st.multiselect("Días", ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"])

        c_hora1, c_hora2 = st.columns(2)
        t_inicio = c_hora1.time_input("Inicio")
        t_fin = c_hora2.time_input("Fin")

        if st.button("Agregar Actividad", width="stretch"):
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
                        "cupo": None,
                        "vacantes": None,
                        "componentes": [{"tipo": "Bloqueo", "horario": str_horario, "dias": ", ".join(act_dias)}],
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
                st.rerun()
            else:
                st.error("Debes poner un nombre y seleccionar al menos un día.")

    st.markdown("---")

    # ==========================================================
    # CONFIGURACIÓN (ANTES SIDEBAR) - AHORA EN COLUMNA IZQUIERDA
    # ==========================================================
    st.caption("Configura aqui las preferencias de tu horario")
    with st.expander("⚙️ Configuración de Pesos", expanded=True):

        tipo_turno = st.selectbox(
            "Preferencia de Turno",
            ["Mañana (Temprano)", "Tarde / Noche", "Mixto"],
            help="Elige en qué momento del día prefieres tomar clases."
        )

        w_turno = st.slider(
            "Importancia del Turno",
            0, 100, 30,
            help="Qué tanto debe esforzarse el sistema por respetar tu preferencia de mañana o tarde."
        )

        w_huecos = st.slider(
            "Minimizar horas muertas",
            0, 100, 50,
            help="Busca juntar tus clases para que no tengas tiempos libres excesivos entre ellas."
        )

        w_profes = st.slider(
            "Calificación de profesores",
            0, 100, 70,
            help="Da prioridad a los profesores con mayor calificación."
        )

        w_carga = st.slider(
            "Cantidad de materias",
            0, 100, 80,
            help="Intenta inscribir el mayor número posible de materias de tu lista."
        )

        pesos = {
            "huecos": w_huecos,
            "profes": w_profes,
            "tipo_turno": tipo_turno,
            "peso_turno": w_turno,
            "carga": w_carga
        }
        # ==========================================================
        # CONFIGURACIÓN AVANZADA POR DÍA (EXPERIMENTAL)
        # ==========================================================
        if "config_dias" not in st.session_state:
            st.session_state.config_dias = {
                "Lun": {"modo": "Normal", "preferencia": "Mixto", "max_bloques": 10, "evitar": False},
                "Mar": {"modo": "Normal", "preferencia": "Mixto",    "max_bloques": 10, "evitar": False},
                "Mie": {"modo": "Normal", "preferencia": "Mixto", "max_bloques": 10, "evitar": False},
                "Jue": {"modo": "Normal", "preferencia": "Mixto",    "max_bloques": 10, "evitar": False},
                "Vie": {"modo": "Normal", "preferencia": "Mixto",    "max_bloques": 10, "evitar": False},
                "Sab": {"modo": "Normal", "preferencia": "Mixto",    "max_bloques": 10, "evitar": False},
            }

        with st.expander("⚙️ Configuración avanzada por día `experimental`", expanded=False):
            st.caption(
                "Personaliza tus preferencias por día. "
                "Ejemplo: Lunes/Miércoles temprano y ligero, Martes/Jueves pesado, Viernes libre."
            )

            # Peso global de esta configuración (qué tanto afecta al score)
            w_dias = st.slider(
                "Importancia de preferencias por día",
                0, 100, 35,
                help="Entre más alto, más tratará de respetar tu preferencia de carga y horarios por día."
            )

            st.markdown("---")

            tabs_dias = st.tabs(["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"])

            dias_lista = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"]

            for idx, dia in enumerate(dias_lista):
                with tabs_dias[idx]:
                    cfg = st.session_state.config_dias[dia]

                    c1, c2 = st.columns([1, 1])

                    modo = c1.selectbox(
                        f"{dia} - Modo",
                        ["Normal", "Prioridad", "Evitar"],
                        index=["Normal", "Prioridad", "Evitar"].index(cfg.get("modo", "Normal")),
                        key=f"modo_{dia}",
                        help="Normal = se comporta normal. Prioridad = intenta meter más carga aquí. Evitar = penaliza clases este día."
                    )

                    preferencia = c2.selectbox(
                        f"{dia} - Preferencia de horario",
                        ["Temprano", "Tarde", "Mixto", "Libre"],
                        index=["Temprano", "Tarde", "Mixto", "Libre"].index(cfg.get("preferencia", "Mixto")),
                        key=f"pref_{dia}",
                        help="Libre intenta evitar ese día (si es posible)."
                    )

                    max_bloques = st.slider(
                        f"{dia} - Máximo de bloques (30 min) deseados",
                        0, 20, int(cfg.get("max_bloques", 10)),
                        key=f"maxb_{dia}",
                        help="0 = idealmente libre. 6 = aprox 3 horas. 10 = 5 horas. 14 = 7 horas."
                    )

                    evitar = st.toggle(
                        f"{dia} - Evitar este día",
                        value=bool(cfg.get("evitar", False)),
                        key=f"ev_{dia}",
                        help="Si está activo, penaliza fuertemente cualquier clase este día."
                    )

                    # Guardar cambios
                    st.session_state.config_dias[dia] = {
                        "modo": modo,
                        "preferencia": preferencia,
                        "max_bloques": max_bloques,
                        "evitar": evitar
                    }

            st.markdown("---")
            st.success("✅ Configuración avanzada guardada automáticamente.")

    st.markdown("---")

    # ==========================================================
    # CARGA MASIVA DE GRUPOS
    # ==========================================================
    with st.expander("⚡ Cargar grupos actuales `experimental`", expanded=False):
        st.info("Pega tus grupos actuales en formato: CLAVE GRUPO (uno por línea). Ejemplo:\n1601 2")

        texto_grupos = st.text_area(
            "Mis grupos actuales:",
            height=120,
            placeholder="1601 2\n1120 3\n1730 1"
        )

        if st.button("Aplicar grupos actuales", width="stretch"):
            cargar_grupos_actuales(texto_grupos, es_obligatorio=True)
            st.rerun()

    # ==========================================================
    # CARGA MASIVA DE CALIFICACIONES
    # ==========================================================
    with st.expander("📋 Carga de Calificaciones `experimental`", expanded=False):
        st.info("Pega aquí tus celdas de Excel. El sistema buscará el nombre del profesor y actualizará su nota.")
        st.markdown("ℹ **Para más información y ejemplos:** [Ver guía en GitHub](https://github.com/Prevaricare/Creador-de-hoarios-fi-unam/tree/main)")

        raw_data = st.text_area(
            "Pegar datos de Excel:",
            height=150,
            placeholder="Clave\tGpo\tProfesor...\tCalificación"
        )

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
    


# ==========================================
# COLUMNA DERECHA: LISTA Y GESTIÓN
# ==========================================
with col_list:
    # Estado global de expanders
    if "expand_materias" not in st.session_state:
        st.session_state.expand_materias = True  # empiezan abiertas

    c_header_1, c_header_2, c_header_3 = st.columns([2, 1, 1])

    c_header_1.subheader("2. Materias Registradas")

    if c_header_2.button("🔄 Refrescar Cupos", use_container_width=True):
        refrescar_vacantes()
        st.rerun()

    label_expand = "📁 Plegar todo" if st.session_state.expand_materias else "📂 Expandir todo"
    if c_header_3.button(label_expand, use_container_width=True):
        st.session_state.expand_materias = not st.session_state.expand_materias
        st.rerun()


    if not st.session_state.materias_db:
        st.info("Tu lista está vacía. Comienza ingresando una clave a la izquierda.")

    for i, m in enumerate(st.session_state.materias_db):
        status = " (Opcional)" if not m['obligatoria'] else ""

        with st.expander(f"{m['materia']}{status}", expanded=st.session_state.expand_materias):

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

            c_mat_left, c_mat_right = st.columns([0.70, 0.30])

            nuevo_tipo = c_mat_left.radio(
                "Tipo",
                ["Obligatorio", "Opcional"],
                index=0 if m.get("obligatoria", True) else 1,
                key=f"radio_tipo_{i}",
                horizontal=True,
                label_visibility="collapsed"
            )

            st.session_state.materias_db[i]["obligatoria"] = (nuevo_tipo == "Obligatorio")

            if c_mat_right.button("🗑️ Eliminar", key=f"del_mat_{i}", use_container_width=True):
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

                vacs = g.get("cupo", g.get("vacantes"))
                if isinstance(vacs, (int, float)):
                    color_vac = "green" if vacs > 5 else ("orange" if vacs > 0 else "red")
                else:
                    color_vac = "gray"
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

                vacs = g.get("cupo", g.get("vacantes"))
                if isinstance(vacs, (int, float)):
                    color_vac = "green" if vacs > 5 else ("orange" if vacs > 0 else "red")
                else:
                    color_vac = "gray"
                salon = g.get("salon", None)

                salon_txt = ""
                if salon and salon.strip().upper() != "SIN":
                    salon_txt = f" <span style='color:#555;'>(Salón: <strong>{salon}</strong>)</span>"
                elif not salon or str(salon).strip().upper() == "SIN":
                    salon_txt = " <span style='color:#777;'>(Salón no publicado)</span>"


                info_html = f"""
                <div style="font-size: 0.9em;">
                    <strong>Gpo {g['gpo']}</strong> - {g['profesor']}{salon_txt}<br>
                    📅 {g['dias']} ({g['horario']})<br>
                    Cupo publicado: <strong style='color: {color_vac}'>{vacs if vacs is not None else "N/D"}</strong><br>
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
st.subheader("3. Generación de horarios")

st.caption(
    "Aquí se generan combinaciones optimizadas con tus materias activas. "
    "El sistema iterará y te mostrará las mejores opciones según tus pesos (huecos, turno, profes, etc.)."
)

# ============================
# OPCIONES DE VISUALIZACIÓN
# ============================
c_vis1, c_vis2 = st.columns([1, 1])

mostrar_sin_cupo = False
c_vis1.caption(
    "La fuente actual publica el cupo total del grupo, no el número exacto de vacantes; "
    "por eso ya no se marca automáticamente ‘SIN CUPO’."
)

if st.button("Generar combinaciones optimizadas", width="stretch"):
    if not st.session_state.materias_db:
        st.error("No puedes generar horarios sin materias. Agrega al menos una.")
    else:
        grupos_input = []

        materias_omitidas = []

        for m in st.session_state.materias_db:
            grupos_validos = [g for g in m["grupos"] if g.get("activo", True)]

            # Una materia marcada como opcional agrega la alternativa real de no cursarla.
            if grupos_validos and not m.get("obligatoria", True):
                grupos_validos = grupos_validos + [{
                    "gpo": "N/A",
                    "profesor": "",
                    "horario": "",
                    "dias": "",
                    "intervalos": [],
                    "calificacion": 0,
                    "materia_nombre": m.get("materia", "Materia opcional"),
                    "cupo": None,
                    "vacantes": None,
                    "activo": True,
                }]

            if grupos_validos:
                grupos_input.append(grupos_validos)
            else:
                materias_omitidas.append(m["materia"])
                continue

        # Aviso al usuario si se omitieron materias
        if materias_omitidas:
            st.warning(
                "⚠️ Se omitieron materias porque no tienen ningún grupo activo:\n\n"
                + "\n".join([f"- {x}" for x in materias_omitidas])
                + "\n\n💡 Tip: Activa al menos 1 grupo si quieres que se considere en el horario."
            )

        # Si al final no quedó nada para combinar
        if not grupos_input:
            st.error("No hay grupos activos para generar horarios. Activa al menos un grupo.")
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
                sc += calcular_penalizacion_por_dia(
                    {"materias": comb},
                    st.session_state.config_dias,
                    w_dias=w_dias,
                )

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

            # El score completo ya se aplicó durante la búsqueda del top 10.
            posibles = sorted(posibles, key=lambda x: x["score"], reverse=True)

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
                    # Detectar rango real del horario (primera clase -> última clase + 30 min)
                    inicios = []
                    fines = []

                    for m_g in opcion["materias"]:
                        if m_g.get("gpo") == "N/A":
                            continue
                        for s in m_g.get("intervalos", []):
                            inicios.append(s.get("inicio", 0))
                            fines.append(s.get("fin", 0))

                    # fallback si algo raro pasa
                    if not inicios or not fines:
                        min_minuto = 7 * 60
                        max_minuto = 22 * 60
                    else:
                        min_minuto = min(inicios)
                        max_minuto = max(fines) + 30  # + media hora extra

                        # límites razonables para que no se rompa visualmente
                        min_minuto = max(min_minuto, 7 * 60)
                        max_minuto = min(max_minuto, 22 * 60)

                    # Generar labels cada 30 min solo dentro del rango
                    horas_labels = []
                    t = (min_minuto // 30) * 30
                    while t <= max_minuto:
                        horas_labels.append(f"{t//60:02d}:{'30' if (t%60==30) else '00'}")
                        t += 30

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
                        salon = m_g.get("salon", "SIN")
                        salon = salon.strip() if salon else "SIN"
                        vacs_grupo = None  # la fuente actual publica cupo total, no vacantes exactas
                        sin_cupo = False
                        try:
                            if vacs_grupo is not None and int(vacs_grupo) <= 0:
                                sin_cupo = True
                        except:
                            sin_cupo = False
                        tag_cupo = " ⚠️SIN CUPO" if (sin_cupo and mostrar_sin_cupo) else ""
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

                                        # Texto compacto para el header del bloque
                                        header_line = ""
                                        if salon.upper() != "SIN":
                                            header_line = f"{salon} G{m_g['gpo']} ({clave}){tag_cupo}"
                                        else:
                                            header_line = f"G{m_g['gpo']} ({clave}){tag_cupo}"

                                        # Profesor corto (1 línea)
                                        profesor_line = f"\"{profesor_corto}\""

                                        if duracion_bloques == 1:
                                            if counter == 0:
                                                texto_celda = header_line

                                        elif duracion_bloques == 2:
                                            if counter == 0:
                                                texto_celda = header_line
                                            if counter == 1:
                                                texto_celda = nombre_limpio

                                        elif duracion_bloques >= 3:
                                            if counter == 0:
                                                texto_celda = header_line
                                            if counter == 1:
                                                texto_celda = nombre_limpio
                                            if counter == 2:
                                                texto_celda = profesor_line

                                        df_text.at[horas_labels[h_idx], dia] = texto_celda


                    # ============================
                    # BOTÓN PNG COMPACTO (ARRIBA)
                    # ============================
                    try:
                        png_bytes = dataframe_a_png(df_text, df_color)
                        btn_png_slot.download_button(
                            label="Descargar imagen (.png)",
                            data=png_bytes,
                            file_name=f"horario_opcion_{i+1}.png",
                            mime="image/png",
                            use_container_width=True
                        )
                    except:
                        btn_png_slot.button("Descargar imagen (.png)", disabled=True, use_container_width=True)
                    
                    alto_tabla = max(520, min(1200, 120 + len(df_text) * 34))

                    st.dataframe(
                        df_text.style.apply(lambda x: df_color, axis=None),
                        height=alto_tabla,
                        use_container_width=True
                    )



                    st.markdown("---")

                    # ============================
                    # RESUMEN DE MATERIAS (NUEVO)
                    # ============================
                    st.markdown("### 📋 Resumen del horario ")

                    lista_resumen = []
                    for g in opcion["materias"]:
                        # Ignoramos si no aplica (N/A)
                        if g.get("gpo") == "N/A":
                            continue

                        materia_nombre = g.get("materia_nombre", "")
                        profesor = g.get("profesor", "")
                        salon = g.get("salon", "SIN")
                        
                        # Extraemos el cupo total publicado por la Facultad
                        vacantes = g.get("cupo", g.get("vacantes")) 

                        # Separar clave y nombre
                        if " - " in materia_nombre:
                            clave, nombre_mat = materia_nombre.split(" - ", 1)
                        else:
                            clave, nombre_mat = "", materia_nombre

                        lista_resumen.append({
                            "Clave": clave,
                            "Grupo": g.get("gpo", ""),
                            "Materia": nombre_mat,
                            "Profesor": profesor,
                            "Salón": salon,
                            "Cupo publicado": vacantes
                        })

                    df_resumen = pd.DataFrame(lista_resumen)
                    
                    # Ordenamos por clave para que se vea limpio
                    if not df_resumen.empty:
                        df_resumen = df_resumen.sort_values(["Clave", "Grupo"], ascending=True)

                    # Mostramos la tabla (usamos st.dataframe para que sea interactiva)
                    st.dataframe(
                        df_resumen,
                        width="stretch",
                        hide_index=True,
                        height=420,
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
    st.markdown("<div style='text-align: center; color: gray; font-size: 0.9em;'>Creado por: Gael prevaricare</div>", unsafe_allow_html=True)
    st.link_button("Instagram", "https://www.instagram.com/gaelprevaricare/", width="stretch")
