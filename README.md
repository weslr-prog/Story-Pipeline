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

1. Smaller context (`LLM_NUM_CTX=4096`)
2. Lower word target pressure (`1800-2400` base)
3. Single expansion pass and single lint repair pass
4. Slower, safer TTS pacing (`REQUEST_DELAY=1.00`, `RETRY_BACKOFF=1.00`)

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
