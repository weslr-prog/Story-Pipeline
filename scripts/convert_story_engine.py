#!/usr/bin/env python3
import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Inputs:
    dna: Path
    bible: Path
    blueprint: Path
    out_dir: Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_after_label(text: str, label: str) -> str:
    pattern = rf"(?im)^\s*(?:[\-*•]\s*)?{re.escape(label)}\s*:\s*(.+)$"
    m = re.search(pattern, text)
    return _clean(m.group(1)) if m else ""


def _extract_block(text: str, start_pat: str, end_pats: list[str]) -> str:
    start = re.search(start_pat, text, flags=re.IGNORECASE | re.MULTILINE)
    if not start:
        return ""
    start_idx = start.end()
    end_idx = len(text)
    for pat in end_pats:
        m = re.search(pat, text[start_idx:], flags=re.IGNORECASE | re.MULTILINE)
        if m:
            end_idx = min(end_idx, start_idx + m.start())
    return text[start_idx:end_idx].strip()


def _extract_list_items(block: str) -> list[str]:
    out: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        line = re.sub(r"^[\-*•]+\s*", "", line)
        line = re.sub(r"^\d+[\.)]\s*", "", line)
        if line:
            out.append(_clean(line))
    return out


def _parse_title_candidates(text: str) -> list[str]:
    block = _extract_block(
        text,
        r"(?im)^\s*Title\s+idea\s*:",
        [r"(?im)^\s*Genre\s*:", r"(?im)^\s*Time\s+period\s*:"],
    )
    items = _extract_list_items(block)
    return items[:3]


def _parse_story_dna(text: str) -> dict[str, Any]:
    return {
        "title_candidates": _parse_title_candidates(text),
        "genre": _extract_after_label(text, "Genre"),
        "time_period": _extract_after_label(text, "Time period"),
        "central_conflict": _extract_after_label(text, "Central conflict (one sentence)"),
        "emotional_core": _extract_after_label(text, "Emotional core"),
        "logline": _extract_after_label(text, "LOGLINE"),
    }


def _parse_characters_from_bible(text: str) -> list[dict[str, Any]]:
    roster_block = _extract_block(
        text,
        r"(?im)^\s*2\.\s*CHARACTER\s+ROSTER",
        [r"(?im)^\s*3\.\s*TONE\s+AND\s+STYLE\s+RULES", r"(?im)^\s*4\.\s*THEME\s+STATEMENT"],
    )
    if not roster_block:
        return []

    chunks = re.split(r"(?m)^_+\s*$", roster_block)
    characters: list[dict[str, Any]] = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue

        header = ""
        for ln in lines:
            if ln.startswith("*") or ln.startswith("-"):
                continue
            header = ln
            break
        if not header:
            continue

        hm = re.match(r"^(.+?)\s*\(([^\)]+)\)\s*$", header)
        if hm:
            name = _clean(hm.group(1))
            age_raw = _clean(hm.group(2))
        else:
            name = _clean(header)
            age_raw = "unknown"

        role = _extract_after_label(chunk, "Role")
        profile = _extract_after_label(chunk, "Profile")
        voice = _extract_after_label(chunk, "Voice note")

        age_or_range: Any
        if re.fullmatch(r"\d+", age_raw):
            age_or_range = int(age_raw)
        else:
            age_or_range = age_raw

        characters.append(
            {
                "name": name,
                "role": role or "Supporting",
                "age_or_range": age_or_range,
                "short_description": profile or "",
                "voice_style": voice or "",
            }
        )

    return characters


def _parse_writing_rules(text: str) -> list[str]:
    block = _extract_block(
        text,
        r"(?im)^\s*3\.\s*TONE\s+AND\s+STYLE\s+RULES",
        [r"(?im)^\s*4\.\s*THEME\s+STATEMENT", r"(?im)^\s*5\.\s*THREE-ACT\s+SKELETON"],
    )
    items = _extract_list_items(block)
    return items[:12]


def _parse_setting_block(text: str) -> str:
    block = _extract_block(
        text,
        r"(?im)^\s*1\.\s*SETTING\s+PROFILE",
        [r"(?im)^\s*2\.\s*CHARACTER\s+ROSTER", r"(?im)^\s*3\.\s*TONE\s+AND\s+STYLE\s+RULES"],
    )
    return _clean(block)


def _guess_place_time(setting_paragraph: str, dna_time: str) -> tuple[str, str]:
    place = ""
    if setting_paragraph:
        place = setting_paragraph.split(".")[0]
    time_val = dna_time or "Present day"
    return place, time_val


