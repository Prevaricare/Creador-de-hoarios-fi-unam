# Generador de Horarios FI - UNAM (Automatizado)

Esta herramienta descarga automáticamente la información oficial de la Facultad de Ingeniería, verifica los cupos en tiempo real y utiliza un algoritmo para generarte las **10 mejores combinaciones posibles** sin traslapes.

Tú decides qué es importante: **¿Buenos profesores? ¿Salir temprano? ¿Evitar huecos?**  
La app hace el resto.

## Prueba la App en vivo
**[Click aquí para usar el Generador](https://horarios-fi-unam.streamlit.app/)**

---

## ¿Qué hace esta app?
- Descarga grupos directamente desde la fuente oficial (sin copiar/pegar tablas).
- Muestra **vacantes en tiempo real** (y puedes refrescarlas cuando quieras).
- Te permite **filtrar grupos** (por ejemplo, ignorar grupos llenos o profes no deseados).
- Te permite asignar **calificaciones personalizadas** a profesores (0 a 10).
- Incluye **bloqueos personales** (trabajo, comida, gym, etc.) para evitar traslapes.
- Genera las **Top-10 mejores opciones** usando optimización por pesos.
- (Nuevo) Consulta sugerencias de calificación de profesores con base en reseñas externas (IngenieriaTracker).

---

## Características Nuevas
### Conexión Directa
Ya no necesitas copiar y pegar texto. Solo ingresa la **clave de la materia**.

### Cupos en Tiempo Real
Visualiza cuántas vacantes quedan y filtra los grupos llenos.

### Bloqueos Personales
¿Trabajas o entrenas? Bloquea esos horarios para que no se toquen.

### Optimización Inteligente
El algoritmo busca entre millones de combinaciones para darte el horario ideal según tus prioridades.

### (Nuevo) Sugerencias de calificación por profesor (IngenieriaTracker)
Dentro de cada materia puedes buscar sugerencias de calificación para apoyar tu decisión al elegir grupo.  
**Importante:** esto es solo una referencia y **no modifica tu calificación manual**.

---

# Guía de Uso Detallada

## 1. Configuración de Prioridades (Pesos)
En el menú lateral izquierdo, define qué es lo más importante para ti.

- **Minimizar horas muertas:** Junta tus clases lo más posible.
- **Calificación de profesores:** Prioriza a los profes que tú califiques alto.
- **Preferencia de turno:** Intenta acomodar todo en la mañana o tarde.
- **Cantidad de materias:** Intenta meter todas las materias de tu lista.

<img width="842" height="804" alt="image" src="https://github.com/user-attachments/assets/62139e48-30ea-4aa3-abdf-c91b2d41eb80" />

> Tip: Si solo te importa meter materias sin importar el horario, baja los otros pesos.

---

## 2. Carga Automática de Materias
Ya no sufras copiando tablas.

1. Busca la **Clave** de tu asignatura (4 dígitos, ej: `1120`, `1601`).  
   Si no la sabes, checa los [Mapas Curriculares](http://escolar.ingenieria.unam.mx/mapas/).
2. Ingresa la clave en el cuadro de texto.
3. Presiona **"Buscar y Agregar Materia"**.

<img width="844" height="798" alt="image" src="https://github.com/user-attachments/assets/3f06310d-cb2d-48bc-810a-53bec4c31522" />

> El sistema se conectará a la DICYG/DCB y bajará los grupos al instante.

---

## 3. Gestión de Grupos y Vacantes
Una vez cargada la materia, aparecerá en la lista de la derecha.

- **Vacantes en color:** Se colorean según disponibilidad.
- **Checkbox ☑️:** Desmarca los grupos que **NO** quieras.
- **Botón 🔄 Refrescar Cupos:** Actualiza vacantes sin volver a cargar todo.

<img width="831" height="795" alt="image" src="https://github.com/user-attachments/assets/27a673fd-0f6e-4a85-9cd1-f26fbecc8df3" />

---

## 4. Calificación de Profesores (Manual)
Asigna un valor del **0 al 10** a cada profesor.

- **10:** Quiero este profe sí o sí.
- **0:** Evitar a toda costa (aunque si es la única opción, el sistema podría usarlo).

<img width="844" height="801" alt="image" src="https://github.com/user-attachments/assets/786fa092-357a-4a98-8394-f6bffa219765" />

---

## 5. Consulta Promedios de Profesores (Nuevo)
Dentro de cada materia, presiona:

**"Buscar sugerencias de calificación (IngenieriaTracker)"**

Esto mostrará una **sugerencia de promedio** por profesor.

- Esta sugerencia **NO modifica** tu calificación manual.
- Si no hay coincidencia, se mostrará **"No encontrado"**.
- Úsalo como referencia rápida cuando no conoces al profesor.

📌 **Esta función es nueva**, si notas algún error o nombre no detectado, mándame mensaje para mejorar coincidencias.

> 🖼️ **Imagen pendiente:** aquí va la captura de pantalla de esta sección.

---

## 6. Agregar Bloqueos (Trabajo/Comida/Gym)
En la columna izquierda, despliega la sección:

**"Agregar Actividad Manual / Bloqueo"**

Define un horario (ej. *Trabajo* de 14:00 a 18:00) y agrégalo.  
El sistema lo tratará como una clase obligatoria que no se puede mover.

<img width="843" height="803" alt="image" src="https://github.com/user-attachments/assets/29d863a0-bd7b-4928-9d87-7f9e83e72969" />

---

## 7. Generar Horarios
Cuando tengas tus materias listas y filtros aplicados, presiona el botón:

**"Generar combinaciones optimizadas"**

Explora las pestañas (Opción 1, Opción 2...) para ver diferentes propuestas gráficas.

<img width="846" height="800" alt="image" src="https://github.com/user-attachments/assets/44270cc8-a871-412b-b3b6-27b00c845bc6" />

---

## 8. Carga Masiva de Calificaciones (Experimental)
Para agilizar el proceso de personalización, la aplicación permite importar calificaciones desde una hoja de cálculo (Excel o Google Sheets).  
Esta función actualiza automáticamente la prioridad de los profesores basándose en tu lista personal.

### Pasos
1. En la barra lateral izquierda, despliega la sección:
   **"Carga Masiva de Calificaciones desde Excel"**
2. Selecciona y copia las celdas de tu tabla de Excel/Sheets.
3. Pega el contenido en el cuadro de texto.
4. Presiona **"Aplicar Calificaciones Masivas"**.

### Formato recomendado
Puedes pegarlo con columnas como:
