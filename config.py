import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    items = [item.strip() for item in raw.split(",")]
    return tuple(item for item in items if item)


@dataclass(frozen=True)
class Settings:
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5:7b")
    writer_model: str = os.getenv("WRITER_MODEL", "")
    editor_model: str = os.getenv("EDITOR_MODEL", "")
    critic_model: str = os.getenv("CRITIC_MODEL", "")
    archivist_model: str = os.getenv("ARCHIVIST_MODEL", "")
    tts_prep_model: str = os.getenv("TTS_PREP_MODEL", "")
    llm_num_ctx: int = _env_int("LLM_NUM_CTX", 4096)

    chatterbox_url: str = os.getenv("CHATTERBOX_URL", "http://127.0.0.1:7860")
    chatterbox_api: str = os.getenv("CHATTERBOX_API", "")

    critic_mode: str = os.getenv("CRITIC_MODE", "local")
    reviews_dir: str = os.getenv("REVIEWS_DIR", "reviews")
    pause_for_external_critic: bool = _env_bool("PAUSE_FOR_EXTERNAL_CRITIC", True)
    pause_before_narration_review: bool = _env_bool("PAUSE_BEFORE_NARRATION_REVIEW", True)
    pause_after_chapter_review: bool = _env_bool("PAUSE_AFTER_CHAPTER_REVIEW", True)

    chapter_count: int = _env_int("CHAPTER_COUNT", 10)
    word_target_min: int = _env_int("WORD_TARGET_MIN", 1800)
    word_target_max: int = _env_int("WORD_TARGET_MAX", 2400)
    target_minutes_min: float = _env_float("TARGET_MINUTES_MIN", 0.0)
    target_minutes_max: float = _env_float("TARGET_MINUTES_MAX", 0.0)
    assumed_wpm: int = _env_int("ASSUMED_WPM", 150)
    expansion_passes: int = _env_int("EXPANSION_PASSES", 1)

    lint_enabled: bool = _env_bool("LINT_ENABLED", True)
    max_lint_repairs: int = _env_int("MAX_LINT_REPAIRS", 1)
    max_duplicate_paragraph_repeats: int = _env_int("MAX_DUPLICATE_PARAGRAPH_REPEATS", 1)
    max_sentence_repeat: int = _env_int("MAX_SENTENCE_REPEAT", 2)
    meta_phrases: tuple[str, ...] = _env_csv(
        "META_PHRASES",
        "this is only the beginning,on a journey,story had only started,dear reader,in this chapter,the author,the writer,prompt,model,ai",
    )
    chapter1_forbidden_terms: tuple[str, ...] = _env_csv(
        "CHAPTER1_FORBIDDEN_TERMS",
        "hidden door,novaBio tracker,tracker lay hidden",
    )

    style_influence: str = os.getenv("STYLE_INFLUENCE", "")

    exaggeration: float = _env_float("EXAGGERATION", 0.40)
    cfg_weight: float = _env_float("CFG_WEIGHT", 0.60)
    temperature: float = _env_float("TEMPERATURE", 0.70)
    silence_pad: float = _env_float("SILENCE_PAD", 0.60)
    sample_rate: int = _env_int("SAMPLE_RATE", 22050)
    request_delay: float = _env_float("REQUEST_DELAY", 1.00)
    max_retries: int = _env_int("MAX_RETRIES", 3)
    retry_backoff: float = _env_float("RETRY_BACKOFF", 1.00)

    voice_sample: str = os.getenv("VOICE_SAMPLE", "voices/narrator.wav")


SETTINGS = Settings()
