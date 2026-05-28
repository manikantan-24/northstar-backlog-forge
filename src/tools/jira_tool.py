"""JIRA tool — mocked fixture mode + live REST mode.

Mock mode (default): reads tickets from a local JSON file. Used by tests,
CI, and offline demos.

Live mode: hits the Jira Cloud REST API at `/rest/api/3/search/jql` with a
JQL query, paginates, and normalises every issue into the same dict shape
the Gap Detector expects: `{id, title, description, status, labels, raw}`.

Environment variables read in live mode:
  JIRA_BASE_URL        e.g. https://your-tenant.atlassian.net
  JIRA_EMAIL           Atlassian account email
  JIRA_API_TOKEN       API token from id.atlassian.com/manage-profile/security/api-tokens
  JIRA_PROJECT_KEY     Default project (used when search() is called with a
                       plain string instead of full JQL)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from logger_setup import get_logger
from tools.base import Tool, ToolError

logger = get_logger(__name__)

Mode = Literal["mock", "live"]

DEFAULT_FIXTURE = Path(__file__).parent.parent.parent / "samples" / "jira_backlog.json"


class JiraTool(Tool):
    """JIRA ticket reader with mock and live modes.

    The Gap Detector only needs `list_all()` and `search(query)`; both
    return a list of dicts shaped like the existing fixture. Live mode
    extracts the same fields from Atlassian's response shape so downstream
    code is unaffected by the swap.
    """

    name = "jira"

    def __init__(
        self,
        fixture_path: Path | None = None,
        *,
        mode: Mode | None = None,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        project_key: str | None = None,
        page_size: int = 50,
        max_results: int = 200,
    ):
        self._fixture_path = Path(fixture_path) if fixture_path else DEFAULT_FIXTURE
        self._cache: list[dict] | None = None

        resolved_mode = (mode or os.environ.get("JIRA_MODE", "mock")).lower()
        if resolved_mode not in ("mock", "live"):
            raise ToolError(f"JIRA_MODE must be 'mock' or 'live' (got {resolved_mode!r}).")
        self._mode: Mode = resolved_mode  # type: ignore[assignment]
        self._base_url = (base_url or os.environ.get("JIRA_BASE_URL") or "").rstrip("/")
        self._email = email or os.environ.get("JIRA_EMAIL") or ""
        self._api_token = api_token or os.environ.get("JIRA_API_TOKEN") or ""
        self._project_key = project_key or os.environ.get("JIRA_PROJECT_KEY") or ""
        self._page_size = int(page_size)
        self._max_results = int(max_results)

    @property
    def mode(self) -> Mode:
        return self._mode

    # ----------------------------------------------------- public API

    def list_all(self) -> list[dict]:
        """Return every visible ticket. Cached after first call per instance."""
        if self._cache is None:
            self._cache = self._load_live() if self._mode == "live" else self._load_fixture()
        return list(self._cache)

    def search(self, query: str) -> list[dict]:
        """Substring search in mock; JQL search in live.

        For live mode, a `query` that looks like JQL (contains `=`, `~`,
        `AND`, `ORDER`) is passed through; anything simpler is wrapped in
        a text-search clause scoped to the configured project key.
        """
        if self._mode != "live":
            all_tickets = self.list_all()
            q = query.lower()
            return [
                t for t in all_tickets
                if q in (t.get("summary") or t.get("title") or "").lower()
                or q in (t.get("description") or "").lower()
            ]
        # Live search
        return self._search_live(query)

    # ----------------------------------------------------- mock

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

    # ----------------------------------------------------- live

    def _require_live_credentials(self) -> None:
        if not self._base_url or not self._email or not self._api_token:
            raise ToolError(
                "Jira live mode requires JIRA_BASE_URL, JIRA_EMAIL and "
                "JIRA_API_TOKEN to be set."
            )

    def _load_live(self) -> list[dict]:
        """Pull every non-done issue in the configured project, paginated."""
        self._require_live_credentials()
        # Sensible default: every issue in the project. Caller can pass full
        # JQL via search() to narrow further.
        if self._project_key:
            jql = f'project = "{self._project_key}" ORDER BY created DESC'
        else:
            jql = "ORDER BY created DESC"
        return self._jql_search(jql)

    def _search_live(self, query: str) -> list[dict]:
        """Live JQL search. Wraps plain strings into a text-search clause."""
        self._require_live_credentials()
        q = query.strip()
        looks_like_jql = any(
            tok in q.upper() for tok in (" AND ", " OR ", " = ", " ~ ", "ORDER BY", "PROJECT ")
        )
        if looks_like_jql:
            jql = q
        elif self._project_key:
            # `text ~` is Jira's full-text search across summary + description.
            escaped = q.replace('"', '\\"')
            jql = f'project = "{self._project_key}" AND text ~ "{escaped}"'
        else:
            escaped = q.replace('"', '\\"')
            jql = f'text ~ "{escaped}"'
        return self._jql_search(jql)

    def _jql_search(self, jql: str) -> list[dict]:
        """Paginated JQL search using the v3 enhanced /search/jql endpoint.

        Returns a normalised list capped at `self._max_results`. The Gap
        Detector only needs a representative sample, so the cap keeps the
        first run cheap on large backlogs.
        """
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise ToolError("'requests' package is required for live mode") from e

        url = f"{self._base_url}/rest/api/3/search/jql"
        # Field allowlist — Jira returns the whole world by default; we only
        # need a few fields per issue, and trimming them avoids cost surprises.
        fields = ["summary", "description", "status", "labels", "priority",
                  "issuetype", "components", "created", "updated"]

        all_issues: list[dict] = []
        next_token: str | None = None

        while True:
            params: dict[str, object] = {
                "jql": jql,
                "fields": ",".join(fields),
                "maxResults": self._page_size,
            }
            if next_token:
                params["nextPageToken"] = next_token

            resp = requests.get(
                url,
                params=params,
                auth=(self._email, self._api_token),
                headers={"Accept": "application/json"},
                timeout=30,
            )
            if resp.status_code in (401, 403):
                raise ToolError(
                    f"Jira auth failed ({resp.status_code}). "
                    "Check JIRA_EMAIL and JIRA_API_TOKEN."
                )
            if resp.status_code == 400:
                raise ToolError(f"Jira rejected JQL: {resp.text[:300]}")
            if resp.status_code >= 400:
                raise ToolError(
                    f"Jira /search/jql returned {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            issues = data.get("issues", []) or []
            all_issues.extend(_normalise_issue(i) for i in issues)
            if len(all_issues) >= self._max_results:
                all_issues = all_issues[: self._max_results]
                logger.info("Jira search hit max_results=%d; capping.", self._max_results)
                break

            # v3 /search/jql uses a cursor (nextPageToken) — present iff more.
            next_token = data.get("nextPageToken")
            if not next_token or not issues:
                break

        logger.info("Jira live fetch returned %d issue(s)", len(all_issues))
        return all_issues


# ----------------------------------------------------- adapter

def _normalise_issue(issue: dict) -> dict:
    """Map a Jira REST issue → the internal ticket dict shape.

    Output fields (matching the fixture):
        id, title, summary, description, status, labels, priority, raw

    `summary` is duplicated to `title` because some downstream callers
    use one or the other; keeping both is cheap and avoids surprises.
    """
    fields = issue.get("fields") or {}
    summary = fields.get("summary") or ""
    description = _adf_to_text(fields.get("description"))
    status = (fields.get("status") or {}).get("name", "")
    priority = (fields.get("priority") or {}).get("name", "")
    labels = fields.get("labels") or []
    return {
        "id": issue.get("key", ""),
        "title": summary,
        "summary": summary,
        "description": description,
        "status": status,
        "priority": priority,
        "labels": labels,
        "raw": issue,
    }


def _adf_to_text(adf) -> str:
    """Best-effort flatten of Atlassian Document Format → plain text.

    The Gap Detector reads description as a string for embedding /
    similarity. ADF is a nested JSON structure of typed nodes; we walk it
    depth-first and concatenate every text leaf. Formatting is lost — fine
    for downstream similarity work, not for re-rendering.
    """
    if not adf:
        return ""
    if isinstance(adf, str):
        return adf

    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                out.append(node["text"])
            for child in node.get("content", []) or []:
                walk(child)
            if node.get("type") in ("paragraph", "heading", "listItem"):
                out.append("\n")
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(adf)
    text = "".join(out)
    # Collapse extra blank lines for cleanliness
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()
