#!/usr/bin/env python3
"""
Validation script for 3-chapter lint fix test run.
Runs chapters 1-3 through the full pipeline with lint fixes enabled.
"""

import sys
import json
from pathlib import Path
from pipeline_novel import run_chapter

print("=" * 80)
print("STARTING 3-CHAPTER VALIDATION RUN")
print("=" * 80)

results = {}
for chapter_num in [1, 2, 3]:
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
        # Continue to next chapter even if one fails
        continue

print("\n" + "=" * 80)
print("VALIDATION SUMMARY")
print("=" * 80)

all_passed = all(r["status"] == "success" for r in results.values())
for ch, result in results.items():
    status = "✓ PASS" if result["status"] == "success" else "✗ FAIL"
    print(f"  Chapter {ch}: {status}")
    if result["status"] == "failed":
        print(f"    Error: {result['error']}")

print("\n" + "=" * 80)
if all_passed:
    print("ALL 3 CHAPTERS COMPLETED SUCCESSFULLY ✓")
    print("\nArtifacts generated:")
    for ch in [1, 2, 3]:
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
