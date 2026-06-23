"""Orchestrator — backward-compatible public interface over the LangGraph pipeline.

The multi-agent workflow now lives in `pipeline.py` as a LangGraph StateGraph.
This module keeps the original `Orchestrator` class so that `app.py`,
`main.py`, and all tests continue to work without any changes.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from logger_setup import get_logger

from pipeline import (
    build_pipeline,
    DEFAULT_STAGE_MODELS,
    _summarize_models,
)

from tools.base import Tool
from tools.jira_tool import JiraTool
from tools.confluence_tool import ConfluenceTool
from tools.github_tool import GithubTool

import os as _os

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


class Orchestrator:
    """Runs the five-agent pipeline via LangGraph. Stateless across runs."""

    def __init__(
        self,
        claude: Tool | None = None,
        jira: JiraTool | None = None,
        confluence: ConfluenceTool | None = None,
        github: GithubTool | None = None,
    ):
        self.claude     = claude
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
        user_email: str = "anonymous",
    ) -> dict[str, Any]:
        """Run the full pipeline and return the synthesised result dict.

        The returned dict has the same keys as before:
          epics, gaps, conflicts, duplicates, topics, constraints, summary,
          audit_trail, token_usage, model, models, guardrail_findings,
          audit_chain_fingerprint
        """
        # ---- Resolve per-stage models ----
        resolved_models: dict[str, str] = dict(DEFAULT_STAGE_MODELS)
        if models:
            for k, v in models.items():
                key = k.replace("constraint_extractor", "constraint")
                if v and key in resolved_models:
                    resolved_models[key] = v

        # ---- Dry-run short-circuit ----
        if dry_run:
            return self._build_dry_run_result(
                transcript_text=transcript_text,
                constraint_text=constraint_text,
                existing_tickets=existing_tickets or [],
                progress_callback=progress_callback,
                models=resolved_models,
            )

        # ---- Build initial pipeline state ----
        run_id = str(uuid.uuid4())
        initial_state: dict[str, Any] = {
            "transcript_text":               transcript_text or "",
            "constraint_text":               constraint_text or "",
            "existing_tickets":              list(existing_tickets or []),
            "vision_attachments":            list(vision_attachments or []),
            "resolved_models":               resolved_models,
            "use_embeddings_for_duplicates": use_embeddings_for_duplicates,
            "persistent_memory":             bool(
                persistent_memory
                if persistent_memory is not None
                else _os.environ.get("MEMORY_PERSISTENT", "").lower() in ("1", "true", "yes")
            ),
            "redact_pii":                    redact_pii,
            "strict_redact":                 strict_redact,
            "auto_switch":                   auto_switch,
            "live_confluence_page_id":       live_confluence_page_id,
            "live_jira":                     live_jira,
            "run_metadata":                  run_metadata or {},
            "run_id":                        run_id,
            "user_email":                    user_email or "anonymous",
        }

        # ---- LangGraph config — non-serialisable objects go here ----
        lg_config: dict[str, Any] = {
            "configurable": {
                "thread_id":          run_id,
                "_jira":              self.jira,
                "_confluence":        self.confluence,
                "_github":            self.github,
                "_claude_fallback":   self.claude,     # None in production
                "progress_callback":  progress_callback,
            }
        }

        # ---- Build a fresh graph per run so its MemorySaver is GC'd afterwards ----
        _graph = build_pipeline()

        # ---- Invoke the LangGraph pipeline ----
        inc_active_synthesis()
        _run_start = time.perf_counter()
        try:
            final_state: dict[str, Any] = _graph.invoke(initial_state, config=lg_config)
        except Exception as e:
            dec_active_synthesis()
            flush_metrics()
            raise e

        # ---- Extract audit trail from the AuditLog object in state ----
        audit = final_state.get("_audit")
        audit_trail_md     = audit.render_markdown() if audit else ""
        audit_fingerprint  = getattr(audit, "chain_fingerprint", "")

        # ---- Build result dict (same shape as the old Orchestrator.run()) ----
        result: dict[str, Any] = {
            "summary":     final_state.get("summary", ""),
            "topics":      final_state.get("topics", []),
            "constraints": final_state.get("constraints", []),
            "epics":       final_state.get("epics", []),
            "gaps":        final_state.get("gaps", []),
            "conflicts":   final_state.get("conflicts", []),
            "duplicates":  final_state.get("duplicates", []),
            "audit_trail": audit_trail_md,
            "token_usage": final_state.get("token_usage", {}),
            "model":       _summarize_models(resolved_models),
            "models":      dict(resolved_models),
            "guardrail_findings":      final_state.get("guardrail_findings", []),
            "audit_chain_fingerprint": final_state.get("audit_chain_fingerprint", audit_fingerprint),
        }

        _run_status = "error" if result.get("guardrail_findings") and any(
            f.get("severity") == "error" for f in result.get("guardrail_findings", [])
        ) else "ok"
        record_synthesis_complete(
            run_id=run_id,
            preset=_summarize_models(resolved_models),
            elapsed_seconds=time.perf_counter() - _run_start,
            status=_run_status,
        )
        flush_metrics()
        dec_active_synthesis()

        return result

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
        """Run the pipeline twice with two different model configurations."""
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

    def _build_dry_run_result(
        self,
        transcript_text: str,
        constraint_text: str,
        existing_tickets: list[dict],
        progress_callback=None,
        models: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the prompts each agent would have sent. No LLM calls."""
        from pathlib import Path
        import json as _json

        prompts_dir = Path(__file__).parent.parent / "prompts"

        def _load(name: str) -> str:
            path = prompts_dir / name
            if not path.exists():
                return f"(prompt template missing: {name})"
            return path.read_text(encoding="utf-8")

        dry_prompts: dict[str, str] = {}

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


def _build_compare_summary(primary: dict, secondary: dict) -> dict:
    """Build a side-by-side metrics summary for two synthesis runs."""
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
