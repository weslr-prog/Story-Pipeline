# === local_llm.py ==========================================================
"""
A very small wrapper around the TurboQuant‑disk server that mimics the
OpenClawClient interface used in pipeline_novel.py.

It expects the server to expose the OpenAI‑compatible chat endpoint:
    POST http://127.0.0.1:8080/v1/chat/completions

Only the fields we need are set:
    - model    → the --alias we passed to the server ("llama3‑turbo‑disk")
    - temperature
    - max_tokens
    - messages (a single user message)
"""

import os
import json
import uuid
import time
import requests
from typing import List, Dict, Any

try:
    from config import SETTINGS
except Exception:
    SETTINGS = None

DEFAULT_URL = (
    SETTINGS.local_disk_kv_url
    if SETTINGS is not None
    else os.getenv("LOCAL_DISK_KV_URL", "http://127.0.0.1:8080/v1/chat/completions")
)
DEFAULT_MODEL = (
    SETTINGS.local_disk_kv_model
    if SETTINGS is not None
    else os.getenv("LOCAL_DISK_KV_MODEL", "caiovicentino1/Qwen3.5-9B-HLWQ-MLX-4bit")
)
DEFAULT_TIMEOUT = (
    int(getattr(SETTINGS, "llm_call_timeout_seconds", 300))
    if SETTINGS is not None
    else int(os.getenv("LOCAL_LLM_TIMEOUT", "300"))
)
DEFAULT_RETRIES = int(os.getenv("LOCAL_LLM_RETRIES", "2"))
DEFAULT_RETRY_DELAY = float(os.getenv("LOCAL_LLM_RETRY_DELAY", "2.0"))
ALLOW_MODEL_FALLBACK = os.getenv("LOCAL_LLM_ALLOW_MODEL_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}
# Qwen3/thinking models consume tokens for reasoning before emitting content.
# We pad every request by this many extra tokens so content never gets cut off.
THINKING_OVERHEAD_TOKENS = int(os.getenv("LLM_THINKING_OVERHEAD", "600"))


def _log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] [LOCAL_LLM] {message}", flush=True)


