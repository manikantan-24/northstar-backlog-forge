"""Mocked Confluence tool.

In a real integration this would call /wiki/rest/api/content for a specific
page. Here we just read a local markdown file passed at construction time
and expose it as a single `get_page()` call.
"""

from __future__ import annotations

from pathlib import Path

from logger_setup import get_logger
from tools.base import Tool, ToolError

logger = get_logger(__name__)


class ConfluenceTool(Tool):
    """Mocked Confluence page reader."""

    name = "confluence"

    def __init__(self, default_page_path: Path | None = None):
        self._default_page_path = Path(default_page_path) if default_page_path else None

    def get_page(self, page_id: str = "default") -> str:
        """Return the body of a wiki page. With the mock, page_id is ignored."""
        if not self._default_page_path or not self._default_page_path.exists():
            raise ToolError(
                "Confluence fixture not configured. Pass --constraints on the CLI "
                "to provide a wiki source, or construct ConfluenceTool with a path."
            )
        return self._default_page_path.read_text(encoding="utf-8")
