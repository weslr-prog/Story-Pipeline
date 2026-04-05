import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from gradio_client import Client, handle_file

from config import SETTINGS

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
_ABBREV_RE = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|approx)\.", re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    cleaned = " ".join(text.split())
    protected = _ABBREV_RE.sub(lambda m: f"{m.group(1)}<DOT>", cleaned)
    parts = _SENTENCE_END_RE.split(protected)
    out = [p.replace("<DOT>", ".").strip() for p in parts]
    return [p for p in out if len(p) > 2]


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


def narrate_chapter(
    text: str,
    voice_sample: str,
    output_path: str,
    chapter_num: int,
    resume: bool = True,
) -> str:
    client = _load_client()
    api_name = resolve_api_name(client)

    sentences = split_sentences(text)
    if not sentences:
        raise ValueError("No narratable sentences found.")

    segments_dir = ROOT / "audio" / "segments" / f"ch{chapter_num:02d}"
    segments_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(segments_dir) if resume else {"completed": []}
    completed = set(manifest.get("completed", []))

    segment_files: list[Path] = []
    for i, sentence in enumerate(sentences):
        seg_name = f"seg_{i:04d}.wav"
        seg_path = segments_dir / seg_name

        if resume and seg_name in completed and seg_path.exists():
            segment_files.append(seg_path)
            continue

        last_exc: Exception | None = None
        for attempt in range(1, SETTINGS.max_retries + 1):
            try:
                generated = _generate_sentence(client, api_name, sentence, voice_sample)
                shutil.copy(generated, seg_path)
                completed.add(seg_name)
                manifest["completed"] = sorted(completed)
                _save_manifest(segments_dir, manifest)
                segment_files.append(seg_path)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                sleep_s = SETTINGS.retry_backoff * attempt
                time.sleep(sleep_s)

        if last_exc is not None:
            print(f"[WARN] Sentence {i} failed after retries: {last_exc}")
            continue

        time.sleep(SETTINGS.request_delay)

    if not segment_files:
        raise RuntimeError("No audio segments produced.")

    stitch_audio(segment_files, Path(output_path))
    return output_path


def stitch_audio(segment_files: list[Path], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = output_path.parent / ".tmp_stitch"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    padded_files: list[Path] = []
    for idx, seg in enumerate(segment_files):
        padded = tmp_dir / f"pad_{idx:04d}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(seg),
                "-af",
                f"aresample={SETTINGS.sample_rate},apad=pad_dur={SETTINGS.silence_pad}",
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

    concat_list = tmp_dir / "concat.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for seg in padded_files:
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

    for p in padded_files:
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
