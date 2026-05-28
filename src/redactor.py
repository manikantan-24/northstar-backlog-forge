"""
Opt-in PII redaction for the LLM pipeline.

Replaces email addresses, phone numbers, claim/policy IDs, and personal names
(via a conservative pattern) with stable placeholders before the source is
sent to the LLM. Story fields are then un-redacted on the way back so the
final output looks normal to the user.

Design choices:
  - Regex-based, not ML-based. Faster, deterministic, and audit-friendly.
  - Stable placeholder per value: same email → same token across the run, so
    the LLM can still reason about co-reference within the document.
  - Conservative on personal names: only matches the explicit "First Last"
    pattern with a capital-letter heuristic; tuneable false-positive rate.

The intent is to keep casual PII out of LLM logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Each pattern produces tokens of the form [<KIND>_<n>], e.g. [EMAIL_1].
# The kind name is also used in the placeholder so partial leaks are still
# semantically informative ("contact [EMAIL_2]" reads better than "[X_2]").
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # International phone with country code, or 10-digit US-style, or 10-digit IN-style.
    ("PHONE", re.compile(r"\b(?:\+\d{1,3}[\s-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}\b")),
    # SSN-shaped 9-digit groups (be conservative — only the dashed form).
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Credit-card-shaped (16 digits with optional separators).
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\d\b")),
    # Insurance-domain identifiers (CLAIM_ID / POLICY_ID / CASE_ID) were
    # intentionally dropped — they belong to the ClaimsBridge variant of this
    # codebase and produced no matches in retail-domain inputs.
]


# Two-word capitalized names. Excludes common false-positive starts found in
# document headings, meeting templates, and tech vocabulary so phrases like
# "Customer Escalation", "Synthetic Test", "Action Items", "Issue 4" don't
# get tokenized as personal names.
# Keep the blocklist explicit; aim for low false-positive rate, accept some
# false-negatives — the goal is reducing PII exposure, not eliminating it.
_NAME_BLOCKLIST = (
    # Original entries (used by prior versions of the demo)
    "User", "Story", "Stories", "Acceptance", "Criteria",
    "New", "Old", "Stage", "Source", "Backlog", "Pipeline",
    "Claim", "Policy", "Project", "Sprint",
    # Common document-heading words seen in meeting notes / requirement docs
    "Customer", "Escalation", "Synthetic", "Test", "File", "Demo",
    "Notes", "Note", "Memo", "Meeting", "Issue", "Issues",
    "Action", "Actions", "Item", "Items", "Decision", "Decisions",
    "Status", "Update", "Updates", "Summary", "Overview", "Agenda",
    "Planning", "Standup", "Review", "Retro", "Retrospective", "Demo",
    "Daily", "Weekly", "Monthly", "Quarterly",
    "Attendees", "Attendee", "Participants", "Present",
    # Tech / domain vocabulary that often starts capitalized words in pairs
    "Email", "Phone", "Card", "Magic", "Link", "Login", "Logout",
    "API", "Webhook", "Rate", "Limit", "Limits", "Retry", "Retries",
    "Production", "Staging", "Development", "Test", "Testing",
    "Two", "One", "Three", "Four", "Five",
    "First", "Second", "Third", "Fourth", "Fifth", "Final",
    # Calendar / time
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
    # Cloud / vendor product names that show up capitalized in two-word phrases
    "AWS", "GCP", "Azure", "Google", "Microsoft", "Amazon", "Stripe", "PayPal",
)
_NAME_PATTERN = re.compile(
    r"\b(?!(?:" + "|".join(re.escape(w) for w in _NAME_BLOCKLIST) + r")\b)"
    r"[A-Z][a-z]{1,15}\s+(?!(?:" + "|".join(re.escape(w) for w in _NAME_BLOCKLIST) + r")\b)"
    r"[A-Z][a-z]{1,15}\b"
)


@dataclass
class RedactionMap:
    """Tracks token ↔ original-value mappings so the run can un-redact LLM output."""

    by_kind_counter: dict[str, int] = field(default_factory=dict)
    token_to_value: dict[str, str] = field(default_factory=dict)
    value_to_token: dict[str, str] = field(default_factory=dict)

    def next_token(self, kind: str) -> str:
        n = self.by_kind_counter.get(kind, 0) + 1
        self.by_kind_counter[kind] = n
        return f"[{kind}_{n}]"

    def get_or_create(self, kind: str, value: str) -> str:
        """Return a stable placeholder for `value`, reusing the same one within a run."""
        existing = self.value_to_token.get(value)
        if existing is not None:
            return existing
        token = self.next_token(kind)
        self.token_to_value[token] = value
        self.value_to_token[value] = token
        return token

    def is_empty(self) -> bool:
        return not self.token_to_value

    def summary(self) -> dict[str, int]:
        """Per-kind count, e.g. {'EMAIL': 3, 'PHONE': 1}."""
        out: dict[str, int] = {}
        for kind, n in self.by_kind_counter.items():
            out[kind] = n
        return out


def redact(
    text: str,
    redact_names: bool = True,
    rmap: Optional["RedactionMap"] = None,
) -> tuple[str, RedactionMap]:
    """Replace PII patterns in `text` with stable placeholders.

    Returns the redacted text and a RedactionMap that can be passed to
    `unredact` to restore the originals.

    If `rmap` is supplied, the same map is extended in place — so identical
    PII values across multiple calls (e.g. source doc + backlog) get the
    same placeholder. Useful for chaining redactions across inputs.

    `redact_names` controls whether the conservative personal-name pattern
    runs. It's the noisiest of the patterns; turn it off if you find it
    swallowing too many product names or business terms.
    """
    if rmap is None:
        rmap = RedactionMap()
    patterns: Iterable[tuple[str, re.Pattern[str]]] = list(_PATTERNS)

    def _sub(kind: str, m: re.Match[str]) -> str:
        return rmap.get_or_create(kind, m.group(0))

    redacted = text
    for kind, pattern in patterns:
        redacted = pattern.sub(lambda m, k=kind: _sub(k, m), redacted)

    if redact_names:
        redacted = _NAME_PATTERN.sub(lambda m: _sub("NAME", m), redacted)

    return redacted, rmap


# Fields in a backlog item that are safe to send to the LLM in cleartext.
# The `id` field is the reference handle the dedup output needs to point at
# specific existing items — redacting it would make dedup output useless.
_BACKLOG_NEVER_REDACT_FIELDS = {"id"}


def redact_backlog(
    items: list[dict],
    rmap: Optional["RedactionMap"] = None,
    redact_names: bool = True,
) -> tuple[list[dict], RedactionMap]:
    """Redact PII inside backlog title/description fields while preserving the `id` field.

    Reuses an existing redaction map so tokens are consistent with the
    source document's redaction (same email → same token across both).
    """
    if rmap is None:
        rmap = RedactionMap()
    out: list[dict] = []
    for item in items:
        new_item: dict[str, object] = {}
        for key, value in item.items():
            if key in _BACKLOG_NEVER_REDACT_FIELDS:
                new_item[key] = value
                continue
            if isinstance(value, str):
                redacted, _ = redact(value, redact_names=redact_names, rmap=rmap)
                new_item[key] = redacted
            else:
                new_item[key] = value
        out.append(new_item)
    return out, rmap


def unredact(text: str, rmap: RedactionMap) -> str:
    """Restore original values in `text` by reversing the redaction map."""
    if rmap.is_empty():
        return text
    # Replace longest tokens first so [EMAIL_10] doesn't get partially replaced
    # by [EMAIL_1]. With our [KIND_N] format the boundaries are unambiguous,
    # but sorting is cheap and defensive.
    for token in sorted(rmap.token_to_value, key=len, reverse=True):
        text = text.replace(token, rmap.token_to_value[token])
    return text


def unredact_obj(obj, rmap: RedactionMap):
    """Recursively unredact strings inside a JSON-shaped object (dict / list / str)."""
    if rmap.is_empty():
        return obj
    if isinstance(obj, str):
        return unredact(obj, rmap)
    if isinstance(obj, list):
        return [unredact_obj(item, rmap) for item in obj]
    if isinstance(obj, dict):
        return {k: unredact_obj(v, rmap) for k, v in obj.items()}
    return obj


# ----------------------------------------------------- strict-mode guard


class StrictRedactionViolation(Exception):
    """Raised when a tool boundary sees PII that should have been redacted.

    `findings` is a list of dicts: {kind, sample, context_excerpt} so the
    caller can show the user exactly what slipped through without echoing
    the raw value back into a log message (the sample is already truncated).
    """

    def __init__(self, findings: list[dict]) -> None:
        msg = (
            f"Strict redaction violated: {len(findings)} unredacted PII "
            f"pattern(s) detected at a tool boundary."
        )
        super().__init__(msg)
        self.findings = findings


# Re-usable scan: same regex set as `redact`, but read-only. The point is
# to detect leftover PII at the audit-checkpoint level — *after* the
# orchestrator has already redacted what it thinks it should.
_SCAN_PATTERNS = [(kind, pat) for kind, pat in _PATTERNS]


def scan_for_pii(text: str, *, include_names: bool = True) -> list[dict]:
    """Return a list of PII findings in `text`.

    Each finding: {kind, sample, context_excerpt}. `sample` is the matched
    string truncated to 32 chars; `context_excerpt` shows the ~60 chars
    surrounding the match for review.
    """
    if not text:
        return []
    findings: list[dict] = []
    patterns = list(_SCAN_PATTERNS)
    if include_names:
        patterns.append(("NAME", _NAME_PATTERN))
    for kind, pattern in patterns:
        for m in pattern.finditer(text):
            start, end = m.span()
            ctx_start = max(0, start - 30)
            ctx_end = min(len(text), end + 30)
            findings.append({
                "kind": kind,
                "sample": m.group(0)[:32],
                "context_excerpt": text[ctx_start:ctx_end],
            })
    return findings


def assert_redacted(text: str, *, include_names: bool = True) -> None:
    """Raise `StrictRedactionViolation` if any PII pattern matches.

    Use at a trust boundary (right before sending a prompt to an LLM) so
    a strict-mode run halts instead of leaking. Cheap enough to call on
    every prompt; the regex is O(n) in input size.
    """
    findings = scan_for_pii(text, include_names=include_names)
    if findings:
        raise StrictRedactionViolation(findings)
