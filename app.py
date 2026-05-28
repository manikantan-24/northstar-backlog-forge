"""Streamlit UI for the Backlog Synthesizer.

Single-file app on purpose — no auth, no role gating, no compare mode.
The multi-agent system is the product; the UI just makes the three
inputs / five outputs visible without a terminal.

To run:
    streamlit run app.py

The UI reads the same `orchestrator.Orchestrator` the CLI does, so any
synthesis you can run via `python src/main.py` you can also run here.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

# -------------------------------------------------------- bootstrap

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# Same Atlassian tenant — same credentials work for Jira and Confluence.
# Promote JIRA_* into CONFLUENCE_* so the live-source toggles below can
# rely on a single set of vars in .env.
for _conf, _jira in (
    ("CONFLUENCE_BASE_URL", "JIRA_BASE_URL"),
    ("CONFLUENCE_EMAIL", "JIRA_EMAIL"),
    ("CONFLUENCE_API_TOKEN", "JIRA_API_TOKEN"),
):
    if not os.environ.get(_conf) and os.environ.get(_jira):
        os.environ[_conf] = os.environ[_jira]

sys.path.insert(0, str(ROOT / "src"))

from input_loader import load_text, load_tickets, InputError  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from output_formatter import write_outputs  # noqa: E402
from ui.styling import get_css  # noqa: E402
from pricing import estimate_cost_usd  # noqa: E402

st.set_page_config(
    page_title="Backlog Synthesizer",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------------------------------------------- styling
# All CSS lives in src/ui/styling.py — kept out of this file so the
# control flow stays readable. One injection per Streamlit rerun.

st.markdown(get_css(), unsafe_allow_html=True)

# -------------------------------------------------------- helpers


def _esc(value: Any) -> str:
    """Minimal HTML escape so user content doesn't escape its container."""
    if value is None:
        return ""
    s = str(value)
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#x27;")
    )


def _pri_class(priority: str) -> str:
    p = (priority or "").strip().lower()
    if p == "high":
        return "pri-high"
    if p in ("medium", "med"):
        return "pri-medium"
    if p == "low":
        return "pri-low"
    return "pri-medium"


# Five-stage pipeline visualization.
# Each entry: (number, display name, description, agent class name used by
# the orchestrator — used to look up per-stage token counts from token_usage).
_STAGES = [
    ("01", "Parser",          "Extract topics, actors, and requirements from the transcript",       "ParserAgent"),
    ("02", "Constraint",      "Pull engineering rules and constraints from the architecture wiki",  "ConstraintAgent"),
    ("03", "Story Writer",    "Generate user stories with Given/When/Then acceptance criteria",     "StoryWriterAgent"),
    ("04", "Epic Decomposer", "Group stories into themed epics and break each into tasks",          "EpicDecomposerAgent"),
    # Gap Detector is a hybrid stage: local sentence-transformers embeddings
    # handle the duplicate-detection sub-step (no LLM cost), and an LLM
    # call judges conflicts and gaps. The model badge on this card refers
    # to the LLM used for conflicts/gaps only.
    ("05", "Gap Detector",    "Local embeddings find duplicates; LLM judges conflicts + gaps",      "GapDetectorAgent"),
]


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


# Mapping table used by both the audit-log token rollup and the
# pipeline-card model badge. Accepts BOTH the agent class name
# ("ParserAgent") and the agent's `.name` attribute ("parser") as input
# — the audit log records the latter, older history entries the former.
# Values are the per-stage keys used by `result["models"]`.
_AGENT_CLASS_TO_STAGE = {
    # Class-name lookups (legacy history entries).
    "ParserAgent":         "parser",
    "ConstraintAgent":     "constraint",
    "StoryWriterAgent":    "story_writer",
    "EpicDecomposerAgent": "epic_decomposer",
    "GapDetectorAgent":    "gap_detector",
    # Stage-name lookups (audit log + token_usage in current runs).
    "parser":              "parser",
    "constraint":          "constraint",
    "story_writer":        "story_writer",
    "epic_decomposer":     "epic_decomposer",
    "gap_detector":        "gap_detector",
}


def _model_for_agent(agent_key: str, models_per_stage: dict) -> str:
    """Return the model id for the given agent identifier.

    `agent_key` can be either the agent class name (`ParserAgent`) or
    the agent's `.name` attribute (`parser`). Returns an empty string
    when neither matches.
    """
    stage = _AGENT_CLASS_TO_STAGE.get(agent_key)
    if stage and models_per_stage:
        return models_per_stage.get(stage, "")
    return ""


# Output-token budget per stage. These are conservative averages from prior
# runs on the bundled sample (`samples/meeting_notes.txt`, 30-ticket backlog).
# Used by the pre-run cost estimator to set expectations BEFORE the user
# clicks Synthesize. The real bill will swing a bit either way; the sidebar
# label is prefixed with "≈" to make that clear.
_PRE_RUN_OUTPUT_BUDGET: dict[str, int] = {
    "parser":          1500,
    "constraint":      1200,
    "story_writer":    4500,
    "epic_decomposer": 3000,
    "gap_detector":    2500,
}


def _estimate_pre_run_cost(
    *,
    transcript_choice: str,
    transcript_upload,
    constraints_choice: str,
    constraints_upload,
    backlog_choice: str,
    backlog_upload,
    models: dict[str, str],
) -> tuple[float, int, int]:
    """Estimate $ cost + input/output tokens before a run.

    Each stage only sees the inputs it actually consumes:
        parser           → transcript
        constraint       → constraints
        story_writer     → parser-output + constraint-output (we treat
                           these as the upstream agents' output budgets)
        epic_decomposer  → story-writer-output
        gap_detector     → story-writer-output + backlog

    This corrects an earlier draft that summed transcript+constraints+
    backlog and fed the lump into every stage — which double-counted
    inputs and pushed the estimate up by ~3x.

    `~4 chars per English token` is the standard back-of-envelope
    ratio. Output token budgets come from `_PRE_RUN_OUTPUT_BUDGET`,
    measured on prior runs of the bundled sample.
    """
    def _chars_of(choice_key: str, options: dict, upload) -> int:
        val = options.get(choice_key)
        if val == "__upload__":
            return int(getattr(upload, "size", 0) or 0)
        if not val:
            return 0
        try:
            return Path(str(val)).stat().st_size
        except OSError:
            return 0

    transcript_chars = _chars_of(transcript_choice, TRANSCRIPT_OPTIONS, transcript_upload)
    constraint_chars = _chars_of(constraints_choice, CONSTRAINTS_OPTIONS, constraints_upload)
    backlog_chars = _chars_of(backlog_choice, BACKLOG_OPTIONS, backlog_upload)

    transcript_tokens = transcript_chars // 4
    constraint_tokens = constraint_chars // 4
    backlog_tokens = backlog_chars // 4

    # Per-stage input tokens, mapping what each agent actually reads.
    parser_in       = transcript_tokens
    constraint_in   = constraint_tokens
    story_writer_in = _PRE_RUN_OUTPUT_BUDGET["parser"] + _PRE_RUN_OUTPUT_BUDGET["constraint"]
    epic_in         = _PRE_RUN_OUTPUT_BUDGET["story_writer"]
    # The Gap Detector sees stories + a sample of the backlog (the
    # vector store caps at ~5 candidates per story, but for a pre-run
    # estimate we model the entire backlog as input to be safe).
    gap_in          = _PRE_RUN_OUTPUT_BUDGET["story_writer"] + backlog_tokens

    stage_inputs = {
        "parser":           parser_in,
        "constraint":       constraint_in,
        "story_writer":     story_writer_in,
        "epic_decomposer":  epic_in,
        "gap_detector":     gap_in,
    }

    total_in = 0
    total_out = 0
    total_cost = 0.0
    for stage, output_tokens in _PRE_RUN_OUTPUT_BUDGET.items():
        model = (models or {}).get(stage) or ""
        if not model:
            continue
        input_tokens = stage_inputs.get(stage, 0)
        c = estimate_cost_usd(model, input_tokens, output_tokens)
        if c is None:
            continue
        total_in += input_tokens
        total_out += output_tokens
        total_cost += c

    return total_cost, total_in, total_out


def _compute_total_cost(token_usage: dict, models_per_stage: dict) -> float:
    """Sum per-agent costs using each agent's stage model rate.

    Skips the `total` row (it's the input/output sum, not a per-agent
    row). Returns 0.0 when no per-agent rows are present or no models
    are known.
    """
    total = 0.0
    if not token_usage:
        return 0.0
    for agent_key, vals in token_usage.items():
        if agent_key == "total":
            continue
        ai = int((vals or {}).get("input", 0) or 0)
        ao = int((vals or {}).get("output", 0) or 0)
        model = _model_for_agent(agent_key, models_per_stage)
        c = estimate_cost_usd(model, ai, ao) if model else None
        if c is not None:
            total += c
    return total


def _render_pipeline(
    stage_states: list[str] | None = None,
    model: str | None = None,
    token_usage: dict | None = None,
    models_per_stage: dict | None = None,
) -> None:
    """Render the 5 stage cards.

    `stage_states[i]` is one of: "idle" (default), "active", "done",
    "failed", "skipped". When None, all stages render as idle. The
    `.stage.active` class drives the glow/pulse animation defined in
    `ui/styling.py`.

    `models_per_stage` — preferred: a `{stage_name: model_id}` dict.
    Each card shows ITS stage's model. Falls back to the summary
    `model` string only when the per-stage dict isn't provided.

    `token_usage` — if set, completed stages show their input/output tokens.
    Tokens are looked up by BOTH the agent class name and the stage
    name (token_usage in current orchestrator runs uses stage names).
    """
    if stage_states is None:
        stage_states = ["idle"] * len(_STAGES)
    token_usage = token_usage or {}
    models_per_stage = models_per_stage or {}
    cells = []
    for i, (num, name, sub, agent_cls) in enumerate(_STAGES):
        state = stage_states[i] if i < len(stage_states) else "idle"
        cls_map = {
            "idle": "stage",
            "active": "stage active",
            "done": "stage done",
            "failed": "stage error",
            "skipped": "stage skipped",
        }
        cls = cls_map.get(state, "stage")
        glyph = {
            "active": "●",
            "done": "✓",
            "failed": "!",
            "skipped": "—",
        }.get(state, "")
        glyph_html = (
            f'<span class="stage-glyph">{glyph}</span>' if glyph else ""
        )

        # Model badge: prefer the per-stage model (so each card shows
        # the model that stage actually used); fall back to the summary
        # string only when we don't have the per-stage dict.
        stage_model = _model_for_agent(agent_cls, models_per_stage)
        badge_text = stage_model or model or ""
        # For the Gap Detector card specifically, append a small "+embed"
        # hint so the user knows the duplicate sub-step is local (no LLM).
        embed_hint = (
            ' <span style="font-size:0.62rem;color:var(--violet);'
            'padding:1px 5px;border-radius:6px;background:var(--violet-glow);'
            'margin-left:0.3rem;">+embed</span>'
            if agent_cls == "GapDetectorAgent" else ""
        )
        model_html = (
            f'<div class="stage-model"><span class="stage-model-dot"></span>'
            f'{_esc(badge_text)}{embed_hint}</div>'
        ) if badge_text else ""

        # Token badge: look up by both class name and stage name so this
        # works against current runs and any legacy history rows.
        tokens_html = ""
        if state in ("done", "failed"):
            stage_key = _AGENT_CLASS_TO_STAGE.get(agent_cls, agent_cls)
            usage = token_usage.get(agent_cls) or token_usage.get(stage_key) or {}
            ai = int(usage.get("input") or 0)
            ao = int(usage.get("output") or 0)
            if ai or ao:
                tokens_html = (
                    f'<div class="stage-tokens">'
                    f'<span class="stage-tokens-in">↓ {_fmt_tokens(ai)}</span>'
                    f'<span class="stage-tokens-out">↑ {_fmt_tokens(ao)}</span>'
                    f'</div>'
                )

        cells.append(
            f'<div class="{cls}">{glyph_html}'
            f'<div class="stage-num">STAGE {num}</div>'
            f'<div class="stage-name">{_esc(name)}</div>'
            f'<div class="stage-sub">{_esc(sub)}</div>'
            f'{model_html}{tokens_html}</div>'
        )
    st.markdown(f'<div class="pipeline">{"".join(cells)}</div>', unsafe_allow_html=True)


