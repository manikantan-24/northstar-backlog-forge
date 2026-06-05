"""Ollama lifecycle manager.

Checks if an Ollama server is reachable. If not, starts `ollama serve` as
a background subprocess and waits for it to become ready.

Called once at app startup — idempotent (does nothing if already running).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
_HEALTH_URL = f"{_BASE_URL}/api/tags"


def is_running() -> bool:
    """Return True if Ollama is reachable right now."""
    try:
        import requests
        return requests.get(_HEALTH_URL, timeout=2).status_code < 400
    except Exception:  # noqa: BLE001
        return False


def ensure_running(timeout: int = 15) -> tuple[bool, str]:
    """Start Ollama if it's not already running.

    Returns (success: bool, message: str).
      success=True  — Ollama is ready (was already running or we started it).
      success=False — could not start Ollama within `timeout` seconds.
    """
    if is_running():
        return True, "Ollama already running."

    binary = shutil.which("ollama")
    if not binary:
        return False, (
            "Ollama binary not found in PATH. "
            "Install from https://ollama.ai then run: ollama pull llama3.2:3b"
        )

    try:
        # Start as detached background process — survives the parent dying.
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # On macOS/Linux, start_new_session detaches from the process group
            # so the server keeps running even if the Streamlit process restarts.
            start_new_session=True,
        )
    except OSError as e:
        return False, f"Failed to start ollama serve: {e}"

    # Wait for it to become ready
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True, f"Ollama started successfully (took {timeout - int(deadline - time.time())}s)."
        time.sleep(0.5)

    return False, (
        f"Ollama did not become ready within {timeout}s. "
        "Try running 'ollama serve' manually in a terminal."
    )


def list_models() -> list[str]:
    """Return names of locally available models."""
    try:
        import requests
        resp = requests.get(_HEALTH_URL, timeout=3)
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return []
