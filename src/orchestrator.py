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

from redactor import (
    RedactionMap,
    StrictRedactionViolation,
    assert_redacted,
    redact,
    redact_backlog,
    unredact_obj,
)

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
            return "ollama/llama3.1"
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
        self.jira = jira or JiraTool()
        self.confluence = confluence or ConfluenceTool()
        self.github = github or GithubTool()

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
                from tools.confluence_tool import ConfluenceTool as _CT
                ct = _CT(mode="live") if self.confluence._mode != "live" else self.confluence  # type: ignore[attr-defined]
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
                from tools.jira_tool import JiraTool as _JT
                jt = _JT(mode="live") if self.jira.mode != "live" else self.jira
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
        # The Gemini tool wrapper doesn't forward image attachments, so a
        # whiteboard/screenshot would be silently dropped if the Parser is on
        # a Gemini model. When vision input is present, bump the Parser to a
        # vision-capable Claude model so the image is actually read.
        if auto_switch and vision_attachments and resolved_models.get("parser", "").lower().startswith("gemini"):
            logger.info(
                "Vision attachment present — switching parser %s → claude-sonnet-4-5 "
                "(Gemini wrapper can't carry images).",
                resolved_models["parser"],
            )
            resolved_models["parser"] = "claude-sonnet-4-5"

        # ---- Dry-run short-circuit ----
        # Build prompts for each agent without invoking the LLM. Each agent
        # exposes its prompt template via `load_prompt(...)`; we replicate
        # the same substitution the agent would do at run time.
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

        # Now that the audit log exists, retroactively log any live-source
        # results so the trail explains where constraint_text / tickets came
        # from. Keeps the audit story honest when reviewers diff a run that
        # used live data vs. one that used fixtures.
        if live_confluence_page_id:
            if _live_confluence_error:
                audit.record(
                    "orchestrator", "live_confluence_fetch_failed",
                    payload={"page_id": live_confluence_page_id,
                             "error": _live_confluence_error[:300]},
                    reasoning="Live Confluence fetch failed; falling back to whatever constraint_text was passed in.",
                )
            else:
                audit.record(
                    "orchestrator", "live_confluence_fetch_ok",
                    payload={"page_id": live_confluence_page_id,
                             "chars_fetched": len(constraint_text)},
                    reasoning="Constraint text pulled from a live Confluence page.",
                )
        if live_jira:
            if _live_jira_error:
                audit.record(
                    "orchestrator", "live_jira_fetch_failed",
                    payload={"error": _live_jira_error[:300]},
                    reasoning="Live Jira fetch failed; existing_tickets stays as passed in.",
                )
            else:
                audit.record(
                    "orchestrator", "live_jira_fetch_ok",
                    payload={"ticket_count": len(existing_tickets)},
                    reasoning="Existing tickets pulled from live Jira via JQL on the configured project.",
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
            audit.record(
                "orchestrator", "pii_redacted",
                payload={"counts": rmap.summary()},
                reasoning="PII redaction was enabled; placeholders shared across all three inputs.",
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
        # The parser runs when EITHER text OR vision input is present —
        # a whiteboard photo with no text is still a valid input for a
        # vision-capable model.
        if transcript_text.strip() or vision_attachments:
            detail_chars = f"reading {len(transcript_text):,} chars"
            if vision_attachments:
                detail_chars += f" + {len(vision_attachments)} image(s)"
            _emit(0, "parser", "started", detail_chars)
            parser_tool = _tool_for(0, "parser")
            if parser_tool is not None:
                parser = ParserAgent(tool=parser_tool, memory=memory, audit=audit)
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

        # ---- Stage 2: Extract constraints from the wiki ----
        if constraint_text.strip():
            _emit(1, "constraint_extractor", "started",
                  f"reading {len(constraint_text):,} chars")
            constraint_tool = _tool_for(1, "constraint_extractor")
            if constraint_tool is not None:
                constraint_agent = ConstraintAgent(
                    tool=constraint_tool,
                    confluence=self.confluence,
                    memory=memory,
                    audit=audit,
                )
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

        # ---- Stage 3: Draft user stories from topics + constraints ----
        topics = memory.get("topics", [])
        if topics:
            _emit(2, "story_writer", "started", f"drafting from {len(topics)} topics")
            story_tool = _tool_for(2, "story_writer")
            if story_tool is not None:
                story_writer = StoryWriterAgent(tool=story_tool, memory=memory, audit=audit)
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

        # ---- Stage 4: Group stories into epics + decompose into tasks ----
        stories = memory.get("stories", [])
        if stories:
            _emit(3, "epic_decomposer", "started", f"grouping {len(stories)} stories")
            decomp_tool = _tool_for(3, "epic_decomposer")
            if decomp_tool is not None:
                decomposer = EpicDecomposerAgent(tool=decomp_tool, memory=memory, audit=audit)
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

        # ---- Stage 5: Find gaps, conflicts, duplicates ----
        if stories:
            _emit(4, "gap_detector", "started",
                  f"comparing {len(stories)} stories against {len(existing_tickets)} tickets")
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
        # Run the cheap deterministic checks against the un-redacted output
        # so error messages reference the real story ids the user will see.
        # Findings are non-blocking; they ride along on the result dict and
        # are audit-logged so reviewers can see them.
        try:
            from guardrails import run_guardrails, summarise
            findings = run_guardrails(result)
            result["guardrail_findings"] = [f.to_dict() for f in findings]
            tally = summarise(findings)
            audit.record(
                "orchestrator", "guardrails_completed",
                payload={"tally": tally, "finding_count": len(findings)},
                reasoning=(
                    f"Post-synthesis guardrails ran. "
                    f"{tally['error']} error / {tally['warn']} warn / {tally['info']} info."
                ),
            )
            # Refresh the audit_trail markdown so the guardrail event shows up.
            result["audit_trail"] = audit.render_markdown()
        except Exception as e:  # noqa: BLE001 — guardrails must never break a run
            logger.warning("Guardrails crashed (suppressed): %s", e)
            result["guardrail_findings"] = []

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
