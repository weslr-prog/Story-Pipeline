# DISK_BACKED_KV_SETUP.md
## Goal
Give the Story_Time pipeline a **disk‑backed KV cache** (TurboQuant) so the model can keep a 256 k‑token context on a 16 GB M1 Air, without any paid API.  
The change is **optional** – you can keep the existing OpenClaw+Gemini setup.  
If something goes wrong, use [OPENCLAW_ROLLBACK.md](OPENCLAW_ROLLBACK.md).

---

## 1️⃣ Prerequisites (run once)

| Tool | Command | Why |
|------|---------|-----|
| Homebrew | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` | Package manager for macOS |
| Xcode CLI | `xcode-select --install` | Needed for C/C++ compilation |
| CMake ≥ 3.14 | `brew install cmake` | Build system for `llama.cpp` |
| Git | `brew install git` | Pull source code |
| Python 3.11 (already in repo) | – | Used by the pipeline |

---

## 2️⃣ Build the TurboQuant‑enabled `llama.cpp` binary

```bash
# ── 2.1 Clone the fork that contains TurboQuant & KV‑disk support ──
git clone https://github.com/TheTom/llama-cpp-turboquant.git ~/src/llama-cpp-turboquant
cd ~/src/llama-cpp-turboquant
git checkout feature/turboquant-kv-cache   # <-- makes sure the KV‑disk code is present

# ── 2.2 Build for Apple‑silicon (Metal) ──
#    The flags enable:
#      • Metal GPU acceleration (fast on M‑series)
#      • KV‑cache dumping to disk (GGML_KV_CACHE_DUMP)
cmake -B build \
  -DGGML_METAL=ON \
  -DGGML_METAL_EMBED_LIBRARY=ON \
  -DGGML_KV_CACHE_DUMP=ON \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build -j$(sysctl -n hw.ncpu)

```

---

## 3️⃣ Configure Story_Time to use local disk‑KV mode

From repo root:

```bash
cd /Users/wes/Desktop/Story_Time
cp .env.example .env
```

Set these values in `.env`:

```dotenv
LLM_BACKEND=local_disk_kv
USE_LOCAL_DISK_KV=0
LOCAL_DISK_KV_URL=http://127.0.0.1:8080/v1/chat/completions
LOCAL_DISK_KV_MODEL=llama3-turbo-disk
```

Notes:

1. `LLM_BACKEND` is now the canonical selector for `pipeline_novel.py`.
2. `USE_LOCAL_DISK_KV=1` still works as a compatibility override.
3. Keep `USE_LOCAL_DISK_KV=0` unless you need compatibility behavior.

---

## 4️⃣ Start your local disk‑KV server

Use your TurboQuant/llama.cpp server startup command with OpenAI-compatible chat endpoint support.

Example shape:

```bash
# Example only: use your actual command/flags for your fork.
~/src/llama-cpp-turboquant/build/bin/llama-server \
  --model /path/to/model.gguf \
  --port 8080 \
  --host 127.0.0.1 \
  --alias llama3-turbo-disk
```

Confirm endpoint is alive:

```bash
curl -sS http://127.0.0.1:8080/ | head
```

---

## 5️⃣ Preflight and pipeline run

Run preflight:

```bash
python scripts/preflight.py
```

Expected when local mode is active:

1. `llm_backend: "local_disk_kv"`
2. `local_disk_kv.ok: true`
3. `ffmpeg.ok: true`
4. `chatterbox_webui.ok: true`

Then run the novel pipeline:

```bash
python pipeline_novel.py
```

Startup log should include:

1. `[INFO] LLM backend: local_disk_kv`
2. `[INFO] Local disk-KV endpoint: ...`
3. `[INFO] Local disk-KV model: ...`

---

## 6️⃣ Current backend selection behavior

`pipeline_novel.py` resolves backend in this order:

1. `LLM_BACKEND` from config/env (`local_disk_kv` or `openclaw`)
2. Compatibility override: `USE_LOCAL_DISK_KV=1` forces local mode
3. Invalid values fallback to `local_disk_kv`

---

## 7️⃣ Keep old path for safety

The OpenClaw path remains supported.

To revert, follow the exact steps in [OPENCLAW_ROLLBACK.md](OPENCLAW_ROLLBACK.md).

For architecture details of the new path, read [LOCAL_DISK_KV_PIPELINE.md](LOCAL_DISK_KV_PIPELINE.md).
