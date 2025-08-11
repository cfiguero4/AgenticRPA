# learn.py — Grabar pasos con parámetros correctos para replay.py
import sys, logging
# Fuerza UTF-8 en stdout/stderr
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
import asyncio, json, os
from typing import Any, Dict, List

from dotenv import load_dotenv
from pyobjtojson import obj_to_json
from browser_use import Agent
from browser_use.llm import ChatGoogle

load_dotenv()

# ---------------- utilidades ----------------

RESERVED_KEYS = {
    "name", "action", "action_name", "type", "timestamp", "ts", "id",
    "success", "error", "message", "result", "status", "duration",
}

LIKELY_PARAM_KEYS = {  # por si quieres priorizar estos
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

        # Nombre (de varias claves) o fallback por índice
        name = (
            a.get("name")
            or a.get("action")
            or a.get("action_name")
            or a.get("type")
        )
        if not name and i < len(action_names):
            name = action_names[i]
        name = (name or "unknown")

        # Params: prioriza 'params'/'arguments' si existen
        params = (
            a.get("params")
            or a.get("arguments")
            or a.get("kwargs")
            or {}
        )
        if not isinstance(params, dict):
            params = {}

        # Si está vacío, toma campos útiles del nivel superior
        if not params:
            # primero intenta tomar llaves "reconocibles"
            extracted = {k: v for k, v in a.items() if k in LIKELY_PARAM_KEYS}
            # si aún queda vacío, toma todo menos reservados/metadatos
            if not extracted:
                extracted = {k: v for k, v in a.items() if k not in RESERVED_KEYS}
            params = extracted or {}

        out.append({"name": name, "params": params})
    return out


def replace_env_placeholders(steps: List[Dict[str, Any]], keys=("USER", "PASS", "BASE_URL")):
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

async def main():
    USER = os.getenv("USER", "standard_user")
    PASS = os.getenv("PASS", "secret_sauce")
    BASE_URL = os.getenv("BASE_URL", "https://www.saucedemo.com/")

    task = f"Inicia sesión en {BASE_URL} con usuario={USER} y password={PASS}. Confirma inventario."
    llm = ChatGoogle(model="gemini-2.5-pro", temperature=0.7)

    agent = Agent(task=task, llm=llm)
    history = await agent.run()  # AgentHistoryList

    # 1) Serializa acciones (Pydantic -> dict)
    raw_actions = history.model_actions()
    raw_json = obj_to_json(raw_actions, check_circular=False)

    # 2) Nombres canónicos (por índice)
    action_names = history.action_names()  # ['go_to_url', 'input_text', ...]

    # 3) Normaliza a [{name, params}] con extracción de campos top-level
    steps = normalize_actions(raw_json, action_names)

    # 4) Parametriza con .env -> {{PLACEHOLDER}}
    steps = replace_env_placeholders(steps)

    # 5) Guarda
    with open("steps.json", "w", encoding="utf-8") as f:
        json.dump(steps, f, indent=2, ensure_ascii=False)

    with open("steps.meta.json", "w", encoding="utf-8") as f:
        json.dump(
            {"visited_urls": history.urls(), "action_names": action_names, "raw": raw_json},
            f, indent=2, ensure_ascii=False
        )

    print(f"✅ Grabado {len(steps)} acciones en steps.json")
    print("ℹ️  Metadatos en steps.meta.json (incluye 'raw' para debug)")

if __name__ == "__main__":
    asyncio.run(main())
