#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing Python runtime at $VENV_PY"
  echo "Create/activate the project venv first."
  exit 1
fi

MODEL="${MLX_MODEL:-${LOCAL_DISK_KV_MODEL:-caiovicentino1/Qwen3.5-9B-HLWQ-MLX-4bit}}"
HOST="${MLX_HOST:-127.0.0.1}"
PORT="${MLX_PORT:-8080}"
MAX_TOKENS="${MLX_MAX_TOKENS:-4096}"
TEMP="${MLX_TEMP:-0.7}"
TOP_P="${MLX_TOP_P:-0.9}"
# Set MLX_DISABLE_THINKING=1 to pass --chat-template-args '{"enable_thinking":false}'
# Enabled by default for Qwen3-family models to avoid thinking tokens eating the budget.
DISABLE_THINKING="${MLX_DISABLE_THINKING:-1}"

echo "Starting MLX server"
echo "  model:           $MODEL"
echo "  host:            $HOST"
echo "  port:            $PORT"
echo "  disable_thinking: $DISABLE_THINKING"

EXTRA_ARGS=()
if [[ "$DISABLE_THINKING" == "1" ]]; then
  EXTRA_ARGS+=(--chat-template-args '{"enable_thinking":false}')
fi

env PYTHONPATH="$ROOT_DIR" \
  "$VENV_PY" -m mlx_lm server \
  --model "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --trust-remote-code \
  --temp "$TEMP" \
  --top-p "$TOP_P" \
  --max-tokens "$MAX_TOKENS" \
  "${EXTRA_ARGS[@]-}"
