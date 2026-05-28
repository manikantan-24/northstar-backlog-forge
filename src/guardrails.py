"""Post-synthesis guardrails.

A bank of cheap, deterministic checks that run AFTER the multi-agent
pipeline finishes but BEFORE the result reaches the user. The intent is
to catch the failure modes that the prompts cannot reliably prevent:
acceptance criteria that read like prose, tags drawn from outside the
canonical vocabulary, stories whose title is a duplicate of another
story's title, and stories that don't trace back to a parsed topic.

Failures are **non-blocking** — the synthesis is still returned. Each
failure is recorded as an audit event so reviewers can see what was
caught, and the result dict grows a `guardrail_findings` list so the
UI can render a warning chip.

This is deliberately scoped to heuristics, not a second LLM call. The
LLM-as-judge in `evaluation/llm_as_judge.py` is the deeper qualitative
check; this module is the fast, cheap, mandatory one.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


# Canonical tag vocabulary, mirrored from `prompts/story_writer_prompt.md`.
# Tags outside this set are allowed (the prompt permits adding new ones
# if nothing fits) but are flagged as "non-canonical" so reviewers can
# decide whether the vocabulary needs to expand.
CANONICAL_TAGS: set[str] = {
    "pos", "mobile-app", "ecommerce", "loyalty", "inventory", "pharmacy",
    "vendor-portal", "store-associate", "analytics", "payments",
    "offline-mode", "accessibility", "performance", "security", "compliance",
}

# An acceptance criterion should read like "Given … when … then …".
# We tolerate a few common variants (lowercase, dashes between clauses)
# but flag anything without at least one of the GWT keywords.
_GWT_RE = re.compile(r"\b(given|when|then)\b", re.IGNORECASE)


@dataclass
class GuardrailFinding:
    """One thing the guardrails noticed. Severity drives UI styling.

    severity:
        - "error"  the result is probably wrong — surface prominently
        - "warn"   the result might be wrong — surface inline
        - "info"   notable but probably fine
    """

    code: str
    severity: str
    message: str
    story_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run_guardrails(synthesis: dict) -> list[GuardrailFinding]:
    """Run every check in order. Returns the combined finding list.

    Each check is independent — if one raises, the others still run.
    Order doesn't affect correctness; we run cheap checks first so an
    early stop wouldn't lose anything important (we don't actually
    early-stop, but the order is robustness insurance).
    """
    findings: list[GuardrailFinding] = []
    epics = synthesis.get("epics") or []
    stories = [s for e in epics for s in (e.get("stories") or [])]

    # 1. Every story has the minimum acceptance criteria count.
    findings.extend(_check_ac_count(stories))

    # 2. AC items look testable (Given / When / Then keywords present).
    findings.extend(_check_ac_grammar(stories))

    # 3. Story titles within a run are unique.
    findings.extend(_check_unique_titles(stories))

    # 4. Tags drawn from the canonical vocabulary.
    findings.extend(_check_canonical_tags(stories))

    # 5. Every story traces back to a parsed topic (evidence block present
    #    OR source_topic_id exists in the topics list).
    findings.extend(_check_story_grounding(stories, synthesis.get("topics") or []))

    # 6. High-priority stories have a non-trivial priority_rationale.
    findings.extend(_check_priority_rationale(stories))

    return findings


# ---------------------------------------------------------------- checks


def _check_ac_count(stories: list[dict]) -> list[GuardrailFinding]:
    out: list[GuardrailFinding] = []
    for s in stories:
        ac = s.get("acceptance_criteria") or []
        if len(ac) < 2:
            out.append(GuardrailFinding(
                code="ac_count_too_low",
                severity="warn",
                message=f"Only {len(ac)} acceptance criterion — prompt asks for 2-5.",
                story_id=s.get("id"),
            ))
        elif len(ac) > 7:
            out.append(GuardrailFinding(
                code="ac_count_too_high",
                severity="info",
                message=f"{len(ac)} acceptance criteria — consider splitting the story.",
                story_id=s.get("id"),
            ))
    return out


def _check_ac_grammar(stories: list[dict]) -> list[GuardrailFinding]:
    out: list[GuardrailFinding] = []
    for s in stories:
        ac = s.get("acceptance_criteria") or []
        for i, item in enumerate(ac):
            text = item if isinstance(item, str) else str(item)
            if not _GWT_RE.search(text):
                out.append(GuardrailFinding(
                    code="ac_missing_gwt",
                    severity="warn",
                    message=(
                        f"AC #{i + 1} doesn't use Given/When/Then — "
                        f"may not be testable as written."
                    ),
                    story_id=s.get("id"),
                ))
    return out


def _check_unique_titles(stories: list[dict]) -> list[GuardrailFinding]:
    """Within one run, identical story titles almost always mean the
    story_writer drafted two slightly-different stories for the same topic.
    The epic decomposer's grouping step usually merges these, but when it
    doesn't, this check surfaces the duplicate."""
    seen: dict[str, str] = {}
    out: list[GuardrailFinding] = []
    for s in stories:
        title_norm = (s.get("title") or "").strip().lower()
        if not title_norm:
            continue
        prior_id = seen.get(title_norm)
        if prior_id:
            out.append(GuardrailFinding(
                code="duplicate_title",
                severity="warn",
                message=f"Same title as story {prior_id}.",
                story_id=s.get("id"),
            ))
        else:
            seen[title_norm] = s.get("id") or "?"
    return out


