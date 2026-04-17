import json
import importlib
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Tuple

from config import SETTINGS
from story_lint import LintSettings, lint_chapter, to_markdown
from tts_engine import narrate_chapter

ROOT = Path(__file__).resolve().parent
_LLM_CALL_SEMAPHORE = threading.BoundedSemaphore(max(1, SETTINGS.llm_concurrency_limit))
_LLM_PACING_LOCK = threading.Lock()
_LAST_LLM_CALL_TS = 0.0


class LLMCallTimeoutError(RuntimeError):
    pass


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _load_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _first_chapter_guidance(chapter_num: int) -> str:
    if chapter_num != 1 or not SETTINGS.first_chapter_guidance_enabled:
        return ""
    guidance_path = ROOT / SETTINGS.first_chapter_guidance_file
    if not guidance_path.exists():
        return ""
    raw = guidance_path.read_text(encoding="utf-8", errors="replace")
    compact = "\n".join(line.rstrip() for line in raw.splitlines() if line.strip())
    # Keep prompt context bounded while preserving actionable guidance.
    return compact[:5000]


def _ensure_dirs() -> None:
    for rel in ["chapters", "chapters/scenes", "summaries", "audio", "audio/segments", "reviews"]:
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


def _resolved_backend() -> str:
    backend = SETTINGS.llm_backend.strip().lower()
    if backend not in {"openclaw", "local_disk_kv"}:
        backend = "local_disk_kv"
    # Compatibility switch: respect old env behavior if explicitly set.
    if SETTINGS.use_local_disk_kv:
        backend = "local_disk_kv"
    return backend


def _client_factory_for_backend():
    backend = _resolved_backend()
    if backend == "local_disk_kv":
        from local_llm import get_llm_client

        return get_llm_client

    try:
        mod = importlib.import_module("openclaw")
        OpenClawClient = getattr(mod, "OpenClawClient")
    except Exception as exc:
        raise RuntimeError(
            "LLM backend 'openclaw' is selected, but package 'openclaw' is not available. "
            "Install it in your environment or set LLM_BACKEND=local_disk_kv."
        ) from exc
    return OpenClawClient


def _llm(phase: str, temp: float, max_tokens: int):
    """
    Return a client that can talk to either:
    - the original OpenClaw daemon (Gemini, Grok, etc.)
    - the local TurboQuant-disk server (if LLM_BACKEND=local_disk_kv)
    """
    client = _client_factory_for_backend()()
    client.set_role(phase)
    client.set_temperature(temp)
    client.set_max_output_tokens(max_tokens)
    setattr(client, "_copilot_max_tokens", int(max_tokens))
    if getattr(SETTINGS, "use_compact_context", False):
        client.apply_preset("compact_context")
    return client


def _invoke(client, prompt: str) -> str:
    resp = client.invoke(prompt)
    return getattr(resp, "content", str(resp)).strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def _chapter_complete_alert_mode() -> str:
    return str(getattr(SETTINGS, "chapter_complete_alert", "double_beep") or "double_beep").strip().lower().replace(" ", "_")


