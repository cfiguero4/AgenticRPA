# replay.py — Replay determinista usando selectores (acciones custom) en Browser-Use
import asyncio, json, os, re, argparse
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from browser_use import Agent, Controller, ActionResult
from browser_use.llm import ChatGoogle
from browser_use.browser.types import Page  # solo para type hints

# -------------------- helpers de variables --------------------
def collect_needed_vars(obj):
    needed = set()
    def scan(v):
        if isinstance(v, str):
            needed.update(re.findall(r"\{\{(.*?)\}\}", v))
        elif isinstance(v, dict):
            for vv in v.values(): scan(vv)
        elif isinstance(v, list):
            for vv in v: scan(vv)
    scan(obj)
    return sorted(needed)

def replace_vars(obj, variables: Dict[str, str]):
    if isinstance(obj, str):
        for k, v in variables.items():
            obj = obj.replace(f"{{{{{k}}}}}", v)
        return obj
    if isinstance(obj, list):
        return [replace_vars(x, variables) for x in obj]
    if isinstance(obj, dict):
        return {k: replace_vars(v, variables) for k, v in obj.items()}
    return obj

# -------------------- derivación de selectores --------------------
def derive_selector_from_meta(meta: Dict[str, Any]) -> Optional[str]:
    if not meta: return None
    attrs = meta.get("attributes") or {}
    if el_id := attrs.get("id"): return f"#{el_id}"
    if data_test := attrs.get("data-test"): return f'[data-test="{data_test}"]'
    if name := attrs.get("name"): return f'[name="{name}"]'
    if css_sel := meta.get("css_selector"): return css_sel
    if xpath := meta.get("xpath"):
        return f"xpath=/{xpath}" if not xpath.startswith("/") else f"xpath={xpath}"
    return None

