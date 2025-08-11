import gradio as gr
import os
import re
import asyncio
import json
import time
from threading import Thread
from queue import Queue

# Importar las funciones refactorizadas
from learn import run_learn_task
from replay import run_replay_task, collect_needed_vars

# --- Configuración y Lógica de Backend ---

# Obtener la ruta absoluta del directorio del script para construir rutas robustas
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS_DIR = os.path.join(SCRIPT_DIR, "workflows")
os.makedirs(WORKFLOWS_DIR, exist_ok=True)

def actualizar_lista_flujos():
    """Escanea el directorio y devuelve un objeto Dropdown actualizado para la UI."""
    try:
        files = [f for f in os.listdir(WORKFLOWS_DIR) if f.endswith(".meta.json")]
        choices = sorted([os.path.splitext(f)[0] for f in files])
        return gr.update(choices=choices)
    except FileNotFoundError:
        return gr.update(choices=[])

def run_async_in_thread(coro, queue):
    """Ejecuta un coroutine en un hilo y pone el resultado en una cola."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(coro)
    queue.put(result)

def aprender_flujo_wrapper(prompt, nombre_archivo):
    """Wrapper con generador para ejecutar learn.py y mostrar estado."""
    if not prompt or not nombre_archivo:
        yield "Error: El prompt y el nombre de archivo son obligatorios.", ""
        return

    base_filename = re.sub(r'[^\w\-. ]', '', nombre_archivo)
    filepath = os.path.join(WORKFLOWS_DIR, base_filename)
    
    yield "Grabando flujo... Esto puede tardar unos minutos.", ""
    
    queue = Queue()
    coro = run_learn_task(prompt, filepath)
    thread = Thread(target=run_async_in_thread, args=(coro, queue))
    thread.start()

    while thread.is_alive():
        yield "Grabando flujo... (en progreso)", ""
        time.sleep(1)
    
    thread.join()
    meta_filepath, result_text = queue.get()
    
    yield f"Flujo '{base_filename}' grabado.", result_text

def ejecutar_flujo_wrapper(nombre_flujo, placeholders_list, *values):
    """Wrapper con generador para ejecutar replay.py y mostrar estado."""
    if not nombre_flujo:
        yield "Error: Selecciona un flujo."
        return

    overrides = {placeholders_list[i]: values[i] for i in range(len(placeholders_list))}
    
    yield "Ejecutando flujo..."
    
    queue = Queue()
    # CORRECCIÓN: Asegurarse de que la ruta completa se pasa a la función de replay
    filepath = os.path.join(WORKFLOWS_DIR, nombre_flujo)
    coro = run_replay_task(filepath, overrides)
    thread = Thread(target=run_async_in_thread, args=(coro, queue))
    thread.start()

    while thread.is_alive():
        yield "Ejecutando flujo... (en progreso)"
        time.sleep(1)
        
    thread.join()
    result_text = queue.get()
    yield result_text

# --- Interfaz de Gradio ---

with gr.Blocks(theme=gr.themes.Soft()) as demo:
    gr.Markdown("# Panel de Control de Automatización")

    with gr.Tab("Aprender Flujo"):
        gr.Markdown("## Grabar un Nuevo Flujo de Trabajo")
        learn_prompt = gr.Textbox(lines=5, label="Prompt de la Tarea", placeholder="Ej: Ve a wikipedia.org, busca 'Python' y extrae el primer párrafo.")
        learn_filename = gr.Textbox(label="Nombre del Flujo", placeholder="Ej: buscar_en_wikipedia")
        learn_button = gr.Button("Grabar Flujo", variant="primary")
        learn_status = gr.Textbox(label="Estado", interactive=False)
        learn_output = gr.Textbox(label="Texto Extraído", lines=5, interactive=False)

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
        replay_output = gr.Textbox(label="Resultado de la Ejecución", lines=10, interactive=False)

    # --- Lógica de la Interfaz ---

    learn_button.click(
        fn=aprender_flujo_wrapper,
        inputs=[learn_prompt, learn_filename],
        outputs=[learn_status, learn_output]
    ).then(
        fn=actualizar_lista_flujos,
        outputs=replay_dropdown
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
        outputs=replay_output
    )

if __name__ == "__main__":
    demo.launch()