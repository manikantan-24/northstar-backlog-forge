"""LangGraph pipeline — replaces the imperative loop in orchestrator.py.

The five-agent workflow is expressed as a LangGraph StateGraph:

    START → initialize → parse → extract_constraints → write_stories
          → decompose_epics → detect_gaps → finalize → END

Each node is a pure function ``(state: PipelineState, config: RunnableConfig) → dict``
that returns only the state keys it updated.  LangGraph merges the partial
updates automatically.

Shared mutable objects (AuditLog, JiraTool, ConfluenceTool) are initialised
once in ``initialize_node`` and stored as ``_audit``, ``_jira``, ``_confluence``
in state — they travel in-memory via MemorySaver without serialization.

Caller interface::

    from pipeline import build_pipeline, DEFAULT_STAGE_MODELS

    graph = build_pipeline()
    result_state = graph.invoke(
        initial_state,
        config={
            "configurable": {
                "thread_id": "run-001",
                "_jira": jira_tool_instance,
                "_confluence": confluence_tool_instance,
                "_claude_fallback": fake_tool_for_tests,  # optional
                "progress_callback": callback_fn,          # optional
            }
        },
    )
"""

from __future__ import annotations

import os
from typing import Any

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
try:
    from langgraph.checkpoint.redis import RedisSaver
    HAS_REDIS_SAVER = True
except ImportError:
    HAS_REDIS_SAVER = False
from langchain_core.runnables import RunnableConfig

from logger_setup import get_logger
from memory.state import PipelineState
from memory.store import MemoryStore
from memory.audit_log import AuditLog
from agents.base import AgentError
from tools.base import Tool, ToolError
from tools.claude_tool import ClaudeTool
from tools.gemini_tool import GeminiTool
from tools.ollama_tool import OllamaTool
from tools.jira_tool import JiraTool
from tools.confluence_tool import ConfluenceTool
from tools.github_tool import GithubTool

try:
    from circuit_breaker import CLAUDE_CB as _CLAUDE_CB, GEMINI_CB as _GEMINI_CB
    _HAS_CB = True
except ImportError:  # pragma: no cover
    _HAS_CB = False

logger = get_logger(__name__)

# ------------------------------------------------------------------ constants

DEFAULT_STAGE_MODELS: dict[str, str] = {
    "parser":           "claude-sonnet-4-5",
    "constraint":       "claude-sonnet-4-5",
    "story_writer":     "claude-sonnet-4-5",
    "epic_decomposer":  "claude-sonnet-4-5",
    "gap_detector":     "claude-sonnet-4-5",
}

# ------------------------------------------------------------------ utilities


def _summarize_models(models: dict[str, str]) -> str:
    distinct = sorted({m for m in models.values() if m})
    if len(distinct) == 1:
        return distinct[0]
    has_claude = any(m.startswith("claude") for m in distinct)
    has_gemini = any(m.startswith("gemini") for m in distinct)
    if has_claude and has_gemini:
        claude_names = [m for m in distinct if m.startswith("claude")]
        gemini_names = [m for m in distinct if m.startswith("gemini")]
        return f"mixed ({', '.join(gemini_names)} + {', '.join(claude_names)})"
    return "mixed (" + ", ".join(distinct) + ")"


def _aggregate_token_usage(audit: AuditLog) -> dict[str, Any]:
    """Build a {agent: {input, output}, total: {input, output}} dict."""
    by_agent: dict[str, dict[str, int]] = {}
    for ev in audit.events:
        if ev.event != "tool_call":
            continue
        payload = ev.payload or {}
        agent = ev.agent
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        if usage:
            ai = int(usage.get("input_tokens") or 0)
            ao = int(usage.get("output_tokens") or 0)
        else:
            combined = payload.get("tokens_used")
            try:
                ai = int(combined or 0)
            except (TypeError, ValueError):
                ai = 0
            ao = 0
        slot = by_agent.setdefault(agent, {"input": 0, "output": 0})
        slot["input"] += ai
        slot["output"] += ao

    total_in  = sum(v["input"]  for v in by_agent.values())
    total_out = sum(v["output"] for v in by_agent.values())
    by_agent["total"] = {"input": total_in, "output": total_out}
    return by_agent


