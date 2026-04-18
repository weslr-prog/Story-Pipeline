# OPENCLAW_ROLLBACK.md

## Purpose
Restore the novel pipeline to OpenClaw-backed operation if local disk-KV mode is not ready.

## Fast rollback (no code changes)

1. Update `.env`:

```dotenv
LLM_BACKEND=openclaw
USE_LOCAL_DISK_KV=0
```

2. Keep or set other model settings as needed for your OpenClaw/OpenClaw-provider flow.

3. Run preflight:

```bash
python scripts/preflight.py
```

4. Run pipeline:

```bash
python pipeline_novel.py
```

## Dependency rollback checklist
If OpenClaw import fails, install dependencies in your active environment:

```bash
cd /Users/wes/Desktop/Story_Time
source .venv/bin/activate
pip install -r requirements.txt
```

If the environment is stale or mixed, rebuild it:

```bash
cd /Users/wes/Desktop/Story_Time
mv .venv .venv_backup_$(date +%Y%m%d_%H%M%S)
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Optional cleanup of local disk-KV settings
These can remain in `.env`, but you can clear them to reduce confusion:

```dotenv
# LOCAL_DISK_KV_URL=
# LOCAL_DISK_KV_MODEL=
```

## Verify rollback succeeded

1. Pipeline startup shows:
- `[INFO] LLM backend: openclaw`

2. No `local_disk_kv` blocker is reported in preflight next actions for your active backend.

3. Chapter generation proceeds with OpenClaw transport.

## If you want to switch back later
Use `DISK_BACKED_KV_SETUP.md` and set:

```dotenv
LLM_BACKEND=local_disk_kv
USE_LOCAL_DISK_KV=0
```
