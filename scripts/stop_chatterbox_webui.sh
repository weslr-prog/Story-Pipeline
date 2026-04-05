#!/usr/bin/env bash
set -euo pipefail

# Stops local Chatterbox Gradio app started from this workspace.
PIDS=$(pgrep -f "gradio_tts_app.py" || true)

if [[ -z "$PIDS" ]]; then
  echo "No running gradio_tts_app.py process found."
  exit 0
fi

echo "Stopping Chatterbox WebUI process(es): $PIDS"
kill $PIDS
sleep 1

LEFT=$(pgrep -f "gradio_tts_app.py" || true)
if [[ -n "$LEFT" ]]; then
  echo "Process still running, forcing stop: $LEFT"
  kill -9 $LEFT
fi

echo "Chatterbox WebUI stopped."