def _get_tool(stage_name: str, state: PipelineState, config: RunnableConfig) -> Tool:
    """Build the LLM tool for a stage, respecting test overrides via config."""
    model_id = (state.get("resolved_models") or {}).get(stage_name, "claude-sonnet-4-5")
    fallback: Tool | None = (config.get("configurable") or {}).get("_claude_fallback")

    # Test-fake passthrough: a non-ClaudeTool stub answers for every stage.
    if fallback is not None and not isinstance(fallback, ClaudeTool):
        return fallback

    mid = (model_id or "").lower().strip()

    # If a ClaudeTool fallback matches the model, reuse it.
    if isinstance(fallback, ClaudeTool) and getattr(fallback, "model", None) == model_id:
        return fallback

    if mid.startswith("claude"):
        if _HAS_CB and _CLAUDE_CB.is_open():
            # Anthropic is down — attempt Gemini degraded mode.
            logger.warning(
                "Anthropic circuit open for stage '%s' — failing over to Gemini", stage_name
            )
            _emit(config, -1, stage_name, "failover",
                  "Anthropic circuit open; using Gemini as degraded-mode fallback")
            if not (_HAS_CB and _GEMINI_CB.is_open()):
                return GeminiTool()  # default Gemini model
            raise ToolError(
                "Both Anthropic and Gemini circuits are open. "
                "The pipeline cannot proceed until a provider recovers."
            )
        return ClaudeTool(model=model_id)
    if mid.startswith("gemini"):
        return GeminiTool(model=model_id)
    if mid.startswith("ollama"):
        return OllamaTool(model=model_id)
    raise ToolError(
        f"Unknown model id '{model_id}'. Expected prefix 'claude-', 'gemini-', or 'ollama/'."
    )


