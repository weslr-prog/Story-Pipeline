import json
import os
import re
import signal
import shutil
import subprocess
import time
import hashlib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from gradio_client import Client, handle_file

from config import SETTINGS

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
_ABBREV_RE = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|approx)\.", re.IGNORECASE)
_PARALINGUISTIC_TAG_RE = re.compile(r"<[^>]+>")


def _split_sentences_with_paragraph_breaks(text: str) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    for p_idx, paragraph in enumerate(paragraphs):
        cleaned = " ".join(paragraph.split())
        protected = _ABBREV_RE.sub(lambda m: f"{m.group(1)}<DOT>", cleaned)
        parts = _SENTENCE_END_RE.split(protected)
        sentences = [p.replace("<DOT>", ".").strip() for p in parts]
        sentences = [p for p in sentences if len(p) > 2]

        for s_idx, sentence in enumerate(sentences):
            para_break_after = s_idx == len(sentences) - 1 and p_idx < len(paragraphs) - 1
            entries.append((sentence, para_break_after))

    if entries:
        return entries

    cleaned = " ".join(text.split())
    if cleaned:
        return [(cleaned, False)]
    return []


def split_sentences(text: str) -> list[str]:
    return [sentence for sentence, _ in _split_sentences_with_paragraph_breaks(text)]


def _load_client() -> Client:
    return Client(SETTINGS.chatterbox_url)


def resolve_api_name(client: Client) -> str:
    if SETTINGS.chatterbox_api:
        return SETTINGS.chatterbox_api

    candidates: list[str] = []
    try:
        api_dict = client.view_api(return_format="dict")
        candidates = list(api_dict.get("named_endpoints", {}).keys())
    except Exception:
        pass

    for preferred in ("/generate", "/predict", "/infer"):
        if preferred in candidates:
            return preferred

    if candidates:
        return candidates[0]

    raise RuntimeError(
        "Could not auto-detect Gradio api_name. Set CHATTERBOX_API in .env after checking WebUI network calls."
    )


def _generate_sentence(
    client: Client,
    api_name: str,
    sentence: str,
    voice_sample: str,
) -> str:
    payloads = [
        {
            "text": sentence,
            "audio_prompt_path": handle_file(voice_sample),
            "exaggeration": SETTINGS.exaggeration,
            "cfg_weight": SETTINGS.cfg_weight,
            "temperature": SETTINGS.temperature,
        },
        {
            "text": sentence,
            "audio_prompt_path": handle_file(voice_sample),
            "exaggeration": SETTINGS.exaggeration,
            "temperature": SETTINGS.temperature,
            "seed_num": 0,
            "cfgw": SETTINGS.cfg_weight,
            "min_p": 0.05,
            "top_p": 1.0,
            "repetition_penalty": 1.2,
        },
    ]

    last_exc: Exception | None = None
    result = None
    for payload in payloads:
        try:
            result = client.predict(api_name=api_name, **payload)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc

    if last_exc is not None:
        raise last_exc

    # gradio may return a string path or nested structures depending on version.
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)) and result:
        first = result[0]
        if isinstance(first, str):
            return first
    raise RuntimeError(f"Unexpected Gradio result type: {type(result)}")


def _segment_manifest_path(segments_dir: Path) -> Path:
    return segments_dir / "manifest.json"


def _load_manifest(segments_dir: Path) -> dict[str, Any]:
    path = _segment_manifest_path(segments_dir)
    if not path.exists():
        return {"completed": []}
    return json.loads(path.read_text())


def _save_manifest(segments_dir: Path, manifest: dict[str, Any]) -> None:
    _segment_manifest_path(segments_dir).write_text(json.dumps(manifest, indent=2))


def _source_fingerprint(sentences: list[tuple[str, bool]]) -> str:
    canonical = "\n".join(f"{s.strip()}|pb={1 if para_break else 0}" for s, para_break in sentences)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reset_segment_cache(segments_dir: Path) -> None:
    for seg in segments_dir.glob("seg_*.wav"):
        seg.unlink(missing_ok=True)


