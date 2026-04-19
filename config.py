import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
# .env is authoritative. Shell env vars are overridden so stale session exports
# (e.g. LOCAL_DISK_KV_MODEL from a previous run) don't silently corrupt settings.
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
    llm_backend: str = os.getenv("LLM_BACKEND", "local_disk_kv")
    use_local_disk_kv: bool = _env_bool("USE_LOCAL_DISK_KV", False)
    local_disk_kv_url: str = os.getenv("LOCAL_DISK_KV_URL", "http://127.0.0.1:8080/v1/chat/completions")
    local_disk_kv_model: str = os.getenv("LOCAL_DISK_KV_MODEL", "caiovicentino1/Qwen3.5-9B-HLWQ-MLX-4bit")

    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    llm_model: str = os.getenv("LLM_MODEL", "caiovicentino1/Qwen3.5-9B-HLWQ-MLX-4bit")
    writer_model: str = os.getenv("WRITER_MODEL", "")
    editor_model: str = os.getenv("EDITOR_MODEL", "")
    critic_model: str = os.getenv("CRITIC_MODEL", "")
    archivist_model: str = os.getenv("ARCHIVIST_MODEL", "")
    tts_prep_model: str = os.getenv("TTS_PREP_MODEL", "")
    llm_num_ctx: int = _env_int("LLM_NUM_CTX", 32768)
    llm_repeat_penalty: float = _env_float("LLM_REPEAT_PENALTY", 1.25)
    llm_call_timeout_seconds: int = _env_int("LLM_CALL_TIMEOUT_SECONDS", 240)
    llm_call_retry_attempts: int = _env_int("LLM_CALL_RETRY_ATTEMPTS", 2)
    llm_call_retry_backoff: float = _env_float("LLM_CALL_RETRY_BACKOFF", 3.0)
    llm_concurrency_limit: int = _env_int("LLM_CONCURRENCY_LIMIT", 2)
    llm_min_request_interval_seconds: float = _env_float("LLM_MIN_REQUEST_INTERVAL_SECONDS", 0.20)

    chatterbox_url: str = os.getenv("CHATTERBOX_URL", "http://127.0.0.1:7860")
    chatterbox_api: str = os.getenv("CHATTERBOX_API", "")

    critic_mode: str = os.getenv("CRITIC_MODE", "local")
    reviews_dir: str = os.getenv("REVIEWS_DIR", "reviews")
    pause_for_external_critic: bool = _env_bool("PAUSE_FOR_EXTERNAL_CRITIC", True)
    pause_before_narration_review: bool = _env_bool("PAUSE_BEFORE_NARRATION_REVIEW", True)
    pause_after_chapter_review: bool = _env_bool("PAUSE_AFTER_CHAPTER_REVIEW", True)
    require_prenarration_approval: bool = _env_bool("REQUIRE_PRENARRATION_APPROVAL", False)
    auto_approve: bool = _env_bool("AUTO_APPROVE", False)
    style_guide_max_chars: int = _env_int("STYLE_GUIDE_MAX_CHARS", 0)

    chapter_count: int = _env_int("CHAPTER_COUNT", 10)
    chapter_start: int = _env_int("CHAPTER_START", 1)
    chapter_last: int = _env_int("CHAPTER_LAST", 10)
    chapter_concurrency: int = _env_int("CHAPTER_CONCURRENCY", 1)
    scene_plan_repair_attempts: int = _env_int("SCENE_PLAN_REPAIR_ATTEMPTS", 1)
    word_target_min: int = _env_int("WORD_TARGET_MIN", 1800)
    word_target_max: int = _env_int("WORD_TARGET_MAX", 2400)
    target_minutes_min: float = _env_float("TARGET_MINUTES_MIN", 15.0)
    target_minutes_max: float = _env_float("TARGET_MINUTES_MAX", 20.0)
    assumed_wpm: int = _env_int("ASSUMED_WPM", 150)
    expansion_passes: int = _env_int("EXPANSION_PASSES", 2)
    intra_chapter_context_chars: int = _env_int("INTRA_CHAPTER_CONTEXT_CHARS", 9000)

    writer_max_tokens: int = _env_int("WRITER_MAX_TOKENS", 2200)
    editor_max_tokens: int = _env_int("EDITOR_MAX_TOKENS", 2000)
    critic_max_tokens: int = _env_int("CRITIC_MAX_TOKENS", 1800)
    expander_max_tokens: int = _env_int("EXPANDER_MAX_TOKENS", 2600)
    compressor_max_tokens: int = _env_int("COMPRESSOR_MAX_TOKENS", 1800)
    archivist_max_tokens: int = _env_int("ARCHIVIST_MAX_TOKENS", 450)
    tts_prep_max_tokens: int = _env_int("TTS_PREP_MAX_TOKENS", 2200)

    lint_enabled: bool = _env_bool("LINT_ENABLED", True)
    max_lint_repairs: int = _env_int("MAX_LINT_REPAIRS", 2)
    max_duplicate_paragraph_repeats: int = _env_int("MAX_DUPLICATE_PARAGRAPH_REPEATS", 1)
    max_sentence_repeat: int = _env_int("MAX_SENTENCE_REPEAT", 2)
    meta_phrases: tuple[str, ...] = _env_csv(
        "META_PHRASES",
        "this is only the beginning,on a journey,story had only started,dear reader,reader,in this chapter,the author,the writer,prompt,model,ai,word count,word target,target word count,chapter brief,character beat,character beats,continuity flags,continuity_flags",
    )
    chapter1_forbidden_terms: tuple[str, ...] = _env_csv(
        "CHAPTER1_FORBIDDEN_TERMS",
        "hidden door,novaBio tracker,tracker lay hidden",
    )
    first_chapter_guidance_enabled: bool = _env_bool("FIRST_CHAPTER_GUIDANCE_ENABLED", True)
    first_chapter_guidance_file: str = os.getenv("FIRST_CHAPTER_GUIDANCE_FILE", "FIRST_CHAPTER.md")
    chapter1_decision_verbs: tuple[str, ...] = _env_csv(
        "CHAPTER1_DECISION_VERBS",
        "decide,choose,refuse,agree,confess,run,steal,lie,confront,accept,decline,promise,risk",
    )
    chapter1_red_flag_phrases: tuple[str, ...] = _env_csv(
        "CHAPTER1_RED_FLAG_PHRASES",
        "woke up,alarm clock,looked in the mirror,it was all a dream",
    )

    style_influence: str = os.getenv("STYLE_INFLUENCE", "")

    exaggeration: float = _env_float("EXAGGERATION", 0.40)
    cfg_weight: float = _env_float("CFG_WEIGHT", 0.45)
    temperature: float = _env_float("TEMPERATURE", 0.70)
    silence_pad: float = _env_float("SILENCE_PAD", 0.25)
    narration_speed: float = _env_float("NARRATION_SPEED", 1.00)
    sample_rate: int = _env_int("SAMPLE_RATE", 22050)
    request_delay: float = _env_float("REQUEST_DELAY", 1.00)
    max_retries: int = _env_int("MAX_RETRIES", 3)
    retry_backoff: float = _env_float("RETRY_BACKOFF", 1.00)
    tts_sentence_timeout_seconds: int = _env_int("TTS_SENTENCE_TIMEOUT_SECONDS", 90)
    intro_lead_in_seconds: float = _env_float("INTRO_LEAD_IN_SECONDS", 1.40)
    pause_multiplier_end: float = _env_float("PAUSE_MULTIPLIER_END", 1.00)
    pause_multiplier_mid: float = _env_float("PAUSE_MULTIPLIER_MID", 1.15)
    pause_paragraph_bonus: float = _env_float("PAUSE_PARAGRAPH_BONUS", 0.18)
    min_pause_end: float = _env_float("MIN_PAUSE_END", 0.16)
    min_pause_mid: float = _env_float("MIN_PAUSE_MID", 0.09)
    chapter_intro_enabled: bool = _env_bool("CHAPTER_INTRO_ENABLED", True)
    chapter_complete_alert: str = os.getenv("CHAPTER_COMPLETE_ALERT", "double_beep")

    voice_sample: str = os.getenv("VOICE_SAMPLE", "voices/narrator.wav")


SETTINGS = Settings()
