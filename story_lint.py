import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any


_META_DEFAULTS = [
    "this is only the beginning",
    "on a journey",
    "story had only started",
    "dear reader",
    "in this chapter",
    "the author",
    "the writer",
    "as a character",
    "prompt",
    "model",
    "ai",
]


@dataclass(frozen=True)
class LintSettings:
    max_duplicate_paragraph_repeats: int = 1
    max_sentence_repeat: int = 2
    meta_phrases: tuple[str, ...] = tuple(_META_DEFAULTS)
    chapter1_forbidden_terms: tuple[str, ...] = (
        "hidden door",
        "novaBio tracker",
        "tracker lay hidden",
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+", text)
    return [c.strip() for c in chunks if len(c.strip()) > 8]


def _check_duplicate_paragraphs(text: str, max_repeat: int) -> dict[str, Any]:
    normalized: list[str] = []
    for paragraph in _paragraphs(text):
        norm = _normalize(paragraph)
        # Ignore punctuation-only separators like "---" and "***".
        if not re.search(r"[a-z0-9]", norm):
            continue
        normalized.append(norm)
    counts = Counter(normalized)
    violations = [{"count": c, "paragraph": p[:260]} for p, c in counts.items() if c > max_repeat]
    return {
        "name": "duplicate_paragraphs",
        "passed": not violations,
        "violations": violations,
    }


def _check_repeated_sentences(text: str, max_repeat: int) -> dict[str, Any]:
    normalized = [_normalize(s) for s in _sentences(text)]
    counts = Counter(normalized)
    violations = [{"count": c, "sentence": s[:220]} for s, c in counts.items() if c > max_repeat]
    return {
        "name": "repeated_sentences",
        "passed": not violations,
        "violations": violations,
    }


def _check_meta_phrases(text: str, phrases: tuple[str, ...]) -> dict[str, Any]:
    lower = text.lower()
    hits: list[str] = []
    for phrase in phrases:
        p = phrase.strip().lower()
        if not p:
            continue
        pattern = r"\b" + re.escape(p) + r"\b"
        if re.search(pattern, lower):
            hits.append(phrase)
    return {
        "name": "meta_awareness",
        "passed": not hits,
        "violations": hits,
    }


def _check_brief_order(text: str, key_events: list[str]) -> dict[str, Any]:
    lower = text.lower()
    cursor = 0
    missing: list[str] = []
    stopwords = {
        "their",
        "there",
        "while",
        "with",
        "from",
        "into",
        "that",
        "this",
        "then",
        "when",
        "like",
        "tied",
        "surface",
        "introduced",
    }

    for ev in key_events:
        tokens = [
            t
            for t in re.findall(r"[a-zA-Z]{4,}", ev.lower())
            if t not in stopwords
        ]
        if not tokens:
            continue

        seen_tokens: list[str] = []
        token_positions: list[int] = []
        for token in tokens:
            if token in seen_tokens:
                continue
            seen_tokens.append(token)
            pos = lower.find(token, cursor)
            if pos != -1:
                token_positions.append(pos)

        required_hits = 1 if len(tokens) <= 3 else 2
        if len(token_positions) < required_hits:
            missing.append(ev)
        else:
            cursor = min(token_positions) + 1

    in_order = not missing
    passed = in_order

    return {
        "name": "brief_event_flow",
        "passed": passed,
        "violations": {
            "missing_events": missing,
            "in_order": in_order,
        },
    }


def _check_chapter1_reveals(text: str, chapter_num: int, forbidden_terms: tuple[str, ...]) -> dict[str, Any]:
    if chapter_num != 1:
        return {"name": "chapter1_reveal_gates", "passed": True, "violations": []}

    lower = text.lower()
    hits: list[str] = []
    for term in forbidden_terms:
        clean = term.strip()
        if not clean:
            continue
        pattern = r"(?<!\w)" + re.escape(clean.lower()) + r"(?!\w)"
        if re.search(pattern, lower):
            hits.append(term)
    return {
        "name": "chapter1_reveal_gates",
        "passed": not hits,
        "violations": hits,
    }


def lint_chapter(text: str, chapter_num: int, brief: dict[str, Any], settings: LintSettings) -> dict[str, Any]:
    checks = [
        _check_duplicate_paragraphs(text, settings.max_duplicate_paragraph_repeats),
        _check_repeated_sentences(text, settings.max_sentence_repeat),
        _check_meta_phrases(text, settings.meta_phrases),
        _check_brief_order(text, brief.get("key_events", [])),
        _check_chapter1_reveals(text, chapter_num, settings.chapter1_forbidden_terms),
    ]

    passed = all(c.get("passed", False) for c in checks)
    return {
        "passed": passed,
        "checks": checks,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = ["# Chapter Lint Report", "", f"Passed: {report.get('passed', False)}", ""]
    for chk in report.get("checks", []):
        lines.append(f"## {chk.get('name')}")
        lines.append(f"- Passed: {chk.get('passed')}")
        lines.append(f"- Violations: {json.dumps(chk.get('violations', []), ensure_ascii=True)}")
        lines.append("")
    return "\n".join(lines)
