import json
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

from config import SETTINGS
from story_lint import LintSettings, lint_chapter, to_markdown
from tts_engine import narrate_chapter

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _load_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _ensure_dirs() -> None:
    for rel in ["chapters", "summaries", "audio", "audio/segments"]:
        (ROOT / rel).mkdir(parents=True, exist_ok=True)


def _validate_inputs() -> None:
    required = [
        ROOT / "story_bible.json",
        ROOT / "characters.json",
        ROOT / "chapter_briefs.json",
        ROOT / "style_guide.txt",
        ROOT / "consistency_checklist.txt",
        ROOT / SETTINGS.voice_sample,
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing required inputs:\n" + "\n".join(missing))


def _model_for(phase: str) -> str:
    phase_models = {
        "writer": SETTINGS.writer_model,
        "editor": SETTINGS.editor_model,
        "critic": SETTINGS.critic_model,
        "archivist": SETTINGS.archivist_model,
        "tts_prep": SETTINGS.tts_prep_model,
    }
    model = phase_models.get(phase, "")
    return model.strip() or SETTINGS.llm_model


def _llm(phase: str, temp: float, max_tokens: int) -> ChatOllama:
    return ChatOllama(
        model=_model_for(phase),
        base_url=SETTINGS.ollama_url,
        temperature=temp,
        num_ctx=8192,
        num_predict=max_tokens,
        repeat_penalty=1.1,
    )


def _invoke(llm: ChatOllama, prompt: str) -> str:
    resp = llm.invoke(prompt)
    return getattr(resp, "content", str(resp)).strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _effective_word_targets() -> tuple[int, int, str]:
    if (
        SETTINGS.target_minutes_min > 0
        and SETTINGS.target_minutes_max > 0
        and SETTINGS.target_minutes_max >= SETTINGS.target_minutes_min
    ):
        min_words = int(round(SETTINGS.target_minutes_min * SETTINGS.assumed_wpm))
        max_words = int(round(SETTINGS.target_minutes_max * SETTINGS.assumed_wpm))
        if min_words > 0 and max_words >= min_words:
            return min_words, max_words, "duration"
    return SETTINGS.word_target_min, SETTINGS.word_target_max, "static"


def _reviews_dir() -> Path:
    path = ROOT / SETTINGS.reviews_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lint_settings() -> LintSettings:
    return LintSettings(
        max_duplicate_paragraph_repeats=SETTINGS.max_duplicate_paragraph_repeats,
        max_sentence_repeat=SETTINGS.max_sentence_repeat,
        meta_phrases=SETTINGS.meta_phrases,
        chapter1_forbidden_terms=SETTINGS.chapter1_forbidden_terms,
    )


def _build_scene_plan(chapter_num: int, brief: dict, context: str) -> str:
    plan_prompt = f"""
You are the Scene Planner Agent.
Build a concise scene plan for chapter {chapter_num}.
Use 2-3 scenes, preserving key events and order from the chapter brief.
Return markdown only with this format:
1) Scene title
- goal
- entry state
- conflict beat
- exit state

CHAPTER BRIEF:
{json.dumps(brief, indent=2)}

CONTEXT:
{context}
"""
    planner = _llm("editor", temp=0.3, max_tokens=1800)
    scene_plan = _invoke(planner, plan_prompt)
    (_reviews_dir() / f"ch{chapter_num:02d}_scene_plan.md").write_text(scene_plan, encoding="utf-8")
    return scene_plan


def _run_lint_repairs(chapter_num: int, chapter_text: str, brief: dict, context: str) -> str:
    if not SETTINGS.lint_enabled:
        return chapter_text

    current = chapter_text
    settings = _lint_settings()
    reviews_dir = _reviews_dir()

    for attempt in range(0, max(0, SETTINGS.max_lint_repairs) + 1):
        report = lint_chapter(current, chapter_num=chapter_num, brief=brief, settings=settings)
        (reviews_dir / f"ch{chapter_num:02d}_lint.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (reviews_dir / f"ch{chapter_num:02d}_lint.md").write_text(to_markdown(report), encoding="utf-8")

        if report.get("passed", False):
            return current

        if attempt >= SETTINGS.max_lint_repairs:
            raise RuntimeError(
                f"Chapter {chapter_num} failed lint checks after {SETTINGS.max_lint_repairs} repair attempts. "
                f"See {reviews_dir / f'ch{chapter_num:02d}_lint.md'}"
            )

        failing_checks = [c for c in report.get("checks", []) if not c.get("passed", False)]

        repair_prompt = f"""
You are the Structural Repair Agent.
Repair the chapter to pass lint checks.
Requirements:
- remove duplicate blocks and repeated sentence loops
- keep key events in brief order
- remove meta-awareness language
- preserve chapter intent and scene order
- do not introduce new major events
Return only repaired chapter text.

FAILING CHECKS:
{json.dumps(failing_checks, indent=2)}

LINT REPORT:
{json.dumps(report, indent=2)}

CHAPTER BRIEF:
{json.dumps(brief, indent=2)}

CONTEXT:
{context}

CHAPTER:
{current}
"""
        repairer = _llm("editor", temp=0.3, max_tokens=6000)
        current = _invoke(repairer, repair_prompt)

    return current


def _run_local_critic(chapter_num: int, edited: str, checklist: str, context: str) -> tuple[str, str]:
    critic_prompt = f"""
You are the Story QA Critic Agent.
Find continuity errors, conflation, plot holes, causality breaks, and knowledge leaks.
Be strict and concrete.

Return markdown with sections in this exact order:
1) BLOCKING ISSUES
2) NON-BLOCKING ISSUES
3) REVISION INSTRUCTIONS

CHECKLIST:
{checklist}

CONTEXT:
{context}

EDITED CHAPTER:
{edited}
"""

    critic = _llm("critic", temp=0.2, max_tokens=1800)
    critic_report = _invoke(critic, critic_prompt)

    revision_prompt = f"""
You are the Revision Agent.
Revise the chapter using the critic report.
Resolve all blocking issues and keep scene order and chapter intent.
Do not add new major plot points not implied by the chapter brief.
Return only revised chapter text.

CRITIC REPORT:
{critic_report}

CHAPTER:
{edited}
"""

    reviser = _llm("editor", temp=0.4, max_tokens=5000)
    revised = _invoke(reviser, revision_prompt)

    report_path = _reviews_dir() / f"ch{chapter_num:02d}_local_critic.md"
    report_path.write_text(critic_report, encoding="utf-8")
    return critic_report, revised


def _external_critic_prompt(chapter_num: int, checklist: str, brief: dict, edited: str) -> str:
    return f"""# External Critic Packet - Chapter {chapter_num}

## Task
Review this chapter for:
- conflation
- storyline consistency
- plot holes
- timeline contradictions
- character knowledge leaks

## Output format
Return markdown with these sections:
1. BLOCKING ISSUES
2. NON-BLOCKING ISSUES
3. REVISION INSTRUCTIONS

## Chapter Brief
{json.dumps(brief, indent=2)}

## Consistency Checklist
{checklist}

## Chapter Text
{edited}
"""


def _apply_external_critic(chapter_num: int, checklist: str, brief: dict, edited: str) -> str:
    reviews_dir = _reviews_dir()
    packet_path = reviews_dir / f"ch{chapter_num:02d}_external_critic_prompt.md"
    report_path = reviews_dir / f"ch{chapter_num:02d}_external_critic.md"
    edited_path = reviews_dir / f"ch{chapter_num:02d}_edited_for_external.txt"

    edited_path.write_text(edited, encoding="utf-8")
    packet_path.write_text(_external_critic_prompt(chapter_num, checklist, brief, edited), encoding="utf-8")

    if not report_path.exists():
        if SETTINGS.pause_for_external_critic:
            raise RuntimeError(
                "External critic mode paused. Provide report at "
                f"{report_path} and rerun pipeline_novel.py."
            )
        return edited

    external_report = report_path.read_text(encoding="utf-8").strip()
    if not external_report:
        return edited

    revision_prompt = f"""
You are the Revision Agent.
Apply external critic feedback to fix blocking issues first.
Preserve chapter arc and style.
Return only revised chapter text.

EXTERNAL REPORT:
{external_report}

CHAPTER:
{edited}
"""

    reviser = _llm("editor", temp=0.4, max_tokens=5000)
    return _invoke(reviser, revision_prompt)


def _enforce_word_targets(chapter_num: int, chapter_text: str, context: str, min_words: int, max_words: int) -> str:
    out = chapter_text

    for _ in range(max(0, SETTINGS.expansion_passes)):
        wc = _word_count(out)
        if wc >= min_words:
            break

        expand_prompt = f"""
You are the Expansion Agent.
Expand this chapter to at least {min_words} words while keeping it under {max_words} words.
Do not add new major plot events.
Deepen existing scenes using sensory detail, subtext, and internal reaction.
Maintain continuity.
Return only revised chapter text.

CONTEXT:
{context}

CHAPTER:
{out}
"""

        expander = _llm("writer", temp=0.55, max_tokens=6500)
        out = _invoke(expander, expand_prompt)

    wc = _word_count(out)
    if wc > max_words:
        trim_prompt = f"""
You are the Compression Agent.
Trim this chapter to no more than {max_words} words without losing required events.
Maintain continuity and voice.
Return only revised chapter text.

CHAPTER:
{out}
"""
        compressor = _llm("editor", temp=0.3, max_tokens=5500)
        out = _invoke(compressor, trim_prompt)

    final_wc = _word_count(out)
    print(f"[INFO] Chapter {chapter_num} word count after enforcement: {final_wc}")
    return out


def load_prior_summaries(chapter_num: int) -> str:
    chunks: list[str] = []
    for i in range(1, chapter_num):
        path = ROOT / "summaries" / f"ch{i:02d}_summary.txt"
        if path.exists():
            chunks.append(f"Chapter {i}: {path.read_text(encoding='utf-8').strip()}")
    return "\n".join(chunks) if chunks else "This is the first chapter."


def run_chapter(chapter_num: int) -> None:
    bible = _load("story_bible.json")
    characters = _load("characters.json")
    briefs = _load("chapter_briefs.json")
    style = _load_text("style_guide.txt")
    checklist = _load_text("consistency_checklist.txt")

    brief = briefs[chapter_num - 1]
    prior = load_prior_summaries(chapter_num)
    word_min, word_max, word_mode = _effective_word_targets()

    influence_block = ""
    if SETTINGS.style_influence.strip():
        influence_block = (
            "\n\nSTYLE INFLUENCE (high-level traits only, do not copy phrasing):\n"
            + SETTINGS.style_influence.strip()
        )

    context = (
        "STORY BIBLE:\n"
        + json.dumps(bible, indent=2)
        + "\n\nCHARACTERS:\n"
        + json.dumps(characters, indent=2)
        + "\n\nPRIOR CHAPTERS:\n"
        + prior
        + "\n\nSTYLE GUIDE:\n"
        + style
        + influence_block
        + "\n\nTHIS CHAPTER BRIEF:\n"
        + json.dumps(brief, indent=2)
    )

    scene_plan = _build_scene_plan(chapter_num, brief, context)

    writer_prompt = f"""
You are the Writer Agent.
Write chapter {chapter_num} as polished prose.
Target {word_min}-{word_max} words.
Respect style and continuity.
- Characters are not aware they are in a story.
- Do not use phrases like "this is only the beginning", "she was on a journey", "the story had only started", or references to reader/writer/prompt/model.
Write strictly from this scene plan and keep scene order.
Return only chapter text.

SCENE PLAN:
{scene_plan}

{context}
"""

    editor_prompt = f"""
You are the Editor Agent.
Revise the chapter for continuity and voice consistency.
Use checklist strictly.
- Remove any meta-narrative self-awareness or journey-framing language; characters must read as living in-world, not narrating a story about themselves.
Return only final chapter text.

CHECKLIST:
{checklist}

DRAFT:
{{draft}}
"""

    archivist_prompt = """
You are the Archivist Agent.
Produce exactly 150 words summarizing factual events in this chapter.
No opinions, no style commentary.

CHAPTER:
{final}
"""

    tts_prep_prompt = """
You are the TTS Prep Agent.
Prepare chapter text for narration:
- remove chapter headings
- expand contractions where natural
- replace long punctuation breaks with comma or period
- ensure each sentence ends with punctuation
Return narration-ready prose only.

TEXT:
{final}
"""

    writer = _llm("writer", temp=0.8, max_tokens=5500)
    draft = _invoke(writer, writer_prompt)

    editor = _llm("editor", temp=0.5, max_tokens=5500)
    edited = _invoke(editor, editor_prompt.format(draft=draft))

    critic_mode = SETTINGS.critic_mode.strip().lower()
    if critic_mode == "off":
        final = edited
    elif critic_mode == "local":
        _, final = _run_local_critic(chapter_num, edited, checklist, context)
    elif critic_mode == "external":
        final = _apply_external_critic(chapter_num, checklist, brief, edited)
    else:
        raise ValueError("CRITIC_MODE must be one of: off, local, external")

    final = _enforce_word_targets(chapter_num, final, context, word_min, word_max)
    final = _run_lint_repairs(chapter_num, final, brief, context)

    archivist = _llm("archivist", temp=0.2, max_tokens=450)
    summary = _invoke(archivist, archivist_prompt.format(final=final))

    tts_prep = _llm("tts_prep", temp=0.3, max_tokens=5500)
    tts_text = _invoke(tts_prep, tts_prep_prompt.format(final=final))

    (ROOT / "chapters" / f"ch{chapter_num:02d}_draft.txt").write_text(draft, encoding="utf-8")
    (ROOT / "chapters" / f"ch{chapter_num:02d}_edited.txt").write_text(edited, encoding="utf-8")
    (ROOT / "chapters" / f"ch{chapter_num:02d}_final.txt").write_text(final, encoding="utf-8")
    (ROOT / "summaries" / f"ch{chapter_num:02d}_summary.txt").write_text(summary, encoding="utf-8")
    (ROOT / "chapters" / f"ch{chapter_num:02d}_tts.txt").write_text(tts_text, encoding="utf-8")

    print(f"[INFO] Word targeting mode: {word_mode} ({word_min}-{word_max})")

    narrate_chapter(
        text=tts_text,
        voice_sample=SETTINGS.voice_sample,
        output_path=str(ROOT / "audio" / f"ch{chapter_num:02d}_narration.wav"),
        chapter_num=chapter_num,
    )

    print(f"[OK] Chapter {chapter_num} complete")


def run_all() -> None:
    _ensure_dirs()
    _validate_inputs()
    briefs = _load("chapter_briefs.json")
    max_chapters = min(SETTINGS.chapter_count, len(briefs))
    if max_chapters == 0:
        raise RuntimeError("chapter_briefs.json is empty.")

    for chapter_num in range(1, max_chapters + 1):
        final_path = ROOT / "chapters" / f"ch{chapter_num:02d}_final.txt"
        if final_path.exists():
            print(f"[SKIP] Chapter {chapter_num} already exists")
            continue
        run_chapter(chapter_num)


if __name__ == "__main__":
    run_all()