def _render_kpis(result: dict) -> None:
    epics = result.get("epics", []) or []
    n_epics = len(epics)
    n_stories = sum(len(e.get("stories", []) or []) for e in epics)
    n_gaps = len(result.get("gaps", []) or [])
    n_conflicts = len(result.get("conflicts", []) or [])
    n_dups = len(result.get("duplicates", []) or [])

    html = f"""
    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">Epics</div>
        <div class="kpi-value">{n_epics}</div>
        <div class="kpi-meta">top-level themes</div></div>
      <div class="kpi"><div class="kpi-label">Stories</div>
        <div class="kpi-value">{n_stories}</div>
        <div class="kpi-meta">with acceptance criteria</div></div>
      <div class="kpi amber"><div class="kpi-label">Gaps</div>
        <div class="kpi-value">{n_gaps}</div>
        <div class="kpi-meta">{'capabilities missing' if n_gaps else 'none detected'}</div></div>
      <div class="kpi rose"><div class="kpi-label">Conflicts</div>
        <div class="kpi-value">{n_conflicts}</div>
        <div class="kpi-meta">{'vs. constraints' if n_conflicts else 'no constraint clashes'}</div></div>
      <div class="kpi violet"><div class="kpi-label">Duplicates</div>
        <div class="kpi-value">{n_dups}</div>
        <div class="kpi-meta">{'overlap with backlog' if n_dups else 'no overlap with backlog'}</div></div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _flatten_stories_for_editor(epics: list[dict]) -> list[dict]:
    """Flatten epic.stories into a flat rows list for st.data_editor.

    Returns rows that include the parent epic id + index so writes can
    round-trip back into the nested epic structure on save.
    """
    rows: list[dict] = []
    for ei, ep in enumerate(epics):
        for si, s in enumerate(ep.get("stories", []) or []):
            ac = s.get("acceptance_criteria") or []
            rows.append({
                "epic": ep.get("title", "")[:60],
                "id": s.get("id", ""),
                "summary": s.get("title", ""),
                "description": s.get("description", "") or "",
                "priority": (s.get("priority") or "Medium").strip().capitalize(),
                "category": (s.get("tags") or ["feature"])[0] if s.get("tags") else "feature",
                "acceptance_criteria": "\n".join(ac) if isinstance(ac, list) else str(ac or ""),
                # Hidden bookkeeping — used to write edits back.
                "_epic_idx": ei,
                "_story_idx": si,
            })
    return rows


def _apply_editor_edits(epics: list[dict], edited_rows: list[dict]) -> list[dict]:
    """Merge edits from the data editor back into the epics structure."""
    out = json.loads(json.dumps(epics))  # deep copy
    for row in edited_rows:
        ei = row.get("_epic_idx")
        si = row.get("_story_idx")
        if ei is None or si is None:
            continue
        if ei >= len(out) or si >= len(out[ei].get("stories", []) or []):
            continue
        s = out[ei]["stories"][si]
        s["title"] = (row.get("summary") or "").strip()
        s["description"] = (row.get("description") or "").strip()
        pri = (row.get("priority") or "Medium").strip()
        s["priority"] = pri or "Medium"
        cat = (row.get("category") or "").strip()
        if cat:
            # Store category as the first tag (matches what _flatten reads).
            tags = s.get("tags") or []
            if tags:
                tags[0] = cat
            else:
                tags = [cat]
            s["tags"] = tags
        ac_text = (row.get("acceptance_criteria") or "").strip()
        s["acceptance_criteria"] = [
            line.strip() for line in ac_text.splitlines() if line.strip()
        ]
    return out


def _render_epics_tab(result: dict) -> None:
    """Render epics either as cards (view) or as a data_editor (edit).

    Edits flow back into `st.session_state.result["epics"]` so the
    download buttons pick them up automatically.
    """
    epics = result.get("epics", []) or []
    total_stories = sum(len(e.get("stories") or []) for e in epics)
    if not epics or total_stories == 0:
        # Special-case the hallucination-check sample: zero stories is the
        # *expected* outcome (negative test), so frame it as a guardrail
        # PASS rather than a generic empty-result message.
        src = (st.session_state.get("source_label") or "").lower()
        if "hallucination_check" in src or "hallucination-check" in src:
            st.markdown(
                """
                <div class="guardrail-pass">
                    <div class="guardrail-pass-tag">✓  Hallucination guardrail · PASS</div>
                    <div class="guardrail-pass-title">
                        Zero stories produced — exactly the expected outcome.
                    </div>
                    <div class="guardrail-pass-body">
                        This run used a deliberately off-topic transcript (no engineering content).
                        The agent prompts instruct each model to return an empty list when there's
                        nothing legitimate to extract, instead of inventing stories. The same input
                        is asserted in the evaluation harness — every change to the prompts
                        re-verifies this behavior.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.info(
                "Zero stories extracted. This happens when the input is too short, off-topic, "
                "or has no actionable content — the agent prompts instruct each model to return "
                "an empty list rather than hallucinate. If you expected stories, double-check the "
                "source text."
            )
        return

    # Edit / view toggle + reset button.
    c1, c2, c3 = st.columns([1, 1, 4])
    with c1:
        view_active = not bool(st.session_state.get("stories_edit_mode"))
        if st.button(
            ("● View" if view_active else "  View"),
            key="stories_view_btn",
            use_container_width=True,
            help="Read-only display.",
        ):
            st.session_state.stories_edit_mode = False
    with c2:
        edit_active = bool(st.session_state.get("stories_edit_mode"))
        if st.button(
            ("● Edit" if edit_active else "  Edit"),
            key="stories_edit_btn",
            use_container_width=True,
            help="Fix story fields before exporting. Edits flow into JSON / MD downloads.",
        ):
            st.session_state.stories_edit_mode = True
    with c3:
        if edit_active and st.session_state.get("epics_original") is not None:
            if st.button(
                "↺ Reset to original",
                key="stories_reset_btn",
                help="Restore the original LLM output and discard your edits.",
            ):
                st.session_state.result["epics"] = json.loads(
                    json.dumps(st.session_state.epics_original)
                )
                st.rerun()

    if st.session_state.get("stories_edit_mode"):
        rows = _flatten_stories_for_editor(epics)
        st.caption(
            "Editing in place — changes save to this session and flow into the "
            "JSON / Markdown downloads. Use **Reset to original** to undo."
        )
        edited = st.data_editor(
            rows,
            num_rows="fixed",
            use_container_width=True,
            column_config={
                "epic": st.column_config.TextColumn("Epic", width="small", disabled=True),
                "id": st.column_config.TextColumn("ID", width="small", disabled=True),
                "summary": st.column_config.TextColumn("Summary", width="medium"),
                "description": st.column_config.TextColumn("Description", width="large"),
                "priority": st.column_config.SelectboxColumn(
                    "Priority", options=["Low", "Medium", "High"], width="small",
                ),
                "category": st.column_config.SelectboxColumn(
                    "Category",
                    options=["feature", "bug", "tech-debt", "spike", "chore"],
                    width="small",
                ),
                "acceptance_criteria": st.column_config.TextColumn(
                    "Acceptance criteria (one per line)", width="large",
                ),
                "_epic_idx": None,
                "_story_idx": None,
            },
            key="stories_editor",
        )
        new_epics = _apply_editor_edits(epics, edited or [])
        st.session_state.result["epics"] = new_epics
        return

    # Read-only render — original card layout.
    for ep in epics:
        ep_html = []
        ep_html.append('<div class="epic-card">')
        ep_html.append(
            f'<div class="epic-head"><span class="epic-id">{_esc(ep.get("id"))}</span>'
            f'<span class="epic-title">{_esc(ep.get("title"))}</span></div>'
        )
        if ep.get("description"):
            ep_html.append(f'<div class="epic-desc">{_esc(ep["description"])}</div>')
        for s in ep.get("stories", []) or []:
            pri = s.get("priority") or "Medium"
            tags = s.get("tags") or []
            ac_items = s.get("acceptance_criteria") or []
            tasks = s.get("tasks") or []
            ep_html.append('<div class="story-card">')
            ep_html.append(
                f'<div class="story-head"><span class="story-id">{_esc(s.get("id"))}</span>'
                f'<span class="story-title">{_esc(s.get("title"))}</span>'
                f'<span class="story-pri {_pri_class(pri)}">{_esc(pri)}</span></div>'
            )
            if s.get("user_story"):
                ep_html.append(f'<div class="story-user">{_esc(s["user_story"])}</div>')
            # Evidence: the parser-captured customer quote that motivated the
            # story. Rendered as an inline blockquote so reviewers can trace
            # every story back to a source utterance in the transcript.
            evidence = s.get("evidence") or []
            if evidence:
                ev = evidence[0]
                quote = (ev.get("raw_quote") or "").strip()
                speaker = (ev.get("speaker") or "").strip()
                if quote:
                    attribution = f" — {_esc(speaker)}" if speaker else ""
                    ep_html.append(
                        f'<div class="story-evidence" style="border-left:3px solid #94a3b8;'
                        f'padding:6px 10px;margin:6px 0;color:#475569;'
                        f'font-style:italic;background:#f8fafc;border-radius:4px;">'
                        f'<span style="font-size:11px;letter-spacing:0.06em;'
                        f'text-transform:uppercase;color:#64748b;'
                        f'font-style:normal;display:block;margin-bottom:2px;">'
                        f'Evidence{attribution}</span>"{_esc(quote)}"'
                        f'</div>'
                    )
            if tags:
                ep_html.append('<div class="tags-row">')
                for t in tags:
                    ep_html.append(f'<span class="tag">{_esc(t)}</span>')
                ep_html.append("</div>")
            if ac_items:
                ep_html.append('<ul class="story-ac">')
                for ac in ac_items:
                    ep_html.append(f"<li>{_esc(ac)}</li>")
                ep_html.append("</ul>")
            if tasks:
                ep_html.append('<ol class="task-list">')
                for tk in tasks:
                    ep_html.append(f'<li>{_esc(tk.get("title"))}</li>')
                ep_html.append("</ol>")
            ep_html.append("</div>")
        ep_html.append("</div>")
        st.markdown("".join(ep_html), unsafe_allow_html=True)


