import streamlit as st
import pandas as pd
import re
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
import json
import copy
import time

try:
    from streamlit_local_storage import LocalStorage
except ImportError:
    LocalStorage = None

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

MODALIDAD_PATRON = re.compile(
    r"\((PRESENCIAL|EN\s*L[IÍ]NEA|ENLINEA|A\s*DISTANCIA|"
    r"H[IÍ]BRID[AO]|REMOT[AO]|VIRTUAL)\)",
    flags=re.IGNORECASE,
)


def normalizar_modalidad_texto(modalidad):
    """Devuelve una etiqueta uniforme para la modalidad publicada."""
    if not modalidad:
        return None

    texto = str(modalidad).strip().upper()
    texto_sin_acentos = unicodedata.normalize("NFD", texto)
    texto_sin_acentos = "".join(
        ch for ch in texto_sin_acentos
        if unicodedata.category(ch) != "Mn"
    )
    texto_sin_acentos = re.sub(r"\s+", " ", texto_sin_acentos)

    if texto_sin_acentos in {"EN LINEA", "ENLINEA", "VIRTUAL", "REMOTO", "REMOTA"}:
        return "EN LÍNEA"
    if texto_sin_acentos in {"HIBRIDO", "HIBRIDA"}:
        return "HÍBRIDA"
    if texto_sin_acentos == "A DISTANCIA":
        return "A DISTANCIA"
    if texto_sin_acentos == "PRESENCIAL":
        return "PRESENCIAL"
    return texto


def limpiar_nombre_profesor(nombre):
    if not nombre:
        return ""

    # La modalidad forma parte de la información del grupo, no del nombre.
    n = str(nombre).replace("\n", " ").strip()
    n = MODALIDAD_PATRON.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()

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


def link_busqueda_google_profesor(nombre_profesor):
    """Genera una búsqueda flexible del profesor en Google."""
    nombre_limpio = limpiar_nombre_profesor(nombre_profesor)
    if not nombre_limpio:
        return None

    # Sin comillas: Google puede tolerar variaciones de nombres y apellidos.
    consulta = urllib.parse.quote_plus(
        f"{nombre_limpio} Facultad de Ingeniería UNAM profesor"
    )
    return f"https://www.google.com/search?q={consulta}"


def coincidencia_profesor(nombre_original, nombre_api):
    """Clasifica de forma conservadora la coincidencia devuelta por la API."""
    a = limpiar_nombre_profesor(nombre_original).upper()
    b = limpiar_nombre_profesor(nombre_api).upper()
    if not a or not b:
        return "ninguna"
    if a == b:
        return "exacta"
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return "ninguna"
    similitud = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
    return "probable" if similitud >= 0.75 else "dudosa"


def actualizar_calificaciones_automaticamente(indice_materia=None):
    """Consulta IngenieríaTracker y aplica solo coincidencias exactas/probables."""
    encontrados = 0
    aplicados = 0
    dudosos = 0
    no_encontrados = 0

    indices = range(len(st.session_state.materias_db))
    if indice_materia is not None:
        indices = [indice_materia]

    for i in indices:
        materia = st.session_state.materias_db[i]
        if materia.get("es_bloqueo"):
            continue
        for j, grupo in enumerate(materia.get("grupos", [])):
            profesor = grupo.get("profesor", "")
            if grupo.get("gpo") == "N/A" or profesor in {"", "Tú", "SIN PROFESOR PUBLICADO"}:
                continue

            nombre_limpio = limpiar_nombre_profesor(profesor)
            if nombre_limpio in st.session_state.api_cache_profes:
                resultado = st.session_state.api_cache_profes[nombre_limpio]
            else:
                resultado = consultar_ingenieria_tracker(profesor)
                st.session_state.api_cache_profes[nombre_limpio] = resultado

            promedio = resultado.get("promedio")
            nombre_api = resultado.get("nombre_api")
            nivel = coincidencia_profesor(profesor, nombre_api)
            destino = st.session_state.materias_db[i]["grupos"][j]
            destino["api_consultado"] = True
            destino["sugerencia_api"] = promedio
            destino["api_num_resenas"] = resultado.get("num_resenas")
            destino["api_nombre_match"] = nombre_api
            destino["api_coincidencia"] = nivel

            if promedio is None:
                no_encontrados += 1
                continue

            encontrados += 1
            if nivel in {"exacta", "probable"}:
                try:
                    valor = round(float(promedio), 2)
                    destino["calificacion"] = valor
                    widget_key = f"cal_{i}_{j}"
                    if widget_key in st.session_state:
                        st.session_state[widget_key] = valor
                    aplicados += 1
                except (TypeError, ValueError):
                    dudosos += 1
            else:
                dudosos += 1

    return {
        "encontrados": encontrados,
        "aplicados": aplicados,
        "dudosos": dudosos,
        "no_encontrados": no_encontrados,
    }


STORAGE_KEY = "horarios_fi_unam_estado_v3"
ESTADO_VERSION = 4
DIAS_SEMANA = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"]


def crear_estado_guardable():
    """Crea un JSON compacto y portable con el estado relevante."""
    materias = []
    bloqueos = []
    for materia in st.session_state.get("materias_db", []):
        if materia.get("es_bloqueo"):
            bloqueos.append(copy.deepcopy(materia))
            continue
        clave = str(materia.get("materia", "")).split(" - ")[0].strip()
        grupos = []
        for g in materia.get("grupos", []):
            grupos.append({
                "gpo": str(g.get("gpo", "")),
                "activo": bool(g.get("activo", True)),
                "calificacion": g.get("calificacion", 10),
                "sugerencia_api": g.get("sugerencia_api"),
                "api_num_resenas": g.get("api_num_resenas"),
                "api_nombre_match": g.get("api_nombre_match"),
                "api_coincidencia": g.get("api_coincidencia"),
            })
        materias.append({
            "clave": clave,
            "obligatoria": bool(materia.get("obligatoria", True)),
            "grupos": grupos,
        })

    return {
        "version": ESTADO_VERSION,
        "guardado_en": datetime.now(timezone.utc).isoformat(),
        "materias": materias,
        "bloqueos": bloqueos,
        "preferencias": {
            "tipo_turno": st.session_state.get("tipo_turno_guardado", "Mixto"),
            "w_turno": st.session_state.get("w_turno_guardado", 30),
            "w_huecos": st.session_state.get("w_huecos_guardado", 50),
            "w_profes": st.session_state.get("w_profes_guardado", 70),
            "w_carga": st.session_state.get("w_carga_guardado", 80),
            "w_dias": st.session_state.get("w_dias_guardado", 35),
            "config_dias": copy.deepcopy(st.session_state.get("config_dias", {})),
        },
    }



