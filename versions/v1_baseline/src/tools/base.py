"""Common base class for tools.

Tools are deterministic surfaces the agents call. Each tool has one job
and a small typed interface. Most tools in this project are mocked so the
demo is self-contained — swapping them for real implementations (real JIRA
REST API, real Confluence API, etc.) is a one-file change.
"""

from __future__ import annotations


class ToolError(Exception):
    """Raised when a tool call cannot succeed (vs. retryable transient errors)."""


class Tool:
    """Base tool. Subclasses set `name` for audit log identification."""

    name: str = "tool"