def _segment_pad_seconds(sentence: str, paragraph_break_after: bool = False) -> float:
    s = sentence.strip()
    if s.endswith(("?", "!", ".")):
        base = max(SETTINGS.min_pause_end, SETTINGS.silence_pad * SETTINGS.pause_multiplier_end)
    elif s.endswith((":", ";", ",")):
        base = max(SETTINGS.min_pause_mid, SETTINGS.silence_pad * SETTINGS.pause_multiplier_mid)
    else:
        base = max(SETTINGS.min_pause_mid * 0.85, SETTINGS.silence_pad * (SETTINGS.pause_multiplier_mid * 0.9))

    if paragraph_break_after:
        base += max(0.0, SETTINGS.pause_paragraph_bonus)

    return base


def _with_timeout(timeout_seconds: int, label: str, fn):
    if timeout_seconds <= 0 or not hasattr(signal, "setitimer"):
        return fn()

    def _handle_timeout(signum, frame):
        raise TimeoutError(f"{label} exceeded {timeout_seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def narrate_chapter(
    text: str,
    voice_sample: str,
    output_path: str,
    chapter_num: int,
    resume: bool = True,
) -> str:
    client = _load_client()
    api_name = resolve_api_name(client)

    if _PARALINGUISTIC_TAG_RE.search(text):
        print("[WARN] Paralinguistic/SSML-style tags detected; stripping unsupported tags for Chatterbox.")
        text = _PARALINGUISTIC_TAG_RE.sub(" ", text)

    sentence_entries = _split_sentences_with_paragraph_breaks(text)
    if not sentence_entries:
        raise ValueError("No narratable sentences found.")

    source_hash = _source_fingerprint(sentence_entries)

    segments_dir = ROOT / "audio" / "segments" / f"ch{chapter_num:02d}"
    segments_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(segments_dir) if resume else {"completed": []}
    manifest_hash = str(manifest.get("source_hash", ""))
    manifest_count = int(manifest.get("sentence_count", 0) or 0)
    manifest_stale = manifest_hash != source_hash or manifest_count != len(sentence_entries)
    if resume and not manifest_hash:
        manifest_stale = True
        print(f"[WARN] Missing source hash in chapter {chapter_num} manifest; forcing regeneration.")

    if resume and manifest_stale:
        print(
            "[INFO] Narration source changed for chapter "
            f"{chapter_num}; invalidating cached segments."
        )
        _reset_segment_cache(segments_dir)
        manifest = {"completed": []}

    manifest["manifest_version"] = 2
    manifest["source_hash"] = source_hash
    manifest["sentence_count"] = len(sentence_entries)
    manifest["voice_sample"] = str(voice_sample)
    manifest["api_name"] = api_name
    manifest["pacing_profile"] = {
        "intro_lead_in_seconds": SETTINGS.intro_lead_in_seconds,
        "pause_multiplier_end": SETTINGS.pause_multiplier_end,
        "pause_multiplier_mid": SETTINGS.pause_multiplier_mid,
        "pause_paragraph_bonus": SETTINGS.pause_paragraph_bonus,
    }
    _save_manifest(segments_dir, manifest)

    completed = set(manifest.get("completed", []))
    failed = set(manifest.get("failed", []))

    segment_files: list[Path] = []
    segment_pads: list[float] = []
    for i, (sentence, paragraph_break_after) in enumerate(sentence_entries):
        seg_name = f"seg_{i:04d}.wav"
        seg_path = segments_dir / seg_name
        pad_seconds = _segment_pad_seconds(sentence, paragraph_break_after)

        if resume and seg_name in completed and seg_path.exists():
            segment_files.append(seg_path)
            segment_pads.append(pad_seconds)
            continue

        last_exc: Exception | None = None
        for attempt in range(1, SETTINGS.max_retries + 1):
            try:
                generated = _with_timeout(
                    SETTINGS.tts_sentence_timeout_seconds,
                    f"chapter {chapter_num} sentence {i}",
                    lambda: _generate_sentence(client, api_name, sentence, voice_sample),
                )
                shutil.copy(generated, seg_path)
                completed.add(seg_name)
                failed.discard(seg_name)
                manifest["completed"] = sorted(completed)
                manifest["failed"] = sorted(failed)
                manifest["last_error"] = ""
                _save_manifest(segments_dir, manifest)
                segment_files.append(seg_path)
                segment_pads.append(pad_seconds)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                sleep_s = SETTINGS.retry_backoff * attempt
                time.sleep(sleep_s)

        if last_exc is not None:
            print(f"[WARN] Sentence {i} failed after retries: {last_exc}")
            failed.add(seg_name)
            manifest["failed"] = sorted(failed)
            manifest["last_error"] = f"sentence {i}: {last_exc}"
            _save_manifest(segments_dir, manifest)
            continue

        time.sleep(SETTINGS.request_delay)

    if not segment_files:
        raise RuntimeError("No audio segments produced.")

    stitch_audio(
        segment_files,
        segment_pads,
        Path(output_path),
        lead_in_seconds=max(0.0, SETTINGS.intro_lead_in_seconds),
    )
    return output_path


