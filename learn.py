# learn.py — Grabar pasos de una tarea de automatización web de forma genérica.
import sys
import asyncio
import json
import os
import argparse
from typing import Any, Dict, List

from dotenv import load_dotenv
from pyobjtojson import obj_to_json
from browser_use import Agent
from browser_use.llm import ChatGoogle

# Fuerza UTF-8 en stdout/stderr para compatibilidad
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

# ---------------- utilidades ----------------

RESERVED_KEYS = {
    "name", "action", "action_name", "type", "timestamp", "ts", "id",
    "success", "error", "message", "result", "status", "duration",
}

LIKELY_PARAM_KEYS = {
    "url", "index", "text", "value", "selector", "role", "name",
    "x", "y", "keys", "delay", "timeout",
}

def normalize_actions(raw_json: List[Dict[str, Any]], action_names: List[str]) -> List[Dict[str, Any]]:
    """
    De lo que entrega obj_to_json(model_actions()) + action_names(),
    produce [{name, params}] asegurando que los parámetros se rellenen
    con las claves útiles del dict en el nivel superior si no vienen en 'params'.
    """
    out: List[Dict[str, Any]] = []
    for i, a in enumerate(raw_json):
        if not isinstance(a, dict):
            out.append({"name": "unknown", "params": {}})
            continue

        name = (
            a.get("name")
            or a.get("action")
            or a.get("action_name")
            or a.get("type")
        )
        if not name and i < len(action_names):
            name = action_names[i]
        name = (name or "unknown")

        params = (
            a.get("params")
            or a.get("arguments")
            or a.get("kwargs")
            or {}
        )
        if not isinstance(params, dict):
            params = {}

        if not params:
            extracted = {k: v for k, v in a.items() if k in LIKELY_PARAM_KEYS}
            if not extracted:
                extracted = {k: v for k, v in a.items() if k not in RESERVED_KEYS}
            params = extracted or {}

        out.append({"name": name, "params": params})
    return out

def replace_env_placeholders(steps: List[Dict[str, Any]], keys: List[str]) -> List[Dict[str, Any]]:
    """
    Reemplaza valores exactos de .env por {{PLACEHOLDER}} en params (strings).
    """
    env_map = {k: os.getenv(k) for k in keys if os.getenv(k)}

    def repl(v: Any):
        if isinstance(v, str):
            for k, val in env_map.items():
                if val and val in v:
                    v = v.replace(val, f"{{{{{k}}}}}")
            return v
        if isinstance(v, dict):
            return {kk: repl(vv) for kk, vv in v.items()}
        if isinstance(v, list):
            return [repl(x) for x in v]
        return v

    for s in steps:
        s["params"] = repl(s.get("params", {}))
    return steps

# ---------------- main ----------------

async def main(args: argparse.Namespace):
    """
    Función principal que ejecuta el agente y guarda los resultados.
    """
    print(f"▶️  Iniciando tarea: {args.prompt}")
    print(f"▶️  Modelo: {args.model}, Temperatura: {args.temperature}")

    # 1. Configurar y ejecutar el agente
    llm = ChatGoogle(model=args.model, temperature=args.temperature)
    agent = Agent(task=args.prompt, llm=llm)
    history = await agent.run()

    # 2. Procesar el historial de acciones
    raw_actions = history.model_actions()
    raw_json = obj_to_json(raw_actions, check_circular=False)
    action_names = history.action_names()
    steps = normalize_actions(raw_json, action_names)
    
    # 3. Reemplazar secretos con placeholders
    if args.env_keys:
        print(f"▶️  Reemplazando placeholders para: {args.env_keys}")
        steps = replace_env_placeholders(steps, keys=args.env_keys)

    # 4. Guardar los artefactos de salida
    output_file = f"{args.output_file}.json"
    meta_file = f"{args.output_file}.meta.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(steps, f, indent=2, ensure_ascii=False)

    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "prompt": args.prompt,
                "model": args.model,
                "temperature": args.temperature,
                "visited_urls": history.urls(),
                "action_names": action_names,
                "raw": raw_json,
            },
            f, indent=2, ensure_ascii=False
        )

    print(f"✅ Grabado {len(steps)} acciones en {output_file}")
    print(f"ℹ️  Metadatos en {meta_file}")

if __name__ == "__main__":
    # --- Configuración de Argumentos de Línea de Comandos ---
    parser = argparse.ArgumentParser(
        description="Graba una sesión de automatización web a partir de un prompt.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Ejemplos de uso:
-----------------
1. Tarea simple (salida por defecto 'steps.json'):
   python learn.py "Ve a google.com y busca 'Python argparse'"

2. Tarea con nombre de archivo de salida personalizado:
   python learn.py "Ve a wikipedia.org y busca 'Refactorización'" -o wiki_refactor

3. Tarea que usa variables de entorno y las reemplaza por placeholders:
   # (Asegúrate de que USER y PASS estén en tu .env)
   python learn.py "Inicia sesión en mi-sitio.com con usuario {{USER}} y clave {{PASS}}" \\
   -o login_test --env-keys USER PASS

4. Usando un modelo y temperatura diferentes:
   python learn.py "Escribe un poema sobre código" --model gemini-1.5-pro --temperature 0.9
"""
    )

    # Argumentos para controlar la ejecución
    parser.add_argument(
        "prompt",
        help="La tarea o prompt a ejecutar por el agente."
    )
    parser.add_argument(
        "-o", "--output-file",
        default="steps",
        help="Nombre base para los archivos de salida (sin extensión). Por defecto: 'steps'."
    )
    parser.add_argument(
        "--env-keys",
        nargs='*',
        default=["USER", "PASS", "BASE_URL"],
        help="Lista de claves de .env a reemplazar por placeholders. Por defecto: USER PASS BASE_URL."
    )

    # Argumentos para controlar el modelo LLM
    parser.add_argument(
        "--model",
        default="gemini-2.5-pro",
        help="El modelo de LLM a utilizar. Por defecto: 'gemini-2.5-pro'."
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="La temperatura para la generación del LLM. Por defecto: 0.7."
    )

    args = parser.parse_args()
    asyncio.run(main(args))
