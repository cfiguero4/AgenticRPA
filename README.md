# Agentic RPA (ARPA)

Un sistema de Automatización Robótica de Procesos (RPA) que utiliza un agente de lenguaje natural (LLM) para **aprender** tareas de automatización web y luego las **reproduce** de forma rápida y determinista sin la necesidad del LLM.

## Cómo Funciona

Este proyecto implementa un enfoque de dos fases para la automatización, combinando la flexibilidad de los modelos de lenguaje con la robustez de los scripts deterministas.

*   **Fase 1: Aprendizaje (`learn.py`)**: Un agente basado en un LLM (como Gemini) interpreta un prompt en lenguaje natural (ej: "Inicia sesión en X y navega a Y"). El agente ejecuta la tarea en un navegador real, registrando cada acción (clics, escritura, etc.) en un archivo de flujo de trabajo (`.json`). Este proceso es ideal para la exploración y la creación inicial de flujos.

*   **Fase 2: Ejecución (`replay.py`)**: El sistema reproduce el flujo de trabajo grabado utilizando acciones predefinidas y deterministas basadas en selectores web estables (como ID, `data-test`, etc.). Esta fase no invoca al LLM, lo que garantiza una ejecución extremadamente rápida, económica y fiable, ideal para entornos de producción.

### Diagrama del Flujo

```text
============================================
 FASE 1: APRENDIZAJE (Dinámico con learn.py)
============================================
[Usuario da un Prompt en lenguaje natural]
           |
           v
     [learn.py]
     (Usa LLM para interpretar y decidir acciones)
           |
           v
     [El agente (browser-use) ejecuta la tarea usando Playwright]
           |
           v
     [Se graba el flujo de acciones en `flujo.json`]
           |
           |
================================================
 FASE 2: EJECUCIÓN (Determinista con replay.py)
================================================
           |
           v
     [Usuario selecciona `flujo.json`]
           |
           v
     [replay.py]
     (Ejecuta acciones predefinidas usando selectores)
           |
           v
     [El agente (browser-use) replica la tarea usando Playwright]
           |
           v
     [Se obtiene el Resultado Final]
```

## Instalación

Sigue estos pasos para poner en marcha el proyecto en tu entorno local.

**1. Prerrequisitos**
*   Python 3.9 o superior.

**2. Clonar el Repositorio**
```bash
git clone https://github.com/tu_usuario/arpa.git
cd arpa
```

**3. Crear y Activar un Entorno Virtual**
```bash
# Crear el entorno
python -m venv venv

# Activar en Windows
source venv/Scripts/activate

# Activar en macOS/Linux
source venv/bin/activate
```

**4. Instalar Dependencias**
```bash
pip install -r requirements.txt
```

**5. Configurar Variables de Entorno**
Crea un archivo llamado `.env` en la raíz del proyecto. Necesitarás, como mínimo, una clave de API para el modelo de lenguaje.

```env
# .env - Ejemplo de configuración
GOOGLE_API_KEY="tu_clave_de_api_de_google_aqui"

# Opcional: variables para flujos de trabajo específicos
USER="tu_usuario_de_prueba"
PASS="tu_contraseña_secreta"
BASE_URL="https://un-sitio-de-ejemplo.com"
```

## Uso (Interfaz Gráfica)

La forma más sencilla de interactuar con la herramienta es a través de su interfaz web.

**1. Iniciar la Aplicación**
```bash
python app.py
```
Abre tu navegador y ve a la dirección que se muestra en la terminal (normalmente `http://127.0.0.1:7860`).

**2. Grabar un Nuevo Flujo**
1.  Ve a la pestaña **Aprender Flujo**.
2.  En **Prompt de la Tarea**, describe la tarea que quieres automatizar. Por ejemplo: `Ve a wikipedia.org, busca 'Inteligencia Artificial' y extrae el primer párrafo.`
3.  En **Nombre del Flujo**, dale un nombre corto y descriptivo (ej: `buscar_wiki_ia`).
4.  Haz clic en **Grabar Flujo**. El sistema abrirá un navegador y realizará la tarea. El estado se mostrará en la interfaz.

**3. Ejecutar un Flujo Grabado**
1.  Ve a la pestaña **Ejecutar Flujo**.
2.  Selecciona el flujo que acabas de crear del menú desplegable.
3.  Si el flujo necesita variables (como un usuario o contraseña), aparecerán campos para que los completes.
4.  Haz clic en **Ejecutar Flujo**. La automatización se ejecutará rápidamente en segundo plano y el resultado aparecerá en pantalla.

## Uso Avanzado (Línea de Comandos)

Para usuarios avanzados o para integrar ARPA en scripts, puedes usar los módulos directamente desde la terminal.

**Grabar un Flujo**
```bash
# Sintaxis: python learn.py "prompt" -o nombre_archivo
python learn.py "Ve a google.com y busca 'Playwright'" -o buscar_google
```

**Ejecutar un Flujo**
```bash
# Sintaxis: python replay.py nombre_archivo
python replay.py buscar_google
```

**Ejecutar un Flujo con Variables**
Si un flujo fue grabado usando placeholders como `{{USER}}` o `{{PASS}}`, puedes proporcionar los valores al ejecutarlo.

```bash
# Las variables se pueden pasar con el flag --override
python replay.py mi_flujo_de_login --override USER=usuario123 PASS=claveSecreta456
```
El sistema también buscará las variables en el archivo `.env` si no se proporcionan directamente.