def stitch_audio(
    segment_files: list[Path],
    segment_pads: list[float],
    output_path: Path,
    lead_in_seconds: float = 0.0,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = output_path.parent / ".tmp_stitch"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    concat_sources: list[Path] = []
    if lead_in_seconds > 0.0:
        pre_roll = tmp_dir / "pre_roll.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={SETTINGS.sample_rate}:cl=mono",
                "-t",
                f"{lead_in_seconds:.2f}",
                "-ar",
                str(SETTINGS.sample_rate),
                "-ac",
                "1",
                "-codec:a",
                "pcm_s16le",
                str(pre_roll),
            ],
            check=True,
            capture_output=True,
        )
        concat_sources.append(pre_roll)

    padded_files: list[Path] = []
    for idx, seg in enumerate(segment_files):
        padded = tmp_dir / f"pad_{idx:04d}.wav"
        pad = segment_pads[idx] if idx < len(segment_pads) else SETTINGS.silence_pad
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(seg),
                "-af",
                f"aresample={SETTINGS.sample_rate},apad=pad_dur={pad}",
                "-ar",
                str(SETTINGS.sample_rate),
                "-ac",
                "1",
                "-codec:a",
                "pcm_s16le",
                str(padded),
            ],
            check=True,
            capture_output=True,
        )
        padded_files.append(padded)

    concat_sources.extend(padded_files)

    concat_list = tmp_dir / "concat.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for seg in concat_sources:
            f.write(f"file '{seg.resolve()}'\n")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-ar",
            str(SETTINGS.sample_rate),
            "-ac",
            "1",
            "-codec:a",
            "pcm_s16le",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )

    if abs(SETTINGS.narration_speed - 1.0) > 0.001:
        sped_path = tmp_dir / "sped_output.wav"
        speed = max(0.5, min(2.0, SETTINGS.narration_speed))
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(output_path),
                "-af",
                f"atempo={speed}",
                "-ar",
                str(SETTINGS.sample_rate),
                "-ac",
                "1",
                "-codec:a",
                "pcm_s16le",
                str(sped_path),
            ],
            check=True,
            capture_output=True,
        )
        shutil.move(str(sped_path), str(output_path))

    for p in concat_sources:
        p.unlink(missing_ok=True)
    concat_list.unlink(missing_ok=True)
    tmp_dir.rmdir()


def smoke_test(text: str, chapter_num: int = 0) -> str:
    out = ROOT / "audio" / f"smoke_{chapter_num:02d}.wav"
    return narrate_chapter(
        text=text,
        voice_sample=SETTINGS.voice_sample,
        output_path=str(out),
        chapter_num=chapter_num,
    )


if __name__ == "__main__":
    sample = (
        "This is a smoke test. The quick brown fox jumps over the lazy dog. "
        "If you hear clear sentence endings, the setup is healthy."
    )
    final = smoke_test(sample, chapter_num=0)
    print(f"Wrote: {final}")
