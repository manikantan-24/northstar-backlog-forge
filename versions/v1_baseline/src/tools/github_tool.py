"""Mocked GitHub Issues tool.

In a real integration this would call /repos/{owner}/{repo}/issues. Here we
just read a local fixture and expose `list_all()` + `search()`.
"""

from __future__ import annotations

import json
from pathlib import Path

from logger_setup import get_logger
from tools.base import Tool, ToolError

logger = get_logger(__name__)


DEFAULT_FIXTURE = Path(__file__).parent.parent.parent / "samples" / "github_issues.json"


class GithubTool(Tool):
    """Mocked GitHub Issues reader."""

    name = "github"

    def __init__(self, fixture_path: Path | None = None):
        self._fixture_path = Path(fixture_path) if fixture_path else DEFAULT_FIXTURE
        self._cache: list[dict] | None = None

    def list_all(self) -> list[dict]:
        if self._cache is None:
            self._cache = self._load_fixture()
        return list(self._cache)

    def search(self, query: str) -> list[dict]:
        all_issues = self.list_all()
        q = query.lower()
        return [
            i for i in all_issues
            if q in (i.get("title") or "").lower()
            or q in (i.get("body") or "").lower()
        ]

    def _load_fixture(self) -> list[dict]:
        if not self._fixture_path.exists():
            logger.warning("GitHub fixture not found at %s; returning empty list", self._fixture_path)
            return []
        try:
            data = json.loads(self._fixture_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ToolError(f"GitHub fixture is not valid JSON: {e}")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        if not isinstance(data, list):
            raise ToolError("GitHub fixture must be a list of issues")
        return data