def firma_estado_guardable():
    """Serializa el estado sin fecha para detectar cambios reales."""
    estado = crear_estado_guardable()
    estado.pop("guardado_en", None)
    return json.dumps(
        estado,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sincronizar_widgets_desde_estado():
    """Hace que los widgets visuales reflejen el estado recién restaurado."""
    # Limpiar claves dinámicas antiguas para evitar que otra materia herede valores.
    prefijos_dinamicos = (
        "tgl_", "cal_", "radio_tipo_",
        "modo_", "pref_", "maxb_", "ev_",
    )
    claves_exactas = {
        "pref_tipo_turno_widget",
        "pref_w_turno_widget",
        "pref_w_huecos_widget",
        "pref_w_profes_widget",
        "pref_w_carga_widget",
        "pref_w_dias_widget",
    }
    for clave in list(st.session_state.keys()):
        if clave in claves_exactas or clave.startswith(prefijos_dinamicos):
            del st.session_state[clave]

    st.session_state["pref_tipo_turno_widget"] = st.session_state.get(
        "tipo_turno_guardado", "Mixto"
    )
    st.session_state["pref_w_turno_widget"] = int(
        st.session_state.get("w_turno_guardado", 30)
    )
    st.session_state["pref_w_huecos_widget"] = int(
        st.session_state.get("w_huecos_guardado", 50)
    )
    st.session_state["pref_w_profes_widget"] = int(
        st.session_state.get("w_profes_guardado", 70)
    )
    st.session_state["pref_w_carga_widget"] = int(
        st.session_state.get("w_carga_guardado", 80)
    )
    st.session_state["pref_w_dias_widget"] = int(
        st.session_state.get("w_dias_guardado", 35)
    )

    for dia in DIAS_SEMANA:
        cfg = st.session_state.get("config_dias", {}).get(dia, {})
        st.session_state[f"modo_{dia}"] = cfg.get("modo", "Normal")
        st.session_state[f"pref_{dia}"] = cfg.get("preferencia", "Mixto")
        st.session_state[f"maxb_{dia}"] = int(cfg.get("max_bloques", 10))
        st.session_state[f"ev_{dia}"] = bool(cfg.get("evitar", False))

    for i, materia in enumerate(st.session_state.get("materias_db", [])):
        st.session_state[f"radio_tipo_{i}"] = (
            "Obligatorio" if materia.get("obligatoria", True) else "Opcional"
        )
        for j, grupo in enumerate(materia.get("grupos", [])):
            st.session_state[f"tgl_{i}_{j}"] = bool(grupo.get("activo", True))
            if grupo.get("profesor") != "Tú":
                try:
                    st.session_state[f"cal_{i}_{j}"] = round(
                        float(grupo.get("calificacion", 10)), 2
                    )
                except (TypeError, ValueError):
                    st.session_state[f"cal_{i}_{j}"] = 10.0


def reiniciar_estado_usuario():
    """Limpia materias y preferencias restaurables sin tocar cachés técnicas."""
    st.session_state.materias_db = []
    st.session_state.tipo_turno_guardado = "Mixto"
    st.session_state.w_turno_guardado = 30
    st.session_state.w_huecos_guardado = 50
    st.session_state.w_profes_guardado = 70
    st.session_state.w_carga_guardado = 80
    st.session_state.w_dias_guardado = 35
    st.session_state.config_dias = {
        dia: {
            "modo": "Normal",
            "preferencia": "Mixto",
            "max_bloques": 10,
            "evitar": False,
        }
        for dia in DIAS_SEMANA
    }
    sincronizar_widgets_desde_estado()


def restaurar_estado_guardado(estado):
    """Recarga claves actuales desde la UNAM y reaplica selecciones guardadas."""
    if isinstance(estado, str):
        estado = json.loads(estado)
    if not isinstance(estado, dict):
        raise ValueError("El respaldo no contiene un objeto válido.")

    nuevas_materias = []
    errores = []
    for item in estado.get("materias", []):
        clave = str(item.get("clave", "")).strip()
        if not clave.isdigit():
            continue
        cargadas = obtener_datos_unam(clave, bool(item.get("obligatoria", True)))
        if not cargadas:
            errores.append(clave)
            continue
        materia = cargadas[0]
        guardados = {str(g.get("gpo")): g for g in item.get("grupos", [])}
        for grupo in materia.get("grupos", []):
            previo = guardados.get(str(grupo.get("gpo")))
            if previo:
                grupo["activo"] = bool(previo.get("activo", True))
                try:
                    grupo["calificacion"] = round(float(previo.get("calificacion", 10)), 2)
                except (TypeError, ValueError):
                    grupo["calificacion"] = 10
                for campo in ["sugerencia_api", "api_num_resenas", "api_nombre_match", "api_coincidencia"]:
                    grupo[campo] = previo.get(campo)
                grupo["api_consultado"] = previo.get("sugerencia_api") is not None
        nuevas_materias.append(materia)

    nuevas_materias.extend(copy.deepcopy(estado.get("bloqueos", [])))
    st.session_state.materias_db = nuevas_materias

    pref = estado.get("preferencias", {})
    for nombre, predeterminado in [
        ("tipo_turno_guardado", "Mixto"), ("w_turno_guardado", 30),
        ("w_huecos_guardado", 50), ("w_profes_guardado", 70),
        ("w_carga_guardado", 80), ("w_dias_guardado", 35),
    ]:
        origen = nombre.replace("_guardado", "")
        st.session_state[nombre] = pref.get(origen, predeterminado)
    if isinstance(pref.get("config_dias"), dict) and pref["config_dias"]:
        st.session_state.config_dias = pref["config_dias"]

    sincronizar_widgets_desde_estado()
    return errores


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
    with st.spinner("Actualizando cupos y vacantes en tiempo real..."):
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
                            g_viejo["sin_vacantes"] = bool(
                                g_viejo.get("vacantes") is not None
                                and g_viejo.get("vacantes") <= 0
                            )
                            g_viejo["dato_publicado"] = "cupo_y_vacantes"
                            g_viejo["horario"] = g_nuevo.get("horario", g_viejo.get("horario", ""))
                            g_viejo["dias"] = g_nuevo.get("dias", g_viejo.get("dias", ""))
                            g_viejo["intervalos"] = g_nuevo.get("intervalos", g_viejo.get("intervalos", []))
                            g_viejo["componentes"] = g_nuevo.get("componentes", g_viejo.get("componentes", []))
                            n_actualizados += 1
                            break

    st.success(f"Se actualizaron cupos y vacantes de {n_actualizados} grupos.")

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
    match_modalidad = MODALIDAD_PATRON.search(profesor_raw)
    modalidad = (
        normalizar_modalidad_texto(match_modalidad.group(1))
        if match_modalidad
        else None
    )
    profesor = limpiar_nombre_profesor(profesor_raw)
    return profesor, modalidad, profesor_raw


def _crear_grupo_base(gpo, profesor_raw, cupo, vacantes, nombre_materia):
    profesor, modalidad, profesor_raw = _datos_profesor(profesor_raw)
    sin_vacantes = vacantes is not None and vacantes <= 0
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
        # La página oficial publica ambos valores por separado.
        "cupo": cupo,
        "vacantes": vacantes,
        "dato_publicado": "cupo_y_vacantes",
        "sin_vacantes": sin_vacantes,
        # Los grupos llenos siguen visibles, pero comienzan desmarcados.
        "activo": not sin_vacantes,
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
            vacantes = (
                _entero_desde_texto(fila[indices["vacantes"]])
                if "vacantes" in indices
                else None
            )

            if not gpo or not horario or not dias_str:
                continue

            if gpo not in grupos:
                grupos[gpo] = _crear_grupo_base(
                    gpo, profesor_raw, cupo, vacantes, nombre_materia
                )
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
                if vacantes is not None:
                    grupos[gpo]["vacantes"] = vacantes
                    grupos[gpo]["sin_vacantes"] = vacantes <= 0

            _agregar_componente(grupos[gpo], tipo, horario, dias_str)

    return grupos


def parsear_grupos_desde_texto(soup, nombre_materia):
    """Respaldo para la versión móvil si la tabla vuelve a cambiar."""
    texto = soup.get_text("\n", strip=True)
    grupos = {}
    patron_grupo = re.compile(
        r"Gpo\.?\s*:\s*([^,\n]+)\s*,\s*Cupo\s*:\s*(\d+)"
        r"(?:\s*,\s*Vacantes\s*:\s*(\d+))?\s*(.*?)"
        r"(?=Gpo\.?\s*:|Horario\s+Actualizado|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for match in patron_grupo.finditer(texto):
        gpo, cupo_txt, vacantes_txt, bloque = match.groups()
        prof_match = re.search(
            r"Profesor\s*:\s*(.*?)(?=\n\s*Tipo\s*:)",
            bloque,
            flags=re.IGNORECASE | re.DOTALL,
        )
        profesor_raw = prof_match.group(1).strip() if prof_match else ""
        grupo = _crear_grupo_base(
            gpo,
            profesor_raw,
            int(cupo_txt),
            int(vacantes_txt) if vacantes_txt is not None else None,
            nombre_materia,
        )

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


# ==========================================================
# DIAGNÓSTICO DE CONFLICTOS DE HORARIO
# ==========================================================
DIAS_ORDEN = {"Lun": 0, "Mar": 1, "Mie": 2, "Jue": 3, "Vie": 4, "Sab": 5}


def minutos_a_hora(minutos):
    """Convierte minutos desde medianoche a HH:MM."""
    try:
        minutos = int(minutos)
    except (TypeError, ValueError):
        return "--:--"
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def nombre_corto_materia(nombre):
    """Elimina la clave para textos compactos, conservándola cuando no hay nombre."""
    nombre = str(nombre or "Materia").strip()
    if " - " in nombre:
        clave, descripcion = nombre.split(" - ", 1)
        return f"{clave} · {descripcion}"
    return nombre


def obtener_traslapes(g1, g2):
    """Devuelve todos los traslapes exactos entre dos grupos."""
    conflictos = []
    for s1 in g1.get("intervalos", []):
        for s2 in g2.get("intervalos", []):
            if s1.get("dia") != s2.get("dia"):
                continue

            inicio = max(int(s1.get("inicio", 0)), int(s2.get("inicio", 0)))
            fin = min(int(s1.get("fin", 0)), int(s2.get("fin", 0)))
            if inicio < fin:
                conflictos.append({
                    "dia": s1.get("dia"),
                    "inicio": inicio,
                    "fin": fin,
                    "duracion": fin - inicio,
                    "tipo_1": s1.get("tipo", ""),
                    "tipo_2": s2.get("tipo", ""),
                })
    return conflictos


def construir_materias_diagnostico(materias_db):
    """
    Construye el problema obligatorio real.

    Las materias opcionales no pueden hacer imposible el horario porque el
    generador siempre puede elegir N/A. Los bloqueos personales sí se incluyen.
    """
    registros = []
    for indice, materia in enumerate(materias_db):
        if not materia.get("obligatoria", True):
            continue

        grupos_activos = [
            g for g in materia.get("grupos", [])
            if g.get("activo", True)
            and g.get("gpo") != "N/A"
            and g.get("intervalos")
        ]
        if not grupos_activos:
            continue

        registros.append({
            "id": indice,
            "indice_db": indice,
            "nombre": materia.get("materia", f"Materia {indice + 1}"),
            "es_bloqueo": bool(materia.get("es_bloqueo", False)),
            "grupos": grupos_activos,
        })
    return registros


def buscar_solucion_por_restricciones(registros, limite_nodos=250_000):
    """
    Busca una combinación válida con backtracking y poda.

    Retorna (estado, solucion, nodos):
      - estado='valido': encontró una combinación.
      - estado='invalido': exploró todo y demostró que no existe.
      - estado='limite': no pudo concluir dentro del límite.
    """
    if not registros:
        return "valido", tuple(), 0

    ordenados = sorted(
        registros,
        key=lambda r: (len(r.get("grupos", [])), 0 if r.get("es_bloqueo") else 1),
    )
    elegidos = []
    nodos = 0
    limite_superado = False

    # Los grupos con menos conflictos se intentan primero. Esto ayuda a encontrar
    # una solución rápidamente cuando sí existe.
    def ordenar_candidatos(posicion):
        candidatos = ordenados[posicion].get("grupos", [])
        if not elegidos:
            return candidatos
        return sorted(
            candidatos,
            key=lambda g: sum(1 for elegido in elegidos if hay_traslape(g, elegido)),
        )

    def backtrack(posicion):
        nonlocal nodos, limite_superado
        if nodos >= limite_nodos:
            limite_superado = True
            return None
        if posicion >= len(ordenados):
            return tuple(elegidos)

        for grupo in ordenar_candidatos(posicion):
            nodos += 1
            if nodos >= limite_nodos:
                limite_superado = True
                return None
            if any(hay_traslape(grupo, elegido) for elegido in elegidos):
                continue
            elegidos.append(grupo)
            resultado = backtrack(posicion + 1)
            if resultado is not None:
                return resultado
            elegidos.pop()
        return None

    solucion = backtrack(0)
    if solucion is not None:
        return "valido", solucion, nodos
    if limite_superado:
        return "limite", None, nodos
    return "invalido", None, nodos


def encontrar_nucleo_conflicto(registros, limite_nodos=120_000):
    """Obtiene un subconjunto irreducible de materias/bloqueos incompatibles."""
    cache = {}

    def estado_de(subconjunto):
        clave = tuple(sorted(r["id"] for r in subconjunto))
        if clave not in cache:
            cache[clave] = buscar_solucion_por_restricciones(
                subconjunto,
                limite_nodos=limite_nodos,
            )[0]
        return cache[clave]

    if estado_de(registros) != "invalido":
        return [], cache

    nucleo = list(registros)
    cambio = True
    while cambio and len(nucleo) > 2:
        cambio = False
        for registro in list(nucleo):
            prueba = [r for r in nucleo if r["id"] != registro["id"]]
            if len(prueba) >= 2 and estado_de(prueba) == "invalido":
                nucleo = prueba
                cambio = True
                break
    return nucleo, cache


def analizar_pares_conflictivos(registros):
    """Calcula incompatibilidad y detalles para cada pareja de materias."""
    pares = []
    ranking_grupos = {}
    ventanas = {}

    for i in range(len(registros)):
        for j in range(i + 1, len(registros)):
            materia_a = registros[i]
            materia_b = registros[j]
            total = len(materia_a["grupos"]) * len(materia_b["grupos"])
            con_conflicto = 0
            minutos_totales = 0
            detalles = []

            for grupo_a in materia_a["grupos"]:
                for grupo_b in materia_b["grupos"]:
                    traslapes = obtener_traslapes(grupo_a, grupo_b)
                    if not traslapes:
                        continue

                    con_conflicto += 1
                    duracion_par = sum(t["duracion"] for t in traslapes)
                    minutos_totales += duracion_par
                    detalles.append({
                        "grupo_a": grupo_a,
                        "grupo_b": grupo_b,
                        "traslapes": traslapes,
                        "duracion": duracion_par,
                    })

                    for materia, grupo, otra in (
                        (materia_a, grupo_a, materia_b),
                        (materia_b, grupo_b, materia_a),
                    ):
                        clave_grupo = (materia["id"], str(grupo.get("gpo", "")))
                        entrada = ranking_grupos.setdefault(clave_grupo, {
                            "materia": materia["nombre"],
                            "grupo": grupo.get("gpo", ""),
                            "profesor": grupo.get("profesor", ""),
                            "pares_conflictivos": 0,
                            "minutos": 0,
                            "materias_afectadas": set(),
                        })
                        entrada["pares_conflictivos"] += 1
                        entrada["minutos"] += duracion_par
                        entrada["materias_afectadas"].add(otra["nombre"])

                    for traslape in traslapes:
                        clave_ventana = (
                            traslape["dia"],
                            traslape["inicio"],
                            traslape["fin"],
                        )
                        ventana = ventanas.setdefault(clave_ventana, {
                            "dia": traslape["dia"],
                            "inicio": traslape["inicio"],
                            "fin": traslape["fin"],
                            "apariciones": 0,
                            "parejas": set(),
                        })
                        ventana["apariciones"] += 1
                        ventana["parejas"].add(
                            (materia_a["nombre"], materia_b["nombre"])
                        )

            if con_conflicto:
                porcentaje = (con_conflicto / total * 100) if total else 0
                pares.append({
                    "materia_a": materia_a,
                    "materia_b": materia_b,
                    "total": total,
                    "con_conflicto": con_conflicto,
                    "porcentaje": porcentaje,
                    "incompatibilidad_total": total > 0 and con_conflicto == total,
                    "minutos_totales": minutos_totales,
                    "detalles": detalles,
                })

    pares.sort(
        key=lambda p: (
            p["incompatibilidad_total"],
            p["porcentaje"],
            p["minutos_totales"],
        ),
        reverse=True,
    )

    grupos_ordenados = sorted(
        ranking_grupos.values(),
        key=lambda g: (g["pares_conflictivos"], g["minutos"]),
        reverse=True,
    )
    ventanas_ordenadas = sorted(
        ventanas.values(),
        key=lambda v: (
            v["apariciones"],
            len(v["parejas"]),
            -DIAS_ORDEN.get(v["dia"], 99),
        ),
        reverse=True,
    )
    return pares, grupos_ordenados, ventanas_ordenadas


def sugerir_grupos_alternativos(registros, materias_db, max_sugerencias=6):
    """Busca grupos inactivos que por sí solos vuelvan factible el problema."""
    sugerencias = []
    for registro in registros:
        materia_original = materias_db[registro["indice_db"]]
        grupos_inactivos = [
            g for g in materia_original.get("grupos", [])
            if not g.get("activo", True)
            and g.get("gpo") != "N/A"
            and g.get("intervalos")
        ]

        for candidato in grupos_inactivos:
            prueba = []
            for actual in registros:
                copia = dict(actual)
                if actual["id"] == registro["id"]:
                    # Activarlo agrega una opción; no obliga a usarlo.
                    copia["grupos"] = list(actual["grupos"]) + [candidato]
                prueba.append(copia)

            estado, solucion, _ = buscar_solucion_por_restricciones(
                prueba,
                limite_nodos=100_000,
            )
            if estado == "valido":
                indice_grupo = next(
                    (
                        idx for idx, grupo_original
                        in enumerate(materia_original.get("grupos", []))
                        if grupo_original is candidato
                    ),
                    None,
                )
                sugerencias.append({
                    "materia": registro["nombre"],
                    "grupo": candidato.get("gpo", ""),
                    "profesor": candidato.get("profesor", ""),
                    "dias": candidato.get("dias", ""),
                    "horario": candidato.get("horario", ""),
                    "vacantes": candidato.get("vacantes"),
                    "indice_db": registro["indice_db"],
                    "indice_grupo": indice_grupo,
                })
                break

        if len(sugerencias) >= max_sugerencias:
            break
    return sugerencias


def analizar_conflictos_horario(materias_db):
    """Genera toda la información necesaria para explicar un fallo."""
    registros = construir_materias_diagnostico(materias_db)
    estado, solucion, nodos = buscar_solucion_por_restricciones(registros)

    resultado = {
        "estado": estado,
        "solucion": solucion,
        "nodos": nodos,
        "registros": registros,
        "nucleo": [],
        "pares": [],
        "ranking_grupos": [],
        "ventanas": [],
        "alternativas": [],
        "omisiones_que_resuelven": [],
    }

    if estado != "invalido":
        return resultado

    nucleo, _ = encontrar_nucleo_conflicto(registros)
    registros_analisis = nucleo or registros
    pares, ranking, ventanas = analizar_pares_conflictivos(registros_analisis)

    omisiones = []
    for registro in registros_analisis:
        prueba = [r for r in registros if r["id"] != registro["id"]]
        estado_prueba, _, _ = buscar_solucion_por_restricciones(
            prueba,
            limite_nodos=120_000,
        )
        if estado_prueba == "valido":
            omisiones.append(registro)

    resultado.update({
        "nucleo": registros_analisis,
        "pares": pares,
        "ranking_grupos": ranking,
        "ventanas": ventanas,
        "alternativas": sugerir_grupos_alternativos(
            registros,
            materias_db,
        ),
        "omisiones_que_resuelven": omisiones,
    })
    return resultado


def activar_grupo_desde_diagnostico(indice_materia, indice_grupo):
    """Activa un grupo sugerido antes de que Streamlit reconstruya los widgets."""
    try:
        grupo = st.session_state.materias_db[indice_materia]["grupos"][indice_grupo]
        grupo["activo"] = True
        st.session_state[f"tgl_{indice_materia}_{indice_grupo}"] = True
    except (IndexError, KeyError, TypeError):
        pass


def marcar_materia_opcional_desde_diagnostico(indice_materia):
    """Marca una materia como opcional desde una sugerencia del diagnóstico."""
    try:
        st.session_state.materias_db[indice_materia]["obligatoria"] = False
        st.session_state[f"radio_tipo_{indice_materia}"] = "Opcional"
    except (IndexError, KeyError, TypeError):
        pass


def texto_intervalos(traslapes):
    partes = []
    for t in sorted(
        traslapes,
        key=lambda x: (DIAS_ORDEN.get(x["dia"], 99), x["inicio"]),
    ):
        partes.append(
            f"{t['dia']} {minutos_a_hora(t['inicio'])}–{minutos_a_hora(t['fin'])}"
        )
    return ", ".join(partes)


def mostrar_diagnostico_conflictos(diagnostico):
    """Presenta un diagnóstico comprensible y accionable en Streamlit."""
    estado = diagnostico.get("estado")
    registros = diagnostico.get("registros", [])

    if len(registros) < 2:
        st.warning(
            "No hay suficientes materias obligatorias con grupos activos para "
            "analizar traslapes. Revisa que cada materia tenga al menos un grupo activo."
        )
        return

    if estado == "limite":
        st.warning(
            "El diagnóstico alcanzó su límite interno de búsqueda y no pudo demostrar "
            "si el horario es imposible. Abajo se muestran los traslapes más frecuentes."
        )
        pares, ranking, ventanas = analizar_pares_conflictivos(registros)
        diagnostico["pares"] = pares
        diagnostico["ranking_grupos"] = ranking
        diagnostico["ventanas"] = ventanas
    elif estado == "invalido":
        st.error(
            "No existe una combinación compatible con las materias obligatorias, "
            "los grupos activos y los bloqueos actuales."
        )

    nucleo = diagnostico.get("nucleo", [])
    if nucleo:
        nombres_nucleo = [nombre_corto_materia(r["nombre"]) for r in nucleo]
        st.markdown("### Núcleo del conflicto")
        st.write(
            "El problema puede reducirse a estas materias o bloqueos; las demás no son "
            "necesarias para reproducir el fallo:"
        )
        for nombre in nombres_nucleo:
            st.markdown(f"- **{nombre}**")

    pares = diagnostico.get("pares", [])
    if pares:
        st.markdown("### Conflictos principales")
        for posicion, par in enumerate(pares[:5], start=1):
            nombre_a = nombre_corto_materia(par["materia_a"]["nombre"])
            nombre_b = nombre_corto_materia(par["materia_b"]["nombre"])
            etiqueta = "Conflicto total" if par["incompatibilidad_total"] else "Conflicto alto"
            st.markdown(
                f"**{posicion}. {nombre_a} ↔ {nombre_b} — {etiqueta} "
                f"({par['con_conflicto']} de {par['total']} combinaciones, "
                f"{par['porcentaje']:.0f}%)**"
            )

            detalles_ordenados = sorted(
                par["detalles"],
                key=lambda d: d["duracion"],
                reverse=True,
            )
            with st.expander("Ver grupos y horas involucradas", expanded=posicion == 1):
                filas = []
                for detalle in detalles_ordenados[:20]:
                    g_a = detalle["grupo_a"]
                    g_b = detalle["grupo_b"]
                    filas.append({
                        "Grupo 1": f"G{g_a.get('gpo', '')} · {g_a.get('profesor', '')}",
                        "Grupo 2": f"G{g_b.get('gpo', '')} · {g_b.get('profesor', '')}",
                        "Traslape": texto_intervalos(detalle["traslapes"]),
                        "Minutos": detalle["duracion"],
                    })
                st.dataframe(
                    pd.DataFrame(filas),
                    width="stretch",
                    hide_index=True,
                )

    ranking = diagnostico.get("ranking_grupos", [])
    ventanas = diagnostico.get("ventanas", [])
    if ranking or ventanas:
        st.markdown("### Dónde se concentra el problema")
        col_grupos, col_horas = st.columns(2)

        with col_grupos:
            st.markdown("**Grupos con más conflictos**")
            filas_grupos = []
            for grupo in ranking[:8]:
                filas_grupos.append({
                    "Materia": nombre_corto_materia(grupo["materia"]),
                    "Grupo": grupo["grupo"],
                    "Conflictos": grupo["pares_conflictivos"],
                    "Materias afectadas": len(grupo["materias_afectadas"]),
                })
            if filas_grupos:
                st.dataframe(
                    pd.DataFrame(filas_grupos),
                    width="stretch",
                    hide_index=True,
                )

        with col_horas:
            st.markdown("**Horas con más traslapes**")
            filas_horas = []
            for ventana in ventanas[:8]:
                filas_horas.append({
                    "Día": ventana["dia"],
                    "Horario": (
                        f"{minutos_a_hora(ventana['inicio'])}–"
                        f"{minutos_a_hora(ventana['fin'])}"
                    ),
                    "Traslapes": ventana["apariciones"],
                    "Parejas": len(ventana["parejas"]),
                })
            if filas_horas:
                st.dataframe(
                    pd.DataFrame(filas_horas),
                    width="stretch",
                    hide_index=True,
                )

    st.markdown("### Posibles soluciones")
    hubo_sugerencias = False

    alternativas = diagnostico.get("alternativas", [])
    for alternativa in alternativas:
        hubo_sugerencias = True
        disponibilidad = alternativa.get("vacantes")
        texto_vacantes = (
            f" · {disponibilidad} vacantes"
            if disponibilidad is not None
            else ""
        )
        st.success(
            f"Activa el **grupo {alternativa['grupo']}** de "
            f"**{nombre_corto_materia(alternativa['materia'])}** "
            f"({alternativa.get('dias', '')} {alternativa.get('horario', '')})"
            f"{texto_vacantes}. Con esa opción sí existe al menos una combinación válida."
        )
        if alternativa.get("indice_grupo") is not None:
            st.button(
                f"Activar grupo {alternativa['grupo']}",
                key=(
                    f"diag_activar_{alternativa['indice_db']}_"
                    f"{alternativa['indice_grupo']}"
                ),
                on_click=activar_grupo_desde_diagnostico,
                args=(alternativa["indice_db"], alternativa["indice_grupo"]),
            )

    for registro in diagnostico.get("omisiones_que_resuelven", [])[:4]:
        hubo_sugerencias = True
        if registro.get("es_bloqueo"):
            st.info(
                f"Ajustar o quitar el bloqueo **{nombre_corto_materia(registro['nombre'])}** "
                "permite generar al menos un horario."
            )
        else:
            st.info(
                f"Marcar **{nombre_corto_materia(registro['nombre'])}** como opcional "
                "permite generar al menos un horario."
            )
            st.button(
                "Marcar como opcional",
                key=f"diag_opcional_{registro['indice_db']}",
                on_click=marcar_materia_opcional_desde_diagnostico,
                args=(registro["indice_db"],),
            )

    if not hubo_sugerencias:
        st.info(
            "No se encontró una solución de un solo cambio. Prueba activando más grupos "
            "en las materias del núcleo del conflicto o ajustando tus bloqueos personales."
        )

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


# ==========================================================
# GENERACIÓN EXHAUSTIVA OPTIMIZADA
# ==========================================================
def construir_grupos_para_generacion(materias_db):
    """Prepara las opciones activas y añade N/A a las materias opcionales."""
    grupos_input = []
    materias_omitidas = []

    for materia in materias_db:
        grupos_validos = [
            grupo
            for grupo in materia.get("grupos", [])
            if grupo.get("activo", True)
        ]

        if grupos_validos and not materia.get("obligatoria", True):
            grupos_validos = grupos_validos + [{
                "gpo": "N/A",
                "profesor": "",
                "horario": "",
                "dias": "",
                "intervalos": [],
                "calificacion": 0,
                "materia_nombre": materia.get("materia", "Materia opcional"),
                "cupo": None,
                "vacantes": None,
                "activo": True,
            }]

        if grupos_validos:
            grupos_input.append(grupos_validos)
        else:
            materias_omitidas.append(materia.get("materia", "Materia"))

    return grupos_input, materias_omitidas


def contar_combinaciones_teoricas(grupos_input):
    """Cuenta el producto cartesiano sin construirlo en memoria."""
    total = 1
    for grupos in grupos_input:
        total *= len(grupos)
    return total if grupos_input else 0


def mascara_horaria_grupo(grupo):
    """
    Codifica todos los minutos ocupados por un grupo en un entero de 8,640 bits.

    La intersección entre dos horarios se comprueba con una sola operación AND,
    conservando precisión de un minuto y sin aproximar a bloques de 30 minutos.
    """
    mascara = 0
    for intervalo in grupo.get("intervalos", []):
        dia = intervalo.get("dia")
        indice_dia = DIAS_ORDEN.get(dia)
        if indice_dia is None:
            continue

        try:
            inicio = max(0, min(1440, int(intervalo.get("inicio", 0))))
            fin = max(0, min(1440, int(intervalo.get("fin", 0))))
        except (TypeError, ValueError):
            continue

        if fin <= inicio:
            continue

        desplazamiento = indice_dia * 1440 + inicio
        mascara_intervalo = ((1 << (fin - inicio)) - 1) << desplazamiento
        mascara |= mascara_intervalo

    return mascara


def _densidad_conflictos_materia(indice, opciones, mascaras_por_id):
    """Estima qué tan útil es procesar una materia temprano para podar ramas."""
    conflictos = 0
    comparaciones = 0
    grupos_a = opciones[indice]

    for otro_indice, grupos_b in enumerate(opciones):
        if otro_indice == indice:
            continue
        for grupo_a in grupos_a:
            mascara_a = mascaras_por_id[id(grupo_a)]
            for grupo_b in grupos_b:
                comparaciones += 1
                if mascara_a & mascaras_por_id[id(grupo_b)]:
                    conflictos += 1

    return conflictos / comparaciones if comparaciones else 0.0


def generar_top_horarios_exhaustivo(
    grupos_input,
    pesos,
    config_dias,
    w_dias,
    top_k=10,
    progreso_callback=None,
):
    """
    Recorre todas las combinaciones potencialmente válidas con backtracking.

    No existe un límite artificial de combinaciones. Cuando una selección parcial
    ya tiene un traslape, elimina de una vez toda la rama descendiente. Por tanto,
    no pierde ningún horario válido y evita evaluar combinaciones imposibles.
    """
    if not grupos_input:
        return [], {
            "total_teorico": 0,
            "procesadas_equivalentes": 0,
            "combinaciones_validas": 0,
            "combinaciones_podadas": 0,
            "nodos_revisados": 0,
            "duracion_segundos": 0.0,
        }

    inicio_busqueda = time.perf_counter()
    total_teorico = contar_combinaciones_teoricas(grupos_input)

    # Precalcular las máscaras una sola vez.
    mascaras_por_id = {
        id(grupo): mascara_horaria_grupo(grupo)
        for grupos in grupos_input
        for grupo in grupos
    }

    # Menor número de candidatos primero; en empate, procesa antes la materia
    # que presenta más conflictos. Este orden aumenta la poda sin cambiar el
    # conjunto de horarios evaluados ni el resultado final.
    indices_originales = list(range(len(grupos_input)))
    densidades = {
        indice: _densidad_conflictos_materia(
            indice,
            grupos_input,
            mascaras_por_id,
        )
        for indice in indices_originales
    }
    orden_indices = sorted(
        indices_originales,
        key=lambda indice: (
            len(grupos_input[indice]),
            -densidades[indice],
        ),
    )

    opciones_ordenadas = []
    for indice in orden_indices:
        grupos = list(grupos_input[indice])
        # Solo cambia el orden de exploración. Los grupos con mejor nota se
        # intentan primero para llenar rápidamente el heap con buenas opciones.
        grupos.sort(
            key=lambda grupo: (
                grupo.get("gpo") == "N/A",
                -float(grupo.get("calificacion", 0) or 0),
            )
        )
        opciones_ordenadas.append(grupos)

    # Cantidad de combinaciones representadas por una rama desde cada nivel.
    producto_sufijo = [1] * (len(opciones_ordenadas) + 1)
    for posicion in range(len(opciones_ordenadas) - 1, -1, -1):
        producto_sufijo[posicion] = (
            producto_sufijo[posicion + 1]
            * len(opciones_ordenadas[posicion])
        )

    top_heap = []
    seleccion_original = [None] * len(grupos_input)
    combinaciones_validas = 0
    combinaciones_podadas = 0
    procesadas_equivalentes = 0
    nodos_revisados = 0
    secuencia_heap = 0
    ultimo_reporte = 0.0

    def reportar(forzar=False):
        nonlocal ultimo_reporte
        if progreso_callback is None:
            return
        ahora = time.perf_counter()
        if forzar or ahora - ultimo_reporte >= 0.15:
            progreso_callback({
                "total_teorico": total_teorico,
                "procesadas_equivalentes": procesadas_equivalentes,
                "combinaciones_validas": combinaciones_validas,
                "combinaciones_podadas": combinaciones_podadas,
                "nodos_revisados": nodos_revisados,
                "duracion_segundos": ahora - inicio_busqueda,
            })
            ultimo_reporte = ahora

    def backtrack(posicion, mascara_acumulada):
        nonlocal combinaciones_validas
        nonlocal combinaciones_podadas
        nonlocal procesadas_equivalentes
        nonlocal nodos_revisados
        nonlocal secuencia_heap

        if posicion == len(opciones_ordenadas):
            combinacion = tuple(seleccion_original)
            combinaciones_validas += 1
            procesadas_equivalentes += 1

            score = calcular_score(combinacion, pesos)
            score += calcular_penalizacion_por_dia(
                {"materias": combinacion},
                config_dias,
                w_dias=w_dias,
            )

            secuencia_heap += 1
            elemento = (score, secuencia_heap, combinacion)
            if len(top_heap) < top_k:
                heapq.heappush(top_heap, elemento)
            elif score > top_heap[0][0]:
                heapq.heapreplace(top_heap, elemento)

            reportar()
            return

        indice_original = orden_indices[posicion]
        descendientes_por_candidato = producto_sufijo[posicion + 1]

        for grupo in opciones_ordenadas[posicion]:
            nodos_revisados += 1
            mascara_grupo = mascaras_por_id[id(grupo)]

            if mascara_acumulada & mascara_grupo:
                # Todas las elecciones restantes de esta rama serían inválidas.
                combinaciones_podadas += descendientes_por_candidato
                procesadas_equivalentes += descendientes_por_candidato
                reportar()
                continue

            seleccion_original[indice_original] = grupo
            backtrack(posicion + 1, mascara_acumulada | mascara_grupo)
            seleccion_original[indice_original] = None

    backtrack(0, 0)
    reportar(forzar=True)

    mejores = sorted(top_heap, key=lambda item: item[0], reverse=True)
    posibles = [
        {"materias": combinacion, "score": score}
        for score, _, combinacion in mejores
    ]

    estadisticas = {
        "total_teorico": total_teorico,
        "procesadas_equivalentes": procesadas_equivalentes,
        "combinaciones_validas": combinaciones_validas,
        "combinaciones_podadas": combinaciones_podadas,
        "nodos_revisados": nodos_revisados,
        "duracion_segundos": time.perf_counter() - inicio_busqueda,
        "orden_materias": orden_indices,
    }
    return posibles, estadisticas


def formato_numero_entero(valor):
    try:
        return f"{int(valor):,}"
    except (TypeError, ValueError):
        return "0"

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
        cupo = g.get("cupo", "")
        vacantes = g.get("vacantes", "")

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
        desc += f"\\nCupo: {cupo}"
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
st.caption("Genera, compara y guarda opciones de horario sin traslapes")

if "materias_db" not in st.session_state:
    st.session_state.materias_db = []
if "api_cache_profes" not in st.session_state:
    st.session_state.api_cache_profes = {}
if "auto_restore_completado" not in st.session_state:
    st.session_state.auto_restore_completado = False
if "auto_restore_intentos" not in st.session_state:
    st.session_state.auto_restore_intentos = 0
if "autoguardado_bloqueado" not in st.session_state:
    st.session_state.autoguardado_bloqueado = False
if "autoguardado_suspendido_una_vez" not in st.session_state:
    st.session_state.autoguardado_suspendido_una_vez = False
if "mensaje_restauracion" not in st.session_state:
    st.session_state.mensaje_restauracion = None

local_storage = LocalStorage() if LocalStorage is not None else None

# Acciones solicitadas desde widgets en el rerun anterior. Se aplican aquí,
# antes de crear cualquier widget, para que Streamlit permita sincronizar sus claves.
if st.session_state.pop("_reinicio_usuario_pendiente", False):
    reiniciar_estado_usuario()
    st.session_state.autoguardado_bloqueado = False
    st.session_state.auto_restore_completado = True
    st.session_state.autoguardado_suspendido_una_vez = True
    st.session_state._ultimo_estado_autoguardado = firma_estado_guardable()
    st.session_state.mensaje_restauracion = "Progreso local borrado. Comenzaste desde cero."

if "_estado_importado_pendiente" in st.session_state:
    estado_importado_pendiente = st.session_state.pop("_estado_importado_pendiente")
    try:
        errores_importacion = restaurar_estado_guardado(estado_importado_pendiente)
        st.session_state.auto_restore_completado = True
        st.session_state.autoguardado_bloqueado = bool(errores_importacion)
        st.session_state._ultimo_estado_autoguardado = None
        if errores_importacion:
            st.session_state.mensaje_restauracion = (
                "El respaldo se importó parcialmente. No se pudieron actualizar estas claves: "
                + ", ".join(errores_importacion)
                + ". El autoguardado quedó pausado para proteger el archivo local."
            )
        else:
            st.session_state.mensaje_restauracion = (
                "Respaldo importado y preparado para guardarse automáticamente."
            )
    except Exception as e:
        st.session_state.auto_restore_completado = True
        st.session_state.autoguardado_bloqueado = True
        st.session_state.mensaje_restauracion = f"No se pudo importar el respaldo: {e}"

# Leer el almacenamiento antes de construir widgets. El componente puede necesitar
# dos renderizados para devolver el valor; durante ese tiempo no se sobrescribe nada.
if local_storage is None:
    st.session_state.auto_restore_completado = True
elif not st.session_state.auto_restore_completado:
    # streamlit-local-storage 0.0.25 acepta la clave del elemento
    # como único argumento. No se debe pasar el parámetro `key=` de Streamlit.
    valor_local_inicio = local_storage.getItem(STORAGE_KEY)
    st.session_state.auto_restore_intentos += 1

    if valor_local_inicio:
        try:
            errores_restauracion = restaurar_estado_guardado(valor_local_inicio)
            st.session_state.auto_restore_completado = True
            st.session_state.autoguardado_bloqueado = bool(errores_restauracion)
            st.session_state._ultimo_estado_autoguardado = firma_estado_guardable()
            if errores_restauracion:
                st.session_state.mensaje_restauracion = (
                    "Se recuperó el progreso, pero no se pudieron actualizar estas claves: "
                    + ", ".join(errores_restauracion)
                    + ". El autoguardado quedó pausado para no sobrescribir el respaldo."
                )
            else:
                st.session_state.mensaje_restauracion = (
                    "Progreso recuperado automáticamente de este navegador."
                )
            st.rerun()
        except Exception as e:
            st.session_state.auto_restore_completado = True
            st.session_state.autoguardado_bloqueado = True
            st.session_state.mensaje_restauracion = (
                f"No se pudo recuperar el guardado local: {e}. "
                "No se sobrescribirá hasta que borres o importes un respaldo."
            )
    elif st.session_state.auto_restore_intentos >= 2:
        # Segunda ejecución sin valor: no había un progreso previo.
        st.session_state.auto_restore_completado = True
        st.session_state._ultimo_estado_autoguardado = firma_estado_guardable()

if st.session_state.get("mensaje_restauracion"):
    mensaje = st.session_state.pop("mensaje_restauracion")
    if st.session_state.get("autoguardado_bloqueado"):
        st.warning(mensaje)
    else:
        st.toast(mensaje, icon="💾")

# --- GUÍA DE USO DETALLADA ---
with st.expander("📘 Cómo usar el generador", expanded=False):
    st.markdown("""
    ### Pasos rápidos

    **1. Agrega tus materias**

    Escribe una clave, o varias separadas por comas, por ejemplo: `1730` o `1120, 1601, 32`. Puedes presionar **Enter** o usar el botón **Agregar materias**.

    Si no conoces la clave, puedes revisarla en los [Mapas Curriculares Oficiales](http://escolar.ingenieria.unam.mx/mapas/).

    **2. Revisa tus grupos**

    Cada materia muestra profesor, horario, salón o modalidad, cupo total y vacantes restantes.

    * Activa solamente los grupos que sí aceptarías en tu horario.
    * Los grupos con **0 vacantes** aparecen desmarcados y en rojo, pero todavía puedes activarlos manualmente.
    * Puedes marcar una materia como **Opcional** para que el generador la omita cuando sea necesario.
    * Usa **🔄 Refrescar vacantes** para consultar nuevamente la información publicada por la Facultad sin borrar tu selección.

    **3. Consulta las calificaciones de profesores**

    Usa **⭐ Consultar profesores** para revisar las calificaciones de IngenieríaTracker. Cuando la coincidencia del nombre es exacta o suficientemente probable, la calificación se aplica automáticamente; de cualquier forma puedes cambiarla manualmente.

    Junto a cada profesor puedes abrir sus reseñas de IngenieríaTracker o usar el enlace discreto **Buscar en Google**.

    **4. Personaliza tu horario**

    * En **🕒 Bloquear una hora** puedes agregar trabajo, comida, transporte u otra actividad que no deba traslaparse.
    * En **⚙️ Preferencias del horario** puedes priorizar turno, menos horas muertas, mejores profesores o una mayor cantidad de materias.
    * La configuración avanzada por día permite preferir, limitar o evitar días específicos.

    **5. Genera y compara**

    Antes de comenzar se muestra el número de combinaciones teóricas. Si dejaste muchos grupos activos, la búsqueda puede tardar más, pero el sistema descarta anticipadamente las ramas con traslapes y no se queda solamente con las primeras combinaciones.

    Los resultados se pueden descargar como imagen o calendario `.ics`. Los grupos sin vacantes quedan marcados con una advertencia dentro del horario.

    **6. Si no existe un horario posible**

    En lugar de mostrar solamente un error, la app señala las materias, grupos, días y horas con más conflictos. También busca grupos alternativos o materias que podrías dejar como opcionales para desbloquear el horario.

    **7. Tu progreso se guarda automáticamente**

    Materias, grupos, calificaciones, bloqueos y preferencias se guardan en este navegador y se intentan recuperar cuando vuelvas a abrir la app. No necesitas descargar el JSON cada vez; ese archivo es solamente un respaldo opcional para mover tu configuración a otro navegador o dispositivo.

    ⚠️ **Aviso importante:** Esta app **NO es dueña** de IngenieríaTracker ni está afiliada. Todo el crédito de las reseñas pertenece a **www.ingenieriatracker.com**.
    """)



# --- BARRA LATERAL (CONFIGURACIÓN) ---

# --- COLUMNAS PRINCIPALES ---
col_in, col_list = st.columns([1, 1.2])

# ==========================================
# COLUMNA IZQUIERDA: ENTRADA DE DATOS
# ==========================================
with col_in:
    st.subheader("1. Agrega tus materias")
    
    st.caption("Escribe una o varias claves separadas por comas. Presiona **Enter** o usa el botón para agregarlas.")
    
    # Un formulario permite agregar las claves tanto con el botón como con Enter.
    with st.form("form_agregar_materias", clear_on_submit=True):
        clave_input = st.text_input(
            "Claves de materias",
            placeholder="Ejemplo: 1730 ó 1120, 1601, 32"
        )
        agregar_materias = st.form_submit_button(
            "Agregar materias",
            width="stretch",
        )

    if agregar_materias:
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
    with st.expander("🕒 Bloquear una hora", expanded=False):
        st.info("Agrega trabajo, comida, transporte o cualquier actividad que no deba traslaparse con tus clases.")
        act_nombre = st.text_input("Nombre de la actividad", "Actividad personal")
        act_dias = st.multiselect("Días", ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"])

        c_hora1, c_hora2 = st.columns(2)
        t_inicio = c_hora1.time_input("Inicio")
        t_fin = c_hora2.time_input("Fin")

        if st.button("Agregar bloqueo", width="stretch"):
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
    st.caption("Elige qué cosas quieres priorizar al ordenar tus opciones.")
    with st.expander("⚙️ Preferencias del horario", expanded=True):

        if "pref_tipo_turno_widget" not in st.session_state:
            st.session_state.pref_tipo_turno_widget = st.session_state.get(
                "tipo_turno_guardado", "Mixto"
            )
        if "pref_w_turno_widget" not in st.session_state:
            st.session_state.pref_w_turno_widget = int(st.session_state.get("w_turno_guardado", 30))
        if "pref_w_huecos_widget" not in st.session_state:
            st.session_state.pref_w_huecos_widget = int(st.session_state.get("w_huecos_guardado", 50))
        if "pref_w_profes_widget" not in st.session_state:
            st.session_state.pref_w_profes_widget = int(st.session_state.get("w_profes_guardado", 70))
        if "pref_w_carga_widget" not in st.session_state:
            st.session_state.pref_w_carga_widget = int(st.session_state.get("w_carga_guardado", 80))

        tipo_turno = st.selectbox(
            "Preferencia de Turno",
            ["Mañana (Temprano)", "Tarde / Noche", "Mixto"],
            key="pref_tipo_turno_widget",
            help="Elige en qué momento del día prefieres tomar clases."
        )

        w_turno = st.slider(
            "Priorizar el turno elegido",
            0, 100,
            key="pref_w_turno_widget",
            help="Qué tanto debe esforzarse el sistema por respetar tu preferencia de mañana o tarde."
        )

        w_huecos = st.slider(
            "Minimizar horas muertas",
            0, 100,
            key="pref_w_huecos_widget",
            help="Busca juntar tus clases para que no tengas tiempos libres excesivos entre ellas."
        )

        w_profes = st.slider(
            "Calificación de profesores",
            0, 100,
            key="pref_w_profes_widget",
            help="Da prioridad a los profesores con mayor calificación."
        )

        w_carga = st.slider(
            "Priorizar más materias",
            0, 100,
            key="pref_w_carga_widget",
            help="Da prioridad a las opciones que incluyan más materias, especialmente cuando tienes materias opcionales."
        )

        pesos = {
            "huecos": w_huecos,
            "profes": w_profes,
            "tipo_turno": tipo_turno,
            "peso_turno": w_turno,
            "carga": w_carga
        }
        st.session_state.tipo_turno_guardado = tipo_turno
        st.session_state.w_turno_guardado = w_turno
        st.session_state.w_huecos_guardado = w_huecos
        st.session_state.w_profes_guardado = w_profes
        st.session_state.w_carga_guardado = w_carga
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

        with st.expander("⚙️ Preferencias avanzadas por día `experimental`", expanded=False):
            st.caption(
                "Ajusta días específicos solamente si lo necesitas. "
                "Ejemplo: lunes y miércoles temprano, martes y jueves con más clases, o viernes libre."
            )

            # Peso global de esta configuración (qué tanto afecta al score)
            if "pref_w_dias_widget" not in st.session_state:
                st.session_state.pref_w_dias_widget = int(
                    st.session_state.get("w_dias_guardado", 35)
                )
            w_dias = st.slider(
                "Importancia de preferencias por día",
                0, 100,
                key="pref_w_dias_widget",
                help="Entre más alto, más tratará de respetar tu preferencia de carga y horarios por día."
            )

            st.session_state.w_dias_guardado = w_dias
            st.markdown("---")

            tabs_dias = st.tabs(["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"])

            dias_lista = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab"]

            for idx, dia in enumerate(dias_lista):
                with tabs_dias[idx]:
                    cfg = st.session_state.config_dias[dia]
                    st.session_state.setdefault(f"modo_{dia}", cfg.get("modo", "Normal"))
                    st.session_state.setdefault(f"pref_{dia}", cfg.get("preferencia", "Mixto"))
                    st.session_state.setdefault(f"maxb_{dia}", int(cfg.get("max_bloques", 10)))
                    st.session_state.setdefault(f"ev_{dia}", bool(cfg.get("evitar", False)))

                    c1, c2 = st.columns([1, 1])

                    modo = c1.selectbox(
                        f"{dia} - Modo",
                        ["Normal", "Prioridad", "Evitar"],
                        key=f"modo_{dia}",
                        help="Normal = se comporta normal. Prioridad = intenta meter más carga aquí. Evitar = penaliza clases este día."
                    )

                    preferencia = c2.selectbox(
                        f"{dia} - Preferencia de horario",
                        ["Temprano", "Tarde", "Mixto", "Libre"],
                        key=f"pref_{dia}",
                        help="Libre intenta evitar ese día (si es posible)."
                    )

                    max_bloques = st.slider(
                        f"{dia} - Máximo de bloques (30 min) deseados",
                        0, 20,
                        key=f"maxb_{dia}",
                        help="0 = idealmente libre. 6 = aprox 3 horas. 10 = 5 horas. 14 = 7 horas."
                    )

                    evitar = st.toggle(
                        f"{dia} - Evitar este día",
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
            st.success("✅ Los cambios se guardan automáticamente.")

    st.markdown("---")

    # ==========================================================
    # FUNCIONES EXPERIMENTALES
    # ==========================================================
    with st.expander("🧪 Funciones experimentales", expanded=False):
        st.markdown("#### 💾 Guardado automático y respaldo")
        st.caption(
            "La app guarda automáticamente materias, grupos, calificaciones, bloqueos y "
            "preferencias en este navegador. Al volver a abrirla intenta "
            "recuperar todo sin que tengas que pulsar ningún botón."
        )

        if local_storage is None:
            st.warning(
                "Instala `streamlit-local-storage==0.0.25` para activar "
                "el guardado automático del navegador."
            )
        elif st.session_state.get("autoguardado_bloqueado"):
            st.warning(
                "El autoguardado está pausado porque una restauración fue "
                "incompleta o el respaldo local no pudo leerse. Puedes importar "
                "un JSON válido o borrar el progreso local para comenzar de cero."
            )
        elif st.session_state.get("auto_restore_completado"):
            st.success("Guardado automático activo en este navegador.")
        else:
            st.info("Leyendo el progreso guardado del navegador...")

        if local_storage is not None and st.button(
            "Borrar progreso local y comenzar de cero",
            key="exp_borrar_progreso_completo",
            use_container_width=True,
        ):
            try:
                if hasattr(local_storage, "eraseItem"):
                    local_storage.eraseItem(STORAGE_KEY)
                else:
                    local_storage.deleteItem(STORAGE_KEY)
            except Exception:
                local_storage.deleteAll()
            st.session_state._reinicio_usuario_pendiente = True
            st.session_state.autoguardado_suspendido_una_vez = True
            time.sleep(0.8)
            st.rerun()

        st.markdown("---")
        st.caption(
            "El JSON es opcional: úsalo únicamente para mover tu configuración a "
            "otro navegador o conservar una copia manual."
        )
        respaldo_json = json.dumps(
            crear_estado_guardable(),
            ensure_ascii=False,
            indent=2,
        )
        c_export, c_import = st.columns(2)
        c_export.download_button(
            "Descargar respaldo opcional (.json)",
            data=respaldo_json.encode("utf-8"),
            file_name="horarios_fi_respaldo.json",
            mime="application/json",
            use_container_width=True,
        )
        archivo_respaldo = c_import.file_uploader(
            "Importar respaldo",
            type=["json"],
            key="archivo_respaldo_json",
        )
        if archivo_respaldo is not None and st.button(
            "Aplicar respaldo importado",
            key="exp_aplicar_respaldo",
            use_container_width=True,
        ):
            try:
                st.session_state._estado_importado_pendiente = json.load(archivo_respaldo)
                st.rerun()
            except Exception as e:
                st.error(f"El archivo no pudo importarse: {e}")


# ==========================================
# COLUMNA DERECHA: LISTA Y GESTIÓN
# ==========================================
with col_list:
    # Estado global de expanders
    if "expand_materias" not in st.session_state:
        st.session_state.expand_materias = True  # empiezan abiertas

    c_header_1, c_header_2, c_header_3, c_header_4 = st.columns([2, 1, 1.25, 1])

    c_header_1.subheader("2. Revisa materias y grupos")

    if c_header_2.button("🔄 Refrescar vacantes", use_container_width=True):
        refrescar_vacantes()
        st.rerun()

    if c_header_3.button("⭐ Consultar profesores", use_container_width=True):
        with st.spinner("Consultando calificaciones en IngenieríaTracker..."):
            resumen_api = actualizar_calificaciones_automaticamente()
        st.success(
            f"Actualizadas: {resumen_api['aplicados']} · "
            f"Coincidencias dudosas: {resumen_api['dudosos']} · "
            f"Sin resultado: {resumen_api['no_encontrados']}"
        )
        st.rerun()

    label_expand = "📁 Plegar todo" if st.session_state.expand_materias else "📂 Expandir todo"
    if c_header_4.button(label_expand, use_container_width=True):
        st.session_state.expand_materias = not st.session_state.expand_materias
        st.rerun()


    if not st.session_state.materias_db:
        st.info("Todavía no has agregado materias. Escribe una clave en la sección de la izquierda para comenzar.")

    for i, m in enumerate(st.session_state.materias_db):
        status = " (Opcional)" if not m['obligatoria'] else ""

        with st.expander(f"{m['materia']}{status}", expanded=st.session_state.expand_materias):

            c_api_1, c_api_2 = st.columns([1, 1])
            if c_api_1.button("⭐ Consultar profesores", key=f"api_mat_{i}", width="stretch"):
                with st.spinner("Consultando calificaciones de profesores..."):
                    resumen_api = actualizar_calificaciones_automaticamente(i)
                c_api_2.success(
                    f"Actualizadas: {resumen_api['aplicados']} · "
                    f"Coincidencias dudosas: {resumen_api['dudosos']} · "
                    f"Sin resultado: {resumen_api['no_encontrados']}"
                )
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

                cupo_grupo = g.get("cupo")
                vacantes_grupo = g.get("vacantes")
                if isinstance(vacantes_grupo, (int, float)):
                    color_vac = (
                        "green" if vacantes_grupo > 5
                        else ("orange" if vacantes_grupo > 0 else "red")
                    )
                else:
                    color_vac = "gray"
                sug = g.get("sugerencia_api", None)
                num_res = g.get("api_num_resenas", None)
                consultado = g.get("api_consultado", False)

                sug_txt = ""

                if consultado:
                    if sug is not None:
                        sug_txt = f"⭐ Calificación consultada: <strong>{float(sug):.2f}</strong>"

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
                        sug_txt = "<span style='color:gray;'>⭐ Calificación consultada: No encontrada</span>"

                salon = g.get("salon", None)
                modalidad = normalizar_modalidad_texto(g.get("modalidad"))
                if not modalidad:
                    _, modalidad_inferida, _ = _datos_profesor(
                        g.get("profesor_raw") or g.get("profesor", "")
                    )
                    modalidad = modalidad_inferida
                    if modalidad:
                        st.session_state.materias_db[i]["grupos"][j]["modalidad"] = modalidad

                # Limpia también datos restaurados desde respaldos de versiones anteriores.
                profesor_mostrado = limpiar_nombre_profesor(g.get("profesor", ""))
                if profesor_mostrado:
                    st.session_state.materias_db[i]["grupos"][j]["profesor"] = profesor_mostrado

                salon_txt = ""
                modalidad_legible = modalidad.title() if modalidad else None

                if salon and str(salon).strip().upper() != "SIN":
                    contenido_salon = f"Salón: <strong>{salon}</strong>"
                    if modalidad_legible:
                        contenido_salon += f" · {modalidad_legible}"
                    salon_txt = f" <span style='color:#555;'>({contenido_salon})</span>"
                elif modalidad == "EN LÍNEA":
                    salon_txt = " <span style='color:#777;'>(En línea)</span>"
                elif modalidad_legible:
                    salon_txt = (
                        " <span style='color:#777;'>"
                        f"(Salón no publicado · {modalidad_legible})</span>"
                    )
                else:
                    salon_txt = " <span style='color:#777;'>(Salón no publicado)</span>"


                estado_vacantes = ""
                if isinstance(vacantes_grupo, (int, float)) and vacantes_grupo <= 0:
                    estado_vacantes = (
                        " <strong style='color:#d32f2f;'>⚠️ Sin vacantes</strong>"
                        " <span style='color:gray;'>(puedes incluirlo manualmente)</span>"
                    )

                info_html = f"""
                <div style="font-size: 0.9em;">
                    <strong>Gpo {g['gpo']}</strong> - {profesor_mostrado or g['profesor']}{salon_txt}<br>
                    📅 {g['dias']} ({g['horario']})<br>
                    Cupo: <strong>{cupo_grupo if cupo_grupo is not None else "N/D"}</strong>
                    · Vacantes: <strong style='color: {color_vac}'>{vacantes_grupo if vacantes_grupo is not None else "N/D"}</strong>
                    {estado_vacantes}<br>
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
                        step=1.0,
                        format="%.2f",
                        key=key_widget,
                        label_visibility="collapsed"
                    )

                    nueva_calif = round(nueva_calif, 2)

                    st.session_state.materias_db[i]['grupos'][j]['calificacion'] = nueva_calif

                    profesor_actual = limpiar_nombre_profesor(g.get("profesor", ""))
                    if profesor_actual not in {"", "Tú", "SIN PROFESOR PUBLICADO"}:
                        link_google = link_busqueda_google_profesor(profesor_actual)
                        if link_google:
                            c_calif.markdown(
                                f"""
                                <div style="text-align:center; font-size:0.78em; margin-top:-8px; white-space:nowrap;">
                                    <a href="{link_google}" target="_blank"
                                       style="color:gray; text-decoration:none;">
                                        Buscar en Google
                                    </a>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )

                    st.markdown(
                        "<div style='height:6px; border-bottom: 1px solid rgba(200,200,200,0.25); margin: 6px 0;'></div>",
                        unsafe_allow_html=True
                    )

# --- BOTÓN DE GENERACIÓN ---
st.markdown("---")
st.subheader("3. Genera tus horarios")

st.caption(
    "El sistema buscará las mejores opciones usando los grupos activos y tus preferencias. "
    "Si no existe una combinación válida, mostrará dónde están los conflictos y qué podrías cambiar."
)

# ============================
# OPCIONES DE VISUALIZACIÓN
# ============================
c_vis1, c_vis2 = st.columns([1, 1])

mostrar_sin_cupo = c_vis1.toggle(
    "Mostrar ⚠️ SIN VACANTES en el horario",
    value=True,
    help=(
        "Los grupos con cero vacantes siguen disponibles para seleccionarlos, "
        "pero se marcan con borde rojo y una advertencia."
    ),
)
c_vis2.caption(
    "Los grupos sin vacantes aparecen desmarcados al cargarlos, pero puedes activarlos manualmente."
)

# Conteo visible antes de generar. No descarga datos ni construye combinaciones.
grupos_preview, _materias_omitidas_preview = construir_grupos_para_generacion(
    st.session_state.materias_db
)
total_comb_preview = contar_combinaciones_teoricas(grupos_preview)
if total_comb_preview:
    st.markdown(
        f"**Opciones posibles antes de revisar traslapes:** "
        f"`{formato_numero_entero(total_comb_preview)}`"
    )
    st.caption(
        "La búsqueda optimizada descarta ramas con traslapes antes de completarlas, "
        "pero evalúa todos los horarios que todavía pueden ser válidos."
    )

if st.button("Generar mis horarios", width="stretch"):
    if not st.session_state.materias_db:
        st.error("No puedes generar horarios sin materias. Agrega al menos una.")
    else:
        grupos_input, materias_omitidas = construir_grupos_para_generacion(
            st.session_state.materias_db
        )

        if materias_omitidas:
            st.warning(
                "⚠️ Se omitieron materias porque no tienen ningún grupo activo:\n\n"
                + "\n".join([f"- {x}" for x in materias_omitidas])
                + "\n\n💡 Tip: Activa al menos un grupo si quieres que se considere."
            )

        if not grupos_input:
            st.error("No hay grupos activos para generar horarios. Activa al menos un grupo.")
            st.stop()

        total_comb = contar_combinaciones_teoricas(grupos_input)
        st.info(
            f"Se analizará un espacio de **{formato_numero_entero(total_comb)} "
            "combinaciones teóricas**. Los traslapes se podarán antes de construir "
            "cada combinación completa."
        )

        if total_comb >= 1_000_000:
            st.warning(
                f"⚠️ Hay **{formato_numero_entero(total_comb)} combinaciones**. "
                "Como permanecen muchos grupos activos o hay pocas restricciones, "
                "la búsqueda exhaustiva puede tardar más de lo habitual. No se "
                "cortará después de las primeras opciones: se revisarán todas las "
                "ramas que puedan contener un horario válido."
            )
        elif total_comb >= 100_000:
            st.info(
                "La selección es relativamente amplia. La generación puede tardar "
                "un poco más, aunque la poda de traslapes suele reducir mucho el trabajo."
            )

        barra_progreso = st.progress(0.0)
        texto_progreso = st.empty()

        def actualizar_progreso_generacion(datos):
            total = max(1, int(datos.get("total_teorico", 1)))
            procesadas = min(total, int(datos.get("procesadas_equivalentes", 0)))
            barra_progreso.progress(min(1.0, procesadas / total))
            texto_progreso.caption(
                f"Analizadas o descartadas: {formato_numero_entero(procesadas)} / "
                f"{formato_numero_entero(total)} · "
                f"Horarios válidos: {formato_numero_entero(datos.get('combinaciones_validas', 0))} · "
                f"Combinaciones descartadas: {formato_numero_entero(datos.get('combinaciones_podadas', 0))}"
            )

        posibles, estadisticas_generacion = generar_top_horarios_exhaustivo(
            grupos_input=grupos_input,
            pesos=pesos,
            config_dias=st.session_state.config_dias,
            w_dias=w_dias,
            top_k=10,
            progreso_callback=actualizar_progreso_generacion,
        )
        barra_progreso.progress(1.0)
        texto_progreso.caption(
            f"Búsqueda completa: {formato_numero_entero(estadisticas_generacion['total_teorico'])} "
            f"combinaciones teóricas · "
            f"{formato_numero_entero(estadisticas_generacion['combinaciones_validas'])} horarios válidos · "
            f"{formato_numero_entero(estadisticas_generacion['combinaciones_podadas'])} combinaciones "
            f"descartadas anticipadamente por traslape · "
            f"{estadisticas_generacion['duracion_segundos']:.2f} s."
        )

        # La búsqueda exhaustiva ya distingue entre cero horarios válidos y un
        # corte artificial, por lo que el diagnóstico solo se ejecuta cuando la
        # incompatibilidad es real.
        diagnostico_conflictos = None
        if not posibles:
            diagnostico_conflictos = analizar_conflictos_horario(
                st.session_state.materias_db
            )

        # ==========================================================
        # Mostrar resultados
        # ==========================================================
        if posibles:

            # El score completo ya se aplicó durante la búsqueda del top 10.
            posibles = sorted(posibles, key=lambda x: x["score"], reverse=True)

            st.success("¡Listo! Encontramos tus mejores opciones de horario.")
            tabs = st.tabs([f"Opción {i+1}" for i in range(len(posibles))])


            colores = [
                "#FFCDD2", "#C5CAE9", "#B2DFDB", "#FFF9C4", "#E1BEE7",
                "#FFCCBC", "#D7CCC8", "#F0F4C3", "#B3E5FC", "#DCEDC8",
                "#F8BBD0", "#CFD8DC"
            ]

            for i, tab in enumerate(tabs):
                with tab:
                    opcion = posibles[i]

                    grupos_sin_vacantes = [
                        g for g in opcion["materias"]
                        if g.get("gpo") != "N/A"
                        and g.get("vacantes") is not None
                        and g.get("vacantes") <= 0
                    ]
                    if grupos_sin_vacantes:
                        detalle_sin_vacantes = ", ".join(
                            f"{g.get('materia_nombre', 'Materia')} · Gpo {g.get('gpo', '')}"
                            for g in grupos_sin_vacantes
                        )
                        st.warning(
                            "⚠️ Esta opción incluye grupos sin vacantes actuales: "
                            + detalle_sin_vacantes
                        )

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
                        vacs_grupo = m_g.get("vacantes")
                        sin_cupo = False
                        try:
                            if vacs_grupo is not None and int(vacs_grupo) <= 0:
                                sin_cupo = True
                        except (TypeError, ValueError):
                            sin_cupo = False
                        tag_cupo = " ⚠️SIN VACANTES" if (sin_cupo and mostrar_sin_cupo) else ""
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
                    st.markdown("### 📋 Materias de esta opción")

                    lista_resumen = []
                    for g in opcion["materias"]:
                        # Ignoramos si no aplica (N/A)
                        if g.get("gpo") == "N/A":
                            continue

                        materia_nombre = g.get("materia_nombre", "")
                        profesor = g.get("profesor", "")
                        salon = g.get("salon", "SIN")
                        
                        cupo = g.get("cupo")
                        vacantes = g.get("vacantes")
                        estado_vacantes = (
                            "Sin vacantes"
                            if vacantes is not None and vacantes <= 0
                            else "Disponible" if vacantes is not None else "No disponible"
                        )

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
                            "Cupo": cupo,
                            "Vacantes": vacantes,
                            "Estado": estado_vacantes,
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
            st.markdown("## No encontramos un horario compatible")
            if diagnostico_conflictos is None:
                diagnostico_conflictos = analizar_conflictos_horario(
                    st.session_state.materias_db
                )
            mostrar_diagnostico_conflictos(diagnostico_conflictos)
        del posibles
        gc.collect()


# ==========================================================
# AUTOGUARDADO EN EL NAVEGADOR
# ==========================================================
# Se ejecuta al final para capturar cambios hechos por cualquier widget durante
# este rerun. La firma ignora la fecha, así que solo escribe cuando algo cambió.
if (
    local_storage is not None
    and st.session_state.get("auto_restore_completado", False)
    and not st.session_state.get("autoguardado_bloqueado", False)
):
    if st.session_state.get("autoguardado_suspendido_una_vez", False):
        st.session_state.autoguardado_suspendido_una_vez = False
    else:
        firma_actual = firma_estado_guardable()
        firma_anterior = st.session_state.get("_ultimo_estado_autoguardado")
        if firma_actual != firma_anterior:
            estado_autoguardado = crear_estado_guardable()
            estado_json_autoguardado = json.dumps(
                estado_autoguardado,
                ensure_ascii=False,
                sort_keys=True,
            )
            # API compatible con streamlit-local-storage 0.0.25.
            local_storage.setItem(STORAGE_KEY, estado_json_autoguardado)
            st.session_state._ultimo_estado_autoguardado = firma_actual

# --- PIE DE PÁGINA ---
st.markdown("---")
footer_col1, footer_col2, footer_col3 = st.columns([3, 2, 3])
with footer_col2:
    st.markdown("<div style='text-align: center; color: gray; font-size: 0.9em;'>Creado por: Gael prevaricare</div>", unsafe_allow_html=True)
    st.link_button("Instagram", "https://www.instagram.com/gaelprevaricare/", width="stretch")
