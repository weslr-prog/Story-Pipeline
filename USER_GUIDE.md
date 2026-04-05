# Story_Time User Guide

This guide covers full operation of the local Story_Time stack: writing with Ollama, narration with Chatterbox Gradio, and output generation for both novel and CYOA pipelines.

## 1) What must be running

Before any pipeline run, these services must be available:

1. Ollama service at `OLLAMA_URL` in `.env` (default `http://localhost:11434`)
2. Chatterbox Gradio WebUI at `CHATTERBOX_URL` in `.env` (default `http://127.0.0.1:7860`)
3. `ffmpeg` on PATH for stitching sentence segments into chapter audio

If any service is down, generation fails.

## 2) One-time setup

From project root:

```bash
cd /Users/wes/Desktop/Story_Time
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Verify interpreter version inside the environment:

```bash
python -V
```

Expected: Python 3.11.x

If your existing `.venv` was created with Python 3.9 or older, archive it and recreate:

```bash
cd /Users/wes/Desktop/Story_Time
mv .venv .venv_py39_backup_$(date +%Y%m%d_%H%M%S)
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e ./chatterbox
```

Install Chatterbox from local checkout if not already installed:

```bash
cd /Users/wes/Desktop/Story_Time
[[ -d chatterbox/.git ]] || git clone https://github.com/resemble-ai/chatterbox.git
python3 -m pip install -e ./chatterbox
```

Install ffmpeg on macOS (if missing):

```bash
brew install ffmpeg
```

## 3) Required user inputs

You must provide real content in these files:

1. `story_bible.json`
2. `characters.json`
3. `chapter_briefs.json`
4. `style_guide.txt`
5. `consistency_checklist.txt`

You must also provide a voice sample file and set its path in `.env`:

```dotenv
VOICE_SAMPLE=voices/s5_023.wav
```

The pipeline validates all required files before running.

## 4) Starting services

### Start Ollama model

```bash
ollama pull qwen2.5:7b
```

### Start Chatterbox WebUI

From project root:

```bash
bash scripts/start_chatterbox_webui.sh
```

Note: run this command from `/Users/wes/Desktop/Story_Time`. If you run from another directory, use an absolute path:

```bash
bash /Users/wes/Desktop/Story_Time/scripts/start_chatterbox_webui.sh
```

Warm-up note: if the first TTS request hangs, open `http://127.0.0.1:7860` in a browser once and click Load/Generate with a short line, then rerun the pipeline request.

## 5) Preflight check

Run:

```bash
python scripts/preflight.py
```

Expected fields in report:

1. `ffmpeg.ok: true`
2. `ollama.ok: true`
3. `chatterbox_webui.ok: true`
4. `gradio_endpoints` contains at least one endpoint (commonly `/generate`)

Set endpoint in `.env`:

```dotenv
CHATTERBOX_API=/generate
```

## 6) Model activation and switching

The writing model is controlled by `.env`:

```dotenv
LLM_MODEL=qwen2.5:7b
OLLAMA_URL=http://localhost:11434
```

To switch models:

1. Pull the model in Ollama
2. Update `LLM_MODEL` in `.env`
3. Re-run `python scripts/preflight.py`
4. Run a short validation (1 chapter) before full runs

For 16 GB Apple Silicon, keep all phases on the same smaller model first:

```dotenv
LLM_MODEL=qwen2.5:7b
WRITER_MODEL=qwen2.5:7b
EDITOR_MODEL=qwen2.5:7b
CRITIC_MODEL=qwen2.5:7b
ARCHIVIST_MODEL=qwen2.5:7b
TTS_PREP_MODEL=qwen2.5:7b
```

You can split model roles later by phase when stable.

## 7) TTS smoke test

Run:

```bash
python tts_engine.py
```

Expected output:

1. `audio/smoke_00.wav`
2. Sentence-level segments under `audio/segments/ch00/`

## 8) Running the novel pipeline

```bash
python pipeline_novel.py
```

What it does per chapter:

1. Writer draft
2. Editor polish
3. Critic check (`CRITIC_MODE=local` or `external`)
4. Revision pass
5. Word-target enforcement loop (if needed)
6. Deterministic lint gates (duplicate loops, event-order drift, reveal leaks, meta-awareness)
7. Automatic structural repair loop (bounded by `MAX_LINT_REPAIRS`)
8. Archivist summary
9. TTS-prep rewrite
10. Sentence-by-sentence narration + ffmpeg stitch

Outputs:

1. `chapters/chXX_draft.txt`
2. `chapters/chXX_edited.txt`
3. `chapters/chXX_final.txt`
4. `chapters/chXX_tts.txt`
5. `summaries/chXX_summary.txt`
6. `audio/chXX_narration.wav`

Critic artifacts:

1. Local critic report: `reviews/chXX_local_critic.md` (when `CRITIC_MODE=local`)
2. External critic packet: `reviews/chXX_external_critic_prompt.md` (when `CRITIC_MODE=external`)
3. External chapter copy: `reviews/chXX_edited_for_external.txt`
4. External report expected from user/model: `reviews/chXX_external_critic.md`

Lint artifacts:

1. Scene plan: `reviews/chXX_scene_plan.md`
2. Lint JSON report: `reviews/chXX_lint.json`
3. Lint markdown report: `reviews/chXX_lint.md`

If lint fails after max repairs, pipeline stops before TTS so broken chapter text is not narrated.

First validation recommendation:

1. Set `CHAPTER_COUNT=2` or `3`
2. Confirm quality and stability
3. Then scale up

## 9) Running the CYOA pipeline

```bash
python pipeline_cyoa.py
```

Current default behavior: direct run processes first 3 nodes for validation.

Outputs:

1. `cyoa/node_map.json`
2. `cyoa/nodes/node_XXX.txt`
3. `cyoa/nodes/node_XXX_narration.wav`

## 10) Chatterbox Gradio controls explained

These controls are exposed in Chatterbox base UI and mirrored by pipeline tuning values.

| Control | What it does | Stable narration range | Too low | Too high |
|---|---|---|---|---|
| `exaggeration` | Emotional intensity and delivery energy | 0.30-0.50 | Flat/monotone | Instability, clipped endings, over-dramatic phrasing |
| `cfgw` / CFG weight | How tightly output follows voice-conditioning | 0.55-0.70 | Voice drift / generic tone | Artifacts, robotic pacing |
| `temperature` | Sampling randomness | 0.65-0.80 | Repetitive/rigid | Jumbled rhythm/wording |
| `seed_num` | Reproducibility seed | 0 for random | N/A | Fixed seeds can sound mechanical if reused too much |
| `min_p` | Alternative sampler floor | 0.02-0.10 | Disabled at 0.0 | Can over-constrain speech if too high |
| `top_p` | Nucleus sampling cap | 1.0 for stable default | Over-pruning if too low | N/A |
| `repetition_penalty` | Discourages loops | 1.1-1.3 | Phrase loops | Forced awkward phrasing |

Practical tuning order when audio quality drops:

1. Lower `EXAGGERATION`
2. Lower `TEMPERATURE`
3. Increase `SILENCE_PAD`
4. Increase `REQUEST_DELAY` if queue/load errors appear

## 11) Does pipeline prompt for voice file?

No interactive prompt is used.

Behavior is automatic:

1. The system reads `VOICE_SAMPLE` from `.env`
2. Pipelines pass that file path directly into TTS calls
3. If file is missing, input validation fails before generation

## 12) Resume and retry behavior

In `tts_engine.py`:

1. Each sentence has retry attempts (`MAX_RETRIES`)
2. Backoff delay uses `RETRY_BACKOFF`
3. Request pacing uses `REQUEST_DELAY`
4. Segment completion is tracked with manifest files, so reruns can continue from completed sentence segments

In novel pipeline:

1. Chapters with existing final files are skipped on rerun
2. In `CRITIC_MODE=external`, pipeline pauses until `reviews/chXX_external_critic.md` exists (if `PAUSE_FOR_EXTERNAL_CRITIC=true`)

## 13) Common problems and fixes

1. `Connection refused` to Chatterbox: start WebUI and verify `CHATTERBOX_URL`
2. Endpoint mismatch: run preflight, set `CHATTERBOX_API` to discovered endpoint
3. `ModuleNotFoundError: requests` in preflight: activate `.venv` and run `python -m pip install -r requirements.txt`
4. `Package 'chatterbox-tts' requires a different Python` during install: recreate `.venv` with Python 3.11
5. No audio generated: verify `VOICE_SAMPLE` path and file format
6. Jumbled sentence endings: reduce exaggeration, then temperature, then raise silence padding
7. Slow or unstable long runs: lower chapter target and run in smaller batches
8. Pipeline pauses in external critic mode: add `reviews/chXX_external_critic.md` then rerun
9. Chapter too short for desired runtime: set `TARGET_MINUTES_MIN/MAX` and increase `EXPANSION_PASSES`

