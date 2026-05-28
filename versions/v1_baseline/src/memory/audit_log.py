"""Append-only audit log of every agent decision.

Each event captures: timestamp, agent name, event type, structured payload,
and an optional human-readable reasoning string. At the end of a run, the
log is rendered as a Markdown trace that a reviewer can read top-to-bottom.

This addresses the requirement: "Audit logs must show how conclusions were
reached."
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class AuditEvent:
    timestamp: str
    agent: str
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


class AuditLog:
    """Append-only log of audit events for one orchestrator run."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    # ---------------------------------------------------------- recording

    def record(
        self,
        agent: str,
        event: str,
        payload: dict[str, Any] | None = None,
        reasoning: str = "",
    ) -> None:
        self._events.append(
            AuditEvent(
                timestamp=_now(),
                agent=agent,
                event=event,
                payload=payload or {},
                reasoning=reasoning,
            )
        )

    def record_tool_call(
        self,
        agent: str,
        tool: str,
        request: dict[str, Any] | None = None,
        response_excerpt: str = "",
        tokens_used: int | None = None,
    ) -> None:
        payload = {
            "tool": tool,
            "request": _truncate_dict(request or {}, max_chars=300),
            "response_excerpt": _truncate(response_excerpt, max_chars=300),
        }
        if tokens_used is not None:
            payload["tokens_used"] = tokens_used
        self.record(agent=agent, event="tool_call", payload=payload)

    def record_failure(self, agent: str, error: str) -> None:
        self.record(
            agent=agent,
            event="failure",
            payload={"error": error},
            reasoning=f"Agent failed permanently after retries: {error}",
        )

    # ----------------------------------------------------------- access

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def as_json_list(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._events]

    def render_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Audit trail")
        lines.append("")
        lines.append(f"Total events: {len(self._events)}")
        lines.append("")
        for i, e in enumerate(self._events, start=1):
            lines.append(f"## {i}. `{e.agent}` — {e.event}")
            lines.append("")
            lines.append(f"- **Timestamp:** {e.timestamp}")
            if e.reasoning:
                lines.append(f"- **Reasoning:** {e.reasoning}")
            if e.payload:
                lines.append("- **Payload:**")
                for k, v in e.payload.items():
                    lines.append(f"    - `{k}`: {_short_repr(v)}")
            lines.append("")
        return "\n".join(lines)


# -------------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(s: str, max_chars: int = 300) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "…"


def _truncate_dict(d: dict[str, Any], max_chars: int = 300) -> dict[str, Any]:
    return {
        k: _truncate(str(v), max_chars) if isinstance(v, str) else v
        for k, v in d.items()
    }


def _short_repr(v: Any, max_chars: int = 300) -> str:
    if isinstance(v, str):
        return _truncate(v, max_chars)
    if isinstance(v, (dict, list)):
        s = str(v)
        return _truncate(s, max_chars)
    return str(v)
