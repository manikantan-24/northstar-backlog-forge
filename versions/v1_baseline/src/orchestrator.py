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

from tools.claude_tool import ClaudeTool
from tools.jira_tool import JiraTool
from tools.confluence_tool import ConfluenceTool
from tools.github_tool import GithubTool

from redactor import RedactionMap, redact, redact_backlog, unredact_obj

logger = get_logger(__name__)


class Orchestrator:
    """Runs the five-agent pipeline. Stateless across runs."""

    def __init__(
        self,
        claude: ClaudeTool | None = None,
        jira: JiraTool | None = None,
        confluence: ConfluenceTool | None = None,
        github: GithubTool | None = None,
    ):
        # Tools — injected for testability. Defaults wire real ones.
        self.claude = claude or ClaudeTool()
        self.jira = jira or JiraTool()
        self.confluence = confluence or ConfluenceTool()
        self.github = github or GithubTool()

    def run(
        self,
        transcript_text: str = "",
        constraint_text: str = "",
        existing_tickets: list[dict] | None = None,
        redact_pii: bool = False,
        progress_callback=None,
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

        memory = MemoryStore()
        audit = AuditLog()

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

        # Seed memory with existing tickets so the Gap Detector can search them
        memory.put("existing_tickets", existing_tickets)

        # ---- Stage 1: Parse the transcript into topics ----
        if transcript_text.strip():
            _emit(0, "parser", "started", f"reading {len(transcript_text):,} chars")
            parser = ParserAgent(claude=self.claude, memory=memory, audit=audit)
            try:
                parser.run(transcript_text)
                _emit(0, "parser", "completed",
                      f"{len(memory.get('topics', []))} topics extracted")
            except AgentError as e:
                logger.warning("Parser failed: %s", e)
                audit.record_failure("parser", str(e))
                _emit(0, "parser", "failed", str(e)[:120])
        else:
            _emit(0, "parser", "skipped", "no transcript provided")

        # ---- Stage 2: Extract constraints from the wiki ----
        if constraint_text.strip():
            _emit(1, "constraint_extractor", "started",
                  f"reading {len(constraint_text):,} chars")
            constraint_agent = ConstraintAgent(
                claude=self.claude,
                confluence=self.confluence,
                memory=memory,
                audit=audit,
            )
            try:
                constraint_agent.run(constraint_text)
                _emit(1, "constraint_extractor", "completed",
                      f"{len(memory.get('constraints', []))} constraints captured")
            except AgentError as e:
                logger.warning("Constraint Extractor failed: %s", e)
                audit.record_failure("constraint_extractor", str(e))
                _emit(1, "constraint_extractor", "failed", str(e)[:120])
        else:
            _emit(1, "constraint_extractor", "skipped", "no wiki / constraints provided")

        # ---- Stage 3: Draft user stories from topics + constraints ----
        topics = memory.get("topics", [])
        if topics:
            _emit(2, "story_writer", "started", f"drafting from {len(topics)} topics")
            story_writer = StoryWriterAgent(claude=self.claude, memory=memory, audit=audit)
            try:
                story_writer.run()
                _emit(2, "story_writer", "completed",
                      f"{len(memory.get('stories', []))} user stories written")
            except AgentError as e:
                logger.warning("Story Writer failed: %s", e)
                audit.record_failure("story_writer", str(e))
                _emit(2, "story_writer", "failed", str(e)[:120])
        else:
            _emit(2, "story_writer", "skipped", "no topics — Parser produced nothing")

        # ---- Stage 4: Group stories into epics + decompose into tasks ----
        stories = memory.get("stories", [])
        if stories:
            _emit(3, "epic_decomposer", "started", f"grouping {len(stories)} stories")
            decomposer = EpicDecomposerAgent(claude=self.claude, memory=memory, audit=audit)
            try:
                decomposer.run()
                _emit(3, "epic_decomposer", "completed",
                      f"{len(memory.get('epics', []))} epics with task breakdowns")
            except AgentError as e:
                logger.warning("Epic Decomposer failed: %s", e)
                audit.record_failure("epic_decomposer", str(e))
                _emit(3, "epic_decomposer", "failed", str(e)[:120])
        else:
            _emit(3, "epic_decomposer", "skipped", "no stories to group")

        # ---- Stage 5: Find gaps, conflicts, duplicates ----
        if stories:
            _emit(4, "gap_detector", "started",
                  f"comparing {len(stories)} stories against {len(existing_tickets)} tickets")
            gap_detector = GapDetectorAgent(
                claude=self.claude,
                jira=self.jira,
                github=self.github,
                memory=memory,
                audit=audit,
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
                logger.warning("Gap Detector failed: %s", e)
                audit.record_failure("gap_detector", str(e))
                _emit(4, "gap_detector", "failed", str(e)[:120])
        else:
            _emit(4, "gap_detector", "skipped", "no stories to compare")

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
        }

        # Un-redact the synthesis on the way out. The audit trail stays
        # redacted on purpose — it's the only artifact that records prompt
        # text the LLM saw, and leaving it redacted preserves the
        # redaction-audit story for compliance review.
        if rmap is not None and not rmap.is_empty():
            for key in ("summary", "topics", "constraints", "epics",
                        "gaps", "conflicts", "duplicates"):
                result[key] = unredact_obj(result[key], rmap)

        return result