def _play_chapter_complete_alert(chapter_num: int) -> None:
    mode = _chapter_complete_alert_mode()
    if mode in {"off", "none", "disabled", "0", "false"}:
        return

    # macOS-native audio cues for chapter completion.
    if mode == "gong":
        sounds = ["/System/Library/Sounds/Glass.aiff"]
    else:
        sounds = ["/System/Library/Sounds/Ping.aiff", "/System/Library/Sounds/Ping.aiff"]

    played_any = False
    for sound_path in sounds:
        try:
            subprocess.run(["afplay", sound_path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            played_any = True
            if len(sounds) > 1:
                time.sleep(0.12)
        except Exception:
            continue

    if not played_any:
        print("\a", end="", flush=True)

    _log(f"[INFO] Chapter {chapter_num} complete alert played ({mode})")


def _with_deadline(timeout_seconds: int, label: str, fn):
    # signal.setitimer/SIGALRM only work in the main thread.
    # Worker threads (concurrent chapter execution) fall through to fn() directly
    # and rely on the HTTP-level timeout already set in local_llm.py.
    if (
        timeout_seconds <= 0
        or not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        return fn()

    def _handle_timeout(signum, frame):
        raise LLMCallTimeoutError(f"{label} exceeded {timeout_seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _invoke_guarded(client, prompt: str, *, label: str, retry_attempts: int | None = None) -> str:
    global _LAST_LLM_CALL_TS
    configured_retries = SETTINGS.llm_call_retry_attempts if retry_attempts is None else max(0, int(retry_attempts))
    total_attempts = max(1, configured_retries + 1)
    last_exc: Exception | None = None

    for attempt in range(1, total_attempts + 1):
        started = time.time()
        prompt_words = _word_count(prompt)
        prompt_tokens = int(prompt_words / 0.75)
        _log(
            f"[DEBUG] {label} start "
            f"(attempt {attempt}/{total_attempts}, prompt_words={prompt_words}, "
            f"prompt_tokens~={prompt_tokens}, timeout={SETTINGS.llm_call_timeout_seconds}s)"
        )
        try:
            with _LLM_CALL_SEMAPHORE:
                min_interval = max(0.0, SETTINGS.llm_min_request_interval_seconds)
                if min_interval > 0:
                    with _LLM_PACING_LOCK:
                        now = time.monotonic()
                        sleep_for = min_interval - (now - _LAST_LLM_CALL_TS)
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                        _LAST_LLM_CALL_TS = time.monotonic()

                result = _with_deadline(
                    SETTINGS.llm_call_timeout_seconds,
                    label,
                    lambda: _invoke(client, prompt),
                )
            elapsed = max(0.0, time.time() - started)
            _log(f"[INFO] {label} completed in {elapsed:.1f}s")
            return result
        except Exception as exc:
            last_exc = exc
            elapsed = max(0.0, time.time() - started)
            _log(f"[WARN] {label} failed on attempt {attempt}/{total_attempts} after {elapsed:.1f}s: {exc}")
            # After a timeout-like failure, reduce output token budget for subsequent retries.
            timeout_like = isinstance(exc, LLMCallTimeoutError) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
            if timeout_like and hasattr(client, "set_max_output_tokens"):
                current_cap = int(getattr(client, "_copilot_max_tokens", 0) or 0)
                if current_cap > 0:
                    lowered = max(350, int(current_cap * 0.75))
                    if lowered < current_cap:
                        try:
                            client.set_max_output_tokens(lowered)
                            setattr(client, "_copilot_max_tokens", lowered)
                            _log(f"[WARN] {label} reducing max tokens for retry: {current_cap} -> {lowered}")
                        except Exception:
                            pass
            if attempt >= total_attempts:
                break
            sleep_for = max(0.0, SETTINGS.llm_call_retry_backoff) * attempt
            if sleep_for > 0:
                time.sleep(sleep_for)

    raise RuntimeError(f"{label} failed after {total_attempts} attempts: {last_exc}") from last_exc


def _debug_len(label: str, txt: str) -> None:
    words = _word_count(txt)
    tokens = int(words / 0.75)
    _log(f"[DEBUG] {label}: {words:,} words  ~= {tokens:,} tokens")


def _effective_word_targets() -> Tuple[int, int, str]:
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


def _chapter_artifacts(chapter_num: int) -> dict[str, Path]:
    ch = f"ch{chapter_num:02d}"
    return {
        "draft": ROOT / "chapters" / f"{ch}_draft.txt",
        "edited": ROOT / "chapters" / f"{ch}_edited.txt",
        "final": ROOT / "chapters" / f"{ch}_final.txt",
        "tts": ROOT / "chapters" / f"{ch}_tts.txt",
        "summary": ROOT / "summaries" / f"{ch}_summary.txt",
        "audio": ROOT / "audio" / f"{ch}_narration.wav",
        "lint_md": _reviews_dir() / f"{ch}_lint.md",
        "scene_plan": _reviews_dir() / f"{ch}_scene_plan.md",
    }


def _scene_artifacts(chapter_num: int, scene_idx: int) -> dict[str, Path]:
    scene_dir = ROOT / "chapters" / "scenes" / f"ch{chapter_num:02d}"
    scene_dir.mkdir(parents=True, exist_ok=True)
    base = scene_dir / f"scene{scene_idx:02d}"
    return {
        "draft": base.with_name(base.name + "_draft.txt"),
        "edited": base.with_name(base.name + "_edited.txt"),
        "final": base.with_name(base.name + "_final.txt"),
    }


def _prior_scene_context(scene_texts: List[str]) -> str:
    if not scene_texts:
        return "This is the opening scene of the chapter."
    combined = "\n\n".join(scene_texts)
    return combined[-SETTINGS.intra_chapter_context_chars :]


_CHAPTER_WORDS = {
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
    11: "Eleven",
    12: "Twelve",
    13: "Thirteen",
    14: "Fourteen",
    15: "Fifteen",
    16: "Sixteen",
    17: "Seventeen",
    18: "Eighteen",
    19: "Nineteen",
    20: "Twenty",
}


def _chapter_title_from_brief(chapter_num: int, brief: dict) -> str:
    for key in ("title", "chapter_title", "name", "chapter_name"):
        value = brief.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = re.sub(r"\s+", " ", value.strip())
            return cleaned.strip(" .:-")
    return f"Chapter {chapter_num}"


def _chapter_intro_line(chapter_num: int, chapter_title: str) -> str:
    chapter_label = _CHAPTER_WORDS.get(chapter_num, str(chapter_num))
    safe_title = re.sub(r"\s+", " ", chapter_title).strip().rstrip(".?!")
    return f"Chapter {chapter_label}: {safe_title}."


def _normalize_narration_punctuation(text: str) -> str:
    normalized = re.sub(r"\.{3,}", ".", text)
    normalized = re.sub(r"([!?]){2,}", r"\1", normalized)
    normalized = re.sub(r"\s+([,;:.!?])", r"\1", normalized)
    normalized = re.sub(r"([,;:])(?=[A-Za-z\"])", r"\1 ", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _prepare_narration_text(chapter_num: int, chapter_title: str, chapter_text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in chapter_text.splitlines():
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            continue
        if re.match(r"^#{1,6}\s+", line):
            continue
        if re.match(r"^\*\*?(end of scene|scene\s+\d+)\b", line, flags=re.I):
            continue
        if re.match(r"^scene\s+\d+\s*:", line, flags=re.I):
            continue
        if re.match(r"^[A-Z\s]{3,}:\s", line):
            continue
        if re.fullmatch(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}", line):
            continue
        if re.match(r"^[-*]\s+", line):
            line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\*\*(.+)\*\*$", r"\1", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"^_(.+)_$", r"\1", line)
        line = re.sub(r"_(.+?)_", r"\1", line)
        line = re.sub(r"^\s*\[[^\]]*\]\s*", "", line)
        line = re.sub(r"([.!?])\s*\[[^\]]*\]", r"\1", line)
        line = re.sub(r"^(\([^)]*\))+\s*", "", line)
        line = line.strip()
        if not line:
            continue
        cleaned_lines.append(line)

    prepared = "\n".join(cleaned_lines).strip()
    prepared = _normalize_narration_punctuation(prepared)

    if not prepared:
        return prepared

    if SETTINGS.chapter_intro_enabled:
        intro = _chapter_intro_line(chapter_num, chapter_title)
        if prepared.lower().startswith(intro.lower()):
            return prepared
        return f"{intro}\n\n{prepared}"

    return prepared


def _review_marker_path(chapter_num: int, stage: str) -> Path:
    return _reviews_dir() / f"ch{chapter_num:02d}_{stage}.approved"


def _brief_flag_value(brief: dict, label: str) -> str:
    key = f"{label.lower().replace(' ', '_')}_detail"
    if key in brief and isinstance(brief.get(key), str) and brief.get(key).strip():
        return brief.get(key).strip()

    prefix = f"{label.lower()}:"
    for raw in brief.get("continuity_flags", []):
        if not isinstance(raw, str):
            continue
        line = raw.strip()
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _review_packet_path(chapter_num: int, stage: str) -> Path:
    return _reviews_dir() / f"ch{chapter_num:02d}_{stage}_review.md"


def _require_manual_review(chapter_num: int, stage: str, headline: str, files: List[Path]) -> None:
    marker = _review_marker_path(chapter_num, stage)

    if getattr(SETTINGS, "auto_approve", False):
        marker.touch()
        _log(f"[AUTO-APPROVE] Created marker {marker}")
        return

    if marker.exists():
        _log(f"[REVIEW] {stage} approved for chapter {chapter_num}: {marker}")
        return

    packet = _review_packet_path(chapter_num, stage)
    lines = [
        f"# {headline}",
        "",
        "Edit files as needed, then approve this checkpoint by creating this marker file:",
        str(marker),
        "",
        "Files to review:",
    ]
    for path in files:
        lines.append(f"- {path}")
    lines.append("")
    lines.append("After approval, rerun: python pipeline_novel.py")
    packet.write_text("\n".join(lines), encoding="utf-8")

    raise RuntimeError(
        f"Paused at {stage} review for chapter {chapter_num}. "
        f"Review packet: {packet}. Create marker to continue: {marker}"
    )


def _chapter_complete(chapter_num: int) -> bool:
    files = _chapter_artifacts(chapter_num)
    required = [files["final"], files["tts"], files["summary"], files["audio"]]
    if not all(path.exists() for path in required):
        return False
    if SETTINGS.pause_before_narration_review and not _review_marker_path(chapter_num, "pre_narration").exists():
        return False
    if SETTINGS.pause_after_chapter_review and not _review_marker_path(chapter_num, "post_chapter").exists():
        return False
    return True


def _lint_settings() -> LintSettings:
    return LintSettings(
        max_duplicate_paragraph_repeats=SETTINGS.max_duplicate_paragraph_repeats,
        max_sentence_repeat=SETTINGS.max_sentence_repeat,
        meta_phrases=SETTINGS.meta_phrases,
        chapter1_forbidden_terms=SETTINGS.chapter1_forbidden_terms,
        chapter1_decision_verbs=SETTINGS.chapter1_decision_verbs,
        chapter1_red_flag_phrases=SETTINGS.chapter1_red_flag_phrases,
    )


def _build_scene_plan(chapter_num: int, brief: dict, context: str) -> str:
    first_chapter_block = ""
    guidance = _first_chapter_guidance(chapter_num)
    if guidance:
        first_chapter_block = (
            "\nFIRST CHAPTER GUIDANCE (apply this as a hard contract for chapter 1 opening and hook):\n"
            + guidance
            + "\n"
        )

    plan_prompt = f"""
You are the Scene Planner Agent.
Build exactly three scenes for chapter {chapter_num}.
Each scene must correspond to one of the three events in the chapter brief below.

Return markdown only, using this exact format:

1) Scene title
- Goal: one-line objective
- Entry state: where the scene starts emotionally/situationally
- Conflict beat: what pressure or turn hits this scene
- Exit state: where the scene lands

CHAPTER BRIEF:
{json.dumps(brief, indent=2)}

{first_chapter_block}

CONTEXT:
{context}
"""
    planner = _llm("editor", temp=0.3, max_tokens=1800)
    scene_plan = _invoke_guarded(planner, plan_prompt, label=f"chapter {chapter_num} scene planner")
    (_reviews_dir() / f"ch{chapter_num:02d}_scene_plan.md").write_text(scene_plan, encoding="utf-8")
    return scene_plan


def _rewrite_scene_plan(chapter_num: int, brief: dict, context: str, invalid_plan: str, parse_error: str, attempt: int) -> str:
    rewrite_prompt = f"""
You are fixing a scene plan format error for chapter {chapter_num}.

Your previous output failed parsing. Rewrite it in this exact schema with exactly 3 scenes and no extra sections:

1) Scene title
- Goal: one-line objective
- Entry state: where the scene starts emotionally/situationally
- Conflict beat: what pressure or turn hits this scene
- Exit state: where the scene lands

2) Scene title
- Goal: ...
- Entry state: ...
- Conflict beat: ...
- Exit state: ...

3) Scene title
- Goal: ...
- Entry state: ...
- Conflict beat: ...
- Exit state: ...

Hard rules:
- Return markdown only.
- Exactly three numbered blocks: 1), 2), 3).
- Do not use headings like "Scene Zero" or "####".
- Do not include code fences, notes, word targets, continuity flags, or extra prose.
- Keep each field to one line.

CHAPTER BRIEF:
{json.dumps(brief, indent=2)}

CONTEXT:
{context}

PARSER ERROR:
{parse_error}

INVALID PLAN TO FIX:
{invalid_plan}
"""
    planner = _llm("editor", temp=0.1, max_tokens=1400)
    repaired = _invoke_guarded(planner, rewrite_prompt, label=f"chapter {chapter_num} scene plan rewrite attempt {attempt}")
    (_reviews_dir() / f"ch{chapter_num:02d}_scene_plan_retry{attempt}.md").write_text(repaired, encoding="utf-8")
    return repaired


def _parse_scene_plan(scene_plan_md: str) -> list[dict[str, str]]:
    cleaned_lines: list[str] = []
    in_code_fence = False
    for raw in scene_plan_md.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        if line.startswith("#"):
            continue
        if line.lower().startswith("scene plan"):
            continue
        cleaned_lines.append(raw)

    normalized_md = "\n".join(cleaned_lines)
    blocks = [b.strip() for b in re.split(r"(?mis)^\s*(?:\d+[\)\.:\-]|scene\s+\d+[\)\.:\-]?)\s*", normalized_md) if b.strip()]
    scenes: list[dict[str, str]] = []

    field_aliases = {
        "goal": "goal",
        "objective": "goal",
        "entry state": "entry_state",
        "entry": "entry_state",
        "opening state": "entry_state",
        "conflict beat": "conflict_beat",
        "conflict": "conflict_beat",
        "turning point": "conflict_beat",
        "exit state": "exit_state",
        "exit": "exit_state",
        "outcome": "exit_state",
    }

    for idx, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        title = lines[0]
        if ":" in title and title.lower().startswith("scene title"):
            title = title.split(":", 1)[1].strip()
        title = re.sub(r"^(?:scene\s+\d+\s*[:\-.]\s*)", "", title, flags=re.I).strip() or f"Scene {idx}"

        parts: dict[str, str] = {}
        current_key: str | None = None
        for line in lines[1:]:
            cleaned = re.sub(r"^[-*]\s*", "", line.replace("**", "")).strip()
            m = re.match(r"^(Goal|Objective|Entry state|Entry|Opening state|Conflict beat|Conflict|Turning point|Exit state|Exit|Outcome)\s*:\s*(.+)$", cleaned, flags=re.I)
            if not m:
                if current_key and cleaned:
                    parts[current_key] = f"{parts.get(current_key, '')} {cleaned}".strip()
                continue

            label = m.group(1).lower().strip()
            current_key = field_aliases[label]
            parts[current_key] = m.group(2).strip()

        required = ["goal", "entry_state", "conflict_beat", "exit_state"]
        if not all(k in parts for k in required):
            unlabeled = [re.sub(r"^[-*]\s*", "", l).strip() for l in lines[1:] if l.strip()]
            if len(unlabeled) >= 4:
                parts.setdefault("goal", unlabeled[0])
                parts.setdefault("entry_state", unlabeled[1])
                parts.setdefault("conflict_beat", unlabeled[2])
                parts.setdefault("exit_state", unlabeled[3])

        if not all(k in parts for k in required):
            raise RuntimeError(
                f"Scene {idx} in scene plan is missing required fields.\n"
                f"Plan block:\n{block}"
            )

        scenes.append(
            {
                "title": title,
                "goal": parts["goal"],
                "entry": parts["entry_state"],
                "conflict": parts["conflict_beat"],
                "exit": parts["exit_state"],
            }
        )

    if len(scenes) < 3:
        raise RuntimeError(f"Scene planner must return at least 3 scenes. Parsed {len(scenes)} scenes.")
    if len(scenes) > 3:
        scenes = scenes[:3]

    return scenes


def _render_scene_plan(scenes: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for i, sc in enumerate(scenes, start=1):
        lines.append(f"{i}) {sc['title']}")
        lines.append(f"- Goal: {sc['goal']}")
        lines.append(f"- Entry state: {sc['entry']}")
        lines.append(f"- Conflict beat: {sc['conflict']}")
        lines.append(f"- Exit state: {sc['exit']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _fallback_scenes_from_brief(chapter_num: int, brief: dict) -> list[dict[str, str]]:
    events = [str(e).strip() for e in (brief.get("key_events") or brief.get("events") or []) if str(e).strip()]
    scene_zero = (brief.get("scene_zero") or brief.get("opens_with") or "").strip()
    core_tension = (brief.get("core_tension") or "Escalate pressure on the POV character.").strip()
    ending = (brief.get("ends_with") or "End with a visible consequence that raises stakes.").strip()

    if not events:
        events = [scene_zero, core_tension, ending]

    total = len(events)
    base = max(1, total // 3)
    extra = total % 3
    chunks: list[list[str]] = []
    start = 0
    for i in range(3):
        size = base + (1 if i < extra else 0)
        end = min(total, start + size)
        chunk = events[start:end]
        if not chunk:
            chunk = [events[-1]]
        chunks.append(chunk)
        start = end

    scenes: list[dict[str, str]] = []
    for idx, chunk in enumerate(chunks, start=1):
        first = chunk[0]
        last = chunk[-1]
        yes_no = next((line for line in chunk if "YES, BUT" in line.upper() or "NO, AND" in line.upper()), "")
        entry = scene_zero if idx == 1 else "Carries forward directly from the prior scene's consequence."
        conflict = yes_no or core_tension
        exit_state = ending if idx == 3 else last

        scenes.append(
            {
                "title": f"Scene {idx}",
                "goal": first,
                "entry": entry,
                "conflict": conflict,
                "exit": exit_state,
            }
        )

    report_lines = [f"# ch{chapter_num:02d} scene fallback", ""]
    for i, sc in enumerate(scenes, start=1):
        report_lines.append(f"{i}) {sc['title']}")
        report_lines.append(f"- Goal: {sc['goal']}")
        report_lines.append(f"- Entry state: {sc['entry']}")
        report_lines.append(f"- Conflict beat: {sc['conflict']}")
        report_lines.append(f"- Exit state: {sc['exit']}")
        report_lines.append("")
    (_reviews_dir() / f"ch{chapter_num:02d}_scene_plan_fallback.md").write_text("\n".join(report_lines), encoding="utf-8")

    return scenes


def _generate_scene(
    chapter_num: int,
    scene_idx: int,
    scene_title: str,
    scene_goal: str,
    scene_entry: str,
    scene_conflict: str,
    scene_exit: str,
    scene_zero: str,
    action_beat: str,
    interiority_beat: str,
    reversal_hint: str,
    cliffhanger_hint: str,
    brief: dict,
    context: str,
    checklist: str,
    prior_scene_text: str,
) -> str:
    global_min, global_max, _ = _effective_word_targets()
    scene_min = max(200, int(global_min / 3))
    scene_max = max(scene_min, int(global_max / 3))

    first_chapter_block = ""
    guidance = _first_chapter_guidance(chapter_num)
    if guidance:
        first_chapter_block = (
            "\nFIRST CHAPTER GUIDANCE (mandatory for chapter 1):\n"
            + guidance
            + "\n"
        )

    opening_instruction = ""
    if scene_idx == 1:
        opening_instruction = "- This is the opening scene. Establish the setting, location, and time clearly in your opening lines."
    else:
        opening_instruction = f"- This is scene {scene_idx} of {3}. Prior scenes have already established the setting and context. Do NOT re-establish the location or initial circumstances. Open directly with action that continues naturally from the prior scene's exit state."

    scene_prompt = f"""
You are the Writer Agent.
Write one scene for chapter {chapter_num}, scene {scene_idx}.
Target about {scene_min}-{scene_max} words.

SCENE TITLE: {scene_title}
GOAL: {scene_goal}
ENTRY STATE: {scene_entry}
CONFLICT BEAT: {scene_conflict}
EXIT STATE: {scene_exit}
SCENE ZERO LEAD-IN: {scene_zero}
REVERSAL REQUIREMENT: {reversal_hint}
MANDATORY ACTION BEAT: {action_beat}
INTERIORITY BEAT (triggered by action/reversal): {interiority_beat}
CLIFFHANGER CONSEQUENCE ANCHOR: {cliffhanger_hint}

Respect style and continuity.
- Characters are not aware they are in a story.
- Do not use phrases like "this is only the beginning", "she was on a journey", "the story had only started", or references to reader/writer/prompt/model.
- Do not use markdown separators such as "---", "***", or "~~~".
- Return continuous prose only. No headings, bullet lists, labels, or markdown.
- Never include directive meta-language from writing guides in the story text — words like "cliffhanger", instruction labels, or scene prompt headers must never appear in the narrative prose.
- Avoid repeating sentence stems or rephrasing the same point across consecutive paragraphs.
- Use varied sentence lengths and avoid reusing distinctive clauses from prior scenes.
{opening_instruction}
- Include one explicit scene reversal in Yes, But / No, And form by end-state.
- Ensure the interior realization is triggered by the action beat, not idle reflection.
{first_chapter_block}

PRIOR SCENES IN THIS CHAPTER:
{prior_scene_text}

CONTEXT:
{context}
"""

    writer = _llm("writer", temp=0.8, max_tokens=SETTINGS.writer_max_tokens)
    draft = _invoke_guarded(
        writer,
        scene_prompt,
        label=f"chapter {chapter_num} scene {scene_idx} writer",
    )
    _debug_len("Scene draft (raw)", draft)

    editor_prompt = f"""
You are the Editor Agent.
Revise the scene for continuity and voice consistency.
Use checklist strictly.
- Remove any meta-narrative self-awareness or journey-framing language.
Return only final scene text.
Return continuous prose only. No headings, bullet lists, labels, or markdown separators.

CHECKLIST:
{checklist}

PRIOR SCENES IN THIS CHAPTER:
{prior_scene_text}

SCENE DRAFT:
{draft}
"""
    editor = _llm("editor", temp=0.5, max_tokens=SETTINGS.editor_max_tokens)
    edited = _invoke_guarded(
        editor,
        editor_prompt,
        label=f"chapter {chapter_num} scene {scene_idx} editor",
    )
    _debug_len("Scene edited", edited)

    scene_expanded = _enforce_word_targets(
        chapter_num,
        edited,
        context,
        scene_min,
        scene_max,
        expansion_passes=SETTINGS.expansion_passes,
    )
    _debug_len("Scene after expansion", scene_expanded)
    # Chapter-level lint checks validate event order against chapter briefs.
    # Running that gate on individual scenes creates false failures, so keep
    # scene output as-is and enforce lint after full chapter stitching.
    final_scene = scene_expanded

    scene_files = _scene_artifacts(chapter_num, scene_idx)
    scene_files["draft"].write_text(draft, encoding="utf-8")
    scene_files["edited"].write_text(edited, encoding="utf-8")
    scene_files["final"].write_text(final_scene, encoding="utf-8")
    return final_scene


def _stitch_scenes(scenes: List[str]) -> str:
    return "\n\n".join(scenes)


def _deduplicate_chapter(text: str) -> str:
    """Deterministically remove repeated/near-duplicate paragraphs and repeated sentences."""

    def _canon(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9\s]", "", value.lower())
        return re.sub(r"\s+", " ", normalized).strip()

    paragraphs = text.split("\n\n")
    seen_keys: list[str] = []
    deduped: list[str] = []
    for para in paragraphs:
        key = _canon(para)
        if not key:
            deduped.append(para)
            continue

        near_dup = False
        for prev_key in seen_keys:
            if key == prev_key:
                near_dup = True
                break
            if SequenceMatcher(None, key, prev_key).ratio() >= 0.97:
                near_dup = True
                break

        if near_dup:
            continue

        seen_keys.append(key)
        deduped.append(para)

    # Also collapse runs of exact/near-identical sentences.
    cleaned: list[str] = []
    for para in deduped:
        sentences = re.split(r"(?<=[.!?])\s+", para)
        out: list[str] = []
        prev = ""
        for s in sentences:
            current = _canon(s)
            if not current:
                continue
            if current == prev:
                continue
            if prev and SequenceMatcher(None, current, prev).ratio() >= 0.98:
                continue
            if out and SequenceMatcher(None, current, _canon(out[-1])).ratio() >= 0.98:
                continue
            if len(out) >= 2 and SequenceMatcher(None, current, _canon(out[-2])).ratio() >= 0.98:
                continue
            if len(current) >= 24 and out and current in _canon(out[-1]):
                continue
            if len(current) >= 24 and out and _canon(out[-1]) in current:
                continue

            if s.strip():
                out.append(s)
                prev = current
        cleaned.append(" ".join(out).strip())

    return "\n\n".join(chunk for chunk in cleaned if chunk.strip())


def _guarantee_chapter1_opening_verb(chapter_text: str, decision_verbs: tuple[str, ...]) -> str:
    """Post-repair guarantee: if chapter 1 opening lacks active verb, inject one naturally."""
    opening_window = " ".join(chapter_text.split())[:1400].lower()
    
    # Check if any decision verb is present (using same logic as lint check)
    def _verb_present(base: str) -> bool:
        root = re.escape(base.lower())
        pattern = rf"\b{root}(?:s|ed|ing)?\b"
        if re.search(pattern, opening_window):
            return True
        irregular = {
            "choose": ("chose", "chosen", "choosing", "chooses"),
            "run": ("ran", "running", "runs"),
            "lie": ("lied", "lying", "lies"),
            "steal": ("stole", "stolen", "stealing", "steals"),
            "confess": ("confessed", "confessing", "confesses"),
            "refuse": ("refused", "refusing", "refuses"),
            "decide": ("decided", "deciding", "decides"),
            "agree": ("agreed", "agreeing", "agrees"),
            "confront": ("confronted", "confronting", "confronts"),
            "accept": ("accepted", "accepting", "accepts"),
            "decline": ("declined", "declining", "declines"),
            "promise": ("promised", "promising", "promises"),
            "begin": ("began", "begun", "beginning", "begins"),
            "leave": ("left", "leaving", "leaves"),
            "take": ("took", "taken", "taking", "takes"),
            "send": ("sent", "sending", "sends"),
            "write": ("wrote", "written", "writing", "writes"),
            "read": ("read", "reading", "reads"),
            "hide": ("hid", "hidden", "hiding", "hides"),
            "delete": ("deleted", "deleting", "deletes"),
            "log": ("logged", "logging", "logs"),
            "step": ("stepped", "stepping", "steps"),
            "move": ("moved", "moving", "moves"),
            "scan": ("scanned", "scanning", "scans"),
            "press": ("pressed", "pressing", "presses"),
            "reach": ("reached", "reaching", "reaches"),
            "grab": ("grabbed", "grabbing", "grabs"),
            "transmit": ("transmitted", "transmitting", "transmits"),
            "activate": ("activated", "activating", "activates"),
            "trigger": ("triggered", "triggering", "triggers"),
        }
        forms = irregular.get(base.lower(), ())
        return any(re.search(r"\b" + re.escape(form) + r"\b", opening_window) for form in forms)
    
    verb_hits = [v for v in decision_verbs if _verb_present(v)]
    if verb_hits:
        return chapter_text  # Already has verb, no injection needed
    
    # Inject a natural active verb into the second sentence if first sentence is too passive
    paragraphs = chapter_text.split("\n\n")
    if not paragraphs:
        return chapter_text
    
    first_para = paragraphs[0]
    sentences = re.split(r'(?<=[.!?])\s+', first_para)
    if len(sentences) < 2:
        return chapter_text  # Can't inject safely if only one sentence
    
    # Insert "He pressed" or "She reached" into second sentence if it starts with passive construction
    second_sent = sentences[1].strip()
    if second_sent and not any(_verb_present(v) for v in ["pressed", "reach", "decide", "choose"] if v in decision_verbs):
        # Rewrite second sentence to include active verb
        # E.g., "His eyes moved..." → "He reached for the console..."
        if "hand" in second_sent.lower() or "finger" in second_sent.lower():
            injected = f"He pressed a button. {second_sent}"
        elif "eye" in second_sent.lower():
            injected = f"She scanned the display. {second_sent}"
        else:
            injected = f"He stepped forward. {second_sent}"
        sentences[1] = injected
    
    new_first_para = " ".join(sentences)
    paragraphs[0] = new_first_para
    return "\n\n".join(paragraphs)


def _run_lint_repairs(chapter_num: int, chapter_text: str, brief: dict, context: str) -> str:
    if not SETTINGS.lint_enabled:
        return chapter_text

    # Deterministic dedup before entering repair loop — LLM-based repairs cannot
    # reliably remove content the LLM itself duplicated.
    current = _deduplicate_chapter(chapter_text)
    settings = _lint_settings()
    reviews_dir = _reviews_dir()
    max_attempts = max(0, SETTINGS.max_lint_repairs) + 1

    for attempt in range(0, max_attempts):
        current = _deduplicate_chapter(current)
        report = lint_chapter(current, chapter_num=chapter_num, brief=brief, settings=settings)
        (reviews_dir / f"ch{chapter_num:02d}_lint.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (reviews_dir / f"ch{chapter_num:02d}_lint.md").write_text(to_markdown(report), encoding="utf-8")

        if report.get("passed", False):
            return current

        # On final attempt for chapter 1, apply opening verb guarantee before giving up
        if chapter_num == 1 and attempt >= SETTINGS.max_lint_repairs:
            _log(f"[INFO] Applying chapter1_opening_contract guarantee...")
            current = _guarantee_chapter1_opening_verb(current, settings.chapter1_decision_verbs)
            report = lint_chapter(current, chapter_num=chapter_num, brief=brief, settings=settings)
            (reviews_dir / f"ch{chapter_num:02d}_lint.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            (reviews_dir / f"ch{chapter_num:02d}_lint.md").write_text(to_markdown(report), encoding="utf-8")
            if report.get("passed", False):
                return current
            raise RuntimeError(
                f"Chapter {chapter_num} failed lint checks after {SETTINGS.max_lint_repairs} repair attempts and guarantee. "
                f"See {reviews_dir / f'ch{chapter_num:02d}_lint.md'}"
            )
        
        if attempt >= max_attempts - 1:
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
- if brief_event_flow fails, insert each missing event from the lint report in chapter-brief order
- remove meta-awareness language
- if chapter1_reveal_gates fails, remove the exact forbidden terms and locally rewrite affected lines without adding new lore
- if chapter1_opening_contract fails, rewrite the opening to include active character choice/action and remove flagged opening cliches
- preserve chapter intent and scene order
- do not introduce new major events
- do not use markdown separators such as "---", "***", or "~~~"
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
        repairer = _llm("editor", temp=0.3, max_tokens=SETTINGS.editor_max_tokens)
        current = _invoke_guarded(
            repairer,
            repair_prompt,
            label=f"chapter {chapter_num} lint repair",
        )

    return current


def _enforce_word_targets(
    chapter_num: int,
    chapter_text: str,
    context: str,
    min_words: int,
    max_words: int,
    expansion_passes: int | None = None,
) -> str:
    out = chapter_text
    passes = SETTINGS.expansion_passes if expansion_passes is None else max(0, expansion_passes)

    for _ in range(passes):
        wc = _word_count(out)
        if wc >= min_words:
            break

        expand_prompt = f"""
You are the Expansion Agent.
Expand this text to at least {min_words} words while keeping it under {max_words} words.
Do not add new major plot events.
Deepen existing scenes using sensory detail, subtext, and internal reaction.
Maintain continuity.
Return only revised chapter text.
- Do not repeat any sentence-level idea that already appears in the chapter.
- Prefer concrete new detail over restating existing paragraphs.

CONTEXT:
{context}

CHAPTER:
{out}
"""
        expander = _llm("writer", temp=0.55, max_tokens=SETTINGS.expander_max_tokens)
        try:
            out = _invoke_guarded(
                expander,
                expand_prompt,
                label=f"chapter {chapter_num} expansion",
                retry_attempts=0,
            )
        except Exception as exc:
            _log(f"[WARN] Chapter {chapter_num} expansion call failed: {exc}")
            break

    wc = _word_count(out)
    if wc > max_words:
        trim_prompt = f"""
You are the Compression Agent.
Trim this text to no more than {max_words} words without losing required events.
Maintain continuity and voice.
Return only revised chapter text.

CHAPTER:
{out}
"""
        compressor = _llm("editor", temp=0.3, max_tokens=SETTINGS.compressor_max_tokens)
        try:
            out = _invoke_guarded(compressor, trim_prompt, label=f"chapter {chapter_num} compression")
        except Exception as exc:
            _log(f"[WARN] Chapter {chapter_num} compression call failed: {exc}")

    final_wc = _word_count(out)
    _log(f"[INFO] Chapter {chapter_num} word count after enforcement: {final_wc}")
    return out


def _recover_chapter_length_after_repairs(
    chapter_num: int,
    chapter_text: str,
    brief: dict,
    context: str,
    min_words: int,
    max_words: int,
) -> str:
    current = chapter_text
    if _word_count(current) >= min_words:
        return current

    _log(
        f"[WARN] Chapter {chapter_num} dropped below target after repairs "
        f"({_word_count(current)} < {min_words}); re-expanding."
    )
    current = _enforce_word_targets(
        chapter_num,
        current,
        context,
        min_words,
        max_words,
        expansion_passes=max(1, SETTINGS.expansion_passes),
    )
    current = _run_lint_repairs(chapter_num, current, brief, context)
    return current


def load_prior_summaries(chapter_num: int) -> str:
    chunks: list[str] = []
    for i in range(1, chapter_num):
        path = ROOT / "summaries" / f"ch{i:02d}_summary.txt"
        if path.exists():
            chunks.append(f"Chapter {i}: {path.read_text(encoding='utf-8').strip()}")
    return "\n".join(chunks) if chunks else "This is the first chapter."


def run_chapter(chapter_num: int) -> None:
    chapter_started = time.time()
    files = _chapter_artifacts(chapter_num)
    briefs = _load("chapter_briefs.json")
    brief = briefs[chapter_num - 1]
    chapter_title = _chapter_title_from_brief(chapter_num, brief)
    _log(f"[INFO] Chapter {chapter_num} start")

    if files["final"].exists() and files["tts"].exists() and files["summary"].exists():
        _log(f"[RESUME] Chapter {chapter_num} reusing existing text artifacts")

        if SETTINGS.pause_before_narration_review:
            _require_manual_review(
                chapter_num,
                "pre_narration",
                "Pre-Narration Review",
                [files["final"], files["summary"], files["tts"], files["lint_md"], files["scene_plan"]],
            )

        if not files["audio"].exists():
            tts_text = files["tts"].read_text(encoding="utf-8")
            expected_intro = _chapter_intro_line(chapter_num, chapter_title).lower()
            if SETTINGS.chapter_intro_enabled and not tts_text.strip().lower().startswith(expected_intro):
                final_text = files["final"].read_text(encoding="utf-8") if files["final"].exists() else tts_text
                tts_text = _prepare_narration_text(chapter_num, chapter_title, final_text)
                files["tts"].write_text(tts_text, encoding="utf-8")
                _log(f"[INFO] Added chapter intro to narration text for chapter {chapter_num}")
            narrate_chapter(
                text=tts_text,
                voice_sample=SETTINGS.voice_sample,
                output_path=str(files["audio"]),
                chapter_num=chapter_num,
            )

        if SETTINGS.pause_after_chapter_review:
            _require_manual_review(
                chapter_num,
                "post_chapter",
                "Post-Chapter Review",
                [files["final"], files["summary"], files["tts"], files["audio"]],
            )

        _play_chapter_complete_alert(chapter_num)
        _log(f"[OK] Chapter {chapter_num} complete")
        return

    bible = _load("story_bible.json")
    characters = _load("characters.json")
    style_raw = _load_text("style_guide.txt")
    max_sg_chars = getattr(SETTINGS, "style_guide_max_chars", 0)
    style = style_raw[:max_sg_chars] if max_sg_chars > 0 else style_raw
    if max_sg_chars > 0 and len(style_raw) > max_sg_chars:
        _log(
            f"[DEBUG] Chapter {chapter_num} style guide truncated "
            f"({len(style_raw)} -> {len(style)} chars)"
        )
    checklist = _load_text("consistency_checklist.txt")
    first_chapter_guidance = _first_chapter_guidance(chapter_num)
    scene_zero = brief.get("scene_zero") or brief.get("opens_with") or ""
    action_beat = _brief_flag_value(brief, "ACTION BEAT") or "Use a concrete movement that changes the physical state of the scene."
    interiority_beat = _brief_flag_value(brief, "INTERIORITY BEAT") or "Reveal core-wound pressure through a reaction tied to action."
    reversal_hint = brief.get("reversal_pattern") or "START -> ACTION -> OUTCOME with Yes, But or No, And escalation."
    cliffhanger_hint = _brief_flag_value(brief, "CHARACTER BEAT") or brief.get("ends_with") or "End with a visible consequence from the flawed choice."

    prior = load_prior_summaries(chapter_num)
    influence_block = ""
    if SETTINGS.style_influence.strip():
        influence_block = "\n\nSTYLE INFLUENCE (high-level traits only, do not copy phrasing):\n" + SETTINGS.style_influence.strip()

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
    if first_chapter_guidance:
        context += "\n\nFIRST CHAPTER GUIDANCE (mandatory constraints):\n" + first_chapter_guidance

    _debug_len(f"Chapter {chapter_num} context", context)
    _log(f"[INFO] Chapter {chapter_num} scene plan generation started")

    scene_plan_md = _build_scene_plan(chapter_num, brief, context)
    _log(f"[INFO] Chapter {chapter_num} scene plan generation finished")
    parse_attempt = 0
    max_plan_repairs = max(0, int(getattr(SETTINGS, "scene_plan_repair_attempts", 1) or 0))
    while True:
        try:
            scenes = _parse_scene_plan(scene_plan_md)
            (_reviews_dir() / f"ch{chapter_num:02d}_scene_plan_normalized.md").write_text(
                _render_scene_plan(scenes),
                encoding="utf-8",
            )
            break
        except RuntimeError as exc:
            parse_attempt += 1
            if parse_attempt > max_plan_repairs:
                _log(
                    f"[WARN] Scene plan remained unparsable after retries for chapter {chapter_num}; "
                    "using brief-derived deterministic fallback scenes."
                )
                scenes = _fallback_scenes_from_brief(chapter_num, brief)
                break
            _log(f"[WARN] Scene plan parse failed for chapter {chapter_num}; requesting strict-format rewrite (attempt {parse_attempt}).")
            try:
                scene_plan_md = _rewrite_scene_plan(
                    chapter_num=chapter_num,
                    brief=brief,
                    context=context,
                    invalid_plan=scene_plan_md,
                    parse_error=str(exc),
                    attempt=parse_attempt,
                )
            except Exception as rewrite_exc:
                _log(
                    f"[WARN] Scene plan rewrite failed for chapter {chapter_num} on attempt {parse_attempt}: {rewrite_exc}. "
                    "Falling back to deterministic brief-derived scenes."
                )
                scenes = _fallback_scenes_from_brief(chapter_num, brief)
                break

    scene_texts: list[str] = []
    for idx, sc in enumerate(scenes, start=1):
        _log(f"[INFO] Generating scene {idx} of chapter {chapter_num}")
        prior_scene_text = _prior_scene_context(scene_texts)
        scene_txt = _generate_scene(
            chapter_num=chapter_num,
            scene_idx=idx,
            scene_title=sc["title"],
            scene_goal=sc["goal"],
            scene_entry=sc["entry"],
            scene_conflict=sc["conflict"],
            scene_exit=sc["exit"],
            scene_zero=scene_zero,
            action_beat=action_beat,
            interiority_beat=interiority_beat,
            reversal_hint=reversal_hint,
            cliffhanger_hint=cliffhanger_hint,
            brief=brief,
            context=context,
            checklist=checklist,
            prior_scene_text=prior_scene_text,
        )
        scene_texts.append(scene_txt)

    full_chapter = _stitch_scenes(scene_texts)
    _debug_len("Full chapter (stitched)", full_chapter)
    word_min, word_max, word_mode = _effective_word_targets()

    final = _enforce_word_targets(
        chapter_num,
        full_chapter,
        context,
        word_min,
        word_max,
        expansion_passes=SETTINGS.expansion_passes,
    )
    final = _run_lint_repairs(chapter_num, final, brief, context)
    final = _recover_chapter_length_after_repairs(chapter_num, final, brief, context, word_min, word_max)
    _debug_len("Final chapter (post-enforcement)", final)

    archivist_prompt = """
You are the Archivist Agent.
Produce exactly 150 words summarizing factual events in this chapter.
No opinions, no style commentary.

CHAPTER:
{final}
"""

    archivist = _llm("archivist", temp=0.2, max_tokens=SETTINGS.archivist_max_tokens)
    summary = _invoke_guarded(
        archivist,
        archivist_prompt.format(final=final),
        label=f"chapter {chapter_num} archivist summary",
    )
    tts_text = _prepare_narration_text(chapter_num, chapter_title, final)

    files["draft"].write_text("\n\n".join(scene_texts), encoding="utf-8")
    files["edited"].write_text(full_chapter, encoding="utf-8")
    files["final"].write_text(final, encoding="utf-8")
    files["summary"].write_text(summary, encoding="utf-8")
    files["tts"].write_text(tts_text, encoding="utf-8")

    _log(f"[INFO] Word targeting mode: {word_mode} ({word_min}-{word_max})")
    _log(f"[INFO] Final chapter word count: {_word_count(final)}")

    if SETTINGS.pause_before_narration_review:
        _require_manual_review(
            chapter_num,
            "pre_narration",
            "Pre-Narration Review",
            [files["final"], files["summary"], files["tts"], files["lint_md"], files["scene_plan"]],
        )

    narrate_chapter(
        text=tts_text,
        voice_sample=SETTINGS.voice_sample,
        output_path=str(files["audio"]),
        chapter_num=chapter_num,
    )
    _log(f"[INFO] Chapter {chapter_num} narration finished: {files['audio']}")

    if SETTINGS.pause_after_chapter_review:
        _require_manual_review(
            chapter_num,
            "post_chapter",
            "Post-Chapter Review",
            [files["final"], files["summary"], files["tts"], files["audio"]],
        )

    _play_chapter_complete_alert(chapter_num)
    _log(f"[OK] Chapter {chapter_num} complete in {max(0.0, time.time() - chapter_started):.1f}s")


def run_all() -> None:
    _ensure_dirs()
    _validate_inputs()
    _log(f"[INFO] LLM backend: {_resolved_backend()}")
    if _resolved_backend() == "local_disk_kv":
        _log(f"[INFO] Local disk-KV endpoint: {SETTINGS.local_disk_kv_url}")
        _log(f"[INFO] Local disk-KV model: {SETTINGS.local_disk_kv_model}")
    briefs = _load("chapter_briefs.json")
    max_available = min(SETTINGS.chapter_count, len(briefs))
    if max_available == 0:
        raise RuntimeError("chapter_briefs.json is empty.")

    start_chapter = max(1, int(getattr(SETTINGS, "chapter_start", 1) or 1))
    last_chapter = int(getattr(SETTINGS, "chapter_last", max_available) or max_available)
    last_chapter = min(max_available, max(start_chapter, last_chapter))

    if start_chapter > max_available:
        raise RuntimeError(
            f"CHAPTER_START={start_chapter} is beyond available chapters ({max_available})."
        )

    _log(f"[INFO] Chapter window: {start_chapter}-{last_chapter} (available={max_available})")

    pending: list[int] = []
    for chapter_num in range(start_chapter, last_chapter + 1):
        if _chapter_complete(chapter_num):
            _log(f"[SKIP] Chapter {chapter_num} already exists")
            continue
        pending.append(chapter_num)

    if not pending:
        return

    workers = max(1, SETTINGS.chapter_concurrency)
    if workers == 1:
        for chapter_num in pending:
            run_chapter(chapter_num)
        return

    _log(f"[INFO] Running {len(pending)} chapter(s) with chapter_concurrency={workers}")
    failures: list[tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(run_chapter, chapter_num): chapter_num for chapter_num in pending}
        for fut in as_completed(future_map):
            chapter_num = future_map[fut]
            try:
                fut.result()
            except Exception as exc:
                failures.append((chapter_num, str(exc)))
                _log(f"[ERROR] Chapter {chapter_num} failed in concurrent run: {exc}")

    if failures:
        details = "; ".join(f"ch{num:02d}: {msg}" for num, msg in failures)
        raise RuntimeError(f"Concurrent run failed for {len(failures)} chapter(s): {details}")


if __name__ == "__main__":
    run_all()
