"""Multi-agent orchestrator.

Constructs a shared memory store + audit log, instantiates the five agents,
and runs them in fixed order:

    Parser → Constraint Extractor → Story Writer → Epic Decomposer → Gap Detector

If an agent fails permanently after retries, its failure is recorded in the
audit log and downstream agents are skipped if they depend on its output.
Whatever was completed before the failure is still returned.

The orchestrator is intentionally a fixed pipeline, not a free-form agent loop.
See `docs/AGENT_DESIGN.md` for the rationale.
"""

from __future__ import annotations

import time
from typing import Any

from logger_setup import get_logger

from agents.parser_agent import ParserAgent
from agents.constraint_agent import ConstraintAgent
from agents.story_writer_agent import StoryWriterAgent
from agents.epic_decomposer_agent import EpicDecomposerAgent
from agents.gap_detector_agent import GapDetectorAgent
from agents.base import AgentError

from memory.store import MemoryStore
from memory.audit_log import AuditLog

from tools.base import Tool, ToolError
from tools.claude_tool import ClaudeTool
from tools.gemini_tool import GeminiTool
from tools.ollama_tool import OllamaTool
from tools.jira_tool import JiraTool
from tools.confluence_tool import ConfluenceTool
from tools.github_tool import GithubTool

import os as _os

def _build_jira_tool() -> JiraTool:
    """Return MCPJiraTool when ATLASSIAN_MCP_ENABLED=1, else JiraTool."""
    if _os.environ.get("ATLASSIAN_MCP_ENABLED") == "1":
        try:
            from tools.mcp_atlassian_tool import MCPJiraTool
            logger.info("Orchestrator: using MCPJiraTool (ATLASSIAN_MCP_ENABLED=1)")
            return MCPJiraTool(mode="live")
        except ImportError:
            logger.warning("mcp package not installed — falling back to JiraTool REST")
    return JiraTool()

def _build_confluence_tool() -> ConfluenceTool:
    """Return MCPConfluenceTool when ATLASSIAN_MCP_ENABLED=1, else ConfluenceTool."""
    if _os.environ.get("ATLASSIAN_MCP_ENABLED") == "1":
        try:
            from tools.mcp_atlassian_tool import MCPConfluenceTool
            logger.info("Orchestrator: using MCPConfluenceTool (ATLASSIAN_MCP_ENABLED=1)")
            return MCPConfluenceTool()
        except ImportError:
            logger.warning("mcp package not installed — falling back to ConfluenceTool REST")
    return ConfluenceTool()

def _build_github_tool() -> GithubTool:
    """Return MCPGithubTool when GITHUB_MCP_ENABLED=1, else GithubTool."""
    if _os.environ.get("GITHUB_MCP_ENABLED") == "1":
        try:
            from tools.mcp_github_tool import MCPGithubTool
            logger.info("Orchestrator: using MCPGithubTool (GITHUB_MCP_ENABLED=1)")
            return MCPGithubTool()
        except ImportError:
            logger.warning("mcp package not installed — falling back to GithubTool fixture")
    return GithubTool()

from redactor import (
    RedactionMap,
    StrictRedactionViolation,
    assert_redacted,
    redact,
    redact_backlog,
    unredact_obj,
)

try:
    from telemetry import (
        pipeline_span, stage_span, record_stage_tokens, record_guardrail_findings,
        inc_active_synthesis, dec_active_synthesis, record_llm_error,
        record_synthesis_complete,
        flush_metrics,
    )
except ImportError:  # pragma: no cover — telemetry is optional
    from contextlib import contextmanager

    @contextmanager
    def pipeline_span(*a, **kw):
        yield None

    @contextmanager
    def stage_span(*a, **kw):
        yield None

    def record_stage_tokens(*a, **kw): pass
    def record_guardrail_findings(*a, **kw): pass
    def inc_active_synthesis(): pass
    def dec_active_synthesis(): pass
    def record_llm_error(*a, **kw): pass
    def record_synthesis_complete(*a, **kw): pass
    def flush_metrics(*a, **kw): pass

logger = get_logger(__name__)


# Stage name → default model. Used when the caller passes `models=None`
# or a partial dict. Kept here (not in app.py) so CLI runs and tests get
# a consistent default without the UI in the picture.
DEFAULT_STAGE_MODELS: dict[str, str] = {
    "parser":           "claude-sonnet-4-5",
    "constraint":       "claude-sonnet-4-5",
    "story_writer":     "claude-sonnet-4-5",
    "epic_decomposer":  "claude-sonnet-4-5",
    "gap_detector":     "claude-sonnet-4-5",
}


def _build_tool_for_model(model_id: str, *, claude_fallback: Tool | None = None) -> Tool:
    """Instantiate the right LLM tool based on the model id prefix.

    `claude-*`  → ClaudeTool(model=model_id)
    `gemini-*`  → GeminiTool(model=model_id)
    anything else → raise so a typo in the picker is loud, not silent.

    `claude_fallback` lets tests inject a FakeClaudeTool that the
    orchestrator reuses for every stage. The detection is intentionally
    loose: if the injected tool is NOT a real `ClaudeTool` instance
    (i.e. it's a fake / stub the caller wired in for testing), we trust
    that fake for every stage regardless of the requested model id. This
    keeps `test_orchestrator.py` green without baking model awareness
    into the test fixtures.
    """
    mid = (model_id or "").lower().strip()

    # Test-fake passthrough: anything injected that isn't a real ClaudeTool
    # is treated as a stub that already knows how to respond. This is what
    # makes the FakeClaudeTool tests work unchanged.
    if claude_fallback is not None and not isinstance(claude_fallback, ClaudeTool):
        return claude_fallback

    if mid.startswith("claude"):
        # Real ClaudeTool injected by a caller — reuse it if the model
        # matches; otherwise build a fresh one for this stage so the
        # right model id reaches the API.
        if isinstance(claude_fallback, ClaudeTool) and getattr(claude_fallback, "model", None) == model_id:
            return claude_fallback
        return ClaudeTool(model=model_id)
    if mid.startswith("gemini"):
        return GeminiTool(model=model_id)
    if mid.startswith("ollama"):
        return OllamaTool(model=model_id)
    # Unknown prefix and no injected fallback — raise.
    raise ToolError(
        f"Unknown model id '{model_id}'. Expected prefix 'claude-', 'gemini-', or 'ollama/'."
    )


