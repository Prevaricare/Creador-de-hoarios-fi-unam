import streamlit as st
import pandas as pd
import re
import itertools

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Scheduler Pro 2026", layout="wide")

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

# NUEVA FUNCIÓN PARSEAR_TEXTO (MÁS FLEXIBLE Y CON LÓGICA DE OPCIONALES)
def parsear_texto(texto_sucio, es_obligatoria):
    materias = []
    # Separar por el patrón de código - Nombre (Ej: 1601 - MATEMATICAS)
    bloques = re.split(r'(\d{3,4}\s+-\s+.+)', texto_sucio)
    
    for i in range(1, len(bloques), 2):
        nombre_materia = bloques[i].split('\t')[0].strip()
        cuerpo = bloques[i+1].strip()
        datos_materia = {"materia": nombre_materia, "obligatoria": es_obligatoria, "grupos": []}
        
        # Regex flexible: detecta cualquier espacio o tabulador \s+
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
                "materia_nombre": nombre_materia  # Se agregó este campo
            })
        
        # Si la materia es opcional, se agrega un grupo "VACÍO" para permitir no cursarla
        if not es_obligatoria and datos_materia["grupos"]:
            datos_materia["grupos"].append({
                "gpo": "N/A", "profesor": "N/A", "horario": "S/H",
                "dias": "N/A", "intervalos": [], "calificacion": 0,
                "materia_nombre": "VACÍO"
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
    # Filtrar grupos vacíos para no arruinar el promedio de profesores
    grupos_reales = [g for g in combinacion if g['gpo'] != "N/A"]
    if not grupos_reales: return -1000
    
    score = 0
    # 1. Huecos
    huecos = 0
    for dia in ["Lun", "Mar", "Mie", "Jue", "Vie"]:
        clases = sorted([s for g in grupos_reales for s in g['intervalos'] if s['dia'] == dia], key=lambda x: x['inicio'])
        for i in range(len(clases)-1):
            huecos += (clases[i+1]['inicio'] - clases[i]['fin']) / 60
    score -= huecos * pesos['huecos']
    
    # 2. Profesores
    promedio_p = sum(g['calificacion'] for g in grupos_reales) / len(grupos_reales)
    score += promedio_p * pesos['profes']
    
    # 3. Temprano (Premiamos que el fin del día sea menor)
    salidas = [s['fin'] for g in grupos_reales for s in g['intervalos']]
    ultima_salida = max(salidas) if salidas else 1440
    score += ((1440 - ultima_salida) / 60) * pesos['temprano']
    
    # 4. Cantidad de clases
    score += len(grupos_reales) * pesos['carga']
    return score

# --- INTERFAZ DE USUARIO (UI) ---
st.title("🎯 Scheduler Pro: El Mejor Horario")

if 'materias_db' not in st.session_state:
    st.session_state.materias_db = []

with st.sidebar:
    st.header("⚙️ Configuración de Pesos")
    w_huecos = st.slider("Evitar huecos (horas muertas)", 0, 100, 50)
    w_profes = st.slider("Mejores profesores (0-11)", 0, 100, 70)
    w_temprano = st.slider("Salir/Entrar temprano", 0, 100, 30)
    w_carga = st.slider("Carga máxima de materias", 0, 100, 80)
    pesos = {"huecos": w_huecos, "profes": w_profes, "temprano": w_temprano, "carga": w_carga}

col_in, col_list = st.columns([1, 1])

with col_in:
    st.subheader("1. Pegar Materia")
    tipo = st.radio("Categoría:", ["Obligatorio", "Opcional"], horizontal=True)
    raw_text = st.text_area("Pega el texto del portal escolar:", height=150, placeholder="1601 - MATERIA EJEMPLO...")
    if st.button("Añadir Materia"):
        nuevas = parsear_texto(raw_text, tipo == "Obligatorio")
        if nuevas:
            st.session_state.materias_db.extend(nuevas)
            st.success(f"Añadida(s) {len(nuevas)} materia(s)")
        else:
            st.error("No se detectó el formato correcto. Revisa el texto.")

with col_list:
    st.subheader("2. Materias para Combinar")
    for i, m in enumerate(st.session_state.materias_db):
        col_m, col_b = st.columns([4, 1])
        # Indicar visualmente si es opcional
        etiqueta = "🔸" if not m['obligatoria'] else "🔹"
        col_m.write(f"{etiqueta} **{m['materia']}** ({len(m['grupos'])} grupos)")
        if col_b.button("🗑️", key=f"del_{i}"):
            st.session_state.materias_db.pop(i)
            st.rerun()

st.divider()

# --- PROCESO DE ITERACIÓN ---
if st.button("🚀 GENERAR TOP 10 HORARIOS", use_container_width=True):
    if not st.session_state.materias_db:
        st.error("Añade algunas materias primero.")
    else:
        grupos_input = [m['grupos'] for m in st.session_state.materias_db]
        posibles = []
        
        # Barra de progreso e información
        todas_comb = list(itertools.product(*grupos_input))
        st.info(f"Analizando {len(todas_comb):,} combinaciones posibles...")
        progreso = st.progress(0)
        
        for idx, comb in enumerate(todas_comb):
            if es_horario_valido(comb):
                sc = calcular_score(comb, pesos)
                posibles.append({"materias": comb, "score": sc})
            
            # Actualizar barra cada 100 iteraciones para no alentar la UI
            if idx % 100 == 0:
                progreso.progress((idx+1)/len(todas_comb))
        
        posibles = sorted(posibles, key=lambda x: x['score'], reverse=True)[:10]
        
        if posibles:
            st.balloons()
            tabs = st.tabs([f"Opción {i+1}" for i in range(len(posibles))])
            for i, tab in enumerate(tabs):
                with tab:
                    score_val = posibles[i]['score']
                    st.write(f"**Puntaje de Excelencia:** `{score_val:.2f}`")
                    
                    # --- NUEVA LÓGICA DE VISUALIZACIÓN (30 MINUTOS) ---
                    # 1. Generar etiquetas de tiempo cada 30 minutos
                    horas_labels = []
                    for h in range(7, 22):
                        horas_labels.append(f"{h:02d}:00")
                        horas_labels.append(f"{h:02d}:30")

                    # 2. Crear el DataFrame con la nueva resolución
                    df_v = pd.DataFrame("", index=horas_labels, columns=["Lun", "Mar", "Mie", "Jue", "Vie"])
                    
                    for m_g in posibles[i]['materias']:
                        if m_g['gpo'] == "N/A": continue 
                        for s in m_g['intervalos']:
                            # Redondeamos al bloque de 30 min más cercano para la visualización
                            # Esto soluciona el problema de las clases de 1:30h o 2:15h
                            h_i = f"{s['inicio']//60:02d}:{'30' if (s['inicio']%60 >= 30) else '00'}"
                            h_f = f"{s['fin']//60:02d}:{'30' if (s['fin']%60 >= 30) else '00'}"
                            
                            if h_i in horas_labels and h_f in horas_labels:
                                start_idx = horas_labels.index(h_i)
                                end_idx = horas_labels.index(h_f)
                                
                                for h_idx in range(start_idx, end_idx):
                                    # Diseño inteligente de la celda
                                    if h_idx == start_idx:
                                        # Primera celda: Nombre de la materia
                                        df_v.iloc[h_idx][s['dia']] = f"📌 {m_g['materia_nombre'][:20]}"
                                    elif h_idx == start_idx + 1:
                                        # Segunda celda: Nombre del profesor
                                        df_v.iloc[h_idx][s['dia']] = f"👨‍🏫 {m_g['profesor'][:18]}"
                                    else:
                                        # Celdas restantes: Indicador de continuidad
                                        df_v.iloc[h_idx][s['dia']] = "║"
                    
                    # Mostrar la tabla con un estilo más limpio
                    st.table(df_v)
                    