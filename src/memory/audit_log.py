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
        usage: dict[str, Any] | None = None,
        prompt: str | None = None,
        response_text: str | None = None,
    ) -> None:
        """Record an LLM/tool invocation with optional full prompt + response capture.

        Parameters:
            request          structured metadata about the call (chars,
                             max_tokens, etc.) — short-truncated for the
                             scorecard summary view.
            response_excerpt 300-char preview used by the markdown rollup.
            prompt           full prompt text sent to the LLM. Kept under
                             a separate key so the markdown render can put
                             it in a collapsible block. Trimmed at 16 KB
                             to keep the audit file readable.
            response_text    full LLM response text. Same 16 KB cap.

        Trimming happens here, not at the call site, so every audit entry
        is consistently bounded. Reviewers who want the unbounded version
        can pull from `synthesis.json` (which has the parsed structured
        output) or the application logs.
        """
        payload = {
            "tool": tool,
            "request": _truncate_dict(request or {}, max_chars=300),
            "response_excerpt": _truncate(response_excerpt, max_chars=300),
        }
        if tokens_used is not None:
            payload["tokens_used"] = tokens_used
        if usage is not None:
            payload["usage"] = {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            }
        # Full prompt + response — capped to 16 KB each so the audit file
        # stays small. The markdown render shows them in collapsible
        # <details> blocks so the high-level trail stays readable.
        if prompt is not None:
            payload["prompt_full"] = _truncate(prompt, max_chars=16_000)
            payload["prompt_chars_actual"] = len(prompt)
        if response_text is not None:
            payload["response_full"] = _truncate(response_text, max_chars=16_000)
            payload["response_chars_actual"] = len(response_text)
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

    # Payload keys that carry potentially long LLM content. These get
    # rendered inside collapsible <details> blocks so the high-level
    # audit narrative stays scannable. Anything else falls through to
    # the short repr path.
    _LONG_PAYLOAD_KEYS = ("prompt_full", "response_full")

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
                    if k in self._LONG_PAYLOAD_KEYS and isinstance(v, str):
                        # Skip — we render these in dedicated blocks below.
                        continue
                    lines.append(f"    - `{k}`: {_short_repr(v)}")

                # Full prompt — collapsible. Streamlit's `st.markdown`
                # renders <details>/<summary> with `unsafe_allow_html=True`,
                # which is how this is shown in the UI's Audit tab.
                prompt_full = e.payload.get("prompt_full")
                if prompt_full:
                    actual = e.payload.get("prompt_chars_actual", len(prompt_full))
                    lines.append("")
                    lines.append(
                        f'<details><summary><strong>📤 Prompt sent to LLM</strong> '
                        f'<em>({actual:,} chars total)</em></summary>'
                    )
                    lines.append("")
                    lines.append("```")
                    lines.append(prompt_full)
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")

                # Full response — same treatment.
                response_full = e.payload.get("response_full")
                if response_full:
                    actual = e.payload.get("response_chars_actual", len(response_full))
                    lines.append("")
                    lines.append(
                        f'<details><summary><strong>📥 Response from LLM</strong> '
                        f'<em>({actual:,} chars total)</em></summary>'
                    )
                    lines.append("")
                    lines.append("```json")
                    lines.append(response_full)
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")
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