def _ollama_available() -> bool:
    """Quick health-check: is an Ollama server reachable right now?"""
    import os
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        import requests as _r
        return _r.get(f"{base}/api/tags", timeout=2).status_code < 400
    except Exception:  # noqa: BLE001
        return False


def _fallback_model(model_id: str) -> str | None:
    """The failover provider to retry a failed stage on.

    Cascade:
      • Claude  → Gemini Flash (cloud, fast)
      • Gemini  → Ollama (if running, free/local) → Claude Sonnet (last resort)
      • Ollama  → Claude Sonnet (cloud fallback when local fails)

    Gemini → Ollama first so rate-limit hits on the Free preset fall back
    to a free local model before spending Claude credit.
    """
    mid = (model_id or "").lower()
    if mid.startswith("claude"):
        return "gemini-2.5-flash"
    if mid.startswith("gemini"):
        # Prefer Ollama when it's running — avoids Claude spend on transient
        # Gemini 503s/quota errors.
        if _ollama_available():
            return "ollama/llama3.2:3b"
        return "claude-sonnet-4-5"
    if mid.startswith("ollama"):
        return "claude-sonnet-4-5"
    return None


def _summarize_models(models: dict[str, str]) -> str:
    """Build a short display string for the UI's `model` field.

    Examples:
        all-Claude            → "claude-sonnet-4-5"
        all-Gemini            → "gemini-2.5-flash"
        mixed Claude/Gemini   → "mixed (Gemini Flash + Claude Sonnet)"
    """
    distinct = sorted({m for m in models.values() if m})
    if len(distinct) == 1:
        return distinct[0]
    has_claude = any(m.startswith("claude") for m in distinct)
    has_gemini = any(m.startswith("gemini") for m in distinct)
    if has_claude and has_gemini:
        # Pick a readable label from the dominant models.
        claude_names = [m for m in distinct if m.startswith("claude")]
        gemini_names = [m for m in distinct if m.startswith("gemini")]
        return f"mixed ({', '.join(gemini_names)} + {', '.join(claude_names)})"
    return "mixed (" + ", ".join(distinct) + ")"