def _check_canonical_tags(stories: list[dict]) -> list[GuardrailFinding]:
    out: list[GuardrailFinding] = []
    for s in stories:
        tags = s.get("tags") or []
        non_canon = [t for t in tags if isinstance(t, str) and t.lower() not in CANONICAL_TAGS]
        if non_canon:
            out.append(GuardrailFinding(
                code="non_canonical_tag",
                severity="info",
                message=(
                    f"Tags outside the canonical set: {non_canon}. "
                    f"Either add them to the vocabulary or normalise."
                ),
                story_id=s.get("id"),
            ))
    return out


def _check_story_grounding(stories: list[dict], topics: list[dict]) -> list[GuardrailFinding]:
    """A story should always trace back to a parsed topic. If `source_topic_id`
    isn't set, OR points to an id we don't have, OR the evidence block is
    empty, the story might be a hallucination."""
    topic_ids = {t.get("id") for t in topics if isinstance(t, dict)}
    out: list[GuardrailFinding] = []
    for s in stories:
        sid = s.get("source_topic_id")
        evidence = s.get("evidence") or []
        if not sid:
            out.append(GuardrailFinding(
                code="ungrounded_story",
                severity="error",
                message="Story has no source_topic_id — cannot trace to transcript.",
                story_id=s.get("id"),
            ))
            continue
        if topic_ids and sid not in topic_ids:
            out.append(GuardrailFinding(
                code="dangling_topic_ref",
                severity="error",
                message=f"source_topic_id={sid!r} doesn't match any parsed topic.",
                story_id=s.get("id"),
            ))
        if not evidence:
            out.append(GuardrailFinding(
                code="missing_evidence",
                severity="info",
                message=(
                    "Evidence block is empty — story can't be traced to a "
                    "specific transcript quote."
                ),
                story_id=s.get("id"),
            ))
    return out


def _check_priority_rationale(stories: list[dict]) -> list[GuardrailFinding]:
    out: list[GuardrailFinding] = []
    for s in stories:
        if (s.get("priority") or "").lower() != "high":
            continue
        rationale = (s.get("priority_rationale") or "").strip()
        if not rationale or len(rationale) < 20:
            out.append(GuardrailFinding(
                code="weak_priority_rationale",
                severity="warn",
                message=(
                    "High-priority story has a thin or missing rationale "
                    f"(len={len(rationale)})."
                ),
                story_id=s.get("id"),
            ))
    return out


# ---------------------------------------------------------------- summary

def summarise(findings: list[GuardrailFinding]) -> dict[str, int]:
    """Tally findings by severity. Used by the audit log + UI badge."""
    out = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out
