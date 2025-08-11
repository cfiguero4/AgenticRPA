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
    """
    Usa la info de 'interacted_element' del steps.json para sacar un selector robusto:
    1) id -> #id
    2) data-test -> [data-test="..."]
    3) name -> [name="..."]
    4) css_selector (si viene completo)
    5) xpath -> 'xpath=//...'
    """
    if not meta:
        return None
    attrs = meta.get("attributes") or {}
    el_id = attrs.get("id")
    if el_id:
        return f"#{el_id}"
    data_test = attrs.get("data-test")
    if data_test:
        return f'[data-test="{data_test}"]'
    name = attrs.get("name")
    if name:
        return f'[name="{name}"]'
    css_sel = meta.get("css_selector")
    if css_sel:
        return css_sel
    xpath = meta.get("xpath")
    if xpath:
        # Playwright acepta prefijo xpath=
        return f"xpath=/{xpath}" if not xpath.startswith("/") else f"xpath={xpath}"
    return None

def flatten_nested_params_for_native(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Para acciones nativas como go_to_url, si vienen anidadas bajo 'go_to_url': {...}
    devuelve {'url':..., 'new_tab':...}
    """
    if not isinstance(params, dict):
        return {}
    nested = params.get(name)
    if isinstance(nested, dict):
        base = {k: v for k, v in nested.items() if v is not None}
    else:
        base = {}
    # Copia claves útiles si están al tope
    for k in ("url", "new_tab", "delay", "timeout"):
        if k in params and params[k] is not None and k not in base:
            base[k] = params[k]
    return base

# -------------------- acciones custom (selector-based) --------------------
controller = Controller()

@controller.action("det_fill_by_selector")
async def det_fill_by_selector(selector: str, text: str, page: Page) -> ActionResult:
    # Espera visibilidad por si la página tarda
    await page.locator(selector).wait_for(state="visible", timeout=15000)
    await page.locator(selector).fill(text)
    return ActionResult(extracted_content=f"Filled {selector}")

@controller.action("det_click_by_selector")
async def det_click_by_selector(selector: str, page: Page) -> ActionResult:
    await page.locator(selector).wait_for(state="visible", timeout=15000)
    await page.locator(selector).click()
    return ActionResult(extracted_content=f"Clicked {selector}")

# -------------------- main --------------------
async def main():
    parser = argparse.ArgumentParser(description="Reproduce steps.json usando selectores estables.")
    parser.add_argument("-o", "--override", nargs="*", help="Sobrescribir variables: -o USER=foo PASS=bar BASE_URL=https://...")
    args = parser.parse_args()

    steps = json.load(open("steps.json", "r", encoding="utf-8"))

    # Variables desde .env y/o CLI
    load_dotenv()
    cli = {}
    if args.override:
        for kv in args.override:
            if "=" in kv:
                k, v = kv.split("=", 1)
                cli[k] = v

    needed = collect_needed_vars(steps)
    variables: Dict[str, str] = {}
    for var in needed:
        variables[var] = cli.get(var) or os.getenv(var) or input(f"Ingrese valor para {var}: ")

    # Inicializa Agent con nuestro controller (no usamos el LLM para decidir acciones)
    llm = ChatGoogle(model="gemini-2.5-pro", temperature=0)
    agent = Agent(task="(replay deterministic via selectors)", llm=llm, controller=controller)

    try:
        # Inicializa browser_session rápido (sin ejecutar pasos del modelo)
        await agent.run(max_steps=0)

        # Recorre pasos
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict) or "name" not in step:
                raise ValueError(f"Paso {i} inválido: {step}")

            name = step["name"]
            raw_params = step.get("params", {}) or {}

            if name == "done":
                print(f"⏭️  [{i}/{len(steps)}] omitido 'done'")
                continue

            # Caso 1: go_to_url (nativo, pero con params planos)
            if name == "go_to_url":
                params = flatten_nested_params_for_native(name, raw_params)
                params = replace_vars(params, variables)
                print(f"▶️  [{i}/{len(steps)}] go_to_url({params})")
                await agent.controller.registry.execute_action(
                    "go_to_url", params, browser_session=agent.browser_session
                )
                continue

            # Caso 2: input_text -> det_fill_by_selector
            if name == "input_text":
                inner = raw_params.get("input_text") or {}
                text = inner.get("text") or raw_params.get("text")  # por si viene al tope
                interacted = raw_params.get("interacted_element") or {}
                selector = derive_selector_from_meta(interacted)

                if not selector:
                    # fallback: intenta por índice, no recomendado
                    idx = inner.get("index") or raw_params.get("index")
                    if idx is not None:
                        print(f"⚠️  [{i}/{len(steps)}] sin selector; intento índice input_text(index={idx})")
                        await agent.controller.registry.execute_action(
                            "input_text",
                            {"index": idx, "text": replace_vars(text, variables)},
                            browser_session=agent.browser_session
                        )
                        continue
                    else:
                        raise RuntimeError(f"Paso {i}: no hay selector ni índice para input_text")

                params = {"selector": replace_vars(selector, variables), "text": replace_vars(text, variables)}
                print(f"▶️  [{i}/{len(steps)}] det_fill_by_selector({params})")
                await agent.controller.registry.execute_action(
                    "det_fill_by_selector", params, browser_session=agent.browser_session
                )
                continue

            # Caso 3: click_element_by_index -> det_click_by_selector
            if name == "click_element_by_index":
                inner = raw_params.get("click_element_by_index") or {}
                interacted = raw_params.get("interacted_element") or {}
                selector = derive_selector_from_meta(interacted)

                if not selector:
                    idx = inner.get("index") or raw_params.get("index")
                    if idx is not None:
                        print(f"⚠️  [{i}/{len(steps)}] sin selector; intento índice click_element_by_index(index={idx})")
                        await agent.controller.registry.execute_action(
                            "click_element_by_index",
                            {"index": idx},
                            browser_session=agent.browser_session
                        )
                        continue
                    else:
                        raise RuntimeError(f"Paso {i}: no hay selector ni índice para click")

                params = {"selector": replace_vars(selector, variables)}
                print(f"▶️  [{i}/{len(steps)}] det_click_by_selector({params})")
                await agent.controller.registry.execute_action(
                    "det_click_by_selector", params, browser_session=agent.browser_session
                )
                continue

            # Si aparece otra acción, intenta replay nativo con params tal cual
            print(f"ℹ️  [{i}/{len(steps)}] acción no mapeada '{name}', intento directo")
            # Aplanar los parámetros para acciones nativas que vienen anidadas
            params = flatten_nested_params_for_native(name, raw_params)
            await agent.controller.registry.execute_action(
                name, replace_vars(params, variables), browser_session=agent.browser_session
            )

        print("✅ Replay completado (selectores).")

    finally:
        # Cierre limpio del navegador/session incluso si algo falla
        try:
            if getattr(agent, "browser_session", None):
                if hasattr(agent.browser_session, "stop"):
                    await agent.browser_session.stop()  # API 0.5.x
                else:
                    ctx = getattr(agent.browser_session, "context", None) or getattr(agent.browser_session, "playwright_context", None)
                    if ctx:
                        await ctx.close()
                    pw_browser = getattr(agent.browser_session, "playwright_browser", None) or getattr(agent.browser_session, "browser", None)
                    if pw_browser:
                        await pw_browser.close()
        except Exception as e:
            print(f"⚠️ No se pudo cerrar el navegador limpiamente: {e}")

if __name__ == "__main__":
    asyncio.run(main())