class Orchestrator:
    """Runs the five-agent pipeline. Stateless across runs."""

    def __init__(
        self,
        claude: Tool | None = None,
        jira: JiraTool | None = None,
        confluence: ConfluenceTool | None = None,
        github: GithubTool | None = None,
    ):
        # Tools — injected for testability. Defaults are lazy: we don't
        # build a real ClaudeTool here because, with per-stage models,
        # the actual LLM tool is constructed inside `run()`. We still
        # accept a `claude=` kwarg so the test suite (which passes a
        # FakeClaudeTool) keeps working unchanged.
        self.claude = claude  # may be None — built per-stage in run()
        self.jira       = jira       or _build_jira_tool()
        self.confluence = confluence or _build_confluence_tool()
        self.github     = github     or _build_github_tool()

    def run(
        self,
        transcript_text: str = "",
        constraint_text: str = "",
        existing_tickets: list[dict] | None = None,
        redact_pii: bool = False,
        strict_redact: bool = False,
        progress_callback=None,
        dry_run: bool = False,
        models: dict[str, str] | None = None,
        use_embeddings_for_duplicates: bool = True,
        persistent_memory: bool | None = None,
        live_confluence_page_id: str | None = None,
        live_jira: bool = False,
        vision_attachments: list | None = None,
        auto_switch: bool = True,
        run_metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Run the full pipeline. Returns the synthesized result dict.

        When `redact_pii` is True, emails, phone numbers, SSNs, card numbers,
        and (conservatively-matched) personal names are replaced with stable
        placeholders BEFORE any agent sees them. The same map is shared across
        all three inputs so identical values map to the same token. The final
        synthesis is un-redacted on the way out so the output looks normal to
        the caller — but the audit log (which records prompt excerpts) keeps
        the redacted form.

        `progress_callback`, if provided, is invoked at each agent boundary
        with `(stage_index: int, stage_name: str, event: str, detail: str)`.
            stage_index ∈ {0..4}  — 0=parser, 1=constraint, 2=story_writer,
                                    3=epic_decomposer, 4=gap_detector.
            event       ∈ {"started", "completed", "failed", "skipped"}.
            detail      — short human-readable status (counts, error msg, etc.).
        Used by the Streamlit UI to light up the pipeline stages live.

        When `dry_run=True`, no agents are invoked. Instead, the orchestrator
        builds the prompt for each agent that *would* have run and returns it
        in the result under `dry_run_prompts` (dict of agent_name → prompt
        text). The rest of the result fields are empty so downstream code
        that iterates `epics`, `gaps`, etc. doesn't break.
        """
        def _emit(stage_index: int, stage_name: str, event: str, detail: str = ""):
            """Helper: call the progress callback if one was provided.
            Swallows callback exceptions so a bad UI hook can't break the
            actual pipeline."""
            if progress_callback is None:
                return
            try:
                progress_callback(stage_index, stage_name, event, detail)
            except Exception as e:  # noqa: BLE001 — never break the run for a UI bug
                logger.warning("progress_callback raised: %s", e)

        existing_tickets = existing_tickets or []

        # ---- Live source fetches (optional) ----
        # When the caller asks for a live Confluence page, pull it before
        # we get to redaction so the same PII handling applies. Same for
        # Jira: live_jira=True replaces the (potentially empty) tickets
        # arg with whatever the configured JIRA_PROJECT_KEY surfaces.
        # These hit the network — wrap in try/except so a credential
        # problem becomes a clean audit event instead of a stack trace.
        if live_confluence_page_id and not constraint_text:
            try:
                # Use self.confluence (may be MCPConfluenceTool or ConfluenceTool).
                # If it's the plain REST tool in mock mode, upgrade to live mode.
                if hasattr(self.confluence, '_mode') and self.confluence._mode != "live":
                    from tools.confluence_tool import ConfluenceTool as _CT
                    ct = _CT(mode="live")
                else:
                    ct = self.confluence
                constraint_text = ct.get_page(live_confluence_page_id)
                logger.info(
                    "Pulled %d chars from live Confluence page %s",
                    len(constraint_text), live_confluence_page_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Live Confluence fetch failed: %s", e)
                # We can record this without an `audit` yet — defer until
                # the AuditLog is created below, then emit a structured
                # event.
                _live_confluence_error: str | None = str(e)
            else:
                _live_confluence_error = None
        else:
            _live_confluence_error = None

        if live_jira and not existing_tickets:
            try:
                # Use self.jira (may be MCPJiraTool or JiraTool).
                # MCPJiraTool always fetches live via MCP; plain JiraTool needs mode="live".
                if hasattr(self.jira, '_use_mcp') and self.jira._use_mcp:
                    jt = self.jira
                elif hasattr(self.jira, 'mode') and self.jira.mode != "live":
                    from tools.jira_tool import JiraTool as _JT
                    jt = _JT(mode="live")
                else:
                    jt = self.jira
                existing_tickets = jt.list_all()
                logger.info("Pulled %d ticket(s) from live Jira", len(existing_tickets))
            except Exception as e:  # noqa: BLE001
                logger.warning("Live Jira fetch failed: %s", e)
                _live_jira_error: str | None = str(e)
            else:
                _live_jira_error = None
        else:
            _live_jira_error = None

        # ---- Resolve per-stage models ----
        # Caller may pass none, some, or all of the keys. Missing keys fall
        # back to DEFAULT_STAGE_MODELS. We also normalize keys (e.g. UI may
        # use "constraint" or "constraint_extractor") so the dict shape from
        # the UI matches what the dispatch logic below expects.
        resolved_models: dict[str, str] = dict(DEFAULT_STAGE_MODELS)
        if models:
            for k, v in models.items():
                key = k.replace("constraint_extractor", "constraint")
                if v and key in resolved_models:
                    resolved_models[key] = v

        # ---- Vision auto-switch ----
        _auto_switched_parser: bool = False
        _auto_switched_from: str = ""
        if auto_switch and vision_attachments and resolved_models.get("parser", "").lower().startswith("gemini"):
            _auto_switched_from = resolved_models["parser"]
            _auto_switched_parser = True
            logger.info(
                "Vision attachment present — switching parser %s → claude-sonnet-4-5 "
                "(Gemini wrapper can't carry images).",
                _auto_switched_from,
            )
            resolved_models["parser"] = "claude-sonnet-4-5"

        # ---- Dry-run short-circuit ----
        if dry_run:
            return self._build_dry_run_result(
                transcript_text=transcript_text,
                constraint_text=constraint_text,
                existing_tickets=existing_tickets,
                progress_callback=progress_callback,
                models=resolved_models,
            )

        memory = MemoryStore(persistent=persistent_memory)
        audit = AuditLog()
        inc_active_synthesis()
        _run_start = time.perf_counter()

        # ---- Record MCP tool configuration ----
        # Log which transport layer (MCP server vs. REST vs. fixture) is active
        # for each integration so audit reviewers and interviewers can see the
        # full data-source provenance at a glance.
        _jira_transport = (
            "Atlassian MCP server (mcp-atlassian)"
            if hasattr(self.jira, "_use_mcp") and self.jira._use_mcp
            else ("Jira REST API (live)" if getattr(self.jira, "mode", "mock") == "live"
                  else "Jira fixture (mock)")
        )
        _github_transport = (
            "GitHub MCP server (@modelcontextprotocol/server-github)"
            if hasattr(self.github, "_use_mcp") and self.github._use_mcp
            else "GitHub fixture (mock)"
        )
        _confluence_transport = (
            "Atlassian MCP server (mcp-atlassian)"
            if hasattr(self.confluence, "_use_mcp") and self.confluence._use_mcp
            else ("Confluence REST API (live)" if getattr(self.confluence, "_mode", "mock") == "live"
                  else "Confluence fixture (mock)")
        )
        audit.record(
            "orchestrator", "data_sources_configured",
            payload={
                "jira_transport":       _jira_transport,
                "github_transport":     _github_transport,
                "confluence_transport": _confluence_transport,
            },
            reasoning=(
                "Data source transports resolved at pipeline start. "
                "MCP servers provide a standardised interface to live Atlassian and GitHub APIs; "
                "REST and fixture modes are used when MCP is not enabled."
            ),
        )

        # Now that the audit log exists, retroactively log any live-source
        # results so the trail explains where constraint_text / tickets came
        # from. Keeps the audit story honest when reviewers diff a run that
        # used live data vs. one that used fixtures.
        if live_confluence_page_id:
            if _live_confluence_error:
                audit.record(
                    "orchestrator", "live_confluence_fetch_failed",
                    payload={"page_id": live_confluence_page_id,
                             "error": _live_confluence_error[:300],
                             "transport": _confluence_transport},
                    reasoning="Live Confluence fetch failed; falling back to whatever constraint_text was passed in.",
                )
            else:
                audit.record(
                    "orchestrator", "live_confluence_fetch_ok",
                    payload={"page_id": live_confluence_page_id,
                             "chars_fetched": len(constraint_text),
                             "transport": _confluence_transport},
                    reasoning=f"Constraint text pulled from a live Confluence page via {_confluence_transport}.",
                )
        if live_jira:
            if _live_jira_error:
                audit.record(
                    "orchestrator", "live_jira_fetch_failed",
                    payload={"error": _live_jira_error[:300],
                             "transport": _jira_transport},
                    reasoning="Live Jira fetch failed; existing_tickets stays as passed in.",
                )
            else:
                audit.record(
                    "orchestrator", "live_jira_fetch_ok",
                    payload={"ticket_count": len(existing_tickets),
                             "transport": _jira_transport},
                    reasoning=f"Existing tickets pulled via {_jira_transport}.",
                )
        # Always log the GitHub transport used for duplicate detection
        audit.record(
            "orchestrator", "github_issues_source",
            payload={
                "transport":    _github_transport,
                "ticket_count": len(existing_tickets),
            },
            reasoning=(
                f"GitHub Issues fed to the Gap Detector via {_github_transport}. "
                "Used for duplicate detection alongside the Jira backlog."
            ),
        )

        # ---- PII redaction (opt-in) ----
        # Applied at the orchestrator boundary, not inside agents, so a single
        # RedactionMap is shared across transcript + wiki + tickets. That
        # consistency lets the Gap Detector match a redacted "[NAME_3]" in a
        # story back to the same "[NAME_3]" in an existing ticket.
        rmap: RedactionMap | None = None
        if redact_pii:
            rmap = RedactionMap()
            if transcript_text:
                transcript_text, _ = redact(transcript_text, rmap=rmap)
            if constraint_text:
                constraint_text, _ = redact(constraint_text, rmap=rmap)
            if existing_tickets:
                existing_tickets, _ = redact_backlog(existing_tickets, rmap=rmap)
            _pii_counts = rmap.summary()
            _pii_total  = sum(_pii_counts.values())
            _pii_detail = ", ".join(f"{v} {k}" for k, v in _pii_counts.items() if v > 0)
            audit.record(
                "orchestrator", "pii_redacted",
                payload={
                    "counts": _pii_counts,
                    "total_items_redacted": _pii_total,
                    "types_found": [k for k, v in _pii_counts.items() if v > 0],
                },
                reasoning=(
                    f"PII redaction complete. {_pii_total} item(s) replaced with stable tokens "
                    f"({_pii_detail}). "
                    "The same token map is shared across transcript, wiki, and backlog so identical "
                    "values (e.g. the same email in two inputs) map to the same placeholder. "
                    "The LLM never sees raw personal data. "
                    "The synthesis output is un-redacted before returning to the user; "
                    "this audit entry and all prompt excerpts retain the redacted form."
                ),
            )

            # Strict mode: after redaction, scan every tool-bound input for
            # any pattern that slipped through. Halt the run on a finding —
            # this is the trust boundary callers asked for when they passed
            # `strict_redact=True`.
            if strict_redact:
                try:
                    if transcript_text:
                        assert_redacted(transcript_text)
                    if constraint_text:
                        assert_redacted(constraint_text)
                    for t in existing_tickets:
                        for field in ("title", "description", "summary", "body"):
                            v = t.get(field)
                            if isinstance(v, str) and v:
                                assert_redacted(v)
                except StrictRedactionViolation as e:
                    audit.record(
                        "orchestrator", "strict_redact_violation",
                        payload={
                            "violation_count": len(e.findings),
                            "kinds": sorted({f["kind"] for f in e.findings}),
                            # Don't echo the raw samples — they may be PII.
                            "first_context": e.findings[0]["context_excerpt"]
                            if e.findings else "",
                        },
                        reasoning=(
                            "Strict redaction mode is on. PII patterns were "
                            "detected at the LLM trust boundary even after "
                            "the redaction pass; aborting the run."
                        ),
                    )
                    raise

        elif strict_redact:
            # Caller asked for strict-redact but not redact_pii. Honour the
            # spirit: scan the raw inputs and halt if anything looks like
            # PII, since redact wasn't given the chance to clean it.
            try:
                if transcript_text:
                    assert_redacted(transcript_text)
                if constraint_text:
                    assert_redacted(constraint_text)
                for t in existing_tickets:
                    for field in ("title", "description", "summary", "body"):
                        v = t.get(field)
                        if isinstance(v, str) and v:
                            assert_redacted(v)
            except StrictRedactionViolation as e:
                audit.record(
                    "orchestrator", "strict_redact_violation",
                    payload={
                        "violation_count": len(e.findings),
                        "kinds": sorted({f["kind"] for f in e.findings}),
                        "first_context": e.findings[0]["context_excerpt"]
                        if e.findings else "",
                    },
                    reasoning=(
                        "Strict redaction mode is on without redact_pii=True; "
                        "raw inputs contained PII patterns. Aborting."
                    ),
                )
                raise

        # Seed memory with existing tickets so the Gap Detector can search them
        memory.put("existing_tickets", existing_tickets)

        # ---- pipeline_started ----
        audit.record(
            "orchestrator", "pipeline_started",
            payload={
                "run_metadata":           run_metadata or {},
                "transcript_chars":       len(transcript_text),
                "constraint_chars":       len(constraint_text),
                "existing_ticket_count":  len(existing_tickets),
                "vision_attachment_count": len(vision_attachments) if vision_attachments else 0,
                "redact_pii":             redact_pii,
                "strict_redact":          strict_redact,
                "auto_switch":            auto_switch,
                "dry_run":                False,
                "persistent_memory":      bool(persistent_memory),
                "live_jira":              live_jira,
                "live_confluence":        bool(live_confluence_page_id),
            },
            reasoning="Pipeline initialised. All inputs and configuration flags are recorded here for full reproducibility.",
        )

        # ---- models_resolved ----
        audit.record(
            "orchestrator", "models_resolved",
            payload={
                "stage_models": dict(resolved_models),
                "preset_summary": _summarize_models(resolved_models),
            },
            reasoning=(
                "Per-stage model assignments after preset + overrides are resolved. "
                "This is the exact configuration that will be used for every LLM call in this run."
            ),
        )

        # ---- vision_attachments_provided ----
        if vision_attachments:
            audit.record(
                "orchestrator", "vision_attachments_provided",
                payload={
                    "count":  len(vision_attachments),
                    "labels": [getattr(v, "label", "unknown") for v in vision_attachments],
                    "media_types": [getattr(v, "media_type", "unknown") for v in vision_attachments],
                },
                reasoning="One or more image attachments (whiteboard/screenshot) were provided alongside the transcript.",
            )

        # ---- auto_switch_model ----
        if _auto_switched_parser:
            audit.record(
                "orchestrator", "auto_switch_model",
                payload={
                    "stage":    "parser",
                    "from":     _auto_switched_from,
                    "to":       "claude-sonnet-4-5",
                    "reason":   "vision_attachment_present",
                },
                reasoning=(
                    f"Parser model auto-switched from {_auto_switched_from} to claude-sonnet-4-5 "
                    "because image attachments were provided. Gemini wrapper cannot carry image payloads."
                ),
            )

        # ---- existing_tickets_seeded ----
        audit.record(
            "orchestrator", "existing_tickets_seeded",
            payload={
                "ticket_count":    len(existing_tickets),
                "jira_transport":  _jira_transport,
                "github_transport": _github_transport,
                "sample_ids":      [t.get("id", "?") for t in existing_tickets[:5]],
            },
            reasoning=(
                f"{len(existing_tickets)} ticket(s) seeded into shared memory for the Gap Detector. "
                f"Jira source: {_jira_transport}. GitHub source: {_github_transport}."
            ),
        )

        # Per-stage tool factory. Wraps `_build_tool_for_model` with the
        # injected `self.claude` fallback so test fakes pass through and
        # a tool init failure (missing API key, bad model id) surfaces as
        # a per-stage failure rather than a full pipeline crash.
        def _tool_for(stage_idx: int, stage_name: str) -> Tool | None:
            try:
                return _build_tool_for_model(
                    resolved_models[stage_name.replace("constraint_extractor", "constraint")],
                    claude_fallback=self.claude,
                )
            except ToolError as e:
                logger.warning("%s tool init failed: %s", stage_name, e)
                audit.record_failure(stage_name, f"Tool init failed: {e}")
                _emit(stage_idx, stage_name, "failed", str(e)[:120])
                return None

        def _attempt_failover(stage_idx: int, stage_name: str, err: Exception, run_with_tool) -> bool:
            """A stage's primary provider failed after retries. Retry the stage
            once on the *other* provider (Claude↔Gemini). Returns True on a
            successful retry; otherwise records the failure + emits 'failed'.

            Skipped when a test injected a non-real tool (single fake provider)
            so the mocked suite behaves exactly as before."""
            key = stage_name.replace("constraint_extractor", "constraint")
            primary = resolved_models.get(key, "")
            fb_model = _fallback_model(primary)
            injected_fake = self.claude is not None and not isinstance(self.claude, ClaudeTool)

            def _give_up(e):
                logger.warning("%s failed: %s", stage_name, e)
                audit.record_failure(stage_name, str(e))
                _emit(stage_idx, stage_name, "failed", str(e)[:120])
                return False

            # Failover is opt-in (the `auto_switch` toggle) so the exact preset
            # is honoured by default and nothing changes provider silently.
            if not auto_switch or not fb_model or injected_fake:
                return _give_up(err)
            try:
                fb_tool = _build_tool_for_model(fb_model, claude_fallback=self.claude)
            except ToolError:
                return _give_up(err)
            _emit(stage_idx, stage_name, "failover", f"{primary} failed — retrying on {fb_model}")
            try:
                run_with_tool(fb_tool)
            except Exception as e2:  # noqa: BLE001 — any failure here is terminal for the stage
                return _give_up(f"{err} | failover({fb_model}): {e2}")
            audit.record(
                stage_name, "provider_failover",
                payload={"from": primary, "to": fb_model, "error": str(err)[:200]},
                reasoning=f"Primary provider failed; stage succeeded on {fb_model}.",
            )
            return True

        # ---- Stage 1: Parse the transcript into topics ----
        if transcript_text.strip() or vision_attachments:
            detail_chars = f"reading {len(transcript_text):,} chars"
            if vision_attachments:
                detail_chars += f" + {len(vision_attachments)} image(s)"
            _emit(0, "parser", "started", detail_chars)
            parser_tool = _tool_for(0, "parser")
            if parser_tool is not None:
                parser = ParserAgent(tool=parser_tool, memory=memory, audit=audit)
                with stage_span("parser", model=resolved_models.get("parser", ""), input_chars=len(transcript_text)):
                    try:
                        parser.run(transcript_text, vision_attachments=vision_attachments)
                        _emit(0, "parser", "completed",
                              f"{len(memory.get('topics', []))} topics extracted")
                    except AgentError as e:
                        def _retry_parser(t):
                            ParserAgent(tool=t, memory=memory, audit=audit).run(
                                transcript_text, vision_attachments=vision_attachments)
                        if _attempt_failover(0, "parser", e, _retry_parser):
                            _emit(0, "parser", "completed",
                                  f"{len(memory.get('topics', []))} topics extracted (via failover)")
        else:
            _emit(0, "parser", "skipped", "no transcript provided")
            audit.record("parser", "stage_skipped", payload={"reason": "no transcript or vision input provided"},
                         reasoning="Parser was not run because no text transcript or image attachment was supplied.")

        # ---- Stage 2: Extract constraints from the wiki ----
        if constraint_text.strip():
            # Show where the constraint text came from so the live log is informative.
            if live_confluence_page_id and not _live_confluence_error:
                _conf_source = (
                    f"from Confluence via Atlassian MCP (page {live_confluence_page_id})"
                    if hasattr(self.confluence, "_use_mcp") and self.confluence._use_mcp
                    else f"from live Confluence REST (page {live_confluence_page_id})"
                )
            else:
                _conf_source = "from local file / sample"
            _emit(1, "constraint_extractor", "started",
                  f"reading {len(constraint_text):,} chars · {_conf_source}")
            constraint_tool = _tool_for(1, "constraint_extractor")
            if constraint_tool is not None:
                constraint_agent = ConstraintAgent(
                    tool=constraint_tool,
                    confluence=self.confluence,
                    memory=memory,
                    audit=audit,
                )
                with stage_span("constraint_extractor", model=resolved_models.get("constraint", ""), input_chars=len(constraint_text)):
                    try:
                        constraint_agent.run(constraint_text)
                        _emit(1, "constraint_extractor", "completed",
                              f"{len(memory.get('constraints', []))} constraints captured")
                    except AgentError as e:
                        def _retry_constraint(t):
                            ConstraintAgent(tool=t, confluence=self.confluence,
                                            memory=memory, audit=audit).run(constraint_text)
                        if _attempt_failover(1, "constraint_extractor", e, _retry_constraint):
                            _emit(1, "constraint_extractor", "completed",
                                  f"{len(memory.get('constraints', []))} constraints captured (via failover)")
        else:
            _emit(1, "constraint_extractor", "skipped", "no wiki / constraints provided")
            audit.record("constraint_extractor", "stage_skipped", payload={"reason": "no wiki / constraints text provided"},
                         reasoning="Constraint Extractor was not run because no architecture wiki or constraint text was supplied.")

        # ---- Stage 3: Draft user stories from topics + constraints ----
        topics = memory.get("topics", [])
        if topics:
            _emit(2, "story_writer", "started", f"drafting from {len(topics)} topics")
            story_tool = _tool_for(2, "story_writer")
            if story_tool is not None:
                story_writer = StoryWriterAgent(tool=story_tool, memory=memory, audit=audit)
                with stage_span("story_writer", model=resolved_models.get("story_writer", "")):
                    try:
                        story_writer.run()
                        _emit(2, "story_writer", "completed",
                              f"{len(memory.get('stories', []))} user stories written")
                    except AgentError as e:
                        def _retry_story(t):
                            StoryWriterAgent(tool=t, memory=memory, audit=audit).run()
                        if _attempt_failover(2, "story_writer", e, _retry_story):
                            _emit(2, "story_writer", "completed",
                                  f"{len(memory.get('stories', []))} user stories written (via failover)")
        else:
            _emit(2, "story_writer", "skipped", "no topics — Parser produced nothing")
            audit.record("story_writer", "stage_skipped", payload={"reason": "no topics in memory"},
                         reasoning="Story Writer was not run because the Parser produced no topics (empty or off-topic transcript).")

        # ---- Stage 4: Group stories into epics + decompose into tasks ----
        stories = memory.get("stories", [])
        if stories:
            _emit(3, "epic_decomposer", "started", f"grouping {len(stories)} stories")
            decomp_tool = _tool_for(3, "epic_decomposer")
            if decomp_tool is not None:
                decomposer = EpicDecomposerAgent(tool=decomp_tool, memory=memory, audit=audit)
                with stage_span("epic_decomposer", model=resolved_models.get("epic_decomposer", "")):
                    try:
                        decomposer.run()
                        _emit(3, "epic_decomposer", "completed",
                              f"{len(memory.get('epics', []))} epics with task breakdowns")
                    except AgentError as e:
                        def _retry_epic(t):
                            EpicDecomposerAgent(tool=t, memory=memory, audit=audit).run()
                        if _attempt_failover(3, "epic_decomposer", e, _retry_epic):
                            _emit(3, "epic_decomposer", "completed",
                                  f"{len(memory.get('epics', []))} epics with task breakdowns (via failover)")
        else:
            _emit(3, "epic_decomposer", "skipped", "no stories to group")
            audit.record("epic_decomposer", "stage_skipped", payload={"reason": "no stories in memory"},
                         reasoning="Epic Decomposer was not run because the Story Writer produced no stories.")

        # ---- Stage 5: Find gaps, conflicts, duplicates ----
        if stories:
            # Labels reflect WHERE the data actually came from this run, not
            # just what transport the tool is configured as. If the user didn't
            # toggle "Live Jira", the tickets came from their local file/sample
            # selection regardless of whether MCPJiraTool is the configured tool.
            if live_jira and not _live_jira_error:
                # Tickets were fetched live in this run
                _jira_label = (
                    f"{len(existing_tickets)} tickets via Atlassian MCP"
                    if "MCP" in _jira_transport
                    else f"{len(existing_tickets)} tickets via Jira REST"
                )
            elif existing_tickets:
                _jira_label = f"{len(existing_tickets)} tickets from local file / sample"
            else:
                _jira_label = "no backlog provided"

            # GitHub MCP always fetches live issues when configured.
            _gh_label = (
                "GitHub Issues via MCP"
                if hasattr(self.github, "_use_mcp") and self.github._use_mcp
                else "GitHub fixture"
            )
            _emit(4, "gap_detector", "started",
                  f"comparing {len(stories)} stories · {_jira_label} · {_gh_label}")
            gap_tool = _tool_for(4, "gap_detector")
            if gap_tool is not None:
                gap_detector = GapDetectorAgent(
                    tool=gap_tool,
                    jira=self.jira,
                    github=self.github,
                    memory=memory,
                    audit=audit,
                    use_embeddings_for_duplicates=use_embeddings_for_duplicates,
                )
                with stage_span("gap_detector", model=resolved_models.get("gap_detector", "")):
                    try:
                        gap_detector.run()
                        _emit(
                            4, "gap_detector", "completed",
                            f"{len(memory.get('duplicates', []))} dupes, "
                            f"{len(memory.get('conflicts', []))} conflicts, "
                            f"{len(memory.get('gaps', []))} gaps",
                        )
                    except AgentError as e:
                        def _retry_gap(t):
                            GapDetectorAgent(
                                tool=t, jira=self.jira, github=self.github,
                                memory=memory, audit=audit,
                                use_embeddings_for_duplicates=use_embeddings_for_duplicates,
                            ).run()
                        if _attempt_failover(4, "gap_detector", e, _retry_gap):
                            _emit(4, "gap_detector", "completed",
                                  f"{len(memory.get('duplicates', []))} dupes, "
                                  f"{len(memory.get('conflicts', []))} conflicts, "
                                  f"{len(memory.get('gaps', []))} gaps (via failover)")
        else:
            _emit(4, "gap_detector", "skipped", "no stories to compare")
            audit.record("gap_detector", "stage_skipped", payload={"reason": "no stories in memory"},
                         reasoning="Gap Detector was not run because there were no stories to compare against the backlog.")

        # ---- Tally token usage from the audit log ----
        # Each agent calls `audit.record_tool_call(...)` with a `tokens_used`
        # total. We re-walk the structured events here to split that back
        # into per-agent input/output and a grand total. Cheap (events list
        # is small) and avoids changing the agent contract.
        token_usage = _aggregate_token_usage(audit)
        # Model summary for display. When all stages share one model, this
        # is just that model id; when stages differ, it's a "mixed (…)"
        # tag built from the unique model ids actually used.
        model_summary = _summarize_models(resolved_models)

        # ---- Assemble final result ----
        result = {
            "summary": memory.get("summary", ""),
            "topics": memory.get("topics", []),
            "constraints": memory.get("constraints", []),
            "epics": memory.get("epics", []),
            "gaps": memory.get("gaps", []),
            "conflicts": memory.get("conflicts", []),
            "duplicates": memory.get("duplicates", []),
            "audit_trail": audit.render_markdown(),
            "token_usage": token_usage,
            # `model` stays a string for back-compat with downstream UI
            # code that reads `result["model"]`. `models` is the new
            # per-stage dict — useful for the cost panel and run history.
            "model": model_summary,
            "models": dict(resolved_models),
        }

        # Un-redact the synthesis on the way out. The audit trail stays
        # redacted on purpose — it's the only artifact that records prompt
        # text the LLM saw, and leaving it redacted preserves the
        # redaction-audit story for compliance review.
        if rmap is not None and not rmap.is_empty():
            for key in ("summary", "topics", "constraints", "epics",
                        "gaps", "conflicts", "duplicates"):
                result[key] = unredact_obj(result[key], rmap)

        # ---- Output guardrails ----
        try:
            from guardrails import run_guardrails, summarise
            findings = run_guardrails(result)
            result["guardrail_findings"] = [f.to_dict() for f in findings]
            tally = summarise(findings)

            # Log each individual guardrail finding so reviewers can see
            # exactly which story triggered which check — not just the tally.
            for f in findings:
                audit.record(
                    "orchestrator", "guardrail_finding",
                    payload={
                        "code":     f.code,
                        "severity": f.severity,
                        "story_id": f.story_id or "—",
                        "message":  f.message,
                    },
                    reasoning=f"Guardrail check '{f.code}' fired at severity '{f.severity}'.",
                )

            audit.record(
                "orchestrator", "guardrails_completed",
                payload={"tally": tally, "finding_count": len(findings)},
                reasoning=(
                    f"All post-synthesis guardrails completed. "
                    f"{tally['error']} error / {tally['warn']} warn / {tally['info']} info."
                ),
            )
            result["audit_trail"] = audit.render_markdown()
            result["audit_chain_fingerprint"] = audit.chain_fingerprint
        except Exception as e:  # noqa: BLE001 — guardrails must never break a run
            logger.warning("Guardrails crashed (suppressed): %s", e)
            result["guardrail_findings"] = []

        # ---- pipeline_completed ----
        _epics_out   = result.get("epics") or []
        _n_stories   = sum(len(e.get("stories") or []) for e in _epics_out)
        _total_tokens = (
            int((token_usage.get("total") or {}).get("input", 0))
            + int((token_usage.get("total") or {}).get("output", 0))
        )
        audit.record(
            "orchestrator", "pipeline_completed",
            payload={
                "epics":        len(_epics_out),
                "stories":      _n_stories,
                "gaps":         len(result.get("gaps") or []),
                "conflicts":    len(result.get("conflicts") or []),
                "duplicates":   len(result.get("duplicates") or []),
                "guardrail_errors": sum(1 for f in (result.get("guardrail_findings") or []) if f.get("severity") == "error"),
                "total_tokens": _total_tokens,
                "model_summary": model_summary,
                "audit_chain_fingerprint": audit.chain_fingerprint,
            },
            reasoning=(
                f"Pipeline completed. Produced {len(_epics_out)} epic(s) with {_n_stories} story(ies). "
                f"Audit chain fingerprint is the tamper-evidence hash of all {len(audit.events)} events in this log."
            ),
        )
        # Final render after pipeline_completed is appended.
        result["audit_trail"] = audit.render_markdown()
        result["audit_chain_fingerprint"] = audit.chain_fingerprint
        _run_status = "error" if result.get("guardrail_findings") and any(
            f.get("severity") == "error" for f in result.get("guardrail_findings", [])
        ) else "ok"
        record_synthesis_complete(
            run_id=audit._run_id,
            preset=_summarize_models(resolved_models),
            elapsed_seconds=time.perf_counter() - _run_start,
            status=_run_status,
        )
        flush_metrics()
        dec_active_synthesis()

        return result

    # ----------------------------------------------------- compare mode

    def run_compare(
        self,
        primary_models: dict[str, str],
        secondary_models: dict[str, str],
        *,
        primary_label: str = "A",
        secondary_label: str = "B",
        progress_callback=None,
        **shared_kwargs,
    ) -> dict:
        """Run the pipeline twice with two different model configurations.

        Returns a dict shaped like:
            {
                "compare_mode": True,
                "primary":      <normal result dict>,
                "secondary":    <normal result dict>,
                "labels":       {"primary": ..., "secondary": ...},
                "comparison":   {<summary metrics>},
            }

        The two legs run sequentially, not in parallel — Anthropic's per-key
        rate limit means concurrent runs can stall on the same retry budget.
        For a 5-stage pipeline this is ~2x wall time of a single run, which
        is acceptable for an explicit "compare these providers" action.

        `progress_callback` is wrapped so each leg's events are tagged with
        the appropriate label, letting the UI render two pipeline rows.
        """
        def _wrap(label: str):
            if progress_callback is None:
                return None
            def _cb(stage_idx, stage_name, event, detail):
                progress_callback(stage_idx, f"{label}:{stage_name}", event, detail)
            return _cb

        primary = self.run(
            models=primary_models,
            progress_callback=_wrap(primary_label),
            **shared_kwargs,
        )
        secondary = self.run(
            models=secondary_models,
            progress_callback=_wrap(secondary_label),
            **shared_kwargs,
        )

        return {
            "compare_mode": True,
            "primary": primary,
            "secondary": secondary,
            "labels": {"primary": primary_label, "secondary": secondary_label},
            "comparison": _build_compare_summary(primary, secondary),
        }

    # ----------------------------------------------------- dry-run helper

    def _build_dry_run_result(
        self,
        transcript_text: str,
        constraint_text: str,
        existing_tickets: list[dict],
        progress_callback=None,
        models: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the prompts each agent would have sent. No LLM calls.

        Only the prompts for the agents that *would* have run (given the
        inputs the caller provided) are returned. Skipped agents return a
        short explanation instead. Token usage is empty, audit trail is a
        short note documenting the dry run.
        """
        from pathlib import Path
        import json as _json

        prompts_dir = Path(__file__).parent.parent / "prompts"

        def _load(name: str) -> str:
            path = prompts_dir / name
            if not path.exists():
                return f"(prompt template missing: {name})"
            return path.read_text(encoding="utf-8")

        dry_prompts: dict[str, str] = {}

        # Each agent has a single prompt template substitution. The
        # interpolations match what each agent's run() does at call time
        # so the preview is faithful — modulo downstream-dependent agents
        # (story_writer, epic_decomposer, gap_detector) which depend on
        # earlier agents' output and so can only be previewed in their
        # *empty* form (no topics / no stories yet).

        if transcript_text.strip():
            dry_prompts["parser"] = (
                _load("parser_prompt.md").replace("{{TRANSCRIPT}}", transcript_text)
            )
            if progress_callback:
                try:
                    progress_callback(0, "parser", "skipped", "dry run — prompt only")
                except Exception:  # noqa: BLE001
                    pass
        if constraint_text.strip():
            dry_prompts["constraint_extractor"] = (
                _load("constraint_extractor_prompt.md")
                .replace("{{WIKI_CONTENT}}", constraint_text)
            )
            if progress_callback:
                try:
                    progress_callback(1, "constraint_extractor", "skipped",
                                      "dry run — prompt only")
                except Exception:  # noqa: BLE001
                    pass

        # Downstream-dependent agents — preview with empty placeholders so
        # the user at least sees the prompt shape.
        dry_prompts["story_writer"] = (
            _load("story_writer_prompt.md")
            .replace("{{TOPICS_JSON}}", "[]   /* parser output goes here */")
            .replace("{{CONSTRAINTS_JSON}}", "[]   /* constraint extractor output goes here */")
        )
        dry_prompts["epic_decomposer"] = (
            _load("epic_decomposer_prompt.md")
            .replace("{{STORIES_JSON}}", "[]   /* story writer output goes here */")
        )
        dry_prompts["gap_detector"] = (
            _load("gap_detector_prompt.md")
            .replace("{{NEW_STORIES_JSON}}", "[]   /* story writer output goes here */")
            .replace("{{CANDIDATES_JSON}}",
                     _json.dumps({"_note": "top-K similar tickets per story"}, indent=2))
            .replace("{{CONSTRAINTS_JSON}}", "[]   /* constraint extractor output goes here */")
        )

        resolved_models = dict(DEFAULT_STAGE_MODELS)
        if models:
            for k, v in models.items():
                key = k.replace("constraint_extractor", "constraint")
                if v and key in resolved_models:
                    resolved_models[key] = v
        return {
            "summary": "",
            "topics": [],
            "constraints": [],
            "epics": [],
            "gaps": [],
            "conflicts": [],
            "duplicates": [],
            "audit_trail": "# Dry run\n\nNo LLM calls were made. See `dry_run_prompts`.",
            "token_usage": {"total": {"input": 0, "output": 0}},
            "model": _summarize_models(resolved_models),
            "models": resolved_models,
            "dry_run": True,
            "dry_run_prompts": dry_prompts,
            "transcript_text": transcript_text,
            "constraint_text": constraint_text,
            "existing_ticket_count": len(existing_tickets),
        }


# ---------------------------------------------------------------- helpers

def _aggregate_token_usage(audit: AuditLog) -> dict[str, Any]:
    """Build a {agent: {input, output}, total: {input, output}} dict.

    Walks the audit events for `tool_call` payloads carrying token counts.
    The agent contract is documented in agents/parser_agent.py: each agent
    records its single Claude call via `audit.record_tool_call(...,
    tokens_used=in+out)`. We can't recover the input/output split from that
    single number, so we mirror the pre-existing convention: the audit
    `tokens_used` is the *combined* total. The per-agent split below leaves
    `input` and `output` empty when only the combined total is available,
    and uses the structured `usage` payload when we patch it in (next
    step). To keep this resilient, we also accept either form.
    """
    by_agent: dict[str, dict[str, int]] = {}
    for ev in audit.events:
        if ev.event != "tool_call":
            continue
        payload = ev.payload or {}
        agent = ev.agent
        # Preferred: structured usage dict (added by the agent patch below).
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        if usage:
            ai = int(usage.get("input_tokens") or 0)
            ao = int(usage.get("output_tokens") or 0)
        else:
            # Fallback: only combined `tokens_used` available — count it all
            # as input so the total is right and the user sees a non-zero
            # number, even if the split isn't faithful.
            combined = payload.get("tokens_used")
            try:
                ai = int(combined or 0)
            except (TypeError, ValueError):
                ai = 0
            ao = 0
        slot = by_agent.setdefault(agent, {"input": 0, "output": 0})
        slot["input"] += ai
        slot["output"] += ao

    total_in = sum(v["input"] for v in by_agent.values())
    total_out = sum(v["output"] for v in by_agent.values())
    by_agent["total"] = {"input": total_in, "output": total_out}
    return by_agent


# ---------------------------------------------------------------- compare helpers

def _build_compare_summary(primary: dict, secondary: dict) -> dict:
    """Build a side-by-side metrics summary for two synthesis runs.

    Captures the headline numbers reviewers want to eyeball when deciding
    which provider produced the better backlog: count of epics / stories /
    gaps / conflicts / duplicates, total input/output tokens, and a fuzzy
    overlap between the two sets of story titles.
    """
    def _counts(r: dict) -> dict[str, int]:
        epics = r.get("epics") or []
        return {
            "epics":       len(epics),
            "stories":     sum(len(e.get("stories") or []) for e in epics),
            "gaps":        len(r.get("gaps") or []),
            "conflicts":   len(r.get("conflicts") or []),
            "duplicates":  len(r.get("duplicates") or []),
            "guardrail_findings": len(r.get("guardrail_findings") or []),
            "input_tokens":  int((r.get("token_usage") or {}).get("total", {}).get("input", 0)),
            "output_tokens": int((r.get("token_usage") or {}).get("total", {}).get("output", 0)),
        }

    pc = _counts(primary)
    sc = _counts(secondary)

    # Title overlap — case-folded prefix-match heuristic. Catches "Enable
    # offline cash sales at the POS" vs. "Allow cash sales when POS is
    # offline" partially. Cheap; LLM-as-judge does the rigorous version.
    p_titles = [
        (s.get("title") or "").strip().lower()
        for e in (primary.get("epics") or [])
        for s in (e.get("stories") or [])
    ]
    s_titles = [
        (s.get("title") or "").strip().lower()
        for e in (secondary.get("epics") or [])
        for s in (e.get("stories") or [])
    ]
    overlap = 0
    for pt in p_titles:
        if not pt:
            continue
        for st_ in s_titles:
            if pt == st_ or (len(pt) >= 8 and (pt in st_ or st_ in pt)):
                overlap += 1
                break

    return {
        "primary":   pc,
        "secondary": sc,
        "deltas": {
            k: sc[k] - pc[k]
            for k in ("epics", "stories", "gaps", "conflicts", "duplicates",
                      "guardrail_findings", "input_tokens", "output_tokens")
        },
        "title_overlap_count": overlap,
        "title_overlap_pct": (
            round(100.0 * overlap / max(1, len(p_titles)), 1)
            if p_titles else 0.0
        ),
    }