def _parse_chapter_outline_from_blueprint(text: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r'(?im)^\s*CHAPTER\s+(\d+)\s*[—-]\s*"?([^"\n]+)"?\s*$', text):
        num = int(m.group(1))
        title = _clean(m.group(2))
        out.append(f"Ch{num} {title}")
    return out


def _parse_story_bible_json(dna_text: str, bible_text: str, blueprint_text: str) -> dict[str, Any]:
    dna = _parse_story_dna(dna_text)

    working_title = _extract_after_label(bible_text, "Working Title")
    title = working_title or (dna["title_candidates"][0] if dna["title_candidates"] else "Untitled Story")

    setting_paragraph = _parse_setting_block(bible_text)
    place, time_val = _guess_place_time(setting_paragraph, dna.get("time_period", ""))

    pov_whose = ""
    chars = _parse_characters_from_bible(bible_text)
    if chars:
        pov_whose = chars[0]["name"]

    writing_rules = _parse_writing_rules(bible_text)
    chapter_outline = _parse_chapter_outline_from_blueprint(blueprint_text)

    themes: list[str] = []
    emotional_core = dna.get("emotional_core", "")
    if emotional_core:
        themes.append(emotional_core)
    theme_statement = _extract_block(
        bible_text,
        r"(?im)^\s*4\.\s*THEME\s+STATEMENT",
        [r"(?im)^\s*5\.\s*THREE-ACT\s+SKELETON"],
    )
    if theme_statement:
        themes.append(_clean(theme_statement))

    world_rules: list[str] = []
    if setting_paragraph:
        world_rules.append(setting_paragraph)
    central_conflict = dna.get("central_conflict", "")
    if central_conflict:
        world_rules.append(central_conflict)

    total_chapters = len(chapter_outline)
    if total_chapters == 0:
        m = re.search(r"(?im)^\s*Chapter\s+count\s*:\s*(\d+)", dna_text)
        if m:
            total_chapters = int(m.group(1))

    return {
        "title": title,
        "genre": dna.get("genre", ""),
        "tone": dna.get("emotional_core", ""),
        "setting": {
            "time": time_val,
            "place": place,
        },
        "pov": {
            "type": "third_person_limited",
            "whose": pov_whose,
        },
        "total_chapters": total_chapters,
        "themes": themes,
        "chapter_outline": chapter_outline,
        "world_rules": world_rules,
        "writing_rules": writing_rules,
    }


def _parse_chapter_briefs(blueprint_text: str) -> list[dict[str, Any]]:
    chapter_matches = list(
        re.finditer(r'(?im)^\s*CHAPTER\s+(\d+)\s*[—-]\s*"?([^"\n]+)"?\s*$', blueprint_text)
    )
    briefs: list[dict[str, Any]] = []

    for idx, m in enumerate(chapter_matches):
        ch_num = int(m.group(1))
        title = _clean(m.group(2))
        start = m.end()
        end = chapter_matches[idx + 1].start() if idx + 1 < len(chapter_matches) else len(blueprint_text)
        block = blueprint_text[start:end]

        pov = _extract_after_label(block, "POV") or _extract_after_label(block, "Pov")
        setting = ""
        s1 = re.search(r"(?im)^\s*[\-*]\s*Scene\s*1\s*:\s*([^\n]+)$", block)
        if s1:
            setting = _clean(s1.group(1).split("—")[0].split("-")[0])

        opens_with = _extract_after_label(block, "CENTRAL QUESTION")
        core_tension = _extract_after_label(block, "CHARACTER BEAT")
        ends_with = _extract_after_label(block, "CLIFFHANGER")

        word_target = 2800
        wm = re.search(r"(?im)^\s*Word\s*target\s*:\s*([0-9][0-9,]*)", block)
        if wm:
            word_target = int(wm.group(1).replace(",", ""))

        key_events: list[str] = []
        scene_block = _extract_block(block, r"(?im)^\s*SCENE\s+BREAKDOWN\s*:", [r"(?im)^\s*THREAD\s+PROGRESS\s*:"])
        if scene_block:
            for item in _extract_list_items(scene_block):
                key_events.append(item)

        continuity_flags: list[str] = []
        for label in ["CHARACTER BEAT", "EMOTIONAL BEAT", "INTERIORITY BEAT", "ACTION BEAT", "Act position"]:
            val = _extract_after_label(block, label)
            if val:
                continuity_flags.append(f"{label}: {val}")

        briefs.append(
            {
                "chapter_number": ch_num,
                "title": title,
                "pov_character": pov,
                "setting": setting,
                "opens_with": opens_with,
                "core_tension": core_tension,
                "key_events": key_events,
                "ends_with": ends_with,
                "word_target": word_target,
                "continuity_flags": continuity_flags,
            }
        )

    return briefs


