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
    # Se modificó el regex para detectar claves de 2, 3 y 4 dígitos (\d{2,4})
    bloques = re.split(r'(\b\d{2,4}\s+-\s+.+)', texto_sucio)
    
    for i in range(1, len(bloques), 2):
        nombre_materia = bloques[i].split('\t')[0].strip()
        cuerpo = bloques[i+1].strip()
        datos_materia = {"materia": nombre_materia, "obligatoria": es_obligatoria, "grupos": []}
        
        patron_grupo = r'(\d+)\s+(.+?)\s+([A-Z])\s+(\d{2}:\d{2}\s+a\s+\d{2}:\d{2})\s+([\w\s,]+)\s+(\d+)\s+(\d+)'
        grupos_encontrados = re.findall(patron_grupo, cuerpo)
        
        for g in grupos_encontrados:
            intervalos = extraer_intervalos(g[3], g[4].split(','))
            datos_materia["grupos"].append({
                "gpo": g[0], 
                "profesor": g[1].strip(), 
                "horario": g[3],
                "dias": g[4], 
                "intervalos": intervalos, 
                "calificacion": int(g[6]),
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

# --- GUÍA RÁPIDA ---
with st.expander("Guía de uso e instrucciones de formato", expanded=True):
    st.write("""
    ### Instrucciones y Flujo Recomendado:
    1. **Preparación en Excel (Recomendado)**: 
        * Copie los horarios directamente desde la página de la facultad a un archivo de Excel.
        * Agregue una última columna titulada **Calificación** (asigne un valor del 1 al 10 a cada profesor).
        * Seleccione y copie los datos desde Excel para pegarlos aquí.
    2. **Configuración**: Ajuste los pesos en la barra lateral según sus prioridades.
    3. **Entrada de datos**: Pegue el texto de sus materias **materia por materia** (incluyendo el nombre y encabezado).
    4. **Formato**: El sistema ahora detecta claves de **2, 3 y 4 dígitos**.

    **Ejemplo de formato correcto (desde Excel):**
    """)
    st.code("""
1601 - COMPORTAMIENTO DE SUELOS
Clave	Gpo	Profesor	Tipo	Horario	Días	Cupo	Calificacion
1601	1	M.I. EDUARDO ALVAREZ CAZARES	T	07:00 a 08:30	Lun, Mie, Vie	25	10
1601	2	ING. ARACELI ANGELICA SANCHEZ	T	08:30 a 10:00	Lun, Mie, Vie	25	9
    """, language="text")
    st.write("""
    5. **Procesamiento**: Presione el botón de generar para obtener las mejores combinaciones.
    """)

with st.sidebar:
    st.header("Configuración de Pesos")
    st.info("Determine la prioridad de cada parámetro para el cálculo del puntaje.")
    w_huecos = st.slider("Minimizar horas muertas", 0, 100, 50)
    w_profes = st.slider("Calificación de profesores", 0, 100, 70)
    w_temprano = st.slider("Preferencia salida temprana", 0, 100, 30)
    w_carga = st.slider("Cantidad de materias", 0, 100, 80)
    pesos = {"huecos": w_huecos, "profes": w_profes, "temprano": w_temprano, "carga": w_carga}

col_in, col_list = st.columns([1, 1])

with col_in:
    st.subheader("1. Carga de Materias")
    tipo = st.radio("Categoría:", ["Obligatorio", "Opcional"], horizontal=True)
    raw_text = st.text_area("Pegue el texto de la materia aquí:", height=180, help="Pegue el bloque de texto completo de una materia (incluyendo nombre, encabezados y grupos con calificacion).")
    if st.button("Procesar Materia"):
        nuevas = parsear_texto(raw_text, tipo == "Obligatorio")
        if nuevas:
            st.session_state.materias_db.extend(nuevas)
            st.success(f"Registrada(s) {len(nuevas)} materia(s)")
        else:
            st.error("Formato no reconocido. Asegúrese de incluir el nombre de la materia (Ej: 101 - Nombre) y los datos tabulados.")

with col_list:
    st.subheader("2. Materias Registradas")
    if not st.session_state.materias_db:
        st.write("No hay materias en la lista.")
    for i, m in enumerate(st.session_state.materias_db):
        col_m, col_b = st.columns([4, 1])
        status = "(Opcional)" if not m['obligatoria'] else "(Obligatoria)"
        col_m.write(f"**{m['materia']}** {status}")
        if col_b.button("Eliminar", key=f"del_{i}"):
            st.session_state.materias_db.pop(i)
            st.rerun()

st.divider()

# --- PROCESO DE GENERACIÓN ---
if st.button("Generar combinaciones optimizadas", use_container_width=True):
    if not st.session_state.materias_db:
        st.error("Lista de materias vacía.")
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
            st.warning("No se encontraron combinaciones viables.")

# --- PIE DE PÁGINA ---
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: gray; font-size: 0.8em;'>"
    "Gael prevaricare"
    "</div>", 
    unsafe_allow_html=True
)