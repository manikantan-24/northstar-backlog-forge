"""GitHub MCP tool adapter — GitHub Issues via @modelcontextprotocol/server-github.

When GITHUB_MCP_ENABLED=1 is set, this class connects to the GitHub MCP server
via npx and calls its tools instead of reading from the local fixture file.
Falls back to the GithubTool fixture mock when MCP is disabled or unavailable.

Setup (one-time):
    # Node.js must be installed (already present on this machine)
    # No separate install needed — npx downloads the package automatically.
    # Set env vars:
    GITHUB_MCP_ENABLED=1
    GITHUB_TOKEN=ghp_...          # Personal Access Token from github.com/settings/tokens
    GITHUB_OWNER=your-org         # GitHub org or username
    GITHUB_REPO=your-repo-name    # Repository to fetch issues from

Getting a GitHub PAT:
    1. github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)
    2. Generate new token → select scopes: repo (or public_repo for public repos)
    3. Copy the ghp_... token
"""

from __future__ import annotations

import asyncio
import json
import os

from logger_setup import get_logger
from tools.base import ToolError
from tools.github_tool import GithubTool

logger = get_logger(__name__)

_MCP_ENABLED = os.environ.get("GITHUB_MCP_ENABLED", "").strip() == "1"
_MCP_PACKAGE = "@modelcontextprotocol/server-github"


async def _call_github_mcp_async(tool_name: str, arguments: dict):
    """Call one tool on the GitHub MCP server via npx stdio transport."""
    try:
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession, StdioServerParameters
    except ImportError as e:
        raise ToolError("The 'mcp' package is not installed: pip install mcp") from e

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ToolError(
            "GITHUB_TOKEN not set. Create a PAT at github.com/settings/tokens "
            "with 'repo' scope and set GITHUB_TOKEN=ghp_..."
        )

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", _MCP_PACKAGE],
        env={**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": token},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            if result and hasattr(result, "content"):
                texts = [b.text for b in result.content if hasattr(b, "text")]
                return "\n".join(texts)
            return result


def _call_github_mcp(tool_name: str, arguments: dict):
    return asyncio.run(_call_github_mcp_async(tool_name, arguments))


class MCPGithubTool(GithubTool):
    """GithubTool that uses GitHub MCP server when GITHUB_MCP_ENABLED=1.

    Uses @modelcontextprotocol/server-github via npx — no Docker or Go needed.

    MCP tools used:
        list_issues     — open issues from the configured repo
        search_issues   — text search across issues + PRs

    Falls back to fixture-based GithubTool when MCP is disabled or unavailable.
    """

    name = "mcp_github"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._use_mcp = _MCP_ENABLED
        self._owner = os.environ.get("GITHUB_OWNER", "")
        self._repo  = os.environ.get("GITHUB_REPO", "")
        if self._use_mcp:
            logger.info("MCPGithubTool: GitHub MCP mode enabled (%s/%s)", self._owner, self._repo)

    def list_all(self) -> list[dict]:
        if not self._use_mcp or not self._owner or not self._repo:
            return super().list_all()
        try:
            from telemetry import child_span as _cs
        except ImportError:
            from contextlib import nullcontext as _cs  # type: ignore[assignment]
        try:
            with _cs("tool.github_list_issues",
                     **{"tool.transport": "github_mcp",
                        "tool.repo": f"{self._owner}/{self._repo}"}) as _span:
                raw = _call_github_mcp("list_issues", {
                    "owner": self._owner,
                    "repo":  self._repo,
                    "state": "open",
                })
            return self._parse(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("MCPGithubTool list_all failed (%s) — falling back to fixture", e)
            self._use_mcp = False
            return super().list_all()

    def search(self, query: str) -> list[dict]:
        if not self._use_mcp or not self._owner or not self._repo:
            return super().search(query)
        try:
            raw = _call_github_mcp("search_issues", {
                "query": f"repo:{self._owner}/{self._repo} {query}",
            })
            return self._parse(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("MCPGithubTool search failed (%s) — falling back to fixture", e)
            self._use_mcp = False
            return super().search(query)

    def _parse(self, raw) -> list[dict]:
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return []
            issues = data if isinstance(data, list) else data.get("items", data.get("issues", []))
        elif isinstance(raw, list):
            issues = raw
        else:
            return []
        return [
            {
                "id":     str(i.get("number", i.get("id", ""))),
                "title":  i.get("title", ""),
                "body":   i.get("body", "") or "",
                "state":  i.get("state", ""),
                "labels": [
                    lb.get("name", lb) if isinstance(lb, dict) else lb
                    for lb in (i.get("labels") or [])
                ],
                "url":    i.get("html_url", ""),
            }
            for i in issues if isinstance(i, dict)
        ]
