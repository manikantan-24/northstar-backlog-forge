"""Wrapped Ollama API client — mirrors the shape of ClaudeTool / GeminiTool.

Ollama runs a local REST server (default: http://localhost:11434) that serves
open-source models (Llama 3.1, Mistral, Phi-3, Gemma, etc.) with no API key
and no per-call cost. This tool lets those models slot into any agent stage
that the `call_for_json(...)` interface.

Recommended use:
    Mechanical extraction stages (Parser, Constraint Extractor, Epic
    Decomposer) where a 7B-8B model is capable enough. Keep Claude on the
    Story Writer and Gap Detector where nuanced reasoning matters.

Setup:
    1. Install Ollama: https://ollama.ai/download
    2. Pull a model:   ollama pull llama3.1  (or mistral, phi3, gemma2, …)
    3. Start server:   ollama serve          (auto-starts on macOS after install)
    4. Set in .env:    OLLAMA_BASE_URL=http://localhost:11434  (already the default)

Model ID convention:
    Use the prefix "ollama/" so the orchestrator dispatcher recognises the
    tool. Examples: "ollama/llama3.1", "ollama/mistral", "ollama/phi3".
    The "ollama/" prefix is stripped before the Ollama API call.

JSON reliability:
    Ollama supports a `format: "json"` option that constrains the model to
    emit valid JSON — similar to Gemini's `response_mime_type`. We enable it
    by default and fall back to `_extract_json_block` for models that ignore
    the constraint. Not all 7B models are as reliable as Gemini Flash at
    schema-following; the Story Writer / Gap Detector prompts are complex
    enough that a local model may produce off-schema output more frequently.

Availability guard:
    If Ollama isn't running when the tool is initialised, a clear ToolError
    is raised immediately — not silently mid-run. The auto_switch failover
    skips Ollama as a fallback target if the health check fails at init time.
"""

from __future__ import annotations

import os
from typing import Any

from logger_setup import get_logger
from tools.base import Tool, ToolError
from tools.claude_tool import ClaudeTool, PROMPTS_DIR  # reuse JSON extractor + prompts dir

logger = get_logger(__name__)

DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL    = os.environ.get("OLLAMA_MODEL", "llama3.1")


class OllamaTool(Tool):
    """Ollama local-model client. Same call_for_json contract as ClaudeTool/GeminiTool."""

    name = "ollama"

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL) -> None:
        try:
            import requests as _requests
        except ImportError as e:  # pragma: no cover
            raise ToolError("'requests' package is required for OllamaTool.") from e

        # Strip the "ollama/" prefix so we send just the model name to the API.
        self.model = model.removeprefix("ollama/")
        self._base_url = base_url.rstrip("/")
        self._requests = _requests
        self.system_prompt = (PROMPTS_DIR / "system_prompt.md").read_text(encoding="utf-8")

        # Fail fast: check the server is reachable before the first agent call.
        # This surfaces a clear error at tool-init time, not mid-run.
        try:
            resp = self._requests.get(f"{self._base_url}/api/tags", timeout=3)
            if resp.status_code >= 400:
                raise ToolError(
                    f"Ollama server returned {resp.status_code}. "
                    "Is 'ollama serve' running?"
                )
        except self._requests.exceptions.ConnectionError:
            raise ToolError(
                f"Cannot reach Ollama at {self._base_url}. "
                "Install Ollama (https://ollama.ai) and run 'ollama serve', "
                "then pull a model: 'ollama pull llama3.1'."
            )
        except self._requests.exceptions.Timeout:
            raise ToolError(
                f"Ollama health check timed out at {self._base_url}. "
                "Check that 'ollama serve' is running."
            )

    # ---------------------------------------------- public

    def call(
        self,
        user_message: str,
        max_tokens: int = 4000,
    ) -> tuple[str, dict[str, Any]]:
        """Make a single Ollama chat call. Returns (text, usage_dict)."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message},
            ],
            # Ask for JSON output — not all models honour it but it helps.
            "format": "json",
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.0,   # deterministic — same as our Claude/Gemini calls
            },
        }
        try:
            resp = self._requests.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=120,  # local generation can be slow on CPU
            )
        except self._requests.exceptions.Timeout as e:
            raise ToolError(
                f"Ollama generation timed out for model '{self.model}'. "
                "Consider using a smaller model or increasing timeout."
            ) from e
        except self._requests.exceptions.ConnectionError as e:
            raise ToolError(
                f"Lost connection to Ollama at {self._base_url}: {e}"
            ) from e

        if resp.status_code >= 400:
            raise ToolError(
                f"Ollama /api/chat returned {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        text = (data.get("message") or {}).get("content", "") or ""

        # Ollama's eval_count ≈ output tokens; prompt_eval_count ≈ input tokens.
        usage: dict[str, Any] = {
            "input_tokens":  data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
        }
        return text, usage

    def call_for_json(
        self,
        user_message: str,
        max_tokens: int = 4000,
    ) -> tuple[dict, dict[str, Any]]:
        """Call Ollama and parse the response as JSON. Returns (parsed_dict, usage)."""
        text, usage = self.call(user_message, max_tokens=max_tokens)
        # Reuse ClaudeTool's defensive JSON extractor — same logic for any
        # provider that occasionally wraps output in ```json fences.
        parsed = ClaudeTool._extract_json_block(text)
        return parsed, usage
