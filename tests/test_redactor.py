"""Tests for the PII redactor.

Covers each pattern (email, phone, claim id, ssn, card, name), the
stable-token guarantee (same value → same token), backlog-item
redaction with id preservation, and full round-trip (redact → unredact).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def test_email_is_redacted_with_stable_token():
    from redactor import redact

    text = "Contact alice@example.com or alice@example.com again."
    redacted, rmap = redact(text)
    # Same email reused → same token
    assert "[EMAIL_1]" in redacted
    assert redacted.count("[EMAIL_1]") == 2
    assert "alice@example.com" not in redacted
    assert rmap.summary().get("EMAIL") == 1


def test_phone_ssn_card_all_redacted():
    """Retail-relevant PII patterns: phone, SSN, card. The insurance-domain
    CLAIM_ID / POLICY_ID / CASE_ID patterns were removed for this project."""
    from redactor import redact

    text = "Phone 415-555-1234, SSN 123-45-6789, card 4111 1111 1111 1111."
    redacted, rmap = redact(text)
    assert "[PHONE_1]" in redacted
    assert "[SSN_1]" in redacted
    assert "[CARD_1]" in redacted
    # No raw values left
    assert "415-555-1234" not in redacted
    assert "123-45-6789" not in redacted


def test_insurance_specific_patterns_are_no_longer_matched():
    """CLM-/POL-/CASE- identifiers should pass through untouched in the
    retail-domain build — they were specific to the ClaimsBridge demo and
    would produce false positives if a future story or ticket id used a
    similar shape."""
    from redactor import redact

    text = "Reference CLM-12345 / POL-99999 / CASE-77777 — these stay verbatim."
    redacted, rmap = redact(text)
    assert "CLM-12345" in redacted
    assert "POL-99999" in redacted
    assert "CASE-77777" in redacted
    assert "CLAIM_ID" not in str(rmap.summary())
    assert "POLICY_ID" not in str(rmap.summary())
    assert "CASE_ID" not in str(rmap.summary())


def test_name_pattern_redacts_two_word_capitalized_names_but_not_headings():
    from redactor import redact

    text = "Hiroshi Tanaka raised the issue. Customer Escalation noted."
    redacted, _ = redact(text)
    # Real name redacted
    assert "[NAME_1]" in redacted
    assert "Hiroshi Tanaka" not in redacted
    # "Customer Escalation" should stay — it's in the blocklist
    assert "Customer Escalation" in redacted


def test_name_redaction_can_be_disabled():
    from redactor import redact

    text = "Hiroshi Tanaka raised the issue."
    redacted, _ = redact(text, redact_names=False)
    assert "Hiroshi Tanaka" in redacted


def test_redact_unredact_round_trip_restores_original():
    from redactor import redact, unredact

    original = "Email alice@example.com about CLM-99 — call 415-555-0100."
    redacted, rmap = redact(original)
    restored = unredact(redacted, rmap)
    assert restored == original


def test_redact_backlog_preserves_id_field_but_redacts_descriptions():
    from redactor import redact_backlog

    items = [
        {"id": "NS-176", "title": "Refill SMS for alice@example.com",
         "description": "Customer alice@example.com wants reminders."},
    ]
    redacted_items, rmap = redact_backlog(items)
    item = redacted_items[0]
    # id is verbatim — Gap Detector relies on it
    assert item["id"] == "NS-176"
    # email redacted in both fields, same token
    assert "[EMAIL_1]" in item["title"]
    assert "[EMAIL_1]" in item["description"]
    assert "alice@example.com" not in item["title"]
    assert "alice@example.com" not in item["description"]
    assert rmap.summary().get("EMAIL") == 1


def test_shared_rmap_keeps_tokens_consistent_across_calls():
    """Same email across two redact() calls must get the same token."""
    from redactor import redact, RedactionMap

    rmap = RedactionMap()
    r1, _ = redact("Reach out to alice@example.com", rmap=rmap)
    r2, _ = redact("Yes, alice@example.com is the right contact.", rmap=rmap)
    # Both should reference [EMAIL_1] — not [EMAIL_1] and [EMAIL_2]
    assert "[EMAIL_1]" in r1
    assert "[EMAIL_1]" in r2
    assert rmap.summary().get("EMAIL") == 1


def test_unredact_obj_recurses_into_dicts_and_lists():
    from redactor import redact, unredact_obj

    _, rmap = redact("alice@example.com is here.")
    # Build a mixed JSON-shaped object with tokens scattered through it
    obj = {
        "story": "Notify [EMAIL_1] when ready.",
        "tags": ["customer", "[EMAIL_1] contact"],
        "meta": {"owner": "[EMAIL_1]"},
    }
    restored = unredact_obj(obj, rmap)
    assert "alice@example.com" in restored["story"]
    assert "alice@example.com" in restored["tags"][1]
    assert restored["meta"]["owner"] == "alice@example.com"


def test_orchestrator_redacts_inputs_when_redact_pii_is_true():
    """E2E: orchestrator with redact_pii=True must produce a redaction
    audit event and pass redacted text to the (mocked) agents."""
    from orchestrator import Orchestrator

    captured_prompts: list[str] = []

    class CapturingClaude:
        name = "claude"

        def __init__(self):
            self.calls = 0

        def call_for_json(self, user_message: str, max_tokens: int = 4000):
            captured_prompts.append(user_message)
            self.calls += 1
            # Return a minimal valid response for whichever agent is calling
            if "extract the distinct topics" in user_message:
                return ({"summary": "S", "topics": [
                    {"theme": "x", "summary": "x", "raw_quote": "q",
                     "speaker": "[NAME_1]", "sentiment": "neutral"}
                ]}, {"input_tokens": 10, "output_tokens": 20})
            if "extract the architectural constraints" in user_message:
                return ({"constraints": []}, {"input_tokens": 5, "output_tokens": 5})
            if "draft well-formed user stories" in user_message:
                return ({"stories": []}, {"input_tokens": 5, "output_tokens": 5})
            return ({}, {"input_tokens": 0, "output_tokens": 0})

    class _NoTool:
        def list_all(self): return []
        def search(self, q): return []
        def get_page(self, page_id="default"): return ""

    orch = Orchestrator(
        claude=CapturingClaude(),
        jira=_NoTool(), confluence=_NoTool(), github=_NoTool(),
    )
    result = orch.run(
        transcript_text="Hiroshi Tanaka emailed alice@example.com.",
        constraint_text="",
        existing_tickets=[],
        redact_pii=True,
    )

    # The Parser saw redacted text, not raw
    assert any("[NAME_1]" in p or "[EMAIL_1]" in p for p in captured_prompts)
    assert not any("alice@example.com" in p for p in captured_prompts)
    assert not any("Hiroshi Tanaka" in p for p in captured_prompts)

    # Audit log shows the redaction event with the count summary
    assert "pii_redacted" in result["audit_trail"]
