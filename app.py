import gradio as gr
import os
import re
import asyncio
import json
import time
from datetime import datetime
from threading import Thread
from queue import Queue
from fpdf import FPDF
import pandas as pd
from urllib.parse import quote

# Importar las funciones refactorizadas
from learn import run_learn_task
from replay import run_replay_task, collect_needed_vars

# --- Configuración y Lógica de Backend ---

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS_DIR = os.path.join(SCRIPT_DIR, "workflows")
OUTPUTS_DIR = os.path.join(SCRIPT_DIR, "outputs")
os.makedirs(WORKFLOWS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

def actualizar_lista_flujos():
    """Escanea el directorio y devuelve un objeto Dropdown actualizado para la UI."""
    try:
        files = [f for f in os.listdir(WORKFLOWS_DIR) if f.endswith(".meta.json")]
        choices = sorted([f.replace(".meta.json", "") for f in files])
        return gr.update(choices=choices)
    except FileNotFoundError:
        return gr.update(choices=[])

def run_async_in_thread(coro):
    """
    Ejecuta una corutina en un nuevo hilo con su propio bucle de eventos
    y devuelve el resultado.
    """
    result_queue = Queue()
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        result_queue.put(result)
    
    thread = Thread(target=run_loop)
    thread.start()
    thread.join()
    return result_queue.get()

def _display_formatted_output(json_str: str) -> str:
    """
    Toma la salida JSON estandarizada y la formatea para una visualización
    amigable en Markdown.
    """
    try:
        data = json.loads(json_str)
        content = data.get("content", "No content found")
        data_type = data.get("data_type", "text")

        if data_type in ("list", "json"):
            # Para JSON o listas, muestra el contenido en un bloque de código
            return f"```json\n{json.dumps(content, indent=2, ensure_ascii=False)}\n```"
        else:
            # Para texto plano, muestra el contenido directamente
            return str(content)
            
    except (json.JSONDecodeError, TypeError):
        # Si no es un JSON válido o el formato es incorrecto, muestra el texto original
        return f"**Resultado (formato no estándar):**\n\n{json_str}"

def aprender_flujo_wrapper(prompt, nombre_archivo):
    """Wrapper con generador para ejecutar learn.py y mostrar estado."""
    if not prompt or not nombre_archivo:
        yield "Error: El prompt y el nombre de archivo son obligatorios.", "", ""
        return

    base_filename = re.sub(r'[^\w\-. ]', '', nombre_archivo)
    filepath = os.path.join(WORKFLOWS_DIR, base_filename)
    
    yield "Grabando flujo... Esto puede tardar varios minutos.", "Grabando...", ""
    
    coro = run_learn_task(prompt, filepath)
    meta_filepath, result_json = run_async_in_thread(coro)
    
    formatted_output = _display_formatted_output(result_json)
    yield f"Flujo '{base_filename}' grabado.", formatted_output, result_json

def ejecutar_flujo_wrapper(nombre_flujo, placeholders_list, *values):
    """Wrapper con generador para ejecutar replay.py y mostrar estado."""
    if not nombre_flujo:
        yield "Error: Selecciona un flujo.", ""
        return

    overrides = {placeholders_list[i]: values[i] for i in range(len(placeholders_list))}
    
    yield "Ejecutando flujo...", ""
    
    filepath = os.path.join(WORKFLOWS_DIR, nombre_flujo)
    coro = run_replay_task(filepath, overrides)
    result_json = run_async_in_thread(coro)
    
    yield _display_formatted_output(result_json), result_json

def guardar_pdf(json_str: str):
    """
    Toma el resultado JSON, lo formatea como tabla si es aplicable, y lo guarda en un PDF.
    Devuelve la ruta del archivo guardado.
    """
    try:
        data = json.loads(json_str)
        content = data.get("content", "")
        data_type = data.get("data_type", "text")

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        
        pdf.cell(0, 10, txt="Resultado de Extracción - Agentic RPA", ln=True, align='C')
        pdf.ln(5)

        is_list_of_dicts = (
            data_type == "list" and
            content and
            isinstance(content, list) and
            all(isinstance(item, dict) for item in content)
        )

        if is_list_of_dicts:
            df = pd.DataFrame(content)
            
            # Ancho efectivo de la página
            page_width = pdf.w - 2 * pdf.l_margin
            
            # Anchos de columna (se pueden ajustar)
            col_widths = {col: page_width / len(df.columns) for col in df.columns}
            
            # Encabezados
            pdf.set_font("Arial", 'B', 10)
            for col in df.columns:
                pdf.cell(col_widths[col], 10, col.capitalize(), border=1, align='C')
            pdf.ln()

            # Filas de datos
            pdf.set_font("Arial", '', 9)
            for index, row in df.iterrows():
                # Guardar la posición Y inicial para la fila
                y_start = pdf.get_y()
                max_height = 0
                
                # Primero, calcular la altura máxima necesaria para la fila
                cells_data = []
                for col in df.columns:
                    text = str(row[col])
                    # Usamos una copia del PDF para no afectar la posición actual
                    temp_pdf = FPDF()
                    temp_pdf.add_page()
                    temp_pdf.set_font("Arial", '', 9)
                    temp_pdf.multi_cell(col_widths[col] - 2, 5, text) # -2 para padding
                    h = temp_pdf.get_y()
                    cells_data.append({'text': text, 'height': h})
                    if h > max_height:
                        max_height = h

                # Ahora dibujar las celdas con la altura calculada
                x_start = pdf.l_margin
                for i, col in enumerate(df.columns):
                    pdf.set_xy(x_start, y_start)
                    pdf.multi_cell(col_widths[col], max_height, cells_data[i]['text'].encode('latin-1', 'replace').decode('latin-1'), border=1, align='L')
                    x_start += col_widths[col]
                
                pdf.set_y(y_start + max_height)

        elif data_type in ("list", "json"):
            pdf.set_font("Courier", size=10)
            text_to_write = json.dumps(content, indent=2, ensure_ascii=False)
            pdf.multi_cell(0, 5, txt=text_to_write.encode('latin-1', 'replace').decode('latin-1'))
        
        else: # Texto plano
            pdf.set_font("Arial", size=11)
            text_to_write = str(content)
            pdf.multi_cell(0, 7, txt=text_to_write.encode('latin-1', 'replace').decode('latin-1'))

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"resultado_{timestamp}.pdf"
        filepath = os.path.join(OUTPUTS_DIR, filename)
        
        pdf.output(filepath)
        
        # Crear un enlace relativo para Gradio
        # La ruta para el enlace solo debe ser el nombre del archivo,
        # ya que Gradio lo buscará en los `allowed_paths`.
        # Gradio necesita la ruta absoluta para servir el archivo desde `allowed_paths`.
        # Nos aseguramos de que use slashes para compatibilidad con URL.
        # Devolver la ruta absoluta del archivo para el componente gr.File
        return filepath

    except Exception as e:
        # Devolver None en caso de error para que el componente de archivo no se actualice
        print(f"Error al guardar el PDF: {e}")
        return None


# --- Interfaz de Gradio ---

theme = gr.themes.Base(
    primary_hue="lime",
    secondary_hue="neutral",
    neutral_hue="slate",
).set(
    body_background_fill="#1a1a1a",
    body_text_color="#ffffff",
    button_primary_background_fill="#87c540",
    button_primary_text_color="#1a1a1a",
    button_secondary_background_fill="#333333",
    button_secondary_text_color="#ffffff",
    border_color_accent="#444444",
    link_text_color="#646cff",
    link_text_color_hover="#535bf2",
    input_background_fill="#2a2a2a",
    input_border_color="#444444",
    input_placeholder_color="#4a4a4a",
    block_background_fill="#333333",
    block_border_color="#444444",
    block_label_text_color="#ffffff",
    table_border_color="#444444",
)

with gr.Blocks(theme=theme) as demo:
    gr.Markdown("# Agentic RPA (ARPA)")

    # Estados para almacenar el último resultado JSON
    learn_result_state = gr.State("")
    replay_result_state = gr.State("")

    with gr.Tab("Aprender Flujo"):
        gr.Markdown("## Grabar un Nuevo Flujo de Trabajo")
        learn_prompt = gr.Textbox(lines=5, label="Prompt de la Tarea", placeholder="Ej: Ve a wikipedia.org, busca 'Python' y extrae el primer párrafo.")
        learn_filename = gr.Textbox(label="Nombre del Flujo", placeholder="Ej: buscar_en_wikipedia")
        learn_button = gr.Button("Grabar Flujo", variant="primary")
        learn_status = gr.Textbox(label="Estado", interactive=False)
        learn_output = gr.Markdown(label="Texto Extraído")
        with gr.Row(visible=False) as learn_actions_row:
            learn_save_pdf = gr.Button("Guardar PDF")
            learn_pdf_download = gr.File(label="Descargar PDF", visible=False)


    with gr.Tab("Ejecutar Flujo"):
        gr.Markdown("## Ejecutar un Flujo Grabado")
        with gr.Row():
            replay_dropdown = gr.Dropdown(label="Seleccionar Flujo", choices=actualizar_lista_flujos().get("choices", []), scale=3)
            replay_refresh = gr.Button("Refrescar", scale=1)
        
        MAX_VARS = 10
        with gr.Group(visible=False) as replay_vars_group:
            gr.Markdown("### Variables Requeridas")
            replay_placeholders = gr.State([])
            replay_var_inputs = [gr.Textbox(visible=False, label=f"Var {i+1}") for i in range(MAX_VARS)]

        replay_button = gr.Button("Ejecutar Flujo", variant="primary")
        replay_output = gr.Markdown(label="Resultado de la Ejecución")
        with gr.Row(visible=False) as replay_actions_row:
            replay_save_pdf = gr.Button("Guardar PDF")
            replay_pdf_download = gr.File(label="Descargar PDF", visible=False)


    # --- Lógica de la Interfaz ---

    learn_button.click(
        fn=aprender_flujo_wrapper,
        inputs=[learn_prompt, learn_filename],
        outputs=[learn_status, learn_output, learn_result_state]
    ).then(
        lambda: gr.update(visible=True),
        outputs=learn_actions_row
    ).then(
        fn=actualizar_lista_flujos,
        outputs=replay_dropdown
    )
    
    def show_download(filepath):
        """Función auxiliar para actualizar la visibilidad y el valor del componente de archivo."""
        return gr.update(value=filepath, visible=True if filepath else False)

    learn_save_pdf.click(
        fn=guardar_pdf,
        inputs=learn_result_state,
        outputs=learn_pdf_download
    ).then(
        fn=show_download,
        inputs=learn_pdf_download,
        outputs=learn_pdf_download
    )

    replay_refresh.click(fn=actualizar_lista_flujos, outputs=replay_dropdown)

    def update_replay_ui(nombre_flujo):
        if not nombre_flujo:
            updates = [gr.update(visible=False)] * MAX_VARS
            return gr.update(visible=False), [], *updates
        
        filepath = os.path.join(WORKFLOWS_DIR, f"{nombre_flujo}.json")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                steps = json.load(f)
            placeholders = collect_needed_vars(steps)[:MAX_VARS]
            
            updates = []
            for i in range(MAX_VARS):
                if i < len(placeholders):
                    updates.append(gr.update(visible=True, label=placeholders[i], value=""))
                else:
                    updates.append(gr.update(visible=False))
            
            return gr.update(visible=bool(placeholders)), placeholders, *updates
        except FileNotFoundError:
            updates = [gr.update(visible=False)] * MAX_VARS
            return gr.update(visible=False), [], *updates

    replay_dropdown.change(
        fn=update_replay_ui,
        inputs=replay_dropdown,
        outputs=[replay_vars_group, replay_placeholders] + replay_var_inputs
    )

    replay_button.click(
        fn=ejecutar_flujo_wrapper,
        inputs=[replay_dropdown, replay_placeholders] + replay_var_inputs,
        outputs=[replay_output, replay_result_state]
    ).then(
        lambda: gr.update(visible=True),
        outputs=replay_actions_row
    )

    replay_save_pdf.click(
        fn=guardar_pdf,
        inputs=replay_result_state,
        outputs=replay_pdf_download
    ).then(
        fn=show_download,
        inputs=replay_pdf_download,
        outputs=replay_pdf_download
    )

if __name__ == "__main__":
    demo.launch(allowed_paths=[OUTPUTS_DIR])