class LocalLLMClient:
    """
    Compatible with the tiny subset of OpenClawClient we use in the pipeline.
    """

    def __init__(self):
        self._model = DEFAULT_MODEL
        self._temperature = 0.7
        self._max_output_tokens = 1024
        self._role = "assistant"  # not used but kept for parity
        self._headers = {"Content-Type": "application/json"}

    def _endpoint(self) -> str:
        return os.getenv("LOCAL_DISK_KV_URL", DEFAULT_URL)

    def _model_name(self) -> str:
        return os.getenv("LOCAL_DISK_KV_MODEL", self._model)

    # --------------------------------------------------------------------
    # OpenClaw‑like setters (the pipeline calls these)
    # --------------------------------------------------------------------
    def set_role(self, role: str) -> None:
        self._role = role

    def set_temperature(self, temp: float) -> None:
        self._temperature = temp

    def set_max_output_tokens(self, max_tokens: int) -> None:
        self._max_output_tokens = max_tokens

    def apply_preset(self, preset_name: str) -> None:
        # No presets for the local server – simply ignore.
        pass

    # --------------------------------------------------------------------
    # Core request
    # --------------------------------------------------------------------
    def invoke(self, prompt: str) -> Dict[str, Any]:
        endpoint = self._endpoint()
        retries = max(0, DEFAULT_RETRIES)
        delay = max(0.0, DEFAULT_RETRY_DELAY)
        data = None
        last_exc: Exception | None = None
        model_candidates = [self._model_name()]
        if ALLOW_MODEL_FALLBACK:
            env_llm_model = os.getenv("LLM_MODEL", "").strip()
            if env_llm_model and env_llm_model not in model_candidates:
                model_candidates.append(env_llm_model)

        payload_variants = [
            lambda model: {
                "model": model,
                "temperature": self._temperature,
                "max_tokens": self._max_output_tokens + THINKING_OVERHEAD_TOKENS,
                "repeat_penalty": getattr(SETTINGS, "llm_repeat_penalty", 1.0) if SETTINGS is not None else 1.0,
                "num_ctx": getattr(SETTINGS, "llm_num_ctx", 8192) if SETTINGS is not None else 8192,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "request_id": str(uuid.uuid4()),
            },
            lambda model: {
                "model": model,
                "temperature": self._temperature,
                "max_output_tokens": self._max_output_tokens + THINKING_OVERHEAD_TOKENS,
                "repeat_penalty": getattr(SETTINGS, "llm_repeat_penalty", 1.0) if SETTINGS is not None else 1.0,
                "num_ctx": getattr(SETTINGS, "llm_num_ctx", 8192) if SETTINGS is not None else 8192,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "request_id": str(uuid.uuid4()),
            },
        ]

        error_context: list[str] = []
        for model_name in model_candidates:
            for variant in payload_variants:
                payload = variant(model_name)
                prompt_words = len(prompt.split())
                prompt_tokens = int(prompt_words / 0.75)
                requested_tokens = payload.get("max_tokens") or payload.get("max_output_tokens") or self._max_output_tokens
                for attempt in range(retries + 1):
                    started = time.time()
                    _log(
                        "request start "
                        f"model={model_name} attempt={attempt + 1}/{retries + 1} "
                        f"endpoint={endpoint} prompt_words={prompt_words} "
                        f"prompt_tokens~={prompt_tokens} request_tokens={requested_tokens} "
                        f"num_ctx={payload.get('num_ctx')} timeout={DEFAULT_TIMEOUT}s"
                    )
                    try:
                        resp = requests.post(endpoint, headers=self._headers, json=payload, timeout=DEFAULT_TIMEOUT)
                        resp.raise_for_status()
                        data = resp.json()
                        elapsed = max(0.0, time.time() - started)
                        _log(
                            "request success "
                            f"model={model_name} attempt={attempt + 1}/{retries + 1} "
                            f"status={resp.status_code} elapsed={elapsed:.1f}s"
                        )
                        break
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as exc:
                        last_exc = exc
                        elapsed = max(0.0, time.time() - started)
                        _log(
                            "request error "
                            f"model={model_name} attempt={attempt + 1}/{retries + 1} "
                            f"elapsed={elapsed:.1f}s error={exc}"
                        )
                        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
                            body = (exc.response.text or "").strip().replace("\n", " ")
                            if body:
                                error_context.append(f"model={model_name} body={body[:240]}")
                        if attempt >= retries:
                            continue
                        sleep_for = delay * (attempt + 1)
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                if data is not None:
                    break
            if data is not None:
                break

        if data is None and last_exc is not None and error_context:
            raise RuntimeError(
                f"Local LLM request failed after fallbacks. Last error: {last_exc}. "
                f"Server context: {' | '.join(error_context)}"
            ) from last_exc

        if data is None:
            raise RuntimeError(f"Local LLM request failed without response: {last_exc}")

        # OpenClaw returns an object with a `.content` attribute.
        # Here we mimic that by giving a tiny namespace object.
        class _Resp:
            def __init__(self, text: str):
                self.content = text

        # The OpenAI-compatible format puts the assistant reply in data["choices"][0]["message"]["content"].
        # Qwen3 thinking models also include a "reasoning" key; we want the final content only.
        # Leading/trailing whitespace is stripped because thinking responses inject a leading '\n'.
        try:
            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
            if not text.strip():
                # Fallback: model finished in reasoning only (truncated) — surface reasoning excerpt
                reasoning = msg.get("reasoning", "")
                raise RuntimeError(
                    f"Local LLM returned empty content (finish_reason={data['choices'][0].get('finish_reason')!r}). "
                    f"Reasoning excerpt: {reasoning[:200]!r}. "
                    f"Try increasing WRITER_MAX_TOKENS or LLM_THINKING_OVERHEAD."
                )
            text = text.strip()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Unexpected response from local LLM server: {data}") from exc

        return _Resp(text)

# -------------------------------------------------------------------------
# Helper factory – the pipeline will import this instead of `OpenClawClient`.
# -------------------------------------------------------------------------
def get_llm_client() -> LocalLLMClient:
    """Return a client that talks to the disk‑KV server."""
    return LocalLLMClient()
# ===========================================================================