## 14) Recommended operating routine

1. Activate env
2. Start Ollama + Chatterbox
3. Run preflight
4. Run smoke test
5. Run 2-3 chapter validation
6. Scale to full novel
7. Run CYOA after novel path is stable

This routine catches nearly all runtime issues early and minimizes rework.

## 15) After Chapter 1: Continue vs Rewrite

Continue to the next chapter:

1. Increase `CHAPTER_COUNT` in `.env` (for example, `2` to generate through Chapter 2)
2. Run `python pipeline_novel.py`
3. Existing completed chapters are skipped automatically

Rewrite one chapter from scratch:

```bash
python scripts/reset_chapter.py --chapter 1
python pipeline_novel.py
```

Preview reset actions without deleting:

```bash
python scripts/reset_chapter.py --chapter 1 --dry-run
```

## 16) External Critic Handoff (optional)

Use this when you want a stronger remote model to critique continuity and plot holes.

Set:

```dotenv
CRITIC_MODE=external
PAUSE_FOR_EXTERNAL_CRITIC=true
```

Run pipeline once. It will generate:

1. `reviews/chXX_external_critic_prompt.md`
2. `reviews/chXX_edited_for_external.txt`

Then:

1. Paste the prompt packet into your external model
2. Save returned critique as `reviews/chXX_external_critic.md`
3. Run `python pipeline_novel.py` again to continue

## 17) Fixing a single narration sentence

List sentence indices for a chapter:

```bash
python scripts/patch_narration.py --chapter 1 --list
```

Regenerate one sentence using existing chapter text:

```bash
python scripts/patch_narration.py --chapter 1 --sentence 42
```

Replace sentence text, regenerate, and persist updated TTS chapter text:

```bash
python scripts/patch_narration.py --chapter 1 --sentence 42 --text "Updated sentence here." --write-tts
```

The tool regenerates selected segments and restitches `audio/chXX_narration.wav`.

## 18) Style influence input

Primary style control lives in `style_guide.txt`.

Optional quick style influence can be set in `.env`:

```dotenv
STYLE_INFLUENCE=Layer in philosophical introspection, ecological systems thinking, court-politics tension, and precise sensory worldbuilding. Keep prose original and avoid mimicry.
```

Guidance:

1. Use trait-level influence, not direct imitation.
2. Put must-keep prose rules in `style_guide.txt` for strongest effect.

## 19) Targeting 15-20 minute chapters

Set duration-based targets:

```dotenv
TARGET_MINUTES_MIN=15
TARGET_MINUTES_MAX=20
ASSUMED_WPM=150
EXPANSION_PASSES=2
```

Notes:

1. Pipeline converts minutes to word targets and enforces minimum length.
2. If chapters still under-run, raise `EXPANSION_PASSES` to `3`.
3. If drift appears, reduce expansion passes and tighten chapter briefs.

## 20) Lint Gate Tuning

These knobs control structural quality blocking before TTS:

```dotenv
LINT_ENABLED=true
MAX_LINT_REPAIRS=2
MAX_DUPLICATE_PARAGRAPH_REPEATS=1
MAX_SENTENCE_REPEAT=2
META_PHRASES=this is only the beginning,on a journey,story had only started,dear reader,in this chapter,the author,the writer,prompt,model,ai
CHAPTER1_FORBIDDEN_TERMS=for elara,hidden door,novaBio tracker,tracker lay hidden
```

Guidance:

1. Keep `MAX_DUPLICATE_PARAGRAPH_REPEATS=1` to block copy loops.
2. Keep `MAX_SENTENCE_REPEAT=2` to catch repetitive echoing.
3. Add or remove phrases in `META_PHRASES` as needed for immersion.
4. Use `CHAPTER1_FORBIDDEN_TERMS` to block early reveals in Chapter 1.

## 21) Prevent Mac Sleep During Long Runs

Long chapter runs can fail if the Mac sleeps. Keep the machine awake while running:

```bash
caffeinate -dimsu python pipeline_novel.py
```

Or keep awake in one terminal and run pipeline in another:

```bash
caffeinate -dimsu
```

Stop `caffeinate` with `Ctrl+C` when done.

## 22) Shutdown and daily operations

Use this checklist before each major run:

1. `PRE_RUN_CHECKLIST.txt`

Stop Chatterbox WebUI when done:

```bash
bash scripts/stop_chatterbox_webui.sh
```