def _emit(
    config: RunnableConfig,
    stage_idx: int,
    stage_name: str,
    event: str,
    detail: str = "",
) -> None:
    """Fire the progress_callback injected via config (no-op if absent)."""
    cb = (config.get("configurable") or {}).get("progress_callback")
    if cb is None:
        return
    try:
        cb(stage_idx, stage_name, event, detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("progress_callback raised: %s", exc)


def _hydrate_memory(state: PipelineState) -> MemoryStore:
    """Create a MemoryStore pre-loaded with the current pipeline state values.

    Each agent node creates its own fresh MemoryStore, populates it from the
    LangGraph state (which carries all outputs from previous nodes), runs the
    agent, then the node extracts the agent's writes back into a state-update
    dict.  This adapter pattern lets the existing agent classes work unchanged.

    A snapshot of the hydrated keys is saved on the store so that
    ``_extract_memory_updates`` can detect which keys the agent actually wrote
    (as opposed to keys that were merely passed in for reading).  This is
    critical under parallel execution: two nodes that both hydrate and return
    the same key trigger LangGraph's ``InvalidUpdateError``.
    """
    persistent = bool(state.get("persistent_memory"))
    mem = MemoryStore(persistent=persistent)
    for key in (
        "topics", "constraints", "stories", "epics",
        "gaps", "conflicts", "duplicates", "existing_tickets", "summary",
    ):
        val = state.get(key)  # type: ignore[literal-required]
        if val is not None:
            mem.put(key, val)
    # Clear the write-tracking set after hydration so only genuine agent
    # writes (put/append calls during agent.run()) appear as updates.
    # Without this, keys hydrated for reading would be returned by every
    # parallel node, triggering LangGraph's InvalidUpdateError.
    mem._written_keys.clear()  # type: ignore[attr-defined]
    return mem


# All PipelineState data fields that an agent is allowed to write.
# Kept as a frozenset so _extract_memory_updates can warn on unknowns.
_PIPELINE_DATA_KEYS: frozenset[str] = frozenset({
    "topics", "constraints", "stories", "epics",
    "gaps", "conflicts", "duplicates", "existing_tickets", "summary",
})


def _extract_memory_updates(mem: MemoryStore) -> dict:
    """Return only keys the agent explicitly wrote via put() or append().

    Uses MemoryStore._written_keys (populated by put/append, cleared after
    hydration) so that hydrated read-only keys are never returned.  This is
    the correct behaviour for parallel fan-out nodes: two nodes that both
    hydrated ``existing_tickets`` but never wrote to it will each return an
    empty dict for that key rather than triggering LangGraph's
    ``InvalidUpdateError``.

    Keys outside ``_PIPELINE_DATA_KEYS`` get a WARNING so agent authors notice
    unintentional writes before they cause hard-to-debug downstream issues.
    """
    written: set[str] = getattr(mem, "_written_keys", set())
    updates: dict = {}
    for key in written:
        if key in _PIPELINE_DATA_KEYS:
            val = mem.get(key)
            if val is not None:
                updates[key] = val
        else:
            logger.warning(
                "Agent wrote MemoryStore key %r which is not a recognised PipelineState "
                "field -- value will not propagate downstream. "
                "Add it to _PIPELINE_DATA_KEYS if this is intentional.",
                key,
            )
    return updates


def _node_with_span(node_name: str, fn):
    """Wrap a LangGraph node function with a per-node OTel span + legacy stage_span.

    The span is named ``pipeline.node.<node_name>`` and carries:
    - ``pipeline.node``     — the node name
    - ``pipeline.run_id``   — the run UUID (for correlating with audit logs)
    - ``output.*_count``    — count of any list keys the node returned

    Errors are recorded on the span and re-raised so LangGraph's normal error
    handling is not affected.  When OTEL_ENABLED is not "1" this is a zero-cost
    wrapper (pipeline_node_span yields a _NoopSpan immediately).
    """
    def _wrapped(state: PipelineState, config: RunnableConfig) -> dict:
        from telemetry import pipeline_node_span, stage_span
        run_id = state.get("run_id") or ""
        resolved_models = state.get("resolved_models") or {}

        # Map node name to stage metadata
        stage_mapping = {
            "parse":               ("parser",             resolved_models.get("parser", ""), len(state.get("transcript_text") or "")),
            "extract_constraints": ("constraint_extractor", resolved_models.get("constraint", ""), len(state.get("constraint_text") or "")),
            "write_stories":       ("story_writer",       resolved_models.get("story_writer", ""), 0),
            "decompose_epics":     ("epic_decomposer",    resolved_models.get("epic_decomposer", ""), 0),
            "detect_gaps":         ("gap_detector",       resolved_models.get("gap_detector", ""), 0),
        }

        with pipeline_node_span(node_name, run_id=run_id) as span:
            # If this node represents a legacy stage, wrap it in a stage_span context
            if node_name in stage_mapping:
                stage_name, model, input_chars = stage_mapping[node_name]
                with stage_span(stage_name, model=model, input_chars=input_chars):
                    result = fn(state, config)
            else:
                result = fn(state, config)

            if isinstance(result, dict):
                for key, attr in (
                    ("topics",     "output.topics_count"),
                    ("stories",    "output.stories_count"),
                    ("epics",      "output.epics_count"),
                    ("gaps",       "output.gaps_count"),
                    ("conflicts",  "output.conflicts_count"),
                    ("duplicates", "output.duplicates_count"),
                ):
                    val = result.get(key)
                    if isinstance(val, list):
                        span.set_attribute(attr, len(val))
            return result
    _wrapped.__name__ = fn.__name__
    return _wrapped


def _record_stage_error(state: PipelineState, stage: str, err: str) -> dict:
    # Return only the new entry. The _merge_dicts reducer on stage_errors
    # merges it with any entries from concurrently-running nodes automatically.
    return {"stage_errors": {stage: err}}


# ------------------------------------------------------------------ nodes


def initialize_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Set up AuditLog, resolve live sources, seed existing_tickets."""
    cfg = config.get("configurable") or {}
    jira: JiraTool = cfg.get("_jira") or JiraTool()
    confluence: ConfluenceTool = cfg.get("_confluence") or ConfluenceTool()

    audit = AuditLog()

    resolved_models = state.get("resolved_models") or dict(DEFAULT_STAGE_MODELS)
    _auto_switched_parser = False
    _auto_switched_from = ""
    auto_switch = state.get("auto_switch", True)
    vision = state.get("vision_attachments") or []
    if auto_switch and vision and resolved_models.get("parser", "").lower().startswith("gemini"):
        _auto_switched_from = resolved_models["parser"]
        _auto_switched_parser = True
        logger.info(
            "Vision attachment present — switching parser %s → claude-sonnet-4-5 "
            "(Gemini wrapper can't carry images).",
            _auto_switched_from,
        )
        resolved_models = dict(resolved_models)
        resolved_models["parser"] = "claude-sonnet-4-5"
    existing_tickets = list(state.get("existing_tickets") or [])
    constraint_text = state.get("constraint_text") or ""

    # Transport labels for audit
    _jira_transport = (
        "Atlassian MCP server (mcp-atlassian)"
        if getattr(jira, "_use_mcp", False)
        else ("Jira REST API (live)" if getattr(jira, "mode", "mock") == "live"
              else "Jira fixture (mock)")
    )
    _confluence_transport = (
        "Atlassian MCP server (mcp-atlassian)"
        if getattr(confluence, "_use_mcp", False)
        else ("Confluence REST API (live)" if getattr(confluence, "_mode", "mock") == "live"
              else "Confluence fixture (mock)")
    )

    audit.record(
        "orchestrator", "data_sources_configured",
        payload={
            "jira_transport":       _jira_transport,
            "confluence_transport": _confluence_transport,
        },
        reasoning="Data source transports resolved at pipeline start.",
    )

    # ---- Live Confluence fetch ----
    live_page = state.get("live_confluence_page_id")
    if live_page and not constraint_text.strip():
        try:
            constraint_text = confluence.get_page(live_page)
            audit.record(
                "orchestrator", "live_confluence_fetch_ok",
                payload={
                    "page_id": live_page,
                    "chars_fetched": len(constraint_text),
                    "transport": _confluence_transport,
                },
                reasoning="Constraint text pulled from live Confluence.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live Confluence fetch failed: %s", exc)
            audit.record(
                "orchestrator", "live_confluence_fetch_failed",
                payload={
                    "page_id": live_page,
                    "error": str(exc)[:300],
                    "transport": _confluence_transport,
                },
                reasoning="Live Confluence fetch failed; falling back to provided text.",
            )

    # ---- Live Jira fetch ----
    if state.get("live_jira") and not existing_tickets:
        try:
            existing_tickets = jira.list_all()
            audit.record(
                "orchestrator", "live_jira_fetch_ok",
                payload={
                    "ticket_count": len(existing_tickets),
                    "transport": _jira_transport,
                },
                reasoning=f"Existing tickets pulled via {_jira_transport}.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live Jira fetch failed: %s", exc)
            audit.record(
                "orchestrator", "live_jira_fetch_failed",
                payload={"error": str(exc)[:300], "transport": _jira_transport},
                reasoning="Live Jira fetch failed; existing_tickets stays as provided.",
            )

    # ---- Pipeline started ----
    vision = state.get("vision_attachments") or []
    audit.record(
        "orchestrator", "pipeline_started",
        payload={
            "run_metadata":           state.get("run_metadata") or {},
            "transcript_chars":       len(state.get("transcript_text") or ""),
            "constraint_chars":       len(constraint_text),
            "existing_ticket_count":  len(existing_tickets),
            "vision_attachment_count": len(vision),
            "redact_pii":             bool(state.get("redact_pii")),
            "strict_redact":          bool(state.get("strict_redact")),
            "auto_switch":            auto_switch,
            "dry_run":                False,
            "persistent_memory":      bool(state.get("persistent_memory")),
            "live_jira":              bool(state.get("live_jira")),
            "live_confluence":        bool(live_page),
        },
        reasoning="Pipeline initialised. All inputs and configuration recorded for reproducibility.",
    )
    audit.record(
        "orchestrator", "models_resolved",
        payload={
            "stage_models":   dict(resolved_models),
            "preset_summary": _summarize_models(resolved_models),
        },
        reasoning="Per-stage model assignments after preset + overrides are resolved.",
    )
    if vision:
        audit.record(
            "orchestrator", "vision_attachments_provided",
            payload={
                "count":       len(vision),
                "labels":      [getattr(v, "label", "unknown") for v in vision],
                "media_types": [getattr(v, "media_type", "unknown") for v in vision],
            },
            reasoning="Image attachments provided alongside the transcript.",
        )
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
    audit.record(
        "orchestrator", "existing_tickets_seeded",
        payload={
            "ticket_count":   len(existing_tickets),
            "jira_transport": _jira_transport,
            "sample_ids":     [t.get("id", "?") for t in existing_tickets[:5]],
        },
        reasoning=f"{len(existing_tickets)} ticket(s) seeded into shared memory for the Gap Detector.",
    )

    # ---- Input size guard — prevent runaway cost on huge transcripts ----
    # Each stage receives the full transcript as context, so cost scales linearly
    # with input length.  We estimate tokens via the 4-chars-per-token heuristic
    # and hard-block runs that would exceed MAX_INPUT_TOKENS_PER_RUN.
    transcript_text = state.get("transcript_text") or ""
    _MAX_INPUT_TOKENS = int(
        os.environ.get("MAX_INPUT_TOKENS_PER_RUN", "50000")
    )
    _transcript_est_tokens = len(transcript_text) // 4
    _constraint_est_tokens = len(constraint_text) // 4
    _total_est_tokens = _transcript_est_tokens + _constraint_est_tokens
    if _total_est_tokens > _MAX_INPUT_TOKENS:
        _msg = (
            f"Input too large: estimated {_total_est_tokens:,} tokens "
            f"(transcript {_transcript_est_tokens:,} + constraints {_constraint_est_tokens:,}) "
            f"exceeds MAX_INPUT_TOKENS_PER_RUN={_MAX_INPUT_TOKENS:,}. "
            "Reduce input size or raise MAX_INPUT_TOKENS_PER_RUN."
        )
        audit.record(
            "orchestrator", "input_too_large",
            payload={
                "estimated_tokens": _total_est_tokens,
                "limit": _MAX_INPUT_TOKENS,
            },
            reasoning=_msg,
        )
        logger.error(_msg)
        raise ToolError(_msg)

    # ---- Prompt-injection scan (runs after live fetches so Confluence text is included) ----
    from security import InputSanitizer  # lazy import — avoids top-level circular risk
    transcript_clean, transcript_findings = InputSanitizer.scan(transcript_text, source="transcript")
    constraint_clean, constraint_findings = InputSanitizer.scan(constraint_text, source="constraint document")
    injection_findings = transcript_findings + constraint_findings

    if injection_findings:
        audit.record(
            "orchestrator", "injection_scan_findings",
            payload={
                "finding_count": len(injection_findings),
                "codes": [f.code for f in injection_findings],
            },
            reasoning=(
                f"{len(injection_findings)} prompt-injection pattern(s) detected and redacted "
                "from user inputs before reaching any LLM stage."
            ),
        )
        logger.warning(
            "Prompt injection detected in inputs (%d finding(s)): %s",
            len(injection_findings),
            [f.code for f in injection_findings],
        )
        try:
            from alerts import post_security_alert
            _run_id = state.get("run_id") or ""
            _user   = state.get("user_email") or "anonymous"
            post_security_alert(
                [f.to_dict() for f in injection_findings],
                run_id=_run_id,
                user=_user,
            )
        except Exception as _alert_exc:  # noqa: BLE001
            logger.debug("Security alert dispatch error: %s", _alert_exc)
    else:
        audit.record(
            "orchestrator", "injection_scan_clean",
            payload={},
            reasoning="Input sanitizer found no injection patterns in transcript or constraint text.",
        )

    # ---- PII redaction (opt-in) ----
    _redaction_map = None
    if state.get("redact_pii"):
        from security import redact_pii, redact_backlog_pii
        transcript_clean, _rmap = redact_pii(transcript_clean)
        constraint_clean, _rmap = redact_pii(constraint_clean, rmap=_rmap)
        existing_tickets, _rmap = redact_backlog_pii(existing_tickets, rmap=_rmap)
        _redaction_map = _rmap
        _pii_counts = _rmap.summary()
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

        if state.get("strict_redact"):
            from redactor import assert_redacted, StrictRedactionViolation
            try:
                if transcript_clean:
                    assert_redacted(transcript_clean)
                if constraint_clean:
                    assert_redacted(constraint_clean)
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
                        "first_context": e.findings[0]["context_excerpt"] if e.findings else "",
                    },
                    reasoning=(
                        "Strict redaction mode is on. PII patterns were "
                        "detected at the LLM trust boundary even after "
                        "the redaction pass; aborting the run."
                    ),
                )
                raise
    elif state.get("strict_redact"):
        from redactor import assert_redacted, StrictRedactionViolation
        try:
            if transcript_clean:
                assert_redacted(transcript_clean)
            if constraint_clean:
                assert_redacted(constraint_clean)
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
                    "first_context": e.findings[0]["context_excerpt"] if e.findings else "",
                },
                reasoning=(
                    "Strict redaction mode is on without redact_pii=True; "
                    "raw inputs contained PII patterns. Aborting."
                ),
            )
            raise

    return {
        "_audit":            audit,
        "_redaction_map":    _redaction_map,
        "transcript_text":   transcript_clean,
        "constraint_text":   constraint_clean,
        "existing_tickets":  existing_tickets,
        "resolved_models":   resolved_models,
        "stage_errors":      {},
        "security_findings": [f.to_dict() for f in injection_findings],
    }


def parse_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Stage 1 — parse transcript into topics."""
    from agents.parser_agent import ParserAgent

    audit: AuditLog = state["_audit"]
    transcript = state.get("transcript_text") or ""
    vision     = state.get("vision_attachments") or []

    if not transcript.strip() and not vision:
        _emit(config, 0, "parser", "skipped", "no transcript provided")
        audit.record(
            "parser", "stage_skipped",
            payload={"reason": "no transcript or vision input provided"},
            reasoning="Parser not run: no text or image input supplied.",
        )
        return {}

    detail = f"reading {len(transcript):,} chars"
    if vision:
        detail += f" + {len(vision)} image(s)"
    _emit(config, 0, "parser", "started", detail)

    try:
        tool = _get_tool("parser", state, config)
    except ToolError as exc:
        audit.record_failure("parser", f"Tool init failed: {exc}")
        _emit(config, 0, "parser", "failed", str(exc)[:120])
        return _record_stage_error(state, "parser", str(exc))

    memory = _hydrate_memory(state)
    agent  = ParserAgent(tool=tool, memory=memory, audit=audit)
    try:
        agent.run(transcript, vision_attachments=vision or None)
    except AgentError as exc:
        logger.warning("Parser failed: %s", exc)
        audit.record_failure("parser", str(exc))
        _emit(config, 0, "parser", "failed", str(exc)[:120])
        return _record_stage_error(state, "parser", str(exc))

    updates = _extract_memory_updates(memory)
    _emit(config, 0, "parser", "completed",
          f"{len(updates.get('topics', []))} topics extracted")
    return updates


def constraint_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Stage 2 — extract architecture constraints from wiki text."""
    from agents.constraint_agent import ConstraintAgent

    audit: AuditLog  = state["_audit"]
    confluence: ConfluenceTool = (config.get("configurable") or {}).get("_confluence") or ConfluenceTool()
    constraint_text  = state.get("constraint_text") or ""

    if not constraint_text.strip():
        _emit(config, 1, "constraint_extractor", "skipped", "no wiki / constraints provided")
        audit.record(
            "constraint_extractor", "stage_skipped",
            payload={"reason": "no wiki / constraints text provided"},
            reasoning="Constraint Extractor not run: no architecture text supplied.",
        )
        return {}

    live_page   = state.get("live_confluence_page_id")
    conf_source = (
        f"from live Confluence (page {live_page})" if live_page
        else "from local file / sample"
    )
    _emit(config, 1, "constraint_extractor", "started",
          f"reading {len(constraint_text):,} chars · {conf_source}")

    try:
        tool = _get_tool("constraint", state, config)
    except ToolError as exc:
        audit.record_failure("constraint_extractor", f"Tool init failed: {exc}")
        _emit(config, 1, "constraint_extractor", "failed", str(exc)[:120])
        return _record_stage_error(state, "constraint_extractor", str(exc))

    memory = _hydrate_memory(state)
    agent  = ConstraintAgent(
        tool=tool, confluence=confluence, memory=memory, audit=audit
    )
    try:
        agent.run(constraint_text)
    except AgentError as exc:
        logger.warning("Constraint Extractor failed: %s", exc)
        audit.record_failure("constraint_extractor", str(exc))
        _emit(config, 1, "constraint_extractor", "failed", str(exc)[:120])
        return _record_stage_error(state, "constraint_extractor", str(exc))

    updates = _extract_memory_updates(memory)
    _emit(config, 1, "constraint_extractor", "completed",
          f"{len(updates.get('constraints', []))} constraints captured")
    return updates


def story_writer_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Stage 3 — draft user stories from topics + constraints."""
    from agents.story_writer_agent import StoryWriterAgent

    audit: AuditLog = state["_audit"]
    topics = state.get("topics") or []

    if not topics:
        _emit(config, 2, "story_writer", "skipped", "no topics — Parser produced nothing")
        audit.record(
            "story_writer", "stage_skipped",
            payload={"reason": "no topics in memory"},
            reasoning="Story Writer not run: Parser produced no topics.",
        )
        return {}

    _emit(config, 2, "story_writer", "started", f"drafting from {len(topics)} topics")

    try:
        tool = _get_tool("story_writer", state, config)
    except ToolError as exc:
        audit.record_failure("story_writer", f"Tool init failed: {exc}")
        _emit(config, 2, "story_writer", "failed", str(exc)[:120])
        return _record_stage_error(state, "story_writer", str(exc))

    memory = _hydrate_memory(state)
    agent  = StoryWriterAgent(tool=tool, memory=memory, audit=audit)
    try:
        agent.run()
    except AgentError as exc:
        logger.warning("Story Writer failed: %s", exc)
        audit.record_failure("story_writer", str(exc))
        _emit(config, 2, "story_writer", "failed", str(exc)[:120])
        return _record_stage_error(state, "story_writer", str(exc))

    updates = _extract_memory_updates(memory)
    _emit(config, 2, "story_writer", "completed",
          f"{len(updates.get('stories', []))} user stories written")
    return updates


def epic_decomposer_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Stage 4 — group stories into epics and break them into tasks."""
    from agents.epic_decomposer_agent import EpicDecomposerAgent

    audit: AuditLog = state["_audit"]
    stories = state.get("stories") or []

    if not stories:
        _emit(config, 3, "epic_decomposer", "skipped", "no stories to group")
        audit.record(
            "epic_decomposer", "stage_skipped",
            payload={"reason": "no stories in memory"},
            reasoning="Epic Decomposer not run: Story Writer produced no stories.",
        )
        return {}

    _emit(config, 3, "epic_decomposer", "started", f"grouping {len(stories)} stories")

    try:
        tool = _get_tool("epic_decomposer", state, config)
    except ToolError as exc:
        audit.record_failure("epic_decomposer", f"Tool init failed: {exc}")
        _emit(config, 3, "epic_decomposer", "failed", str(exc)[:120])
        return _record_stage_error(state, "epic_decomposer", str(exc))

    memory = _hydrate_memory(state)
    agent  = EpicDecomposerAgent(tool=tool, memory=memory, audit=audit)
    try:
        agent.run()
    except AgentError as exc:
        logger.warning("Epic Decomposer failed: %s", exc)
        audit.record_failure("epic_decomposer", str(exc))
        _emit(config, 3, "epic_decomposer", "failed", str(exc)[:120])
        return _record_stage_error(state, "epic_decomposer", str(exc))

    updates = _extract_memory_updates(memory)
    _emit(config, 3, "epic_decomposer", "completed",
          f"{len(updates.get('epics', []))} epics with task breakdowns")
    return updates


def gap_detector_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Stage 5 — detect gaps, conflicts, and duplicate stories."""
    from agents.gap_detector_agent import GapDetectorAgent

    audit: AuditLog = state["_audit"]
    cfg = config.get("configurable") or {}
    jira: JiraTool  = cfg.get("_jira") or JiraTool()
    github: GithubTool = cfg.get("_github") or GithubTool()
    stories          = state.get("stories") or []
    existing_tickets = state.get("existing_tickets") or []

    if not stories:
        _emit(config, 4, "gap_detector", "skipped", "no stories to compare")
        audit.record(
            "gap_detector", "stage_skipped",
            payload={"reason": "no stories in memory"},
            reasoning="Gap Detector not run: no stories to compare against backlog.",
        )
        return {}

    live_jira = state.get("live_jira", False)
    jira_label = (
        f"{len(existing_tickets)} tickets via Jira REST" if live_jira
        else (f"{len(existing_tickets)} tickets from local file / sample"
              if existing_tickets else "no backlog provided")
    )
    _emit(config, 4, "gap_detector", "started",
          f"comparing {len(stories)} stories · {jira_label}")

    try:
        tool = _get_tool("gap_detector", state, config)
    except ToolError as exc:
        audit.record_failure("gap_detector", f"Tool init failed: {exc}")
        _emit(config, 4, "gap_detector", "failed", str(exc)[:120])
        return _record_stage_error(state, "gap_detector", str(exc))

    memory = _hydrate_memory(state)
    agent  = GapDetectorAgent(
        tool=tool,
        jira=jira,
        github=github,
        memory=memory,
        audit=audit,
        use_embeddings_for_duplicates=state.get("use_embeddings_for_duplicates", True),
    )
    try:
        agent.run()
    except AgentError as exc:
        logger.warning("Gap Detector failed: %s", exc)
        audit.record_failure("gap_detector", str(exc))
        _emit(config, 4, "gap_detector", "failed", str(exc)[:120])
        return _record_stage_error(state, "gap_detector", str(exc))

    updates = _extract_memory_updates(memory)
    _emit(config, 4, "gap_detector", "completed",
          f"{len(updates.get('duplicates', []))} dupes, "
          f"{len(updates.get('conflicts', []))} conflicts, "
          f"{len(updates.get('gaps', []))} gaps")
    return updates


def finalize_node(state: PipelineState, config: RunnableConfig) -> dict:  # noqa: ARG001
    """Run guardrails, aggregate token usage, close audit trail."""
    audit: AuditLog = state["_audit"]
    resolved_models = state.get("resolved_models") or {}

    partial_result = {
        "summary":     state.get("summary", ""),
        "topics":      state.get("topics", []),
        "constraints": state.get("constraints", []),
        "epics":       state.get("epics", []),
        "gaps":        state.get("gaps", []),
        "conflicts":   state.get("conflicts", []),
        "duplicates":  state.get("duplicates", []),
    }

    # Accumulates crash messages for any post-synthesis check that fails so they
    # are merged into stage_errors and surfaced in the UI error indicators.
    _extra_errors: dict[str, str] = {}

    # ---- Guardrails ----
    guardrail_findings: list[dict] = []
    try:
        from guardrails import run_guardrails, summarise
        findings = run_guardrails(partial_result)
        guardrail_findings = [f.to_dict() for f in findings]
        tally = summarise(findings)
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("Guardrails crashed: %s", exc, exc_info=True)
        _extra_errors["guardrails"] = str(exc)
        guardrail_findings.append({
            "code": "guardrails_failed",
            "severity": "error",
            "message": f"Quality guardrail checks did not complete: {exc}",
            "story_id": None,
        })

    # ---- Output safety scan (PII / toxicity / demographic bias) ----
    try:
        from security import OutputScanner
        epics_for_scan = state.get("epics") or []
        output_sec_findings = OutputScanner.scan_stories(epics_for_scan)
        for f in output_sec_findings:
            audit.record(
                "orchestrator", "output_security_finding",
                payload={
                    "code":     f.code,
                    "severity": f.severity,
                    "story_id": f.story_id or "—",
                    "message":  f.message,
                },
                reasoning=f"Output safety scan fired '{f.code}' at severity '{f.severity}'.",
            )
        if output_sec_findings:
            audit.record(
                "orchestrator", "output_scan_findings",
                payload={"finding_count": len(output_sec_findings),
                         "codes": [f.code for f in output_sec_findings]},
                reasoning=(
                    f"Output safety scan complete — {len(output_sec_findings)} finding(s) "
                    "in synthesised story text."
                ),
            )
        else:
            audit.record(
                "orchestrator", "output_scan_clean",
                payload={},
                reasoning="Output safety scan found no PII, toxicity, or bias markers.",
            )
        guardrail_findings.extend(f.to_dict() for f in output_sec_findings)
        if output_sec_findings:
            try:
                from alerts import post_security_alert
                _run_id = state.get("run_id") or ""
                _user   = state.get("user_email") or "anonymous"
                post_security_alert(
                    [f.to_dict() for f in output_sec_findings],
                    run_id=_run_id,
                    user=_user,
                )
            except Exception as _alert_exc:  # noqa: BLE001
                logger.debug("Security alert dispatch error: %s", _alert_exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Output safety scan crashed: %s", exc, exc_info=True)
        _extra_errors["output_scan"] = str(exc)

    # ---- PII restore — un-redact tokens in all story/epic output ----
    _rmap = state.get("_redaction_map")
    if _rmap is not None and not _rmap.is_empty():
        from security import unredact_obj_pii
        for key in ("epics", "gaps", "conflicts", "duplicates", "topics", "constraints"):
            if partial_result.get(key):
                partial_result[key] = unredact_obj_pii(partial_result[key], _rmap)
        partial_result["summary"] = unredact_obj_pii(partial_result.get("summary", ""), _rmap)
        audit.record(
            "orchestrator", "pii_unredaction_applied",
            payload={"tokens_restored": len(_rmap.token_to_value)},
            reasoning="PII tokens restored in final output before returning to caller.",
        )

    # ---- Merge input-injection findings (recorded in initialize_node) ----
    prior_security = list(state.get("security_findings") or [])
    if prior_security:
        guardrail_findings = prior_security + guardrail_findings

    # ---- Token usage ----
    token_usage   = _aggregate_token_usage(audit)
    model_summary = _summarize_models(resolved_models)

    # ---- Final audit record ----
    epics     = state.get("epics") or []
    n_stories = sum(len(e.get("stories", [])) for e in epics)
    total_tok = (
        int((token_usage.get("total") or {}).get("input", 0))
        + int((token_usage.get("total") or {}).get("output", 0))
    )
    audit.record(
        "orchestrator", "pipeline_completed",
        payload={
            "epics":            len(epics),
            "stories":          n_stories,
            "gaps":             len(state.get("gaps") or []),
            "conflicts":        len(state.get("conflicts") or []),
            "duplicates":       len(state.get("duplicates") or []),
            "guardrail_errors": sum(
                1 for f in guardrail_findings if f.get("severity") == "error"
            ),
            "total_tokens":     total_tok,
            "model_summary":    model_summary,
            "audit_chain_fingerprint": audit.chain_fingerprint,
        },
        reasoning=(
            f"Pipeline completed. Produced {len(epics)} epic(s) with {n_stories} story(ies)."
        ),
    )

    result: dict = {
        "token_usage":              token_usage,
        "guardrail_findings":       guardrail_findings,
        "audit_chain_fingerprint":  audit.chain_fingerprint,
    }
    if _extra_errors:
        # Return only the new entries; the _merge_dicts reducer handles merging
        # with stage_errors written by earlier nodes.
        result["stage_errors"] = _extra_errors
    return result


# ------------------------------------------------------------------ graph


def build_pipeline(checkpointer=None):
    """Build and compile the LangGraph StateGraph.

    Returns a compiled graph whose ``.invoke()`` / ``.stream()`` methods
    accept ``PipelineState`` as input and return the final state dict.

    Pass ``checkpointer=SqliteSaver.from_conn_string("logs/checkpoints.db")``
    for fault-tolerant multi-run persistence (requires a unique ``thread_id``
    in the config's ``configurable`` dict).  Defaults to MemorySaver
    (in-process, ephemeral) which is safe for single-run usage.
    """
    graph: StateGraph = StateGraph(PipelineState)

    graph.add_node("initialize",          _node_with_span("initialize",          initialize_node))
    graph.add_node("parse",               _node_with_span("parse",               parse_node))
    graph.add_node("extract_constraints", _node_with_span("extract_constraints", constraint_node))
    graph.add_node("write_stories",       _node_with_span("write_stories",       story_writer_node))
    graph.add_node("decompose_epics",     _node_with_span("decompose_epics",     epic_decomposer_node))
    graph.add_node("detect_gaps",         _node_with_span("detect_gaps",         gap_detector_node))
    graph.add_node("finalize",            _node_with_span("finalize",            finalize_node))

    # parse (stage 0) and extract_constraints (stage 1) are independent:
    # parse reads transcript_text; constraint reads constraint_text.
    # Running them in parallel via LangGraph fan-out cuts ~40% off wall time
    # on Premium preset where both stages call long LLM completions.
    # write_stories is the fan-in: LangGraph waits for both before proceeding.
    graph.add_edge(START,                 "initialize")
    graph.add_edge("initialize",          "parse")               # fan-out branch 1
    graph.add_edge("initialize",          "extract_constraints") # fan-out branch 2
    graph.add_edge("parse",               "write_stories")       # fan-in
    graph.add_edge("extract_constraints", "write_stories")       # fan-in
    graph.add_edge("write_stories",       "decompose_epics")
    graph.add_edge("decompose_epics",     "detect_gaps")
    graph.add_edge("detect_gaps",         "finalize")
    graph.add_edge("finalize",            END)

    if checkpointer is None:
        redis_url = os.environ.get("REDIS_URL")
        if redis_url and HAS_REDIS_SAVER:
            checkpointer = RedisSaver(redis_url=redis_url)
        else:
            try:
                from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
                _serde = JsonPlusSerializer(pickle_fallback=True)
                checkpointer = MemorySaver(serde=_serde)
            except (ImportError, TypeError):
                # Older LangGraph versions: MemorySaver() without custom serde.
                checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)