def _render_compare_banner(
    summary: dict,
    labels: dict,
    result: dict,
) -> None:
    """Render a compact two-column summary of a compare-mode run.

    Built to be quickly scannable: each row is one metric, with the two
    providers side-by-side and a delta arrow. The full secondary
    synthesis is one click away via the "View secondary" expander.
    """
    primary_lbl = labels.get("primary", "A")
    secondary_lbl = labels.get("secondary", "B")
    pc = summary.get("primary") or {}
    sc = summary.get("secondary") or {}
    deltas = summary.get("deltas") or {}

    rows = [
        ("Epics", "epics"),
        ("Stories", "stories"),
        ("Gaps", "gaps"),
        ("Conflicts", "conflicts"),
        ("Duplicates", "duplicates"),
        ("Guardrail findings", "guardrail_findings"),
        ("Input tokens", "input_tokens"),
        ("Output tokens", "output_tokens"),
    ]

    table_rows = []
    for label, key in rows:
        p_val = pc.get(key, 0)
        s_val = sc.get(key, 0)
        d = deltas.get(key, 0)
        if d == 0:
            delta_html = '<span style="color:var(--text-faint);">=</span>'
        elif d > 0:
            delta_html = f'<span style="color:var(--green);">▲ +{d:,}</span>'
        else:
            delta_html = f'<span style="color:var(--rose);">▼ {d:,}</span>'
        table_rows.append(
            f'<tr>'
            f'<td style="color:var(--text-muted);font-weight:600;">{_esc(label)}</td>'
            f'<td style="text-align:right;font-family:\'IBM Plex Mono\',monospace;">{p_val:,}</td>'
            f'<td style="text-align:right;font-family:\'IBM Plex Mono\',monospace;">{s_val:,}</td>'
            f'<td style="text-align:right;">{delta_html}</td>'
            f'</tr>'
        )

    overlap_pct = summary.get("title_overlap_pct", 0.0)
    st.markdown(
        f'<div style="margin:0.6rem 0 1rem;padding:1rem 1.2rem;'
        f'background:var(--bg-elev-1);border:1px solid var(--violet);'
        f'border-radius:12px;">'
        f'<div style="display:flex;align-items:baseline;justify-content:space-between;'
        f'margin-bottom:0.7rem;">'
        f'<span style="font-size:0.62rem;font-weight:700;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:var(--violet);">Compare mode</span>'
        f'<span style="font-size:0.78rem;color:var(--text-muted);">'
        f'<strong style="color:var(--text);">{overlap_pct}%</strong> story-title overlap'
        f'</span></div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
        f'<thead><tr style="border-bottom:1px solid var(--border);">'
        f'<th style="text-align:left;padding-bottom:0.3rem;color:var(--text-faint);'
        f'font-weight:700;font-size:0.7rem;letter-spacing:0.06em;">METRIC</th>'
        f'<th style="text-align:right;padding-bottom:0.3rem;color:var(--accent);'
        f'font-weight:700;font-size:0.7rem;">{_esc(primary_lbl)}</th>'
        f'<th style="text-align:right;padding-bottom:0.3rem;color:var(--violet);'
        f'font-weight:700;font-size:0.7rem;">{_esc(secondary_lbl)}</th>'
        f'<th style="text-align:right;padding-bottom:0.3rem;color:var(--text-faint);'
        f'font-weight:700;font-size:0.7rem;">Δ</th>'
        f'</tr></thead>'
        f'<tbody>{"".join(table_rows)}</tbody></table>'
        f'<div style="margin-top:0.6rem;font-size:0.75rem;color:var(--text-faint);">'
        f'The detailed view below shows the <strong style="color:var(--accent);">'
        f'{_esc(primary_lbl)}</strong> run. Expand the section below to inspect '
        f'<strong style="color:var(--violet);">{_esc(secondary_lbl)}</strong>.'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Secondary synthesis — collapsible, shows headlines + downloads only.
    secondary = result.get("_compare_secondary")
    if secondary:
        with st.expander(f"View {secondary_lbl} run details", expanded=False):
            sec_epics = secondary.get("epics") or []
            sec_stories = sum(len(e.get("stories") or []) for e in sec_epics)
            st.caption(
                f"{secondary_lbl} produced {len(sec_epics)} epic(s) with "
                f"{sec_stories} stor{'y' if sec_stories == 1 else 'ies'}, "
                f"{len(secondary.get('duplicates') or [])} dup(s), "
                f"{len(secondary.get('conflicts') or [])} conflict(s)."
            )
            sec_json = json.dumps(
                {k: v for k, v in secondary.items() if not k.startswith("_")},
                indent=2,
            )
            st.download_button(
                f"↓  synthesis_{secondary_lbl.lower()}.json",
                sec_json,
                file_name=f"synthesis_{secondary_lbl.lower()}.json",
                mime="application/json",
            )
            st.markdown("**Audit trail (secondary):**")
            st.markdown(secondary.get("audit_trail", "_No audit trail captured._"))


def _render_guardrails_tab(result: dict) -> None:
    """Render the post-LLM guardrail findings, grouped by severity.

    Empty state = a green "all clear" pass — the guardrails ran and
    found nothing worth flagging. That itself is a useful signal so we
    surface it explicitly rather than hiding the tab.
    """
    findings = result.get("guardrail_findings") or []
    if not findings:
        st.markdown(
            '<div class="guardrail-pass">'
            '<div class="guardrail-pass-tag">✓ All guardrails pass</div>'
            '<div class="guardrail-pass-title">No issues caught by post-LLM checks.</div>'
            '<div class="guardrail-pass-body">'
            'Story grounding, acceptance-criteria grammar, unique titles, '
            'canonical tags, and priority-rationale length all looked '
            'reasonable on this run.</div></div>',
            unsafe_allow_html=True,
        )
        return

    severity_order = {"error": 0, "warn": 1, "info": 2}
    findings_sorted = sorted(findings, key=lambda f: severity_order.get(f.get("severity"), 99))

    palette = {
        "error": ("var(--rose)", "var(--rose-glow)", "rgba(251,113,133,.4)"),
        "warn":  ("var(--amber)", "var(--amber-glow)", "rgba(251,191,36,.4)"),
        "info":  ("var(--accent)", "var(--accent-glow)", "rgba(34,211,238,.4)"),
    }

    for f in findings_sorted:
        sev = f.get("severity", "info")
        color, bg, border = palette.get(sev, palette["info"])
        story_ref = f.get("story_id")
        story_chip = (
            f'<span style="font-family:\'IBM Plex Mono\',monospace;'
            f'font-size:0.72rem;color:var(--text-faint);'
            f'margin-left:0.6rem;">{_esc(story_ref)}</span>'
            if story_ref else ""
        )
        st.markdown(
            f'<div style="margin-bottom:0.55rem;padding:0.6rem 0.85rem;'
            f'background:{bg};border:1px solid {border};border-radius:8px;">'
            f'<div style="display:flex;align-items:baseline;gap:0.5rem;">'
            f'<span style="font-size:0.65rem;font-weight:700;letter-spacing:0.12em;'
            f'text-transform:uppercase;color:{color};">{sev}</span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;'
            f'color:var(--text-faint);">{_esc(f.get("code", ""))}</span>'
            f'{story_chip}</div>'
            f'<div style="font-size:0.85rem;color:var(--text);margin-top:0.2rem;">'
            f'{_esc(f.get("message", ""))}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_findings_tab(result: dict, kind: str) -> None:
    items = result.get(kind, []) or []
    if not items:
        kind_label = {"gaps": "gaps", "conflicts": "conflicts", "duplicates": "duplicates"}[kind]
        st.info(f"No {kind_label} detected for this run.")
        return
    css = {"gaps": "finding-gap", "conflicts": "finding-conflict", "duplicates": "finding-dup"}[kind]
    kind_label = {"gaps": "GAP", "conflicts": "CONFLICT", "duplicates": "DUPLICATE"}[kind]
    for item in items:
        parts = [f'<div class="finding-card {css}">']
        parts.append('<div class="finding-head">')
        parts.append(f'<span class="finding-kind">{kind_label}</span>')
        if kind == "gaps":
            parts.append(f'<span class="finding-title">{_esc(item.get("title") or item.get("description") or "")}</span>')
        elif kind == "conflicts":
            parts.append(
                f'<span class="finding-title">'
                f'{_esc(item.get("story_id") or "")} ↔ {_esc(item.get("with") or "constraint")}'
                f' · severity: {_esc(item.get("severity") or "unknown")}</span>'
            )
        else:  # duplicates
            parts.append(
                f'<span class="finding-title">'
                f'{_esc(item.get("story_id") or "")} ↔ existing {_esc(item.get("existing_id") or "?")}'
                f' · {_esc(item.get("confidence") or "")} confidence</span>'
            )
        parts.append("</div>")

        body = item.get("description") or item.get("reason") or ""
        if body:
            parts.append(f'<div class="finding-body">{_esc(body)}</div>')
        if item.get("evidence"):
            parts.append(f'<div class="finding-evidence">↳ {_esc(item["evidence"])}</div>')
        parts.append("</div>")
        st.markdown("".join(parts), unsafe_allow_html=True)


# ----- word-diff for duplicate compare modal --------------------------

def _tokenize_for_diff(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"\w+|\s+|[^\w\s]", text)


def _word_diff_html(new_text: str, old_text: str) -> tuple[str, str]:
    """Return (new_html, old_html) with word-level highlight markup.

    Added (only-in-new) words: `.dup-diff-add` (green badge).
    Removed (only-in-existing) words: `.dup-diff-del` (amber strikethrough).
    Equal regions are rendered as plain escaped text.
    """
    new_tokens = _tokenize_for_diff(new_text)
    old_tokens = _tokenize_for_diff(old_text)
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    new_parts, old_parts = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_chunk = "".join(old_tokens[i1:i2])
        new_chunk = "".join(new_tokens[j1:j2])
        if tag == "equal":
            new_parts.append(_esc(new_chunk))
            old_parts.append(_esc(old_chunk))
        elif tag == "delete":
            old_parts.append(f'<span class="dup-diff-del">{_esc(old_chunk)}</span>')
        elif tag == "insert":
            new_parts.append(f'<span class="dup-diff-add">{_esc(new_chunk)}</span>')
        elif tag == "replace":
            new_parts.append(f'<span class="dup-diff-add">{_esc(new_chunk)}</span>')
            old_parts.append(f'<span class="dup-diff-del">{_esc(old_chunk)}</span>')
    return "".join(new_parts), "".join(old_parts)


@st.dialog("Duplicate comparison", width="large")
def show_duplicate_compare_dialog(focus_index: int = 0) -> None:
    """Open a modal that walks through every duplicate as side-by-side cards.

    `focus_index` is which duplicate to highlight first; the modal renders
    all of them in order, so it's mostly a scroll-anchor hint right now.
    """
    result = st.session_state.get("result") or {}
    dupes = result.get("duplicates", []) or []
    backlog = st.session_state.get("existing_tickets_cache", []) or []

    # Build lookup maps for both the new stories and the existing tickets.
    stories_by_id: dict[str, dict] = {}
    for ep in result.get("epics", []) or []:
        for s in ep.get("stories", []) or []:
            sid = s.get("id")
            if sid:
                stories_by_id[sid] = s

    backlog_by_id: dict[str, dict] = {}
    for item in backlog:
        if not isinstance(item, dict):
            continue
        for key in ("id", "key", "number"):
            v = item.get(key)
            if v is not None:
                backlog_by_id[str(v)] = item

    if not dupes:
        st.markdown('<div class="dup-side-missing">No duplicates flagged.</div>',
                    unsafe_allow_html=True)
        return

    st.markdown(
        f'<div style="font-size: 0.84rem; color: var(--text-muted); margin-bottom: 1rem;">'
        f'{len(dupes)} duplicate{"s" if len(dupes) != 1 else ""} flagged. '
        f'Review each pair below — added words are highlighted green; removed amber.</div>',
        unsafe_allow_html=True,
    )

    for d in dupes:
        sid = str(d.get("story_id", ""))
        existing_id = str(d.get("existing_id", ""))
        confidence = d.get("confidence", "")
        reason = d.get("reason", "")

        new_story = stories_by_id.get(sid, {})
        existing = backlog_by_id.get(existing_id, {})

        new_title = new_story.get("title", "(unknown story)")
        new_desc = (new_story.get("description") or "").strip()
        old_title = (
            existing.get("title")
            or existing.get("summary")
            or "(not found in backlog)"
        )
        old_desc = (existing.get("description") or existing.get("body") or "").strip()

        if new_desc or old_desc:
            new_desc_html, old_desc_html = _word_diff_html(new_desc, old_desc)
            new_desc_block = f'<div class="dup-side-desc">{new_desc_html}</div>'
            old_desc_block = f'<div class="dup-side-desc">{old_desc_html}</div>'
        else:
            new_desc_block = '<div class="dup-side-missing">No description.</div>'
            old_desc_block = '<div class="dup-side-missing">No description in backlog.</div>'

        new_title_html, old_title_html = _word_diff_html(new_title, old_title)

        st.markdown(
            f"""
            <div class="dup-diff-legend">
                <span class="dup-diff-legend-item"><span class="dup-diff-add">added</span> only in the new story</span>
                <span class="dup-diff-legend-item"><span class="dup-diff-del">removed</span> only in the existing ticket</span>
            </div>
            <div class="dup-pair">
                <div class="dup-side new">
                    <div class="dup-side-label">New · {_esc(sid)}</div>
                    <div class="dup-side-title">{new_title_html}</div>
                    {new_desc_block}
                </div>
                <div class="dup-vs">vs</div>
                <div class="dup-side existing">
                    <div class="dup-side-label">Existing · {_esc(existing_id)}</div>
                    <div class="dup-side-title">{old_title_html}</div>
                    {old_desc_block}
                </div>
            </div>
            <div class="dup-reason">
                <span class="conf-tag">{_esc(confidence)} confidence</span>{_esc(reason)}
            </div>
            """,
            unsafe_allow_html=True,
        )


# ------------------------------------------------------- run history I/O

RUNS_DIR = ROOT / "logs" / "runs"


def _save_run_to_disk(summary: dict[str, Any]) -> Path:
    """Write `summary` as JSON to `logs/runs/<timestamp>_<id>.json`.

    Returns the path. Failures are swallowed and logged via st.warning so a
    history-write failure can't break the run flow.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    short_id = uuid.uuid4().hex[:6]
    stamp = summary.get("timestamp") or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RUNS_DIR / f"{stamp}_{short_id}.json"
    try:
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as e:
        st.warning(f"Could not save run history: {e}")
    return path


def _load_run_history() -> list[dict[str, Any]]:
    """Read all `logs/runs/*.json` files. Sorted newest-first."""
    if not RUNS_DIR.exists():
        return []
    entries: list[dict[str, Any]] = []
    for p in RUNS_DIR.glob("*.json"):
        try:
            entries.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries


@st.dialog("Run history", width="large")
def show_run_history_dialog() -> None:
    """Modal: list past runs from logs/runs/*.json with a "Load" button each.

    Polish over the v1 dialog: free-text search, date-bucket grouping
    (Today / Yesterday / This week / Older), per-row delete, and a small
    aggregate strip showing total runs + total spend across history.
    """
    history = _load_run_history()
    if not history:
        st.markdown(
            '<div style="padding: 1.4rem; text-align: center; color: var(--text-muted);">'
            'No persisted runs yet. After your next synthesis completes, this '
            'list will populate from <code>logs/runs/</code>.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ---- Aggregate strip (total runs + cumulative cost) ----
    total_cost = 0.0
    total_stories = 0
    for h in history:
        try:
            total_cost += float(h.get("cost_usd") or 0)
        except (TypeError, ValueError):
            pass
        total_stories += int(h.get("story_count") or h.get("n_stories") or 0)

    st.markdown(
        '<div style="display:flex;gap:0.6rem;margin-bottom:0.85rem;">'
        f'<div class="rh-summary-chip"><span>Runs</span>{len(history)}</div>'
        f'<div class="rh-summary-chip"><span>Stories drafted</span>{total_stories}</div>'
        f'<div class="rh-summary-chip"><span>Total est. cost</span>${total_cost:.4f}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ---- Search / filter ----
    query = st.text_input(
        "Filter",
        placeholder="Search by source name, model, or timestamp…",
        key="rh_search",
        label_visibility="collapsed",
    ).strip().lower()

    if query:
        filtered = [
            h for h in history
            if query in (h.get("source_label") or "").lower()
            or query in (h.get("model") or "").lower()
            or query in (h.get("timestamp") or "").lower()
        ]
    else:
        filtered = history

    if not filtered:
        st.caption(f"No matches for '{query}'. Clear the filter to see all runs.")
        return

    # ---- Bucket by recency ----
    now = datetime.now()
    buckets: dict[str, list[dict]] = {"Today": [], "Yesterday": [], "This week": [], "Older": []}
    for entry in filtered:
        stamp = entry.get("timestamp", "")
        try:
            dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        except (ValueError, TypeError):
            buckets["Older"].append(entry)
            continue
        delta_days = (now.date() - dt.date()).days
        if delta_days == 0:
            buckets["Today"].append(entry)
        elif delta_days == 1:
            buckets["Yesterday"].append(entry)
        elif delta_days <= 7:
            buckets["This week"].append(entry)
        else:
            buckets["Older"].append(entry)

    current_run_id = (st.session_state.get("run_dir") or "").name \
        if hasattr(st.session_state.get("run_dir") or "", "name") else ""

    for bucket_name, entries in buckets.items():
        if not entries:
            continue
        st.markdown(
            f'<div style="font-size:0.66rem;font-weight:700;letter-spacing:0.16em;'
            f'text-transform:uppercase;color:var(--text-faint);'
            f'margin:1rem 0 0.55rem;">{bucket_name} · {len(entries)}</div>',
            unsafe_allow_html=True,
        )
        for entry in entries:
            _render_history_row(entry, current_run_id)


def _render_history_row(entry: dict, current_run_id: str) -> None:
    """One run card inside the history dialog. Factored out so the buckets
    above can iterate without nesting columns inside columns."""
    stamp = entry.get("timestamp", "—")
    try:
        dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        date_label = dt.strftime("%b %d, %Y · %H:%M:%S")
    except (ValueError, TypeError):
        date_label = stamp

    run_id = entry.get("run_id", stamp)
    is_current = bool(current_run_id) and current_run_id == run_id

    cols = st.columns([5, 1, 1])
    with cols[0]:
        chips = []
        for label, val in (
            ("epics", entry.get("epic_count") or entry.get("n_epics") or 0),
            ("stories", entry.get("story_count") or entry.get("n_stories") or 0),
            ("dups", entry.get("dup_count") or entry.get("n_dups") or 0),
            ("elapsed", f"{float(entry.get('elapsed_seconds', 0) or 0):.1f}s"),
            ("model", entry.get("model") or "—"),
        ):
            chips.append(f'<span class="rh-chip">{_esc(label)}={_esc(val)}</span>')
        cost = entry.get("cost_usd")
        if cost is not None:
            try:
                chips.append(f'<span class="rh-chip rh-chip-accent">${float(cost):.4f}</span>')
            except (TypeError, ValueError):
                pass
        current_badge = (
            '<span class="rh-chip rh-chip-current">⌖ current</span>'
            if is_current else ""
        )
        card_cls = "rh-card rh-card-current" if is_current else "rh-card"
        st.markdown(
            f'<div class="{card_cls}">'
            f'<div class="rh-card-top">'
            f'<div><div class="rh-card-date">{_esc(date_label)}{current_badge}</div>'
            f'<div class="rh-card-source">{_esc(entry.get("source_label", "—"))}</div></div>'
            f'</div>'
            f'<div class="rh-card-meta">{"".join(chips)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        if st.button("Load", key=f"history_load_{run_id}", use_container_width=True,
                     disabled=is_current,
                     help="Already loaded" if is_current else "Re-open this run"):
            _load_history_into_state(entry)
            st.rerun()
    with cols[2]:
        if st.button("✕", key=f"history_delete_{run_id}", use_container_width=True,
                     help="Delete this run's metadata file (output files are kept)."):
            _delete_history_entry(entry)
            st.rerun()


def _delete_history_entry(entry: dict) -> None:
    """Remove a single run's metadata JSON from logs/runs/.

    We delete only the metadata file — the corresponding `outputs/<stamp>/`
    directory is kept so the synthesis artefacts aren't lost on a stray
    click. If the user wants a clean wipe, that's a shell rm.
    """
    run_id = entry.get("run_id") or entry.get("timestamp", "")
    if not run_id:
        return
    runs_dir = ROOT / "logs" / "runs"
    if not runs_dir.exists():
        return
    # Filename convention is "<timestamp>_<short-id>.json"; matching by
    # run_id (which includes the timestamp prefix) catches both formats.
    deleted = 0
    for p in runs_dir.glob(f"{run_id}*.json"):
        try:
            p.unlink()
            deleted += 1
        except OSError:
            pass
    if deleted:
        st.toast(f"Deleted run metadata · {deleted} file(s)", icon="✕")
    else:
        st.toast(f"No metadata file found for run {run_id}", icon="⚠")


def _load_history_into_state(entry: dict[str, Any]) -> None:
    """Restore a saved run's outputs into session_state for re-display."""
    outputs = entry.get("outputs", {}) or {}
    synth_path = outputs.get("synthesis_json")
    if synth_path:
        p = Path(synth_path)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                st.error(f"Could not load run outputs: {e}")
                return
            # The historical synthesis.json doesn't carry audit_trail —
            # try to read the sibling file.
            audit_md = p.parent / "audit_trail.md"
            if audit_md.exists():
                data["audit_trail"] = audit_md.read_text(encoding="utf-8")
            data.setdefault("token_usage", entry.get("token_usage") or {})
            data.setdefault("model", entry.get("model") or "")
            st.session_state.result = data
            st.session_state.run_dir = p.parent
            st.session_state.elapsed = entry.get("elapsed_seconds") or 0
            st.session_state.source_label = entry.get("source_label") or ""
            st.session_state.stage_states = ["done"] * len(_STAGES)
            st.session_state.tokens_total = (
                (entry.get("token_usage") or {}).get("total", {}).get("input", 0)
                + (entry.get("token_usage") or {}).get("total", {}).get("output", 0)
            )
            st.session_state.cost_usd = entry.get("cost_usd") or 0
            st.session_state.model_used = entry.get("model") or ""
            st.session_state.epics_original = json.loads(json.dumps(data.get("epics") or []))
            # Reset transient UI state so a loaded historical run renders
            # cleanly: drop any stale dry-run preview and edit-mode flag.
            st.session_state.dry_run_result = None
            st.session_state.stories_edit_mode = False


# -------------------------------------------------------- session state

if "result" not in st.session_state:
    st.session_state.result = None
if "run_dir" not in st.session_state:
    st.session_state.run_dir = None
if "elapsed" not in st.session_state:
    st.session_state.elapsed = None
if "source_label" not in st.session_state:
    st.session_state.source_label = ""
if "stage_states" not in st.session_state:
    st.session_state.stage_states = None
if "model_used" not in st.session_state:
    st.session_state.model_used = ""
if "tokens_total" not in st.session_state:
    st.session_state.tokens_total = 0
if "cost_usd" not in st.session_state:
    st.session_state.cost_usd = 0.0
if "token_usage" not in st.session_state:
    st.session_state.token_usage = {}
if "stories_edit_mode" not in st.session_state:
    st.session_state.stories_edit_mode = False
if "epics_original" not in st.session_state:
    # Pristine copy of the LLM-produced epics; used by Reset-to-original.
    st.session_state.epics_original = None
if "existing_tickets_cache" not in st.session_state:
    # Stored so the duplicate compare dialog can look up backlog rows.
    st.session_state.existing_tickets_cache = []
if "dry_run_result" not in st.session_state:
    # Holds the dry-run prompts + source preview for rendering on the canvas.
    st.session_state.dry_run_result = None


# -------------------------------------------------------- model presets
# Per-stage model selection. The orchestrator accepts `models=dict[str,str]`
# keyed by stage name; the sidebar lets the user pick a preset (Free /
# Balanced / Premium) or override each stage individually.
#
# Preset definitions are deliberately small and explicit — the spec lists
# these exact mappings. "Balanced" is the default new-session value.
MODEL_PRESETS: dict[str, dict[str, str]] = {
    "free": {
        "parser":          "gemini-2.5-flash",
        "constraint":      "gemini-2.5-flash",
        "story_writer":    "gemini-2.5-flash",
        "epic_decomposer": "gemini-2.5-flash",
        "gap_detector":    "gemini-2.5-flash",
    },
    "balanced": {
        "parser":          "gemini-2.5-flash",
        "constraint":      "gemini-2.5-flash",
        "story_writer":    "claude-sonnet-4-5",
        "epic_decomposer": "gemini-2.5-flash",
        "gap_detector":    "gemini-2.5-flash",
    },
    "premium": {
        "parser":          "claude-sonnet-4-5",
        "constraint":      "claude-sonnet-4-5",
        "story_writer":    "claude-sonnet-4-5",
        "epic_decomposer": "claude-sonnet-4-5",
        "gap_detector":    "claude-sonnet-4-5",
    },
}
# Cost-per-run band shown below the preset chips. Rough heuristics from
# the spec — the real number lives in the post-run cost panel.
PRESET_COST_BAND = {
    "free":     "Free tier · ~$0",
    "balanced": "~$0.005 per run",
    "premium":  "~$0.03 per run",
    "custom":   "custom mix",
}
# Models available in the advanced per-stage selectbox row.
MODEL_OPTIONS = [
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]
STAGE_KEYS = ("parser", "constraint", "story_writer", "epic_decomposer", "gap_detector")

# -------- Persisted UI state ---------------------------------------
# Streamlit doesn't keep selectbox / preset state across a hard browser
# reload (new session). We mirror a small subset of the state to a JSON
# file so reopening the tab restores the user's last picks.
UI_STATE_FILE = ROOT / "logs" / ".ui_state.json"


def _load_ui_state() -> dict:
    if UI_STATE_FILE.exists():
        try:
            return json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_ui_state(state: dict) -> None:
    try:
        UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        UI_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # non-critical


_persisted_ui = _load_ui_state()


def _default_index(saved_key: str, options_dict: dict) -> int:
    saved = _persisted_ui.get(saved_key)
    keys = list(options_dict.keys())
    if saved in keys:
        return keys.index(saved)
    return 0


if "models" not in st.session_state:
    # Default = Balanced (or persisted preset). Per-key overrides go into
    # this dict from the advanced expander; preset buttons replace it.
    _saved_preset = _persisted_ui.get("active_preset", "balanced")
    if _saved_preset not in MODEL_PRESETS:
        _saved_preset = "balanced"
    st.session_state.models = dict(MODEL_PRESETS[_saved_preset])
    # If the saved preset was "custom", restore the saved per-stage map
    saved_custom = _persisted_ui.get("models") or {}
    if _saved_preset == "custom" and isinstance(saved_custom, dict):
        for k, v in saved_custom.items():
            if k in STAGE_KEYS and v in MODEL_OPTIONS:
                st.session_state.models[k] = v
if "active_preset" not in st.session_state:
    st.session_state.active_preset = _persisted_ui.get("active_preset", "balanced")
    if st.session_state.active_preset not in (*MODEL_PRESETS.keys(), "custom"):
        st.session_state.active_preset = "balanced"


# -------------------------------------------------------- sidebar

SAMPLES_DIR = ROOT / "samples"
GOLDEN_TRANSCRIPTS_DIR = ROOT / "evaluation" / "golden_dataset" / "transcripts"

TRANSCRIPT_OPTIONS = {
    "Meeting notes — NorthStar Q3 planning":
        SAMPLES_DIR / "meeting_notes.txt",
    "Strategy doc — NorthStar Q3":
        SAMPLES_DIR / "product_strategy.md",
    "Pharmacy refill escalation (eval case 02)":
        GOLDEN_TRANSCRIPTS_DIR / "case_02_pharmacy_escalation.txt",
    "Mobile team Slack standup (eval case 03)":
        GOLDEN_TRANSCRIPTS_DIR / "case_03_mobile_standup.txt",
    "Customer support note (eval case 04 — negative)":
        GOLDEN_TRANSCRIPTS_DIR / "case_04_support_note.txt",
    "Upload my own…": "__upload__",
}
CONSTRAINTS_OPTIONS = {
    "Architecture constraints (recommended)":
        SAMPLES_DIR / "architecture_constraints.md",
    "Product strategy doc":
        SAMPLES_DIR / "product_strategy.md",
    "(none — skip the Constraint Extractor)": "",
    "Upload my own…": "__upload__",
}
BACKLOG_OPTIONS = {
    "JIRA backlog — 30 tickets (recommended)":
        SAMPLES_DIR / "jira_backlog.json",
    "GitHub issues — 6 tickets":
        SAMPLES_DIR / "github_issues.json",
    "(none — skip duplicate detection)": "",
    "Upload my own…": "__upload__",
}

with st.sidebar:
    st.markdown(
        '<div class="app-header">'
        '<span class="app-mark">◆</span>'
        '<div><div class="app-title">Backlog Synthesizer</div>'
        '<div class="app-tagline">Multi-agent · five specialists · audited</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown("### Transcript")
    transcript_choice = st.selectbox(
        "Source",
        options=list(TRANSCRIPT_OPTIONS.keys()),
        index=_default_index("transcript_choice", TRANSCRIPT_OPTIONS),
        label_visibility="collapsed",
        key="transcript_choice",
        help="Meeting notes, strategy doc, or transcript — the document the Parser Agent reads.",
    )
    transcript_upload = None
    if TRANSCRIPT_OPTIONS[transcript_choice] == "__upload__":
        transcript_upload = st.file_uploader(
            "Upload transcript", type=["txt", "md", "pdf"],
            key="transcript_upload", label_visibility="collapsed",
        )

    # Vision input — separate uploader so it doesn't compete with the
    # text-transcript picker. Vision-capable models (Claude Sonnet/Opus/
    # Haiku 4.x) accept the images alongside whatever text source is
    # active. Whiteboard photos and screenshots flow through here.
    with st.expander("Add whiteboard photos / screenshots", expanded=False):
        vision_uploads = st.file_uploader(
            "Vision attachments (PNG / JPG / WEBP)",
            type=["png", "jpg", "jpeg", "webp", "gif"],
            accept_multiple_files=True,
            key="vision_uploads",
            help=(
                "Vision-capable models only. Each image is sent as a "
                "first-class source material block to the Parser agent "
                "alongside any text transcript."
            ),
        )

    st.markdown("### Architecture / wiki")
    constraints_choice = st.selectbox(
        "Constraints source",
        options=list(CONSTRAINTS_OPTIONS.keys()),
        index=_default_index("constraints_choice", CONSTRAINTS_OPTIONS),
        label_visibility="collapsed",
        key="constraints_choice",
        help="Architecture constraints — performance budgets, required integrations, compliance rules.",
    )
    constraints_upload = None
    if CONSTRAINTS_OPTIONS[constraints_choice] == "__upload__":
        constraints_upload = st.file_uploader(
            "Upload wiki / constraints", type=["md", "txt"],
            key="constraints_upload", label_visibility="collapsed",
        )

    st.markdown("### Existing backlog")
    backlog_choice = st.selectbox(
        "Backlog source",
        options=list(BACKLOG_OPTIONS.keys()),
        index=_default_index("backlog_choice", BACKLOG_OPTIONS),
        label_visibility="collapsed",
        key="backlog_choice",
        help="JIRA / GitHub ticket export. Enables duplicate detection.",
    )
    backlog_upload = None
    if BACKLOG_OPTIONS[backlog_choice] == "__upload__":
        backlog_upload = st.file_uploader(
            "Upload backlog JSON", type=["json"],
            key="backlog_upload", label_visibility="collapsed",
        )

    # ---------------- Live Atlassian sources ----------------
    # When toggled on, the orchestrator pulls constraint_text from the
    # given Confluence page (instead of the file selected above) and
    # existing_tickets from the configured Jira project (instead of the
    # backlog JSON selected above). Either or both can be enabled
    # independently. Credentials come from .env — see README.
    st.markdown("### Live Atlassian sources")
    with st.expander("Use live Confluence / Jira", expanded=False):
        use_live_confluence = st.toggle(
            "Pull constraints from live Confluence",
            value=False,
            help=(
                "Fetches a Confluence page by ID and uses it as the wiki / "
                "architecture-constraints source for this run. Overrides the "
                "Architecture/wiki selector above when on."
            ),
            key="use_live_confluence",
        )
        live_confluence_page_id = ""
        if use_live_confluence:
            live_confluence_page_id = st.text_input(
                "Confluence page ID",
                value=os.environ.get("CONFLUENCE_PAGE_ID", ""),
                placeholder="e.g. 65830",
                key="live_confluence_page_id",
                help=(
                    "Numeric page id from the Confluence URL "
                    "(e.g. .../wiki/spaces/SD/pages/<ID>/...)."
                ),
            )

        use_live_jira = st.toggle(
            "Pull backlog from live Jira",
            value=False,
            help=(
                f"Fetches issues from project "
                f"`{os.environ.get('JIRA_PROJECT_KEY') or '?'}` "
                "in your configured Jira tenant. Overrides the Existing-"
                "backlog selector above when on."
            ),
            key="use_live_jira",
        )

    st.markdown("### Privacy")
    redact_pii = st.toggle(
        "Mask personal & sensitive info",
        value=False,
        help=(
            "Replace emails, phones, SSNs, card numbers, and (conservatively-"
            "matched) personal names with stable placeholders before sending "
            "to Claude. The final synthesis is un-redacted; the audit log "
            "stays redacted for compliance review."
        ),
    )

    st.markdown("### Options")
    dry_run = st.toggle(
        "Dry run (preview prompts only)",
        value=False,
        help=(
            "Build the prompts each agent would send, but skip every LLM call. "
            "The main canvas shows a source preview on the left and the "
            "constructed prompts on the right — useful for prompt inspection "
            "without spending API credit."
        ),
    )

    # ---------------- MODELS ----------------
    # Preset radio (Free / Balanced / Premium) + Advanced per-stage override
    # expander. Using st.radio instead of st.columns(3) of buttons because
    # the sidebar is too narrow — button labels wrap to "Pre / miu / m".
    # The orchestrator receives `models=session_state.models` at run time
    # regardless of how the user got there.
    st.markdown("### Models")

    _preset_labels = ["Free", "Balanced", "Premium"]
    _label_to_key = {"Free": "free", "Balanced": "balanced", "Premium": "premium"}
    _key_to_label = {v: k for k, v in _label_to_key.items()}

    # If the active preset is "custom", default the radio to whichever
    # preset most closely matches (or just Balanced) but the caption below
    # will say "Custom" to keep the user oriented.
    _active = st.session_state.active_preset
    _radio_index = _preset_labels.index(_key_to_label[_active]) if _active in _key_to_label else 1

    _picked_label = st.radio(
        "Model preset",
        options=_preset_labels,
        index=_radio_index,
        horizontal=True,
        label_visibility="collapsed",
        key="preset_radio",
        help=(
            "Free: all Gemini Flash · free tier.  "
            "Balanced: Gemini Flash + Claude Sonnet for the Story Writer.  "
            "Premium: all Claude Sonnet 4.5."
        ),
    )
    _picked_key = _label_to_key[_picked_label]

    # Apply the picked preset whenever it doesn't match active state. We
    # skip when active is "custom" AND the radio defaulted — otherwise
    # the radio would force a reset of the user's custom overrides.
    if _picked_key != _active and _active != "custom":
        st.session_state.models = dict(MODEL_PRESETS[_picked_key])
        st.session_state.active_preset = _picked_key
        st.rerun()
    elif _active == "custom" and _picked_key != _key_to_label.get(_active):
        # User clicked a preset while in custom mode — replace overrides.
        st.session_state.models = dict(MODEL_PRESETS[_picked_key])
        st.session_state.active_preset = _picked_key
        st.rerun()

    # (The static "Active: <preset> · ~$X per run" caption was removed:
    #  the figures it carried were hardcoded constants that didn't match
    #  reality — Balanced shipped a $0.005/run label while the real cost
    #  on the bundled sample is closer to $0.10. The pre-run cost
    #  estimate strip below now carries the real number based on the
    #  actual inputs that are loaded.)

    with st.expander("Advanced — per-stage override", expanded=False):
        # Each selectbox writes back to session_state.models[<stage>] and
        # flips active_preset → "custom" so the chip row reflects that the
        # mix is no longer a vanilla preset.
        _stage_labels = {
            "parser":          "Parser",
            "constraint":      "Constraint Extractor",
            "story_writer":    "Story Writer",
            "epic_decomposer": "Epic Decomposer",
            "gap_detector":    "Gap Detector",
        }
        for _stage in STAGE_KEYS:
            _cur = st.session_state.models.get(_stage, MODEL_PRESETS["balanced"][_stage])
            try:
                _idx = MODEL_OPTIONS.index(_cur)
            except ValueError:
                _idx = 0
            _picked = st.selectbox(
                _stage_labels[_stage],
                options=MODEL_OPTIONS,
                index=_idx,
                key=f"model_pick_{_stage}",
            )
            if _picked != _cur:
                st.session_state.models[_stage] = _picked
                # Any deviation from a preset → "custom".
                _matches = next(
                    (
                        name for name, mp in MODEL_PRESETS.items()
                        if mp == st.session_state.models
                    ),
                    None,
                )
                st.session_state.active_preset = _matches or "custom"
        st.caption(
            "Mix and match per stage. Claude needs `ANTHROPIC_API_KEY`; "
            "Gemini needs `GOOGLE_API_KEY` (free at aistudio.google.com)."
        )

    # ---------------- Compare mode ----------------
    # Run the pipeline twice — once with the primary preset above, once
    # with a secondary preset chosen here — and show a side-by-side
    # summary in the results. Useful for evaluating whether the cheaper
    # Free preset is producing similar output to the Premium preset.
    st.markdown("### Compare providers")
    compare_enabled = st.toggle(
        "Run a second pass with a different preset",
        value=False,
        key="compare_enabled",
        help=(
            "Runs the pipeline twice (sequentially). Doubles wall time and "
            "API spend; surfaces a side-by-side metrics summary so you can "
            "see which preset produced more / better output."
        ),
    )
    compare_with_preset = "free"
    if compare_enabled:
        compare_with_preset = st.selectbox(
            "Compare against preset",
            options=list(MODEL_PRESETS.keys()),
            index=list(MODEL_PRESETS.keys()).index("free"),
            key="compare_with_preset",
        )

    _transcript_ready = (
        TRANSCRIPT_OPTIONS[transcript_choice] != "__upload__"
        or transcript_upload is not None
    )

    # ---------------- Pre-run cost estimate ----------------
    # Quick heuristic so the user knows roughly what a Synthesize click
    # will spend BEFORE they spend it. Input-token estimate comes from
    # the size of whatever the sidebar currently has selected; output is
    # a fixed budget per stage tuned from prior runs. Cheap to compute —
    # only file size + a price-table lookup per stage.
    _pre_cost_usd, _pre_in_tokens, _pre_out_tokens = _estimate_pre_run_cost(
        transcript_choice=transcript_choice,
        transcript_upload=transcript_upload,
        constraints_choice=constraints_choice,
        constraints_upload=constraints_upload,
        backlog_choice=backlog_choice,
        backlog_upload=backlog_upload,
        models=st.session_state.models,
    )
    if _transcript_ready and (_pre_in_tokens > 0 or _pre_out_tokens > 0):
        cost_line = (
            f"≈ <strong style='color:var(--accent)'>${_pre_cost_usd:.4f}</strong> "
            f"<span style='color:var(--text-faint)'>·</span> "
            f"<span style='color:var(--text-muted)'>"
            f"{_pre_in_tokens // 1000}k in, ~{_pre_out_tokens // 1000}k out"
            f"</span>"
        )
        st.markdown(
            "<div style='padding:0.55rem 0.8rem;background:var(--bg-elev-1);"
            "border:1px solid var(--border);border-radius:8px;font-size:0.8rem;"
            "margin-bottom:0.55rem;'>"
            "<span style='font-size:0.62rem;font-weight:700;letter-spacing:0.12em;"
            "text-transform:uppercase;color:var(--text-faint);display:block;"
            "margin-bottom:0.25rem;'>Estimated run cost</span>"
            f"{cost_line}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("### Action")
    run_clicked = st.button(
        "▶  Synthesize",
        type="primary",
        use_container_width=True,
        disabled=not _transcript_ready,
    )
    if not _transcript_ready:
        st.caption("↑ Pick a transcript source first.")

    # Run history — always show the button. Even when empty it explains where
    # past runs come from.
    st.markdown("### Run history")
    history_btn = st.button(
        "⌕  View past runs",
        use_container_width=True,
        key="open_history_btn",
        help="Past runs persisted to logs/runs/*.json.",
    )

# Persist user's selections so a hard browser reload preserves them.
_save_ui_state({
    "transcript_choice":   transcript_choice,
    "constraints_choice":  constraints_choice,
    "backlog_choice":      backlog_choice,
    "active_preset":       st.session_state.get("active_preset", "balanced"),
    "models":              dict(st.session_state.get("models") or {}),
})


# -------------------------------------------------------- main canvas

st.markdown(
    '<div class="app-header">'
    '<span class="app-mark">◆</span>'
    '<div><div class="app-title">Synthesize epics, stories and tasks</div>'
    '<div class="app-tagline">'
    "From a transcript + a wiki + an existing backlog — in one multi-agent pass."
    "</div></div></div>",
    unsafe_allow_html=True,
)

_pipeline_placeholder = st.empty()
_progress_placeholder = st.empty()

with _pipeline_placeholder.container():
    _render_pipeline(
        stage_states=st.session_state.get("stage_states"),
        model=st.session_state.get("model_used") or None,
        token_usage=st.session_state.get("token_usage") or None,
        models_per_stage=(
            (st.session_state.get("result") or {}).get("models")
            or st.session_state.get("models")
        ),
    )


# -------------------------------------------------------- run handler

def _read_uploaded_text(uploaded) -> str:
    if uploaded is None:
        return ""
    name = uploaded.name
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        tmp = ROOT / "logs" / f"_upload_{int(time.time())}_{name}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(uploaded.getvalue())
        try:
            return load_text(str(tmp))
        finally:
            tmp.unlink(missing_ok=True)
    return uploaded.getvalue().decode("utf-8", errors="replace")


def _read_uploaded_tickets(uploaded) -> list[dict]:
    if uploaded is None:
        return []
    raw = uploaded.getvalue().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InputError(f"Backlog JSON parse error: {e}") from e
    if not isinstance(data, list):
        raise InputError("Backlog JSON must be a list of ticket objects.")
    return data


if history_btn:
    show_run_history_dialog()


# The sidebar Synthesize button and the home-screen ANALYZE button both
# trigger the same pipeline. The latter sets `_pending_run` and reruns
# (because the button isn't bound at script-init time), so we consume
# the flag here and clear it before invoking the pipeline.
_main_canvas_run = bool(st.session_state.pop("_pending_run", False))

if run_clicked or _main_canvas_run:
    # ---- Resolve inputs ----
    try:
        t_choice_val = TRANSCRIPT_OPTIONS[transcript_choice]
        if t_choice_val == "__upload__":
            transcript_text = _read_uploaded_text(transcript_upload)
            source_label = transcript_upload.name if transcript_upload else "(uploaded)"
        else:
            transcript_text = load_text(str(t_choice_val))
            source_label = Path(t_choice_val).name

        c_choice_val = CONSTRAINTS_OPTIONS[constraints_choice]
        if c_choice_val == "__upload__":
            constraint_text = _read_uploaded_text(constraints_upload)
        elif c_choice_val == "":
            constraint_text = ""
        else:
            constraint_text = load_text(str(c_choice_val))

        b_choice_val = BACKLOG_OPTIONS[backlog_choice]
        if b_choice_val == "__upload__":
            existing_tickets = _read_uploaded_tickets(backlog_upload)
        elif b_choice_val == "":
            existing_tickets = []
        else:
            existing_tickets = load_tickets(str(b_choice_val))
    except InputError as e:
        st.error(f"Could not load inputs: {e}")
        st.stop()

    # ---- Dry-run branch ----
    if dry_run:
        try:
            orch = Orchestrator()
        except Exception as e:
            st.error(f"Orchestrator init failed: {e}")
            st.stop()
        dry_result = orch.run(
            transcript_text=transcript_text,
            constraint_text=constraint_text,
            existing_tickets=existing_tickets,
            redact_pii=redact_pii,
            dry_run=True,
            models=st.session_state.models,
        )
        st.session_state.dry_run_result = {
            **dry_result,
            "source_label": source_label,
        }
        # Clear regular result so the dry-run view takes over.
        st.session_state.result = None
        st.session_state.stage_states = ["skipped"] * len(_STAGES)
        st.rerun()

    # ---- Live-run branch ----
    # Clear any prior dry-run preview and stale per-stage metadata so the
    # in-flight UI doesn't render alongside the new live run.
    st.session_state.dry_run_result = None
    st.session_state.model_used = ""
    st.session_state.token_usage = {}

    t0 = time.perf_counter()
    stage_states = ["idle"] * len(_STAGES)
    with _pipeline_placeholder.container():
        _render_pipeline(stage_states=stage_states)
    _progress_placeholder.markdown(
        '<div class="progress-status"><strong>BOOT</strong>'
        'Initializing orchestrator…</div>',
        unsafe_allow_html=True,
    )

    try:
        orch = Orchestrator()
    except Exception as e:
        _progress_placeholder.error(f"Orchestrator init failed: {e}")
        st.stop()

    # Per-stage start timestamps so completed/failed events can report
    # how long the stage actually took. Reset on every pipeline run.
    stage_started_at: dict[int, float] = {}

    def _on_progress(stage_index: int, stage_name: str, event: str, detail: str):
        now = time.perf_counter()
        if event == "started":
            stage_states[stage_index] = "active"
            stage_started_at[stage_index] = now
        elif event == "completed":
            stage_states[stage_index] = "done"
        elif event == "failed":
            stage_states[stage_index] = "failed"
        elif event == "skipped":
            stage_states[stage_index] = "skipped"

        # Build an "elapsed" suffix once the stage finishes so reviewers
        # can see which agent dominates wall time.
        elapsed_suffix = ""
        if event in ("completed", "failed") and stage_index in stage_started_at:
            secs = now - stage_started_at[stage_index]
            elapsed_suffix = f" · {secs:.1f}s"

        st.session_state["current_stage"] = stage_index
        pretty_name = stage_name.replace("_", " ").title()
        line = (
            f'<div class="progress-status">'
            f'<strong>{pretty_name.upper()}</strong>{_esc(event)}'
            f'{(" · " + _esc(detail)) if detail else ""}{elapsed_suffix}</div>'
        )
        with _pipeline_placeholder.container():
            _render_pipeline(stage_states=stage_states)
        _progress_placeholder.markdown(line, unsafe_allow_html=True)

    # First-run model download — sentence-transformers downloads ~80MB on
    # first use. Pre-warm the embedding tool inside a spinner so the user
    # sees what's happening instead of an unexplained pause partway through
    # the Gap Detector. Subsequent runs hit the cache and this is a no-op.
    if existing_tickets and not st.session_state.get("_embeddings_warmed"):
        try:
            with st.spinner("Loading embedding model… (~80MB, one-time download)"):
                from tools.embedding_tool import EmbeddingTool  # local import
                EmbeddingTool().encode(["warmup"])
            st.session_state._embeddings_warmed = True
        except Exception as e:  # noqa: BLE001 — don't block the run on warmup failure
            # Not fatal: the GapDetectorAgent will fall back to LLM dedup.
            st.warning(f"Embedding warmup skipped: {e}")

    # When live Atlassian sources are toggled on, blank out whatever the
    # file-based selectors loaded so the orchestrator's live-fetch path
    # owns those inputs. Both toggles surface a sidebar warning + audit
    # event if the live fetch fails, so the user can tell that they're
    # not silently falling back.
    _live_conf_pid = st.session_state.get("live_confluence_page_id", "").strip() \
        if st.session_state.get("use_live_confluence") else ""
    _use_live_jira = bool(st.session_state.get("use_live_jira"))
    if _live_conf_pid:
        constraint_text = ""
    if _use_live_jira:
        existing_tickets = []

    # Build vision attachments from any sidebar image uploads. Errors
    # while reading bytes are surfaced inline; the run still proceeds
    # without the image rather than failing.
    _vision_atts = None
    _vision_files = st.session_state.get("vision_uploads") or []
    if _vision_files:
        try:
            from tools.base import VisionAttachment
            _vision_atts = []
            for f in _vision_files:
                _vision_atts.append(
                    VisionAttachment.from_bytes(
                        f.getvalue(),
                        media_type=getattr(f, "type", "image/png"),
                        label=getattr(f, "name", "upload"),
                    )
                )
        except Exception as e:  # noqa: BLE001
            st.warning(f"Skipping vision attachments: {e}")
            _vision_atts = None

    _is_compare_run = bool(st.session_state.get("compare_enabled"))
    try:
        if _is_compare_run:
            secondary_preset = st.session_state.get("compare_with_preset", "free")
            secondary_models = dict(MODEL_PRESETS.get(secondary_preset, MODEL_PRESETS["free"]))
            primary_label = (st.session_state.get("active_preset") or "primary").title()
            secondary_label = secondary_preset.title()
            compare_result = orch.run_compare(
                primary_models=st.session_state.models,
                secondary_models=secondary_models,
                primary_label=primary_label,
                secondary_label=secondary_label,
                progress_callback=_on_progress,
                transcript_text=transcript_text,
                constraint_text=constraint_text,
                existing_tickets=existing_tickets,
                redact_pii=redact_pii,
                live_confluence_page_id=_live_conf_pid or None,
                live_jira=_use_live_jira,
                vision_attachments=_vision_atts,
            )
            # Surface the primary result through the normal render path;
            # the secondary + comparison ride along in session state and
            # are rendered by a dedicated banner below.
            result = compare_result["primary"]
            result["_compare_secondary"] = compare_result["secondary"]
            result["_compare_summary"] = compare_result["comparison"]
            result["_compare_labels"] = compare_result["labels"]
        else:
            result = orch.run(
                transcript_text=transcript_text,
                constraint_text=constraint_text,
                existing_tickets=existing_tickets,
                redact_pii=redact_pii,
                progress_callback=_on_progress,
                models=st.session_state.models,
                live_confluence_page_id=_live_conf_pid or None,
                live_jira=_use_live_jira,
                vision_attachments=_vision_atts,
            )
    except Exception as e:
        _progress_placeholder.error(f"Pipeline failed: {e}")
        st.stop()

    elapsed = time.perf_counter() - t0
    n_done = sum(1 for s in stage_states if s == "done")
    n_failed = sum(1 for s in stage_states if s == "failed")
    n_skipped = sum(1 for s in stage_states if s == "skipped")
    summary_tag = (
        f'<strong>DONE</strong>{n_done}/{len(_STAGES)} agents completed'
        + (f' · {n_failed} failed' if n_failed else '')
        + (f' · {n_skipped} skipped' if n_skipped else '')
        + f' · {elapsed:.1f}s'
    )
    with _pipeline_placeholder.container():
        _render_pipeline(
            stage_states=stage_states,
            model=result.get("model"),
            token_usage=result.get("token_usage"),
            models_per_stage=result.get("models") or st.session_state.models,
        )
    _progress_placeholder.markdown(
        f'<div class="progress-status">{summary_tag}</div>',
        unsafe_allow_html=True,
    )

    # ---- Persist outputs ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "outputs" / stamp
    # `result` from the orchestrator now includes `token_usage` and `model`.
    # write_outputs reads the synthesis content fields; the extras are
    # carried through to the JSON dump as well — useful downstream.
    synth_payload = {k: v for k, v in result.items() if k != "audit_trail"}
    json_path, md_path = write_outputs(synth_payload, run_dir, source_label=source_label)
    audit_path = run_dir / "audit_trail.md"
    audit_path.write_text(result["audit_trail"], encoding="utf-8")

    # ---- Token + cost tally (from orchestrator's structured token_usage) ----
    # With per-stage model selection, we can no longer use a single model
    # rate for the whole run. Instead, walk the per-agent token usage and
    # apply the matching stage's model rate; sum to get the total.
    token_usage = result.get("token_usage") or {}
    total = token_usage.get("total") or {"input": 0, "output": 0}
    tokens_total = int(total.get("input", 0)) + int(total.get("output", 0))
    model_name = result.get("model") or ""
    models_per_stage = result.get("models") or {}
    cost_usd = _compute_total_cost(token_usage, models_per_stage)

    st.session_state.result = result
    st.session_state.run_dir = run_dir
    st.session_state.elapsed = elapsed
    st.session_state.source_label = source_label
    st.session_state.stage_states = stage_states
    st.session_state.tokens_total = tokens_total
    st.session_state.cost_usd = cost_usd
    st.session_state.token_usage = token_usage
    st.session_state.model_used = model_name
    st.session_state.existing_tickets_cache = existing_tickets
    st.session_state.epics_original = json.loads(json.dumps(result.get("epics") or []))
    st.session_state.stories_edit_mode = False
    st.session_state.dry_run_result = None

    # ---- Append to persisted run history ----
    epics = result.get("epics") or []
    n_stories = sum(len(e.get("stories") or []) for e in epics)
    history_summary = {
        "run_id": f"{stamp}_{uuid.uuid4().hex[:6]}",
        "timestamp": stamp,
        "source_label": source_label,
        "elapsed_seconds": elapsed,
        "epic_count": len(epics),
        "story_count": n_stories,
        "dup_count": len(result.get("duplicates") or []),
        "gap_count": len(result.get("gaps") or []),
        "conflict_count": len(result.get("conflicts") or []),
        "model": model_name,
        "models": models_per_stage,
        "token_usage": token_usage,
        "cost_usd": cost_usd,
        "outputs": {
            "synthesis_json": str(json_path),
            "synthesis_md": str(md_path),
            "audit_md": str(audit_path),
        },
    }
    _save_run_to_disk(history_summary)


# -------------------------------------------------------- results / empty state

dry_view = st.session_state.dry_run_result
result = st.session_state.result

if dry_view is not None and result is None:
    # ---- Dry-run two-column preview ----
    st.markdown(
        '<div class="run-meta">'
        f'<span class="run-meta-item"><span class="run-meta-icon">⊘</span>'
        f'<span class="run-meta-label">Mode</span>Dry run — no LLM calls</span>'
        f'<span class="run-meta-sep">·</span>'
        f'<span class="run-meta-item"><span class="run-meta-icon">✦</span>'
        f'<span class="run-meta-label">Source</span>{_esc(dry_view.get("source_label", "—"))}</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([1, 1])
    with col_left:
        st.markdown("#### Source preview")
        src = dry_view.get("transcript_text") or ""
        st.code(
            src[:1500] + ("\n\n... [truncated]" if len(src) > 1500 else ""),
            language="text",
        )
        constraint_src = dry_view.get("constraint_text") or ""
        if constraint_src:
            with st.expander(f"Wiki / constraint text ({len(constraint_src):,} chars)"):
                st.code(
                    constraint_src[:1500] + ("\n\n... [truncated]" if len(constraint_src) > 1500 else ""),
                    language="text",
                )
        st.caption(
            f"Existing tickets: **{dry_view.get('existing_ticket_count', 0)}** — "
            "used by the Gap Detector at run time."
        )
    with col_right:
        st.markdown("#### Constructed prompts")
        prompts = dry_view.get("dry_run_prompts") or {}
        for agent_name, prompt_text in prompts.items():
            pretty = agent_name.replace("_", " ").title()
            with st.expander(f"{pretty} prompt ({len(prompt_text):,} chars)"):
                # Showing the full prompt — these are template files, not
                # user secrets.
                st.code(prompt_text, language="markdown")
    st.info(
        "Dry run active. Toggle **Dry run** off in the sidebar, then click "
        "**Synthesize** to actually run the agents."
    )

elif result is None:
    # ---- Empty state ----
    # Home-screen explainer ported from UI-smart-backlog-assistant. Unlike
    # the previous "Selected inputs" block, this view is purely
    # instructional — it doesn't echo whichever preset is currently
    # selected in the sidebar, so the home page reads as a clean landing
    # surface rather than a dashboard. The user picks inputs in the
    # sidebar; the CTA on the main canvas mirrors the sidebar Synthesize
    # button.
    st.markdown(
        """
        <div class="empty-state">
            <div class="empty-state-eyebrow">Backlog Synthesizer · multi-agent</div>
            <div class="empty-state-title">Five specialized agents turn raw input into a structured, audited backlog</div>
            <div class="empty-state-subtitle">
                Feed in a meeting transcript, an architecture wiki page, and an existing ticket backlog. A bounded
                five-agent pipeline produces epics, stories with Given/When/Then acceptance criteria, and flags every
                gap, conflict, and duplicate it finds against the existing work. Pick a source on the left and click <strong>SYNTHESIZE</strong>.
            </div>
            <div class="empty-step-grid">
                <div class="empty-step">
                    <div class="empty-step-num">Three inputs</div>
                    <div class="empty-step-title">Transcript · wiki · backlog</div>
                    <div class="empty-step-body">Text or PDF transcript, an architecture-constraints wiki (file or <strong>live Confluence</strong> page), and a ticket export (file or <strong>live Jira</strong> project). Whiteboard photos accepted for vision-capable models.</div>
                </div>
                <div class="empty-step">
                    <div class="empty-step-num">Five agents</div>
                    <div class="empty-step-title">Parser → Constraint → Story → Epic → Gap</div>
                    <div class="empty-step-body">A bounded pipeline, not an autonomous loop. Each agent has one job and writes its output to shared memory; downstream agents read it and append. Reproducible, cost-bounded, auditable.</div>
                </div>
                <div class="empty-step">
                    <div class="empty-step-num">Auditable output</div>
                    <div class="empty-step-title">Epics + AC + gaps + conflicts + duplicates</div>
                    <div class="empty-step-body">Every story traces back to a transcript quote. Every LLM call is captured in an audit trail you can read top-to-bottom. Post-LLM guardrails catch hallucinations before the result reaches you.</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Primary action on the main canvas — large, centered, mirrors the
    # sidebar's Synthesize button. Sets `main_run_clicked` and reruns;
    # the run handler at the top of the script picks either source.
    _, _main_cta_col, _ = st.columns([1, 2, 1])
    with _main_cta_col:
        st.markdown('<div class="main-cta-wrap">', unsafe_allow_html=True)
        main_run_clicked = st.button(
            "⟶  SYNTHESIZE",
            key="main_synthesize_btn",
            use_container_width=True,
            disabled=not _transcript_ready,
        )
        if not _transcript_ready:
            st.caption("Pick a transcript source in the sidebar to enable synthesis.")
        st.markdown('</div>', unsafe_allow_html=True)
    if main_run_clicked:
        st.session_state["_pending_run"] = True
        st.rerun()

    with st.expander("How the five-agent pipeline works", expanded=False):
        st.markdown(
            "**Parser** reads the transcript and extracts distinct topics.\n\n"
            "**Constraint Extractor** reads the wiki for architecture rules.\n\n"
            "**Story Writer** drafts user stories with Given/When/Then "
            "acceptance criteria for each topic, respecting constraints.\n\n"
            "**Epic Decomposer** groups stories into epics and breaks each "
            "story into 3-7 concrete tasks.\n\n"
            "**Gap Detector** compares new stories against the existing "
            "backlog and constraints to find duplicates, conflicts, and gaps.\n\n"
            "Every agent decision is captured in `audit_trail.md` — a "
            "chronological trace a reviewer can read top-to-bottom."
        )
else:
    # ---- Run-meta strip ----
    elapsed = st.session_state.elapsed or 0
    tokens = st.session_state.tokens_total or 0
    cost = st.session_state.cost_usd or 0.0
    model = st.session_state.model_used or "—"
    cost_label = f"${cost:.4f}" if cost > 0 else "—"

    st.markdown(
        '<div class="run-meta">'
        f'<span class="run-meta-item"><span class="run-meta-icon">✦</span>'
        f'<span class="run-meta-label">Source</span>{_esc(st.session_state.source_label)}</span>'
        '<span class="run-meta-sep">·</span>'
        f'<span class="run-meta-item"><span class="run-meta-icon">⧗</span>'
        f'<span class="run-meta-label">Elapsed</span>{elapsed:.1f} s</span>'
        '<span class="run-meta-sep">·</span>'
        f'<span class="run-meta-item"><span class="run-meta-icon">⚙</span>'
        f'<span class="run-meta-label">Model</span>{_esc(model)}</span>'
        '<span class="run-meta-sep">·</span>'
        f'<span class="run-meta-item"><span class="run-meta-icon">⊕</span>'
        f'<span class="run-meta-label">Tokens</span>{tokens:,}</span>'
        '<span class="run-meta-sep">·</span>'
        f'<span class="run-meta-item"><span class="run-meta-icon">$</span>'
        f'<span class="run-meta-label">Cost</span>{cost_label}</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    _render_kpis(result)

    # ---- Compare-mode banner ----
    # When the run was a compare, surface the side-by-side metrics
    # immediately under the KPIs so the user sees the two providers'
    # outputs without leaving the page.
    cmp_summary = result.get("_compare_summary")
    cmp_labels = result.get("_compare_labels") or {}
    if cmp_summary:
        _render_compare_banner(cmp_summary, cmp_labels, result)

    # ---- Guardrail findings strip ----
    # Non-blocking heuristic checks ran post-LLM. We surface a coloured
    # summary chip; the full list lives in a dedicated tab below.
    findings = result.get("guardrail_findings") or []
    if findings:
        n_err = sum(1 for f in findings if f.get("severity") == "error")
        n_warn = sum(1 for f in findings if f.get("severity") == "warn")
        n_info = sum(1 for f in findings if f.get("severity") == "info")
        if n_err:
            tone, accent = "error", "var(--rose)"
            verdict = f"{n_err} issue{'s' if n_err != 1 else ''} the synthesis should address"
        elif n_warn:
            tone, accent = "warn", "var(--amber)"
            verdict = f"{n_warn} warning{'s' if n_warn != 1 else ''} worth a quick scan"
        else:
            tone, accent = "info", "var(--accent)"
            verdict = f"{n_info} note{'s' if n_info != 1 else ''} for review"
        st.markdown(
            f'<div style="margin:0.4rem 0 0.8rem;padding:0.6rem 0.9rem;'
            f'background:var(--bg-elev-1);border:1px solid var(--border);'
            f'border-left:3px solid {accent};border-radius:8px;'
            f'font-size:0.82rem;color:var(--text-muted);">'
            f'<span style="font-size:0.62rem;font-weight:700;letter-spacing:0.14em;'
            f'text-transform:uppercase;color:{accent};margin-right:0.5rem;">'
            f'Guardrails · {tone}</span>{_esc(verdict)}'
            f' <span style="color:var(--text-faint);">— open the '
            f'<strong>Guardrails</strong> tab below for the full list.</span></div>',
            unsafe_allow_html=True,
        )

    # ---- "What's next" action row — each button is a real action ----
    epics_list = result.get("epics") or []
    n_stories = sum(len(e.get("stories") or []) for e in epics_list)
    dup_count = len(result.get("duplicates") or [])

    actions: list[tuple[str, str, str]] = []  # (key, label, button_type)
    if n_stories > 0:
        actions.append((
            "review",
            f"◇  Review {n_stories} stor{'y' if n_stories == 1 else 'ies'}",
            "secondary",
        ))
    if dup_count > 0:
        actions.append((
            "compare",
            f"⬢  Compare {dup_count} duplicate{'s' if dup_count != 1 else ''}",
            "primary",
        ))
    if n_stories > 0:
        actions.append(("edit", "✎  Edit stories", "secondary"))
    actions.append(("export", "↓  Export JSON / MD", "secondary"))

    st.markdown(
        '<div class="next-strip-label-row">WHAT&rsquo;S NEXT</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="next-action-row">', unsafe_allow_html=True)
    cols = st.columns(len(actions))
    for i, (akey, label, btn_type) in enumerate(actions):
        with cols[i]:
            if st.button(
                label,
                key=f"next_action_{akey}",
                type=btn_type,
                use_container_width=True,
            ):
                if akey == "compare":
                    show_duplicate_compare_dialog()
                elif akey == "edit":
                    st.session_state.stories_edit_mode = True
                    st.toast("Edit mode on — open the Epics tab to edit", icon="✎")
                elif akey == "review":
                    st.session_state.stories_edit_mode = False
                    st.toast("Stories are in the Epics tab below", icon="◇")
                elif akey == "export":
                    st.toast("Download buttons are inside each tab", icon="↓")
    st.markdown('</div>', unsafe_allow_html=True)

    # ---- Cost / token panel (expander) ----
    # Per-stage models mean per-row models: each agent's row prices its
    # tokens at the rate of *its* stage model. The Total row sums those
    # per-agent costs rather than re-applying a single model rate.
    token_usage = st.session_state.token_usage or {}
    if token_usage:
        with st.expander("Cost & tokens", expanded=False):
            from pricing import is_free_tier_eligible  # local import
            models_per_stage = result.get("models") or {}
            rows = []
            row_total_cost = 0.0
            for agent_name, vals in token_usage.items():
                if agent_name == "total":
                    continue
                ai = int(vals.get("input", 0) or 0)
                ao = int(vals.get("output", 0) or 0)
                row_model = _model_for_agent(agent_name, models_per_stage)
                agent_cost = estimate_cost_usd(row_model, ai, ao) if row_model else None
                if agent_cost is not None:
                    row_total_cost += agent_cost
                tag = " (free tier)" if is_free_tier_eligible(row_model) else ""
                rows.append({
                    "agent": agent_name.replace("_", " ").title(),
                    "model": (row_model + tag) if row_model else "—",
                    "input_tokens": ai,
                    "output_tokens": ao,
                    "cost_usd": f"${agent_cost:.4f}" if agent_cost is not None else "—",
                })
            total = token_usage.get("total") or {"input": 0, "output": 0}
            rows.append({
                "agent": "Total",
                "model": "—",
                "input_tokens": int(total.get("input", 0)),
                "output_tokens": int(total.get("output", 0)),
                "cost_usd": f"${row_total_cost:.4f}",
            })
            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "agent": st.column_config.TextColumn("Agent"),
                    "model": st.column_config.TextColumn("Model"),
                    "input_tokens": st.column_config.NumberColumn("Input tokens", format="%d"),
                    "output_tokens": st.column_config.NumberColumn("Output tokens", format="%d"),
                    "cost_usd": st.column_config.TextColumn("Est. USD"),
                },
            )

            # ---- Visual breakdown ----
            # Per-agent input/output bar chart so reviewers see which agent
            # dominates the bill without parsing the table by eye.
            chart_rows = [r for r in rows if r["agent"] != "Total"]
            if chart_rows:
                chart_data = {
                    "Agent": [r["agent"] for r in chart_rows],
                    "Input tokens": [r["input_tokens"] for r in chart_rows],
                    "Output tokens": [r["output_tokens"] for r in chart_rows],
                }
                try:
                    import pandas as pd  # local — only needed when the panel renders
                    df = pd.DataFrame(chart_data).set_index("Agent")
                    st.bar_chart(df, height=180)
                except ImportError:
                    pass

            # ---- Cost trend across recent runs ----
            # Loads the last ten run summaries from disk and renders a tiny
            # line chart so users can see whether per-run cost is creeping up
            # after a prompt or model change. Cheap (reads ~10 small JSONs);
            # silent fallback if history isn't present yet.
            history = _load_run_history()[:10]
            trend = [
                {"run": h.get("source_label") or h.get("run_dir") or "?",
                 "cost_usd": float(h.get("cost_usd") or 0)}
                for h in reversed(history)
                if (h.get("cost_usd") or 0) > 0
            ]
            if len(trend) >= 2:
                try:
                    import pandas as pd
                    tdf = pd.DataFrame(trend).set_index("run")
                    st.caption("Cost across recent runs (USD)")
                    st.line_chart(tdf, height=140)
                except ImportError:
                    pass

            st.caption(
                "Prices from `src/pricing.py` — paid-tier list rates for Anthropic "
                "Claude (Sonnet 4.5 / Haiku 4.5) and Google Gemini (2.5 Flash / Pro) "
                "as of late 2025. `(free tier)` marks models eligible for AI Studio's "
                "free quota where your actual bill is $0. Labeled *est.* because "
                "cache hits and batch discounts aren't accounted for."
            )

    # ---- Tabs ----
    n_findings = len(result.get("guardrail_findings") or [])
    tab_epics, tab_gaps, tab_conf, tab_dups, tab_guard, tab_audit = st.tabs([
        f"Epics ({n_stories} stories)",
        f"Gaps ({len(result.get('gaps') or [])})",
        f"Conflicts ({len(result.get('conflicts') or [])})",
        f"Duplicates ({dup_count})",
        f"Guardrails ({n_findings})",
        "Audit trail",
    ])

    with tab_epics:
        if result.get("summary"):
            st.markdown(
                f'<div class="summary-card">'
                f'<div class="summary-label">Run summary</div>'
                f'{_esc(result["summary"])}'
                f'</div>',
                unsafe_allow_html=True,
            )
        _render_epics_tab(result)
    with tab_gaps:
        _render_findings_tab(result, "gaps")
    with tab_conf:
        _render_findings_tab(result, "conflicts")
    with tab_dups:
        _render_findings_tab(result, "duplicates")
    with tab_guard:
        _render_guardrails_tab(result)
    with tab_audit:
        # Audit markdown contains <details> blocks for full prompt + response
        # capture; render with raw HTML enabled so the collapsibles work.
        st.markdown(
            result.get("audit_trail", "_No audit trail captured._"),
            unsafe_allow_html=True,
        )

    # ---- Downloads ----
    st.markdown("### Downloads")
    run_dir: Path = st.session_state.run_dir
    cols = st.columns(3)
    # Build live JSON / MD from current (possibly edited) result so the
    # download reflects edits the user made in the data_editor.
    live_json = json.dumps(
        {k: v for k, v in result.items() if k != "audit_trail"},
        indent=2,
    )
    from output_formatter import _render_markdown as _build_md  # local import
    live_md = _build_md(result, source_label=st.session_state.source_label)
    run_stem = run_dir.name if run_dir else datetime.now().strftime("%Y%m%d_%H%M%S")
    with cols[0]:
        st.download_button(
            "↓  synthesis.md",
            live_md,
            file_name=f"{run_stem}_synthesis.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with cols[1]:
        st.download_button(
            "↓  synthesis.json",
            live_json,
            file_name=f"{run_stem}_synthesis.json",
            mime="application/json",
            use_container_width=True,
        )
    with cols[2]:
        audit_md = (run_dir / "audit_trail.md") if run_dir else None
        if audit_md and audit_md.exists():
            st.download_button(
                "↓  audit_trail.md",
                audit_md.read_text(encoding="utf-8"),
                file_name=f"{run_stem}_audit_trail.md",
                mime="text/markdown",
                use_container_width=True,
            )
        else:
            st.download_button(
                "↓  audit_trail.md",
                result.get("audit_trail", ""),
                file_name=f"{run_stem}_audit_trail.md",
                mime="text/markdown",
                use_container_width=True,
            )
    if run_dir is not None:
        try:
            rel = run_dir.relative_to(ROOT)
            st.caption(f"All three artifacts also live on the server under `{rel}/`.")
        except ValueError:
            pass