def _build_conversion_prompt(dna_text: str, bible_text: str, blueprint_text: str) -> str:
    return f"""# Story Engine Conversion Prompt

Convert the three source documents into three JSON files with strict output rules.
Return only valid JSON blocks for each file.

## Output file 1: story_bible.json
Required shape:
{{
  \"title\": \"...\",
  \"genre\": \"...\",
  \"tone\": \"...\",
  \"setting\": {{\"time\": \"...\", \"place\": \"...\"}},
  \"pov\": {{\"type\": \"third_person_limited\", \"whose\": \"...\"}},
  \"total_chapters\": 0,
  \"themes\": [\"...\"],
  \"chapter_outline\": [\"...\"],
  \"world_rules\": [\"...\"],
  \"writing_rules\": [\"...\"]
}}

## Output file 2: characters.json
Array of objects:
[
  {{
    \"name\": \"...\",
    \"role\": \"...\",
    \"age_or_range\": 0,
    \"short_description\": \"...\",
    \"voice_style\": \"...\"
  }}
]

## Output file 3: chapter_briefs.json
Array of chapter objects:
[
  {{
    \"chapter_number\": 1,
    \"title\": \"...\",
    \"pov_character\": \"...\",
    \"setting\": \"...\",
    \"opens_with\": \"...\",
    \"core_tension\": \"...\",
    \"key_events\": [\"...\"],
    \"ends_with\": \"...\",
    \"word_target\": 2800,
    \"continuity_flags\": [\"...\"]
  }}
]

## Source 1: Story DNA Summary
{dna_text}

## Source 2: Story Bible
{bible_text}

## Source 3: Chapter Blueprint
{blueprint_text}
"""


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def convert_rule(inputs: Inputs) -> None:
    dna_text = _read(inputs.dna)
    bible_text = _read(inputs.bible)
    blueprint_text = _read(inputs.blueprint)

    story_bible = _parse_story_bible_json(dna_text, bible_text, blueprint_text)
    characters = _parse_characters_from_bible(bible_text)
    chapter_briefs = _parse_chapter_briefs(blueprint_text)

    _write_json(inputs.out_dir / "story_bible.json", story_bible)
    _write_json(inputs.out_dir / "characters.json", characters)
    _write_json(inputs.out_dir / "chapter_briefs.json", chapter_briefs)


def write_prompt(inputs: Inputs) -> None:
    prompt = _build_conversion_prompt(_read(inputs.dna), _read(inputs.bible), _read(inputs.blueprint))
    out = inputs.out_dir / "story_engine_conversion_prompt.md"
    out.write_text(prompt, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Story Engine text docs into pipeline JSON.")
    parser.add_argument("--dna", default=str(ROOT / "The Gap Protocol" / "Story DNA Summary.txt"))
    parser.add_argument("--bible", default=str(ROOT / "The Gap Protocol" / "Story Bible.txt"))
    parser.add_argument("--blueprint", default=str(ROOT / "The Gap Protocol" / "Chapter Blueprint.txt"))
    parser.add_argument("--out-dir", default=str(ROOT))
    parser.add_argument(
        "--mode",
        choices=["rule", "prompt", "hybrid"],
        default="rule",
        help="rule: deterministic local conversion, prompt: generate LLM conversion prompt, hybrid: both",
    )

    args = parser.parse_args()
    inputs = Inputs(
        dna=Path(args.dna),
        bible=Path(args.bible),
        blueprint=Path(args.blueprint),
        out_dir=Path(args.out_dir),
    )

    for p in [inputs.dna, inputs.bible, inputs.blueprint]:
        if not p.exists():
            raise FileNotFoundError(f"Missing input: {p}")

    inputs.out_dir.mkdir(parents=True, exist_ok=True)

    if args.mode in {"rule", "hybrid"}:
        convert_rule(inputs)
        print(f"[OK] Wrote {inputs.out_dir / 'story_bible.json'}")
        print(f"[OK] Wrote {inputs.out_dir / 'characters.json'}")
        print(f"[OK] Wrote {inputs.out_dir / 'chapter_briefs.json'}")

    if args.mode in {"prompt", "hybrid"}:
        write_prompt(inputs)
        print(f"[OK] Wrote {inputs.out_dir / 'story_engine_conversion_prompt.md'}")


if __name__ == "__main__":
    main()
