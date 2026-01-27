import streamlit as st
import pandas as pd
import re
import itertools

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Generador de Horarios", layout="wide")

# --- LÓGICA DEL PARSER ---
def hora_a_minutos(hora_str):
    h, m = map(int, hora_str.split(':'))
    return h * 60 + m

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
    bloques = re.split(r'(\b\d{2,4}\s+-\s+.+)', texto_sucio)
    
    for i in range(1, len(bloques), 2):
        nombre_materia = bloques[i].split('\t')[0].strip()
        cuerpo = bloques[i+1].strip()
        datos_materia = {"materia": nombre_materia, "obligatoria": es_obligatoria, "grupos": []}
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
    huecos = 0
    for dia in ["Lun", "Mar", "Mie", "Jue", "Vie"]:
        clases = sorted([s for g in grupos_reales for s in g['intervalos'] if s['dia'] == dia], key=lambda x: x['inicio'])
        for i in range(len(clases)-1):
            huecos += (clases[i+1]['inicio'] - clases[i]['fin']) / 60
    score -= huecos * pesos['huecos']
    
    promedio_p = sum(g['calificacion'] for g in grupos_reales) / len(grupos_reales)
    score += promedio_p * pesos['profes']
    
    salidas = [s['fin'] for g in grupos_reales for s in g['intervalos']]
    ultima_salida = max(salidas) if salidas else 1440
    score += ((1440 - ultima_salida) / 60) * pesos['temprano']
    
    score += len(grupos_reales) * pesos['carga']
    return score

# --- INTERFAZ DE USUARIO ---
st.title("Generador de Horarios")

if 'materias_db' not in st.session_state:
    st.session_state.materias_db = []

# --- GUÍA DE USO DETALLADA ---
with st.expander("Guía de uso e instrucciones de formato", expanded=False):
    st.markdown("""
    ### Pasos para generar tu horario ideal:
    
    **1. Consulta los horarios oficiales:**
    Ve a la página de la facultad: [Horarios FI UNAM (SSA)](https://www.ssa.ingenieria.unam.mx/horarios.html).
    
    **2. Copia la información:**
    Selecciona y copia todo el bloque de texto de la materia que te interesa. Asegúrate de incluir desde el nombre de la materia (Ej: `1601 - COMPORTAMIENTO...`) hasta el último grupo disponible.
    
    **3. Pega y Procesa:**
    Pega el texto en el cuadro de "Carga de Materias" y presiona el botón **Procesar Materia**. Repite esto con cada asignatura que quieras cursar.
    
    **4. Ajusta las calificaciones:**
    En la columna derecha ("Materias Registradas"), verás las materias que cargaste. Abre el menú desplegable de cada una y **asigna una calificación del 0 al 10** a los profesores. El algoritmo usará esto para recomendarte los mejores grupos.
    
    **5. Genera:**
    Presiona el botón final para que el sistema cree las mejores combinaciones posibles basándose en tus preferencias.
    """)
    
    st.markdown("**Ejemplo de texto válido para copiar:**")
    st.code("""
1601 - COMPORTAMIENTO DE SUELOS
ASIGNATURA IMPARTIDA POR LA DICYG
http://escolar.ingenieria.unam.mx/asesoria/asesores/#DICYG
GRUPOS CON VACANTES
Clave	Gpo	Profesor	Tipo	Horario	Días	Cupo	Vacantes
1601	1	M.I. EDUARDO ALVAREZ CAZARES
(PRESENCIAL)	T	07:00 a 08:30	Lun, Mie, Vie	25	25
1601	2	ING. ARACELI ANGELICA SANCHEZ ENRIQUEZ
(PRESENCIAL)	T	08:30 a 10:00	Lun, Mie, Vie	25	25
    """, language="text")

# --- BARRA LATERAL (CONFIGURACIÓN) ---
with st.sidebar:
    st.header("Configuración de Pesos")
    st.info("Define qué es lo más importante para ti al armar el horario.")
    
    st.markdown("---")
    
    w_huecos = st.slider("Minimizar horas muertas", 0, 100, 50, 
                         help="Si aumentas este valor, el sistema buscará horarios compactos para evitar tiempos de espera entre clases.")
    
    w_profes = st.slider("Calificación de profesores", 0, 100, 70, 
                         help="Si aumentas este valor, el sistema priorizará a los profesores a los que les diste una calificación alta (10), aunque el horario sea feo.")
    
    w_temprano = st.slider("Preferencia salida temprana", 0, 100, 30, 
                         help="Si aumentas este valor, el sistema intentará que tu última clase termine lo más temprano posible.")
    
    w_carga = st.slider("Cantidad de materias", 0, 100, 80, 
                        help="Si aumentas este valor, el sistema priorizará meter la mayor cantidad de materias posibles en el horario.")
    
    pesos = {"huecos": w_huecos, "profes": w_profes, "temprano": w_temprano, "carga": w_carga}

# --- COLUMNAS PRINCIPALES ---
col_in, col_list = st.columns([1, 1.2])

with col_in:
    st.subheader("1. Carga de Materias")
    tipo = st.radio("Categoría:", ["Obligatorio", "Opcional"], horizontal=True)
    raw_text = st.text_area("Pega el texto del portal aquí:", height=250, placeholder="Pega aquí el contenido copiado de la página de horarios...")
    
    if st.button("Procesar Materia", use_container_width=True):
        nuevas = parsear_texto(raw_text, tipo == "Obligatorio")
        if nuevas:
            st.session_state.materias_db.extend(nuevas)
            st.success(f"Se ha registrado correctamente: {len(nuevas)} materia(s).")
            st.rerun()
        else:
            st.error("No se detectaron grupos válidos. Verifica que copiaste el encabezado de la materia y la tabla de grupos.")

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
            
            st.write("**Grupos detectados (ajusta la calificación):**")
            
            for j, g in enumerate(m['groups' if 'groups' in m else 'grupos']):
                if g['gpo'] == "N/A": continue
                
                c1, c2, c3 = st.columns([1, 3, 1.5])
                c1.write(f"**Gpo {g['gpo']}**")
                c2.write(f"{g['profesor']}\n\n{g['dias']} ({g['horario']})")
                
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
                    df_v = pd.DataFrame("", index=horas_labels, columns=["Lun", "Mar", "Mie", "Jue", "Vie"])
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
                                        df_v.iloc[h_idx][s['dia']] = m_g['profesor'][:18]
                                    else:
                                        df_v.iloc[h_idx][s['dia']] = "|"
                    st.table(df_v)
        else:
            st.warning("No se encontraron combinaciones posibles sin traslapes. Intenta marcar alguna materia como 'Opcional' o verifica los horarios.")

# --- PIE DE PÁGINA ---
st.markdown("---")
footer_col1, footer_col2, footer_col3 = st.columns([3, 2, 3])
with footer_col2:
    st.markdown("<div style='text-align: center; color: gray; font-size: 0.9em;'>Gael prevaricare</div>", unsafe_allow_html=True)
    st.link_button("Instagram", "https://www.instagram.com/gaelprevaricare/", use_container_width=True)