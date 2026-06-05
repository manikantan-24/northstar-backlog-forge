"""Backlog Synthesizer — MCP Server

Exposes the multi-agent pipeline as a set of MCP tools so it can be called
from Claude Desktop, other Claude agents, or any MCP-compatible client.

Usage:
    # Run the MCP server (stdio transport — Claude Desktop connects to this)
    python mcp_server.py

    # Or via uvx / npx:
    uvx mcp_server.py

Claude Desktop config (~/.claude/claude_desktop_config.json):
{
  "mcpServers": {
    "backlog-synthesizer": {
      "command": "python",
      "args": ["/path/to/backlog-synthesizer/mcp_server.py"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "JIRA_BASE_URL": "https://your-tenant.atlassian.net",
        "JIRA_EMAIL": "you@company.com",
        "JIRA_API_TOKEN": "...",
        "JIRA_PROJECT_KEY": "PROJ"
      }
    }
  }
}

Available tools (callable by Claude Desktop / agents):
  synthesize_backlog     — run the full 5-agent pipeline
  get_run_history        — list recent synthesis runs
  get_run_result         — fetch a specific run's synthesis output
  push_to_jira           — push a completed synthesis to live Jira
  preview_prompts        — dry-run: see what each agent would send to the LLM
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Bootstrap path ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

try:
    from fastmcp import FastMCP
except ImportError:
    print("FastMCP not installed. Run: pip install mcp-atlassian  (includes fastmcp)")
    sys.exit(1)

from orchestrator import Orchestrator
from pricing import estimate_cost_usd

# ── Preset definitions (mirrors app.py) ───────────────────────────────────────
MODEL_PRESETS: dict[str, dict[str, str]] = {
    "free": {
        "parser": "gemini-2.5-flash", "constraint": "gemini-2.5-flash",
        "story_writer": "gemini-2.5-flash", "epic_decomposer": "gemini-2.5-flash",
        "gap_detector": "gemini-2.5-flash",
    },
    "balanced": {
        "parser": "gemini-2.5-flash", "constraint": "gemini-2.5-flash",
        "story_writer": "claude-sonnet-4-5", "epic_decomposer": "gemini-2.5-flash",
        "gap_detector": "claude-sonnet-4-5",
    },
    "premium": {
        "parser": "claude-sonnet-4-5", "constraint": "claude-sonnet-4-5",
        "story_writer": "claude-sonnet-4-5", "epic_decomposer": "claude-sonnet-4-5",
        "gap_detector": "claude-sonnet-4-5",
    },
}

RUNS_DIR = ROOT / "logs" / "runs"

# ── MCP server ─────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "Backlog Synthesizer",
    instructions=(
        "A multi-agent pipeline that synthesizes sprint backlogs from meeting transcripts. "
        "It extracts topics, applies architectural constraints, writes user stories with "
        "Given/When/Then acceptance criteria, groups them into epics, and detects "
        "duplicates/conflicts/gaps against your existing Jira and GitHub backlog. "
        "Use synthesize_backlog to run the full pipeline. "
        "Use push_to_jira to create the output in Jira after reviewing it."
    ),
)


@mcp.tool()
def synthesize_backlog(
    transcript: str,
    constraints: str = "",
    preset: str = "balanced",
    redact_pii: bool = True,
) -> dict[str, Any]:
    """Run the full 5-agent backlog synthesis pipeline.

    Args:
        transcript:   Meeting transcript, sprint notes, or any text describing
                      what needs to be built. Plain text or Markdown.
        constraints:  Architecture wiki, technical constraints, or design rules
                      the stories must respect. Optional.
        preset:       Model preset — "free" (Gemini only), "balanced" (Gemini +
                      Claude, default), or "premium" (all Claude).
        redact_pii:   Replace personal data with stable placeholders before
                      sending to the LLM. Default True.

    Returns:
        A dict with:
          epics        — list of epic dicts, each with nested stories and tasks
          gaps         — capabilities missing from the existing backlog
          conflicts    — stories that clash with architectural constraints
          duplicates   — stories that already exist in Jira / GitHub
          audit_trail  — markdown trace of every agent decision
          token_usage  — per-stage input/output token counts
    """
    if not transcript.strip():
        return {"error": "transcript is required and cannot be empty."}

    models = MODEL_PRESETS.get(preset, MODEL_PRESETS["balanced"])
    orch = Orchestrator()

    result = orch.run(
        transcript_text=transcript,
        constraint_text=constraints,
        redact_pii=redact_pii,
        models=models,
        run_metadata={"caller": "mcp_server", "preset": preset},
    )

    # Return a summary-focused view — omit the full audit trail markdown from
    # the tool result to keep the response manageable. It's saved to disk.
    epics = result.get("epics") or []
    n_stories = sum(len(e.get("stories") or []) for e in epics)
    return {
        "summary": result.get("summary", ""),
        "epics_count": len(epics),
        "stories_count": n_stories,
        "gaps_count": len(result.get("gaps") or []),
        "conflicts_count": len(result.get("conflicts") or []),
        "duplicates_count": len(result.get("duplicates") or []),
        "guardrail_errors": sum(
            1 for f in (result.get("guardrail_findings") or [])
            if f.get("severity") == "error"
        ),
        "token_usage": result.get("token_usage", {}),
        "model": result.get("model", ""),
        "epics": epics,
        "gaps": result.get("gaps", []),
        "conflicts": result.get("conflicts", []),
        "duplicates": result.get("duplicates", []),
        "audit_chain_fingerprint": result.get("audit_chain_fingerprint", ""),
    }


@mcp.tool()
def preview_prompts(
    transcript: str,
    constraints: str = "",
    preset: str = "balanced",
) -> dict[str, str]:
    """Dry-run the pipeline — see the exact prompt each agent would send to the LLM.

    No LLM calls are made. Useful for reviewing what the pipeline will do
    before spending API budget.

    Args:
        transcript:  Meeting transcript text.
        constraints: Architecture wiki / constraints text.
        preset:      Model preset for cost estimation.

    Returns:
        A dict mapping agent name → prompt text for each stage that would run.
    """
    models = MODEL_PRESETS.get(preset, MODEL_PRESETS["balanced"])
    orch = Orchestrator()
    result = orch.run(
        transcript_text=transcript,
        constraint_text=constraints,
        dry_run=True,
        models=models,
    )
    return result.get("dry_run_prompts", {})


@mcp.tool()
def get_run_history(limit: int = 10) -> list[dict[str, Any]]:
    """Return recent synthesis runs, newest first.

    Args:
        limit: Maximum number of runs to return (default 10, max 50).

    Returns:
        List of run summary dicts with: run_id, timestamp, source_label,
        epic_count, story_count, gap_count, model, cost_usd.
    """
    limit = min(max(1, limit), 50)
    if not RUNS_DIR.exists():
        return []
    entries: list[dict] = []
    for p in RUNS_DIR.glob("**/*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            entries.append({
                "run_id":       data.get("run_id", ""),
                "timestamp":    data.get("timestamp", ""),
                "source_label": data.get("source_label", ""),
                "epic_count":   data.get("epic_count", 0),
                "story_count":  data.get("story_count", 0),
                "gap_count":    data.get("gap_count", 0),
                "model":        data.get("model", ""),
                "cost_usd":     data.get("cost_usd", 0.0),
            })
        except (OSError, json.JSONDecodeError):
            continue
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:limit]


@mcp.tool()
def get_run_result(run_id: str) -> dict[str, Any]:
    """Fetch the full synthesis output for a specific past run.

    Args:
        run_id: The run ID returned by get_run_history (e.g. '20260604_143022_abc123').

    Returns:
        The synthesis dict with epics, stories, gaps, conflicts, duplicates.
        Returns {"error": "..."} if the run is not found.
    """
    if not RUNS_DIR.exists():
        return {"error": "No runs directory found."}

    # Find the metadata file by run_id prefix
    for p in RUNS_DIR.glob("**/*.json"):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            if meta.get("run_id", "").startswith(run_id) or run_id in str(p):
                outputs = meta.get("outputs", {})
                synth_path = outputs.get("synthesis_json")
                if synth_path and Path(synth_path).exists():
                    return json.loads(Path(synth_path).read_text(encoding="utf-8"))
                return {"error": f"Run {run_id} found but synthesis.json is missing."}
        except (OSError, json.JSONDecodeError):
            continue
    return {"error": f"Run {run_id!r} not found. Use get_run_history to list available runs."}


@mcp.tool()
def push_to_jira(
    run_id: str,
    project_key: str = "",
    create_subtasks: bool = True,
) -> dict[str, Any]:
    """Push a completed synthesis to live Jira as Epic → Story → Sub-task.

    Creates real Jira issues. Requires JIRA_BASE_URL, JIRA_EMAIL,
    JIRA_API_TOKEN, and JIRA_PROJECT_KEY to be set.

    Args:
        run_id:          Run ID from get_run_history.
        project_key:     Jira project key (e.g. 'PROJ'). Falls back to
                         JIRA_PROJECT_KEY env var if not provided.
        create_subtasks: Also create Sub-task issues for each story's tasks.

    Returns:
        {"created": [...], "errors": [...], "counts": {...}, "project": "..."}
    """
    result = get_run_result(run_id)
    if "error" in result:
        return result

    proj = project_key or os.environ.get("JIRA_PROJECT_KEY", "")
    if not proj:
        return {"error": "project_key is required (or set JIRA_PROJECT_KEY env var)."}

    required = ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"]
    missing = [v for v in required if not os.environ.get(v, "").strip()]
    if missing:
        return {"error": f"Missing Jira credentials: {', '.join(missing)}"}

    from tools.jira_tool import JiraTool
    jt = JiraTool(mode="live", project_key=proj)
    publish_result = jt.publish_synthesis(
        result,
        project_key=proj,
        create_subtasks=create_subtasks,
    )
    return publish_result


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(
        "Backlog Synthesizer MCP server starting…\n"
        "Add to Claude Desktop config:\n"
        '  "command": "python",\n'
        f'  "args": ["{ROOT / "mcp_server.py"}"]',
        file=sys.stderr,
    )
    mcp.run()
