import json
import shutil
import subprocess
import sys
from pathlib import Path

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
    report = {
        "python": sys.version.split()[0],
        "ffmpeg": {},
        "ollama": {},
        "chatterbox_webui": {},
        "gradio_endpoints": [],
        "next_action": "",
    }

    ffmpeg_ok, ffmpeg_msg = check_ffmpeg()
    report["ffmpeg"] = {"ok": ffmpeg_ok, "detail": ffmpeg_msg}

    ollama_ok, ollama_msg = check_ollama()
    report["ollama"] = {"ok": ollama_ok, "detail": ollama_msg}

    chatterbox_ok, chatterbox_msg = check_chatterbox()
    report["chatterbox_webui"] = {"ok": chatterbox_ok, "detail": chatterbox_msg}

    if chatterbox_ok:
        report["gradio_endpoints"] = discover_api_names()

    if not ffmpeg_ok:
        report["next_action"] = "Install ffmpeg before running TTS stitching."
    elif not chatterbox_ok:
        report["next_action"] = "Start the Chatterbox Gradio app, then rerun preflight."
    elif not ollama_ok:
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
