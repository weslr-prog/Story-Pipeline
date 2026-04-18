import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from gradio_client import Client

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from config import SETTINGS  # noqa: E402


def check_binary(name: str) -> bool:
    return shutil.which(name) is not None


def check_ollama() -> tuple[bool, str]:
    try:
        resp = requests.get(f"{SETTINGS.ollama_url}/api/tags", timeout=4)
        if not resp.ok:
            return False, f"HTTP {resp.status_code}"
        return True, "reachable"
    except Exception as exc:
        return False, str(exc)


def check_chatterbox() -> tuple[bool, str]:
    try:
        resp = requests.get(SETTINGS.chatterbox_url, timeout=4)
        return resp.ok, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


def check_local_disk_kv() -> tuple[bool, str]:
    # LOCAL_DISK_KV_URL is a chat-completions endpoint; probing host root is enough.
    target = SETTINGS.local_disk_kv_url.strip()
    try:
        parts = urlsplit(target)
        if not parts.scheme or not parts.netloc:
            return False, "invalid LOCAL_DISK_KV_URL"
        probe = f"{parts.scheme}://{parts.netloc}/"
        resp = requests.get(probe, timeout=4)
        return True, f"{probe} HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


def probe_local_disk_kv_model() -> tuple[bool, str]:
    # Qwen3-family thinking models spend 5-30 s on reasoning even for tiny prompts.
    # Raise the probe timeout so we don't false-fail on a working server.
    probe_timeout = int(os.getenv("PREFLIGHT_PROBE_TIMEOUT", "120"))
    payload = {
        "model": SETTINGS.local_disk_kv_model,
        "temperature": 0.0,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }
    try:
        resp = requests.post(SETTINGS.local_disk_kv_url, json=payload, timeout=probe_timeout)
        if not resp.ok:
            body = (resp.text or "").strip().replace("\n", " ")
            return False, f"HTTP {resp.status_code} {body[:220]}"
        data = resp.json()
        msg = data.get("choices", [{}])[0].get("message", {})
        content = msg.get("content", "") or msg.get("reasoning", "")
        if isinstance(content, str):
            return True, f"ok ({SETTINGS.local_disk_kv_model})"
        return True, f"ok ({SETTINGS.local_disk_kv_model})"
    except Exception as exc:
        return False, str(exc)


def discover_api_names() -> list[str]:
    client = Client(SETTINGS.chatterbox_url)
    names: list[str] = []

    # gradio_client returns either dict or text depending on version.
    try:
        api_dict = client.view_api(return_format="dict")
        for endpoint in api_dict.get("named_endpoints", {}):
            names.append(endpoint)
    except Exception:
        pass

    if names:
        return sorted(set(names))

    try:
        text = client.view_api()
        for candidate in ("/generate", "/predict", "/infer"):
            if candidate in str(text):
                names.append(candidate)
    except Exception:
        pass

    return sorted(set(names))


def check_ffmpeg() -> tuple[bool, str]:
    if not check_binary("ffmpeg"):
        return False, "not found in PATH"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"], check=True, capture_output=True, text=True
        )
        first_line = proc.stdout.splitlines()[0] if proc.stdout else "ok"
        return True, first_line
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    backend = SETTINGS.llm_backend.strip().lower()
    if backend not in {"openclaw", "local_disk_kv"}:
        backend = "local_disk_kv"
    if SETTINGS.use_local_disk_kv:
        backend = "local_disk_kv"

    report = {
        "python": sys.version.split()[0],
        "llm_backend": backend,
        "llm_num_ctx": SETTINGS.llm_num_ctx,
        "local_disk_kv_model": SETTINGS.local_disk_kv_model,
        "ffmpeg": {},
        "ollama": {},
        "local_disk_kv": {},
        "local_disk_kv_model_probe": {},
        "chatterbox_webui": {},
        "gradio_endpoints": [],
        "next_action": "",
    }

    ffmpeg_ok, ffmpeg_msg = check_ffmpeg()
    report["ffmpeg"] = {"ok": ffmpeg_ok, "detail": ffmpeg_msg}

    ollama_ok, ollama_msg = check_ollama()
    report["ollama"] = {"ok": ollama_ok, "detail": ollama_msg}

    local_kv_ok, local_kv_msg = check_local_disk_kv()
    report["local_disk_kv"] = {"ok": local_kv_ok, "detail": local_kv_msg}
    if backend == "local_disk_kv" and local_kv_ok:
        model_ok, model_msg = probe_local_disk_kv_model()
        report["local_disk_kv_model_probe"] = {"ok": model_ok, "detail": model_msg}
    else:
        report["local_disk_kv_model_probe"] = {"ok": False, "detail": "skipped"}

    chatterbox_ok, chatterbox_msg = check_chatterbox()
    report["chatterbox_webui"] = {"ok": chatterbox_ok, "detail": chatterbox_msg}

    if chatterbox_ok:
        report["gradio_endpoints"] = discover_api_names()

    if not ffmpeg_ok:
        report["next_action"] = "Install ffmpeg before running TTS stitching."
    elif backend == "local_disk_kv" and not local_kv_ok:
        report["next_action"] = (
            "Start your local disk-KV OpenAI-compatible server or fix LOCAL_DISK_KV_URL, then rerun preflight."
        )
    elif backend == "local_disk_kv" and not report["local_disk_kv_model_probe"].get("ok", False):
        report["next_action"] = (
            "Server is reachable but model alias probe failed. Confirm LOCAL_DISK_KV_MODEL matches server alias, then rerun preflight."
        )
    elif not chatterbox_ok:
        report["next_action"] = "Start the Chatterbox Gradio app, then rerun preflight."
    elif backend != "local_disk_kv" and not ollama_ok:
        report["next_action"] = "Start Ollama service before pipeline runs."
    elif not report["gradio_endpoints"]:
        report["next_action"] = (
            "Open browser DevTools Network, trigger generation, and set CHATTERBOX_API in .env."
        )
    else:
        report["next_action"] = (
            "Set CHATTERBOX_API in .env to one discovered endpoint and run smoke tests."
        )

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
