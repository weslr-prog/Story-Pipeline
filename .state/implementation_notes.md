# Implementation Notes

## 2026-04-10
- Started execution phase after backup gate.
- Added narration cache integrity checks so stale chapter text cannot reuse old segment manifests.
- Added dashboard auto-refresh controls (30-60 seconds) and runtime telemetry.
- Integrated FIRST_CHAPTER guidance into chapter-1 planning/writing context.
- Added chapter-1 opening-contract lint check for active opening and red-flag opener patterns.
- Pending: run chapter-2 reset + rerun verification and capture new dashboard evidence.

## 2026-04-11
- Fixed local LLM request payload to use max_tokens (OpenAI-compatible field).
- Changed dotenv loading to preserve explicitly exported runtime env vars over .env defaults.
- Added local LLM retry/backoff behavior for timeout resilience.
- Added timestamped pipeline logs in runtime tail output.
- Added dashboard "Clear Run Logs" action wired to backend log cleanup.
- Performed full all-chapters reset and reran pipeline with narrated outputs through chapter 3.
- Verified chapter narration artifacts:
	- ch01_narration.wav (~339.0s)
	- ch02_narration.wav (~365.7s)
	- ch03_narration.wav (~205.9s)
- Quality caveat: chapter 3 final text word count ended low (679) despite chapter stitched draft near target; investigate compression/repair interactions before long production batch.
