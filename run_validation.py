#!/usr/bin/env python3
"""
Validation script for 3-chapter lint fix test run.
Runs chapters 1-3 through the full pipeline with lint fixes enabled.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from pipeline_novel import run_chapter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run validation chapters through pipeline_novel.")
    parser.add_argument(
        "--chapters",
        default="1,2,3",
        help="Comma-separated chapter numbers to run (default: 1,2,3)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Chapter-level concurrency workers (default: 1)",
    )
    return parser.parse_args()


def _parse_chapters(raw: str) -> list[int]:
    values: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        values.append(int(chunk))
    if not values:
        raise ValueError("No chapters provided")
    return values


def _run_sequential(chapters: list[int], results: dict[int, dict[str, str]]) -> None:
    for chapter_num in chapters:
        print(f"\n{'='*80}")
        print(f"CHAPTER {chapter_num}")
        print(f"{'='*80}")
        try:
            run_chapter(chapter_num)
            results[chapter_num] = {"status": "success"}
            print(f"\n✓ CHAPTER {chapter_num} COMPLETE")
        except Exception as e:
            results[chapter_num] = {"status": "failed", "error": str(e)[:500]}
            print(f"\n✗ CHAPTER {chapter_num} FAILED")
            print(f"Error: {e}")
            continue


def _run_concurrent(chapters: list[int], workers: int, results: dict[int, dict[str, str]]) -> None:
    print(f"\nRunning concurrent validation: workers={workers}, chapters={chapters}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_chapter, chapter_num): chapter_num for chapter_num in chapters}
        for fut in as_completed(futures):
            chapter_num = futures[fut]
            try:
                fut.result()
                results[chapter_num] = {"status": "success"}
                print(f"\n✓ CHAPTER {chapter_num} COMPLETE")
            except Exception as e:
                results[chapter_num] = {"status": "failed", "error": str(e)[:500]}
                print(f"\n✗ CHAPTER {chapter_num} FAILED")
                print(f"Error: {e}")

print("=" * 80)
print("STARTING 3-CHAPTER VALIDATION RUN")
print("=" * 80)

args = _parse_args()
chapters = _parse_chapters(args.chapters)
workers = max(1, args.workers)

results = {}
if workers == 1:
    _run_sequential(chapters, results)
else:
    _run_concurrent(chapters, workers, results)

print("\n" + "=" * 80)
print("VALIDATION SUMMARY")
print("=" * 80)

all_passed = all(r["status"] == "success" for r in results.values())
for ch in chapters:
    result = results.get(ch, {"status": "failed", "error": "missing result"})
    status = "✓ PASS" if result["status"] == "success" else "✗ FAIL"
    print(f"  Chapter {ch}: {status}")
    if result["status"] == "failed":
        print(f"    Error: {result['error']}")

print("\n" + "=" * 80)
if all_passed:
    print("ALL VALIDATION CHAPTERS COMPLETED SUCCESSFULLY ✓")
    print("\nArtifacts generated:")
    for ch in chapters:
        ch_str = f"ch{ch:02d}"
        artifacts = [
            f"chapters/scenes/{ch_str}/scene01_final.txt",
            f"chapters/scenes/{ch_str}/scene02_final.txt",
            f"chapters/scenes/{ch_str}/scene03_final.txt",
            f"chapters/{ch_str}_final.txt",
            f"summaries/{ch_str}_summary.txt",
            f"chapters/{ch_str}_tts.txt",
            f"audio/{ch_str}_narration.wav",
            f"reviews/{ch_str}_lint.md",
        ]
        print(f"\n  Chapter {ch}:")
        for artifact in artifacts:
            p = Path(artifact)
            if p.exists():
                if p.is_file():
                    size = p.stat().st_size
                    if size > 1024*1024:
                        size_str = f"{size/(1024*1024):.1f}MB"
                    elif size > 1024:
                        size_str = f"{size/1024:.1f}KB"
                    else:
                        size_str = f"{size}B"
                    print(f"    ✓ {artifact} ({size_str})")
                else:
                    print(f"    ✓ {artifact} (directory)")
            else:
                print(f"    ✗ {artifact} (missing)")
else:
    print("SOME CHAPTERS FAILED - Check output above")
print("=" * 80)

sys.exit(0 if all_passed else 1)
