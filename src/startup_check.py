"""Startup secret validation.

Called once when the app or CLI boots. Raises RuntimeError with a clear
message if any required environment variable is unset, so misconfiguration
is caught at startup rather than mid-run with a cryptic API error.

Optional groups are checked as a set: if ANY var in the group is set,
ALL vars in that group must be set (partial config is worse than none).
"""

from __future__ import annotations

import os


_REQUIRED = [
    ("ANTHROPIC_API_KEY", "Anthropic API key — required for Claude models"),
]

_OPTIONAL_GROUPS = [
    (
        ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"],
        "Jira live integration (all three must be set together)",
    ),
    (
        ["CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN"],
        "Confluence live integration (all three must be set together)",
    ),
    (
        ["GOOGLE_API_KEY"],
        "Google Gemini models",
    ),
    (
        ["GITHUB_TOKEN"],
        "GitHub MCP live integration",
    ),
]


def check_python_version() -> list[str]:
    """Return warnings if Python version is below 3.10 (MCP packages unavailable)."""
    import sys
    if sys.version_info < (3, 10):
        return [
            f"Python {sys.version_info.major}.{sys.version_info.minor} detected. "
            "MCP packages (mcp, mcp-atlassian) require Python 3.10+. "
            "ATLASSIAN_MCP_ENABLED and GITHUB_MCP_ENABLED will fall back to REST/fixture. "
            "Use venv313 (./start.sh) for full MCP support."
        ]
    return []


def check_required_secrets() -> list[str]:
    """Validate secrets. Returns a list of warning strings (non-fatal).

    Raises RuntimeError for any missing *required* secret so the app
    refuses to start rather than failing mid-run with a cryptic error.

    Returns warning strings for partially-configured optional groups so
    callers can surface them in the UI without blocking startup.
    """
    # Hard failures — required for any run.
    missing_required = [
        desc
        for var, desc in _REQUIRED
        if not os.environ.get(var, "").strip()
    ]
    if missing_required:
        raise RuntimeError(
            "Missing required environment variable(s):\n"
            + "\n".join(f"  • {d}" for d in missing_required)
            + "\n\nSet them in your deployment environment (not in .env for production). "
            "See .env.example for local development."
        )

    # Soft warnings — optional but must be complete if partially set.
    warnings: list[str] = []
    for vars_in_group, label in _OPTIONAL_GROUPS:
        set_vars = [v for v in vars_in_group if os.environ.get(v, "").strip()]
        if not set_vars:
            continue  # group not configured at all — fine
        missing = [v for v in vars_in_group if not os.environ.get(v, "").strip()]
        if missing:
            warnings.append(
                f"{label}: partially configured — missing {', '.join(missing)}. "
                "Set all vars in the group or none."
            )

    return warnings


def get_configured_integrations() -> dict[str, bool]:
    """Return which optional integrations are fully configured.

    Used by the UI to show/hide live-source toggles and the Jira push button.
    """
    return {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        "jira": all(
            os.environ.get(v, "").strip()
            for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
        ),
        "confluence": all(
            os.environ.get(v, "").strip()
            for v in ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN")
        ),
        "google": bool(os.environ.get("GOOGLE_API_KEY", "").strip()),
        "github": bool(os.environ.get("GITHUB_TOKEN", "").strip()),
        "atlassian_mcp": bool(os.environ.get("ATLASSIAN_MCP_ENABLED", "").strip()),
        "github_mcp": bool(os.environ.get("GITHUB_MCP_ENABLED", "").strip()),
    }