def flatten_nested_params_for_native(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(params, dict): return {}
    nested = params.get(name)
    base = {k: v for k, v in nested.items() if v is not None} if isinstance(nested, dict) else {}
    for k in ("url", "new_tab", "delay", "timeout"):
        if k in params and params[k] is not None and k not in base:
            base[k] = params[k]
    return base

# -------------------- acciones custom (selector-based) --------------------
controller = Controller()

@controller.action("det_fill_by_selector")
async def det_fill_by_selector(selector: str, text: str, page: Page) -> ActionResult:
    await page.locator(selector).wait_for(state="visible", timeout=15000)
    await page.locator(selector).fill(text)
    return ActionResult(extracted_content=f"Filled {selector}")

@controller.action("det_click_by_selector")
async def det_click_by_selector(selector: str, page: Page) -> ActionResult:
    await page.locator(selector).wait_for(state="visible", timeout=15000)
    await page.locator(selector).click()
    return ActionResult(extracted_content=f"Clicked {selector}")

@controller.action("det_search_and_submit")
async def det_search_and_submit(selector: str, text: str, page: Page) -> ActionResult:
    """Rellena un campo, presiona Enter y espera a que la página cargue."""
    await page.locator(selector).wait_for(state="visible", timeout=15000)
    await page.locator(selector).fill(text)
    await page.press(selector, "Enter")
    await page.wait_for_load_state("domcontentloaded", timeout=15000)
    return ActionResult(extracted_content=f"Searched for '{text}' in {selector}")

# -------------------- main --------------------
async def main(cli_args=None, return_result=False):
    if cli_args:
        args = cli_args
    else:
        parser = argparse.ArgumentParser(description="Reproduce steps.json usando selectores estables.")
        parser.add_argument("filename", nargs="?", default="steps", help="Nombre base de los archivos a reproducir (ej: 'login').")
        parser.add_argument("-o", "--override", nargs="*", help="Sobrescribir variables: -o USER=foo PASS=bar")
        args = parser.parse_args()

    input_file = f"{args.filename}.json"
    meta_file = f"{args.filename}.meta.json"

    steps = json.load(open(input_file, "r", encoding="utf-8"))
    try:
        meta = json.load(open(meta_file, "r", encoding="utf-8"))
        task = meta.get("task", "Replay task from meta file")
    except FileNotFoundError:
        task = "Replay task from file"

    load_dotenv()
    cli_vars = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in args.override or [] if "=" in kv}
    needed = collect_needed_vars(steps)
    variables = {var: cli_vars.get(var) or os.getenv(var) or input(f"Ingrese valor para {var}: ") for var in needed}

    llm = ChatGoogle(model="gemini-2.5-pro", temperature=0)
    agent = Agent(task=task, llm=llm, controller=controller)

    try:
        # En modo replay, no ejecutamos el agente con LLM. Solo iniciamos el navegador.
        # La llamada agent.run(max_steps=0) ya no funciona como antes en nuevas versiones.
        await agent.browser_session.start()
        print("ℹ️  Sesión de navegador iniciada para replay.")
        
        step_iterator = iter(enumerate(steps, 1))
        for i, step in step_iterator:
            name = step.get("name")
            if not name:
                print(f"⏭️  [{i}/{len(steps)}] Paso inválido, omitiendo.")
                continue

            raw_params = step.get("params", {}) or {}
            
            # --- Lógica de Replay Inteligente ---

            # Patrón de Búsqueda: input_text seguido de clics
            if name == "input_text":
                interacted = raw_params.get("interacted_element") or {}
                selector = derive_selector_from_meta(interacted)
                attrs = interacted.get("attributes", {})
                is_search = "search" in selector.lower() or attrs.get("type") == "search" or "search" in (attrs.get("name") or "")

                if is_search and selector:
                    text = raw_params.get("input_text", {}).get("text") or raw_params.get("text")
                    print(f"▶️  [{i}/{len(steps)}] Patrón de búsqueda detectado. Ejecutando: det_search_and_submit")
                    await agent.controller.registry.execute_action(
                        "det_search_and_submit",
                        {"selector": selector, "text": replace_vars(text, variables)},
                        browser_session=agent.browser_session
                    )
                    # Omitir los siguientes 2 pasos (clics en botón y sugerencia)
                    print(f"⏭️  Omitiendo los siguientes 2 pasos de clic redundantes.")
                    next(step_iterator, None); next(step_iterator, None)
                    continue

            # --- Manejo de Pasos Individuales ---

            if name == "done":
                final_text = (raw_params.get("done", {}).get("text", ""))
                if final_text:
                    print("\n✅ Tarea completada. Resultado final:")
                    print("--------------------------------------------------")
                    print(final_text)
                    print("--------------------------------------------------")
                    if return_result:
                        return final_text
                else:
                    print(f"⏭️  [{i}/{len(steps)}] 'done'")
                continue

            if name in ("input_text", "click_element_by_index"):
                action_map = {"input_text": "det_fill_by_selector", "click_element_by_index": "det_click_by_selector"}
                interacted = raw_params.get("interacted_element") or {}
                selector = derive_selector_from_meta(interacted)
                if not selector:
                    print(f"❌  [{i}/{len(steps)}] No se pudo derivar un selector para {name}. Omitiendo.")
                    continue
                
                params = {"selector": selector}
                if name == "input_text":
                    params["text"] = raw_params.get("input_text", {}).get("text") or raw_params.get("text")

                print(f"▶️  [{i}/{len(steps)}] {action_map[name]}({params})")
                await agent.controller.registry.execute_action(
                    action_map[name], replace_vars(params, variables), browser_session=agent.browser_session
                )
                continue

            # --- Fallback para otras acciones ---

            # Omitir acciones que modifican archivos o dependen de un LLM en modo replay
            if name in ("extract_structured_data", "write_file", "replace_file_str"):
                print(f"⏭️  [{i}/{len(steps)}] Omitiendo acción no interactiva: {name}")
                continue
            
            params = flatten_nested_params_for_native(name, raw_params)
            print(f"▶️  [{i}/{len(steps)}] {name}({params})")
            try:
                await agent.controller.registry.execute_action(
                    name, replace_vars(params, variables), browser_session=agent.browser_session
                )
            except Exception as e:
                print(f"❌  [{i}/{len(steps)}] Falló el intento directo para '{name}': {e}")

        print("\n✅ Replay completado.")

    finally:
        # Cierre limpio del navegador
        try:
            if getattr(agent, "browser_session", None) and hasattr(agent.browser_session, "stop"):
                await agent.browser_session.stop()
        except Exception as e:
            print(f"⚠️ No se pudo cerrar el navegador limpiamente: {e}")
    
    if return_result:
        return "Replay completado, pero no se encontró texto extraído en la acción 'done'."

async def run_replay_task(filename: str, overrides: dict = None):
    """
    Función programática para ejecutar una tarea de replay.
    Devuelve el resultado final extraído.
    """
    # Simula el objeto de argumentos de argparse
    override_list = [f"{k}={v}" for k, v in (overrides or {}).items()]
    
    # El llamador (app.py) ya proporciona la ruta base correcta sin extensión.
    # No es necesario procesar `filename` aquí.
    args = argparse.Namespace(filename=filename, override=override_list)
    
    # Llama a la lógica principal y captura el resultado
    final_text = await main(args, return_result=True)
    return final_text

if __name__ == "__main__":
    asyncio.run(main())
