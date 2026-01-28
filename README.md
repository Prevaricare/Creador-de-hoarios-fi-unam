# 📅 Generador de Horarios FI - UNAM (Automatizado)

¡Olvídate de armar horarios a mano! Esta herramienta descarga automáticamente la información oficial de la Facultad de Ingeniería, verifica los cupos en tiempo real y utiliza un algoritmo para generarte las 10 mejores combinaciones posibles sin traslapes.

Tú decides qué es importante: ¿Buenos profesores? ¿Salir temprano? ¿Evitar huecos? La app hace el resto.

## 🚀 Prueba la App en vivo
👉 **[Click aquí para usar el Generador](https://horarios-fi-unam.streamlit.app/)**

---

## ✨ Características Nuevas
* **Conexión Directa:** Ya no necesitas copiar y pegar texto. Solo ingresa la clave de la materia.
* **Cupos en Tiempo Real:** Visualiza cuántas vacantes quedan y filtra los grupos llenos.
* **Bloqueos Personales:** ¿Trabajas o entrenas? Bloquea esos horarios para que no se toquen.
* **Optimización Inteligente:** El algoritmo busca entre millones de combinaciones para darte el horario perfecto.

---

## 📖 Guía de Uso Detallada

### 1. Configuración de Prioridades (Pesos)
En el menú lateral izquierdo, define qué es lo más importante para ti.
* **Minimizar horas muertas:** Junta tus clases lo más posible.
* **Calificación de profesores:** Prioriza a los profes que tú califiques alto.
* **Preferencia de turno:** Intenta acomodar todo en la mañana o tarde.
* **Cantidad de materias:** Intenta meter todas las materias de tu lista.

![Configuración de Pesos]
<img width="842" height="804" alt="image" src="https://github.com/user-attachments/assets/62139e48-30ea-4aa3-abdf-c91b2d41eb80" />
*(Tip: Si solo te importa meter materias sin importar el horario, baja los otros pesos)*

### 2. Carga Automática de Materias
Ya no sufras copiando tablas.
1.  Busca la **Clave** de tu asignatura (4 dígitos, ej: `1120`, `1601`). Si no la sabes, checa los [Mapas Curriculares](http://escolar.ingenieria.unam.mx/mapas/).
2.  Ingresa la clave en el cuadro de texto a la izquierda.
3.  Presiona **"Buscar y Agregar Materia"**.

![Carga de Materias]
<img width="844" height="798" alt="image" src="https://github.com/user-attachments/assets/3f06310d-cb2d-48bc-810a-53bec4c31522" />
*El sistema se conectará a la DICYG/DCB y bajará los grupos al instante.*

### 3. Gestión de Grupos y Vacantes (¡Nuevo!)
Una vez cargada la materia, aparecerá en la lista de la derecha.
* **Verde/Rojo:** Los números de vacantes se colorean según la disponibilidad.
* **Checkbox ☑️:** Desmarca los grupos que **NO** quieras (por ejemplo, grupos llenos o profes que no te gustan). El generador los ignorará.
* **Botón 🔄 Refrescar Cupos:** Si pasaron 10 minutos y quieres ver si se abrió un lugar, presiona este botón arriba de la lista.

![Gestión de Cupos]
<img width="831" height="795" alt="image" src="https://github.com/user-attachments/assets/27a673fd-0f6e-4a85-9cd1-f26fbecc8df3" />


### 4. Calificación de Profesores
Asigna un valor del **0 al 10** a cada profesor.
* **10:** ¡Quiero este profe sí o sí!
* **0:** Evitar a toda costa (aunque si es la única opción, el sistema podría usarlo).

<img width="844" height="801" alt="image" src="https://github.com/user-attachments/assets/786fa092-357a-4a98-8394-f6bffa219765" />


### 5. Agregar Bloqueos (Trabajo/Comida)
En la columna izquierda, despliega la sección **"Agregar Actividad Manual / Bloqueo"**.
Define un horario (ej. "Trabajo" de 14:00 a 18:00) y agrégalo. El sistema lo tratará como una clase obligatoria que no se puede mover.

![Bloqueos]
<img width="843" height="803" alt="image" src="https://github.com/user-attachments/assets/29d863a0-bd7b-4928-9d87-7f9e83e72969" />

### 6. Generar Horarios
Cuando tengas tus materias listas y filtros aplicados, presiona el botón grande al final: **"Generar combinaciones optimizadas"**.
Explora las pestañas (Opción 1, Opción 2...) para ver las diferentes propuestas gráficas.

![Resultados]

<img width="846" height="800" alt="image" src="https://github.com/user-attachments/assets/44270cc8-a871-412b-b3b6-27b00c845bc6" />


---

## 🛠️ Instalación Local (Para Desarrolladores)

Si quieres correr esto en tu propia computadora:

1.  Clona el repositorio:
    ```bash
    git clone [https://github.com/Prevaricare/Creador-de-hoarios-fi-unam.git](https://github.com/Prevaricare/Creador-de-hoarios-fi-unam.git)
    ```
2.  Instala las dependencias:
    ```bash
    pip install -r requirements.txt
    ```
3.  Ejecuta la app:
    ```bash
    streamlit run app.py
    ```

---
**¿Encontraste un fallo?** Mándame un mensaje o abre un "Issue" aquí en GitHub.
Hecho con ❤️ por [Gael Prevaricare](https://www.instagram.com/gaelprevaricare/)
