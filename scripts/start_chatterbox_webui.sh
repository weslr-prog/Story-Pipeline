#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/chatterbox"

echo "Starting Chatterbox Gradio TTS app on default port..."
export GRADIO_SERVER_PORT=7865
exec /opt/homebrew/bin/python3.11 gradio_tts_app.py
