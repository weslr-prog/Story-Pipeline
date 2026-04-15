# Story_Time Pipeline

Local-first story generation and narration pipeline using Ollama + Chatterbox Gradio WebUI.

## Story Studio (Web UI v1)

Run local web UI:

```bash
python app.py
```

Story Studio prefers `http://127.0.0.1:7861`.
If that port is busy, it automatically selects the next open port in `7861-7871` and prints the exact URL at startup.

Optional launch env overrides:

1. `STUDIO_HOST` (default `127.0.0.1`)
2. `STUDIO_PORT` (default `7861`)
3. `STUDIO_PORT_MAX` (default `7871`)
4. `STUDIO_STRICT_PORT=1` to fail instead of fallback

Unified startup (recommended):

```bash
python scripts/start_story_runner.py
```

This launcher starts/checks Ollama, Chatterbox, and Story Studio, opens Story Studio, and shows a live terminal dashboard.

Current v1 includes:

1. Project creation and active-project switching.
2. Upload/edit of Story Engine source documents without manual file paths.
3. One-click template creation for `style_guide.txt` and `consistency_checklist.txt` inside the UI.
4. Readiness checks before conversion/sync (required docs + outputs + voice status).
5. Rule/prompt/hybrid conversion to project-scoped JSON outputs.
6. Voice upload/sync/download workflow with accepted-format labeling (WAV recommended).
7. Output preview and project file download.
8. One-click sync of converted JSON (plus style/consistency files) into root pipeline files for existing CLI runs.
9. Run Dashboard tab for run mode selection (`One Chapter`, `Sequential`, or `Resume`), explicit one-chapter targeting, existing-output handling (`Prompt each time`, `Rebuild`, `Skip`, `Cancel`), live run snapshot, review marker approvals, and narration text editing.

## Full Documentation

Use `USER_GUIDE.md` for complete setup, server requirements, model selection, TTS control explanations, and full operation workflow.

Local disk-backed KV mode docs:

1. `DISK_BACKED_KV_SETUP.md` (setup and run path)
2. `LOCAL_DISK_KV_PIPELINE.md` (how backend selection and wrapper flow work)
3. `OPENCLAW_ROLLBACK.md` (revert steps)

Use `PRE_RUN_CHECKLIST.txt` before long runs to catch common setup misses early.

## What this project includes

- `tts_engine.py`: shared sentence-level TTS with retry, resume, and ffmpeg stitching.
- `pipeline_novel.py`: linear chapter pipeline (Writer -> Editor -> Archivist -> TTS Prep -> narration).
- `pipeline_cyoa.py`: branching CYOA pipeline with per-node narration.
- `scripts/preflight.py`: environment and endpoint checks.

## 1) Environment setup

1. Create env (venv example):

```bash
cd /Users/wes/Desktop/Story_Time
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

2. Copy env template:

```bash
cp .env.example .env
```

3. Put your narrator sample in `voices/` and set `VOICE_SAMPLE` in `.env` to that file path.

4. Choose backend in `.env`:

```dotenv
LLM_BACKEND=local_disk_kv
USE_LOCAL_DISK_KV=0
```

Set `LLM_BACKEND=openclaw` to revert.

## 2) Start dependencies

1. Start Ollama service and ensure model is present:

```bash
ollama pull qwen2.5:7b
```

2. Start Chatterbox Gradio UI (in your chosen chatterbox checkout).

3. Stop Chatterbox when done:

```bash
bash scripts/stop_chatterbox_webui.sh
```

## 3) Preflight and endpoint discovery

Run:

```bash
python scripts/preflight.py
```

- If `gradio_endpoints` is empty, use browser DevTools Network while clicking Generate in Chatterbox, then set `CHATTERBOX_API` in `.env`.

## 4) TTS smoke test

```bash
python tts_engine.py
```

Expected: `audio/smoke_00.wav` is generated.

## 5) Fill story framework docs

Replace placeholders in:

- `story_bible.json`
- `characters.json`
- `chapter_briefs.json`
- `style_guide.txt`
- `consistency_checklist.txt`

If using `Update Story Pipeline.txt`, feed Story Engine Phase outputs into these files:

1. Story Bible -> `story_bible.json`
2. Character roster -> `characters.json`
3. Chapter blueprint -> `chapter_briefs.json`
4. Writing package rules -> `style_guide.txt` and `consistency_checklist.txt`

### Auto-convert Story Engine text docs to JSON

Use the converter when you have these three text files:

1. `Story DNA Summary.txt`
2. `Story Bible.txt`
3. `Chapter Blueprint.txt`

Run deterministic local conversion (no API cost):

```bash
python scripts/convert_story_engine.py \
	--dna "The Gap Protocol/Story DNA Summary.txt" \
	--bible "The Gap Protocol/Story Bible.txt" \
	--blueprint "The Gap Protocol/Chapter Blueprint.txt" \
	--mode rule \
	--out-dir .
```

This writes:

1. `story_bible.json`
2. `characters.json`
3. `chapter_briefs.json`

Other modes:

1. `--mode prompt`: writes `story_engine_conversion_prompt.md` for use with any external LLM.
2. `--mode hybrid`: writes JSON and the prompt file in one run.

## 6) Run novel pipeline

```bash
python pipeline_novel.py
```

Human review gates are enabled by default:

1. `pre_narration` gate: pipeline pauses after chapter text files are written and before TTS narration.
2. `post_chapter` gate: pipeline pauses again after narration and before the next chapter.

When paused, a review packet is written under `reviews/chXX_*_review.md`. Edit JSON/TXT files in your local editor, then approve by creating the marker file shown in the packet (`reviews/chXX_*.approved`) and rerun.

Stable low-load mode is now default for reliability on 16 GB systems:

1. Moderate context (`LLM_NUM_CTX=8192`)
2. Lower word target pressure (`2000-2600` for validation)
3. Bounded expansion/lint repair passes
4. Safer TTS pacing with chapter intro lead-in and paragraph-aware pause control

For first validation, keep `CHAPTER_COUNT` at `2` or `3` in `.env`, then scale up.

Recommended baseline model on 16 GB Apple Silicon is `qwen2.5:7b`.
If you need larger context, use TurboQuant disk-backed KV with `LOCAL_DISK_KV_MODEL` only after validating stability in short runs.

## 7) Run CYOA pipeline

```bash
python pipeline_cyoa.py
```

Default script run renders first 3 nodes for validation.

## Operational notes

- Resume behavior: completed chapter files and segment manifests are reused.
- If Chatterbox queue errors appear, increase `REQUEST_DELAY` in `.env`.
- If sentence endings sound clipped, lower `EXAGGERATION` first, then `TEMPERATURE`, then increase `SILENCE_PAD`.
- Chapter narration now prepends `Chapter N: Title` automatically when `CHAPTER_INTRO_ENABLED=true`.
- Add room to absorb narration with `INTRO_LEAD_IN_SECONDS`, `PAUSE_MULTIPLIER_MID`, and `PAUSE_PARAGRAPH_BONUS`.
- SSML/paralinguistic tags are not supported by this Chatterbox path; tags are stripped before synthesis.
- Backend boundary: novel pipeline supports `local_disk_kv`; current CYOA pipeline runs through Ollama settings.
