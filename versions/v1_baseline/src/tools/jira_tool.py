"""Mocked JIRA tool.

Reads tickets from a local JSON file by default. A real implementation would
hit JIRA's REST API at /rest/api/3/search with the same `search()` interface.

This is intentionally narrow: the agents only need to read existing tickets
for the Gap Detector. We don't write back to JIRA in this version.
"""

from __future__ import annotations

import json
from pathlib import Path

from logger_setup import get_logger
from tools.base import Tool, ToolError

logger = get_logger(__name__)


DEFAULT_FIXTURE = Path(__file__).parent.parent.parent / "samples" / "jira_backlog.json"


class JiraTool(Tool):
    """Mocked JIRA ticket reader. Real version would hit /rest/api/3/search."""

    name = "jira"

    def __init__(self, fixture_path: Path | None = None):
        self._fixture_path = Path(fixture_path) if fixture_path else DEFAULT_FIXTURE
        self._cache: list[dict] | None = None

    def list_all(self) -> list[dict]:
        """Return every ticket in the mocked project. Cached after first call."""
        if self._cache is None:
            self._cache = self._load_fixture()
        return list(self._cache)

    def search(self, query: str) -> list[dict]:
        """Naive substring search across summary/description. Mock of JQL."""
        all_tickets = self.list_all()
        q = query.lower()
        return [
            t for t in all_tickets
            if q in (t.get("summary") or "").lower()
            or q in (t.get("description") or "").lower()
        ]

    def _load_fixture(self) -> list[dict]:
        if not self._fixture_path.exists():
            logger.warning("JIRA fixture not found at %s; returning empty list", self._fixture_path)
            return []
        try:
            data = json.loads(self._fixture_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ToolError(f"JIRA fixture is not valid JSON: {e}")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        if not isinstance(data, list):
            raise ToolError("JIRA fixture must be a list of tickets")
        return data
