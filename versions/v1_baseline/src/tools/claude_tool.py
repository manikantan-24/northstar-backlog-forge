"""Wrapped Claude API client used by every agent.

Centralizing the API call here lets us:
  - Pick the model once via ANTHROPIC_MODEL env var (default: claude-sonnet-4-5)
  - Apply retry logic uniformly (via tenacity)
  - Parse JSON responses defensively (handle fenced / prose-wrapped output)
  - Log token usage to the audit trail
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from logger_setup import get_logger
from tools.base import Tool, ToolError

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "3"))
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


# Lazy import — tests can mock the SDK without it being installed
try:
    from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError
except ImportError:  # pragma: no cover
    Anthropic = None
    APIError = APIConnectionError = RateLimitError = Exception


class ClaudeTool(Tool):
    """Wrapped Claude API client with retry + JSON-safe parsing."""

    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL):
        if Anthropic is None:
            raise ToolError(
                "The `anthropic` package isn't installed. Run: pip install -r requirements.txt"
            )
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ToolError(
                "ANTHROPIC_API_KEY isn't set. See .env.example for setup instructions."
            )
        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.system_prompt = (PROMPTS_DIR / "system_prompt.md").read_text(encoding="utf-8")

    # ---------------------------------------------- public

    def call(self, user_message: str, max_tokens: int = 4000) -> tuple[str, dict[str, Any]]:
        """Make a single Claude API call. Returns (text, usage_dict)."""
        return self._call_with_retry(user_message, max_tokens)

    def call_for_json(self, user_message: str, max_tokens: int = 4000) -> tuple[dict, dict[str, Any]]:
        """Call Claude and parse the response as JSON. Returns (parsed_dict, usage)."""
        text, usage = self.call(user_message, max_tokens=max_tokens)
        parsed = self._extract_json_block(text)
        return parsed, usage

    # ---------------------------------------------- retry-wrapped raw call

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _call_with_retry(self, user_message: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except (RateLimitError, APIConnectionError):
            raise  # let tenacity catch + retry
        except APIError as e:
            raise ToolError(f"Anthropic API error: {e}") from e

        parts = [b.text for b in response.content if hasattr(b, "text")]
        text = "".join(parts)
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
        }
        return text, usage

    # ---------------------------------------------- JSON extraction

    @staticmethod
    def _extract_json_block(text: str) -> dict:
        """Pull a JSON object out of model output. Handles fences and prose."""
        # Try fenced block first
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            candidate = fence_match.group(1)
        else:
            brace_match = re.search(r"\{.*\}", text, re.DOTALL)
            if not brace_match:
                raise ToolError(f"No JSON object found in model output:\n{text[:300]}")
            candidate = brace_match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ToolError(f"Model produced invalid JSON: {e}\nGot:\n{candidate[:500]}")
