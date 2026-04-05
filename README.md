# Story_Time Pipeline

Local-first story generation and narration pipeline using Ollama + Chatterbox Gradio WebUI.

## Full Documentation

Use `USER_GUIDE.md` for complete setup, server requirements, model selection, TTS control explanations, and full operation workflow.

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

## 6) Run novel pipeline

```bash
python pipeline_novel.py
```

For first validation, keep `CHAPTER_COUNT` at `2` or `3` in `.env`, then scale up.

## 7) Run CYOA pipeline

```bash
python pipeline_cyoa.py
```

Default script run renders first 3 nodes for validation.

## Operational notes

- Resume behavior: completed chapter files and segment manifests are reused.
- If Chatterbox queue errors appear, increase `REQUEST_DELAY` in `.env`.
- If sentence endings sound clipped, lower `EXAGGERATION` first, then `TEMPERATURE`, then increase `SILENCE_PAD`.
