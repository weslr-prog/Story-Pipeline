# LOCAL_DISK_KV_PIPELINE.md

## Purpose
This document describes how the current novel pipeline uses a local disk-backed KV LLM server while preserving OpenClaw as a fallback backend.

## Scope
Applies to:

1. `pipeline_novel.py`
2. `local_llm.py`
3. `config.py`
4. `scripts/preflight.py`

Does not change `pipeline_cyoa.py` behavior.

## Backend selection
`pipeline_novel.py` selects backend with this precedence:

1. `LLM_BACKEND` (`local_disk_kv` or `openclaw`)
2. Compatibility override: `USE_LOCAL_DISK_KV=1` forces local mode
3. Invalid values fallback to `local_disk_kv`

## Required environment values
For local mode:

```dotenv
LLM_BACKEND=local_disk_kv
USE_LOCAL_DISK_KV=0
LOCAL_DISK_KV_URL=http://127.0.0.1:8080/v1/chat/completions
LOCAL_DISK_KV_MODEL=llama3-turbo-disk
```

For OpenClaw fallback:

```dotenv
LLM_BACKEND=openclaw
USE_LOCAL_DISK_KV=0
```

## How `pipeline_novel.py` creates clients

1. `_resolved_backend()` determines active backend.
2. `_client_factory_for_backend()` lazily imports the right factory:
- local mode: `local_llm.get_llm_client`
- openclaw mode: `openclaw.OpenClawClient`
3. `_llm(...)` configures the returned client:
- `set_role(phase)`
- `set_temperature(temp)`
- `set_max_output_tokens(max_tokens)`
- optional `apply_preset("compact_context")`

## Local wrapper contract
`local_llm.py` provides `LocalLLMClient` with methods used by the pipeline:

1. `set_role`
2. `set_temperature`
3. `set_max_output_tokens`
4. `apply_preset`
5. `invoke`

`invoke` sends OpenAI-compatible JSON to `LOCAL_DISK_KV_URL` and maps response text to `.content` for compatibility.

## Preflight expectations
Run:

```bash
python scripts/preflight.py
```

When local mode is active, you should see:

1. `llm_backend: local_disk_kv`
2. `local_disk_kv.ok: true`
3. `ffmpeg.ok: true`
4. `chatterbox_webui.ok: true`

## Runtime behavior in local mode

1. Startup prints backend, endpoint, and model.
2. Chapter generation flow remains unchanged (scene plan, scene generation, lint, summary, tts prep, narration).
3. Only the LLM transport layer changes.

## Failure modes and quick fixes

1. `local_disk_kv.ok: false`
- Start the server.
- Verify `LOCAL_DISK_KV_URL` is reachable.

2. Runtime says OpenClaw package missing
- Set `LLM_BACKEND=local_disk_kv`, or install OpenClaw for fallback mode.

3. Slow/timeout response
- Check server load and model size.
- Keep chapter count low during validation (`CHAPTER_COUNT=1` or `2`).

## Rollback
Use `OPENCLAW_ROLLBACK.md` for exact revert steps.
