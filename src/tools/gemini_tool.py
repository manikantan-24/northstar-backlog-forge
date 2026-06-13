"""Wrapped Google Gemini API client — mirrors the shape of `ClaudeTool`.

Why this exists:
    Agents are coded to talk to one "LLM tool" instance. By exposing the
    *same* public surface (`call`, `call_for_json`, `model`, `name`) as
    `ClaudeTool`, the orchestrator can pick a provider per stage and swap
    the tool instance into the agent constructor without the agent
    knowing or caring which provider is in play.

Implementation notes:
  - Uses the **new** `google-genai` SDK (`from google import genai`) NOT the
    legacy `google-generativeai` package. See V2's `analyzer.py` for the
    reference call shape — we copy it.
  - Reads `GOOGLE_API_KEY` from the environment.
  - Returns usage as `{"input_tokens": N, "output_tokens": N}` so the
    orchestrator's token aggregator (which keys by agent name and reads
    `usage.input_tokens` / `usage.output_tokens`) works unchanged.
  - JSON extraction reuses `_extract_json_block` from `claude_tool` so the
    defensive parsing logic stays in one place.
"""

from __future__ import annotations

import os
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from logger_setup import get_logger
from tools.base import Tool, ToolError
from tools.claude_tool import ClaudeTool, PROMPTS_DIR  # reuse JSON extractor + prompts dir

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "3"))


# Lazy SDK import — tests / Claude-only deployments shouldn't need the
# `google-genai` package installed to import this module.
try:
    from google import genai as _genai  # new SDK
    from google.genai import types as _genai_types  # noqa: F401 — kept for parity / future image use
except ImportError:  # pragma: no cover
    _genai = None
    _genai_types = None


class _TransientGeminiError(Exception):
    """Internal: a retryable Gemini failure. Wrapped by tenacity below."""


class GeminiTool(Tool):
    """Wrapped Gemini API client with retry + JSON-safe parsing.

    Public surface matches `ClaudeTool` so agents can use either via the
    same `tool.call(...)` / `tool.call_for_json(...)` API.
    """

    name = "gemini"

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        if _genai is None:
            raise ToolError(
                "The `google-genai` package isn't installed. Run: "
                "pip install -r requirements.txt  (adds google-genai>=1.0.0)"
            )
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ToolError(
                "GOOGLE_API_KEY isn't set. Get a free key at "
                "https://aistudio.google.com/ and add it to .env. "
                "See .env.example for the exact line."
            )
        try:
            self._client = _genai.Client(api_key=api_key)
        except Exception as e:  # noqa: BLE001 — surface as ToolError so orchestrator handles it
            raise ToolError(f"Could not initialize Gemini client: {e}") from e
        self.model = model
        # Same system_prompt source as ClaudeTool so both providers see
        # identical agent-system context.
        self.system_prompt = (PROMPTS_DIR / "system_prompt.md").read_text(encoding="utf-8")

    # ---------------------------------------------- public

    def call(self, user_message: str, max_tokens: int = 4000) -> tuple[str, dict[str, Any]]:
        """Make a single Gemini API call. Returns (text, usage_dict)."""
        return self._call_with_retry(user_message, max_tokens)

    def call_for_json(self, user_message: str, max_tokens: int = 4000) -> tuple[dict, dict[str, Any]]:
        """Call Gemini and parse the response as JSON. Returns (parsed_dict, usage)."""
        text, usage = self.call(user_message, max_tokens=max_tokens)
        # Reuse ClaudeTool's defensive JSON extractor — same logic applies
        # to any provider that occasionally fences output with ```json.
        parsed = ClaudeTool._extract_json_block(text)
        return parsed, usage

    # ---------------------------------------------- retry-wrapped raw call

    @retry(
        retry=retry_if_exception_type(_TransientGeminiError),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _call_with_retry(self, user_message: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        try:
            from telemetry import child_span as _cs
        except ImportError:
            from contextlib import nullcontext as _cs  # type: ignore[assignment]

        with _cs("llm.call", **{"llm.provider": "google", "llm.model": self.model,
                                "llm.max_tokens": max_tokens}) as _llm_span:
          try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=user_message,
                config={
                    "system_instruction": self.system_prompt,
                    "max_output_tokens": max_tokens,
                    "response_mime_type": "application/json",
                },
            )
          except Exception as e:  # noqa: BLE001 — Gemini's exception taxonomy is wide
            msg = str(e).lower()
            # Classify: transient (quota / rate / 5xx / network) vs permanent (auth, invalid).
            if any(t in msg for t in (
                "quota", "rate", "429", "resource_exhausted",
                "deadline", "unavailable", "503", "502", "500",
                "timeout", "connection",
            )):
                raise _TransientGeminiError(f"Gemini transient error: {e}") from e
            raise ToolError(f"Gemini API error: {e}") from e

          # New SDK exposes the joined text on `response.text`.
          text = getattr(response, "text", "") or ""
          # Warn when finish_reason signals truncation so logs show why JSON may be partial.
          try:
              candidate = (getattr(response, "candidates", None) or [None])[0]
              finish_reason = getattr(candidate, "finish_reason", None)
              if finish_reason and str(finish_reason) not in ("FinishReason.STOP", "STOP", "1"):
                  logger.warning("Gemini finish_reason=%s — response may be truncated", finish_reason)
          except Exception:  # noqa: BLE001
              pass
          usage = {"input_tokens": None, "output_tokens": None}
          um = getattr(response, "usage_metadata", None)
          if um is not None:
              usage = {
                  "input_tokens": getattr(um, "prompt_token_count", None),
                  "output_tokens": getattr(um, "candidates_token_count", None),
              }
          try:
              _llm_span.set_attribute("llm.tokens_in",  usage.get("input_tokens")  or 0)
              _llm_span.set_attribute("llm.tokens_out", usage.get("output_tokens") or 0)
          except Exception:  # noqa: BLE001
              pass
          return text, usage
