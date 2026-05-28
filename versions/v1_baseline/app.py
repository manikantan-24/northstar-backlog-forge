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

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

# -------------------------------------------------------- bootstrap

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "src"))

from input_loader import load_text, load_tickets, InputError  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from output_formatter import write_outputs  # noqa: E402

st.set_page_config(
    page_title="Backlog Synthesizer",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------------------------------------------- styling

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
    --bg: #0a0e1a;
    --bg-elev-1: #11162a;
    --bg-elev-2: #161c34;
    --bg-card: #1a2140;
    --border: #232a4a;
    --border-strong: #353e64;
    --text: #e6ebf5;
    --text-muted: #94a3c4;
    --text-faint: #5e6a8a;
    --accent: #22d3ee;
    --accent-glow: rgba(34, 211, 238, 0.16);
    --violet: #a78bfa;
    --violet-glow: rgba(167, 139, 250, 0.14);
    --green: #34d399;
    --green-glow: rgba(52, 211, 153, 0.14);
    --amber: #fbbf24;
    --amber-glow: rgba(251, 191, 36, 0.14);
    --rose: #fb7185;
    --rose-glow: rgba(251, 113, 133, 0.14);
}

.stApp {
    background:
      radial-gradient(ellipse 1100px 600px at 18% -10%, rgba(34,211,238,0.07), transparent 60%),
      radial-gradient(ellipse 800px 500px at 90% 110%, rgba(167,139,250,0.06), transparent 60%),
      var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
}

/* Streamlit chrome: hide outright (display:none reclaims the space too — the
   white strip you'd otherwise see at the top is the toolbar wrapper). */
#MainMenu,
footer,
header[data-testid="stHeader"],
[data-testid="stHeader"],
[data-testid="stDeployButton"],
[data-testid="stToolbar"],
[data-testid="stToolbarActions"],
[data-testid="stStatusWidget"],
[data-testid="stAppDeployButton"],
.stAppHeader {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
}
/* Pull the main canvas up into the space the now-hidden header left behind. */
[data-testid="stAppViewContainer"] > .main,
[data-testid="stMain"] {
    padding-top: 0 !important;
}
.block-container {
    padding-top: 1.5rem !important;
}

/* ===== Sidebar ===== */
section[data-testid="stSidebar"] {
    background: var(--bg-elev-1);
    border-right: 1px solid var(--border);
}
section[data-testid="stSidebar"] h3, section[data-testid="stSidebar"] .stMarkdown h3 {
    color: var(--text);
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-top: 1rem;
}

/* ===== Header strip ===== */
.app-header {
    display: flex; align-items: center; gap: 0.85rem;
    padding: 0.5rem 0 1.2rem 0;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem;
}
.app-mark {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.5rem;
    color: var(--accent);
}
.app-title {
    font-size: 1.4rem; font-weight: 700; color: var(--text);
    line-height: 1.1;
}
.app-tagline {
    font-size: 0.84rem; color: var(--text-muted); margin-top: 0.2rem;
}

/* ===== Pipeline timeline ===== */
.pipeline {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 0.6rem;
    margin: 0 0 1.5rem 0;
}
.stage {
    padding: 0.7rem 0.85rem;
    background: var(--bg-elev-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    position: relative;
    transition: all 0.15s ease;
}
.stage.active {
    border-color: var(--accent);
    background: var(--accent-glow);
    box-shadow: 0 0 16px rgba(34,211,238,0.18);
}
.stage.done {
    border-color: var(--green);
    background: var(--green-glow);
}
.stage.error {
    border-color: var(--rose);
    background: var(--rose-glow);
}
.stage.skipped {
    opacity: 0.55;
    background: var(--bg-elev-1);
    border-style: dashed;
}
.stage-glyph {
    position: absolute;
    top: 0.45rem;
    right: 0.55rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    font-weight: 700;
    line-height: 1;
}
.stage.active .stage-glyph {
    color: var(--accent);
    animation: pulse 1.2s ease-in-out infinite;
}
.stage.done .stage-glyph    { color: var(--green); }
.stage.error .stage-glyph   { color: var(--rose); }
.stage.skipped .stage-glyph { color: var(--text-faint); }
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.55; transform: scale(0.85); }
}

/* Live progress status line below the pipeline strip. */
.progress-status {
    margin: -0.5rem 0 1.3rem 0;
    padding: 0.6rem 0.9rem;
    background: var(--bg-elev-1);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 6px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.8rem;
    color: var(--text-muted);
}
.progress-status strong {
    color: var(--accent);
    margin-right: 0.5rem;
    font-weight: 700;
    letter-spacing: 0.04em;
}
.stage-num {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; font-weight: 700;
    color: var(--text-faint);
    letter-spacing: 0.08em;
    margin-bottom: 0.25rem;
}
.stage-name {
    font-size: 0.92rem; font-weight: 600; color: var(--text);
    line-height: 1.25;
}
.stage-sub {
    font-size: 0.7rem; color: var(--text-muted); margin-top: 0.2rem;
}

/* ===== KPI cards ===== */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 0.7rem;
    margin: 0 0 1.5rem 0;
}
.kpi {
    padding: 1rem 1.1rem;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
}
.kpi-label {
    font-size: 0.66rem; font-weight: 700;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--text-faint);
    margin-bottom: 0.45rem;
}
.kpi-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2rem; font-weight: 700; color: var(--text);
    line-height: 1;
}
.kpi-meta {
    font-size: 0.74rem; color: var(--text-muted);
    margin-top: 0.4rem;
}
.kpi.violet .kpi-value { color: var(--violet); }
.kpi.amber .kpi-value { color: var(--amber); }
.kpi.rose .kpi-value { color: var(--rose); }
.kpi.green .kpi-value { color: var(--green); }

/* ===== Empty state ===== */
.empty-state {
    padding: 2.5rem 2rem;
    background: var(--bg-elev-1);
    border: 1px dashed var(--border-strong);
    border-radius: 14px;
    text-align: center;
}
.empty-eyebrow {
    font-size: 0.7rem; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 0.6rem;
}
.empty-title {
    font-size: 1.5rem; font-weight: 700; color: var(--text);
    margin-bottom: 0.6rem;
}
.empty-sub {
    font-size: 0.95rem; color: var(--text-muted);
    max-width: 640px; margin: 0 auto 1.5rem;
}
.empty-steps {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.85rem;
    max-width: 900px; margin: 0 auto;
    text-align: left;
}
.empty-step {
    padding: 0.95rem 1.05rem;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
}
.empty-step-num {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; font-weight: 700; color: var(--accent);
    letter-spacing: 0.12em; margin-bottom: 0.3rem;
}
.empty-step-title {
    font-size: 0.9rem; font-weight: 600; color: var(--text);
    margin-bottom: 0.25rem;
}
.empty-step-body {
    font-size: 0.78rem; color: var(--text-muted); line-height: 1.4;
}

/* ===== Epic / story / task cards ===== */
.epic-card {
    background: var(--bg-elev-2);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 10px;
    padding: 1rem 1.2rem;
    margin-bottom: 1.2rem;
}
.epic-head {
    display: flex; align-items: baseline; gap: 0.7rem;
    margin-bottom: 0.5rem;
}
.epic-id {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; font-weight: 700;
    color: var(--text-faint);
    letter-spacing: 0.08em; text-transform: uppercase;
}
.epic-title {
    font-size: 1.05rem; font-weight: 700; color: var(--text);
}
.epic-desc {
    font-size: 0.85rem; color: var(--text-muted); line-height: 1.45;
    margin-bottom: 0.7rem;
}
.story-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.85rem 1rem;
    margin-bottom: 0.7rem;
}
.story-head {
    display: flex; align-items: center; gap: 0.55rem;
    margin-bottom: 0.4rem;
}
.story-id {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.66rem; font-weight: 700;
    color: var(--text-faint);
    letter-spacing: 0.08em; text-transform: uppercase;
}
.story-title {
    font-size: 0.95rem; font-weight: 600; color: var(--text);
}
.story-pri {
    font-size: 0.66rem; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase;
    padding: 0.14rem 0.5rem;
    border-radius: 999px;
    margin-left: auto;
}
.pri-high   { background: var(--rose-glow);  color: var(--rose);  border: 1px solid rgba(251,113,133,.4); }
.pri-medium { background: var(--amber-glow); color: var(--amber); border: 1px solid rgba(251,191,36,.4); }
.pri-low    { background: var(--green-glow); color: var(--green); border: 1px solid rgba(52,211,153,.4); }
.story-user {
    font-size: 0.82rem; color: var(--text);
    font-style: italic; margin-bottom: 0.4rem;
}
.story-ac {
    margin: 0.4rem 0 0.4rem 1rem;
    padding: 0;
    font-size: 0.8rem; color: var(--text-muted); line-height: 1.45;
}
.story-ac li { margin-bottom: 0.15rem; }
.tags-row {
    display: flex; flex-wrap: wrap; gap: 0.3rem;
    margin: 0.45rem 0;
}
.tag {
    font-size: 0.66rem; font-weight: 600;
    padding: 0.14rem 0.5rem;
    background: var(--bg-elev-2); color: var(--text-muted);
    border: 1px solid var(--border); border-radius: 999px;
}
.task-list {
    margin: 0.35rem 0 0 1rem;
    padding: 0;
    font-size: 0.76rem; color: var(--text-muted); line-height: 1.5;
}

/* ===== Finding cards (gap / conflict / duplicate) ===== */
.finding-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0.95rem 1.1rem;
    margin-bottom: 0.8rem;
}
.finding-gap      { border-left: 3px solid var(--amber); }
.finding-conflict { border-left: 3px solid var(--rose); }
.finding-dup      { border-left: 3px solid var(--violet); }
.finding-head {
    display: flex; align-items: baseline; gap: 0.55rem;
    margin-bottom: 0.3rem;
}
.finding-kind {
    font-size: 0.66rem; font-weight: 700;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--text-faint);
}
.finding-title {
    font-size: 0.95rem; font-weight: 600; color: var(--text);
}
.finding-body {
    font-size: 0.83rem; color: var(--text-muted); line-height: 1.5;
}
.finding-evidence {
    margin-top: 0.45rem;
    padding: 0.55rem 0.7rem;
    background: var(--bg-elev-2);
    border-left: 2px solid var(--border-strong);
    border-radius: 4px;
    font-size: 0.77rem; color: var(--text-muted); font-style: italic;
}

/* ===== Run meta strip ===== */
.run-meta {
    display: flex; flex-wrap: wrap; gap: 0.5rem 1.1rem; align-items: center;
    padding: 0.5rem 0.8rem;
    background: var(--bg-elev-1);
    border: 1px solid var(--border); border-radius: 8px;
    font-size: 0.78rem; color: var(--text-muted);
    margin-bottom: 1rem;
}
.run-meta strong { color: var(--text); margin-right: 0.35rem; font-weight: 600; }
</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)

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


# Five-stage pipeline visualization
_STAGES = [
    ("01", "Parser", "Topics from transcript"),
    ("02", "Constraint", "Rules from wiki"),
    ("03", "Story Writer", "User stories + AC"),
    ("04", "Epic Decomposer", "Epics → tasks"),
    ("05", "Gap Detector", "Dupes / conflicts / gaps"),
]


def _render_pipeline(stage_states: list[str] | None = None) -> None:
    """Render the 5 stage cards.

    `stage_states[i]` is one of: "idle" (default), "active", "done",
    "failed", "skipped". When None, all stages render as idle. This is
    the function the live-progress callback repaints on every event.
    """
    if stage_states is None:
        stage_states = ["idle"] * len(_STAGES)
    cells = []
    for i, (num, name, sub) in enumerate(_STAGES):
        state = stage_states[i] if i < len(stage_states) else "idle"
        cls_map = {
            "idle": "stage",
            "active": "stage active",
            "done": "stage done",
            "failed": "stage error",
            "skipped": "stage skipped",
        }
        cls = cls_map.get(state, "stage")
        # Tiny status icon by state — purely decorative, helps the eye scan.
        glyph = {
            "active": "●",
            "done": "✓",
            "failed": "!",
            "skipped": "—",
        }.get(state, "")
        glyph_html = (
            f'<span class="stage-glyph">{glyph}</span>' if glyph else ""
        )
        cells.append(
            f'<div class="{cls}">{glyph_html}'
            f'<div class="stage-num">STAGE {num}</div>'
            f'<div class="stage-name">{_esc(name)}</div>'
            f'<div class="stage-sub">{_esc(sub)}</div></div>'
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


def _render_epics_tab(result: dict) -> None:
    epics = result.get("epics", []) or []
    if not epics:
        st.info("No epics were produced. Either the synthesis didn't run or the transcript was off-topic.")
        return
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
        # Choose a title field by kind
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

        # Body text picks from a few likely fields
        body = item.get("description") or item.get("reason") or ""
        if body:
            parts.append(f'<div class="finding-body">{_esc(body)}</div>')
        if item.get("evidence"):
            parts.append(f'<div class="finding-evidence">↳ {_esc(item["evidence"])}</div>')
        parts.append("</div>")
        st.markdown("".join(parts), unsafe_allow_html=True)


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
    # Frozen snapshot of the last completed run's pipeline. Persists across
    # reruns so the cards stay green/red after the page settles.
    st.session_state.stage_states = None
if "run_history" not in st.session_state:
    # In-memory list of past runs (this browser session only).
    # Each entry: {run_dir, source_label, timestamp, elapsed, counts, tokens, cost_usd}
    st.session_state.run_history = []


# -------------------------------------------------------- sidebar

SAMPLES_DIR = ROOT / "samples"
GOLDEN_TRANSCRIPTS_DIR = ROOT / "evaluation" / "golden_dataset" / "transcripts"

# Each option maps a human-readable label → file path. The empty-string key
# means "no file" (skip this input), available on the two optional inputs.
# "__upload__" is the sentinel for "show a file uploader".
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

    # ---- Transcript (required) ----
    st.markdown("### Transcript")
    transcript_choice = st.selectbox(
        "Source",
        options=list(TRANSCRIPT_OPTIONS.keys()),
        index=0,
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

    # ---- Wiki / architecture constraints (optional) ----
    st.markdown("### Architecture / wiki")
    constraints_choice = st.selectbox(
        "Constraints source",
        options=list(CONSTRAINTS_OPTIONS.keys()),
        index=0,
        label_visibility="collapsed",
        key="constraints_choice",
        help="Architecture constraints — performance budgets, required integrations, compliance rules. Optional but improves conflict detection.",
    )
    constraints_upload = None
    if CONSTRAINTS_OPTIONS[constraints_choice] == "__upload__":
        constraints_upload = st.file_uploader(
            "Upload wiki / constraints", type=["md", "txt"],
            key="constraints_upload", label_visibility="collapsed",
        )

    # ---- Existing backlog (optional) ----
    st.markdown("### Existing backlog")
    backlog_choice = st.selectbox(
        "Backlog source",
        options=list(BACKLOG_OPTIONS.keys()),
        index=0,
        label_visibility="collapsed",
        key="backlog_choice",
        help="JIRA / GitHub ticket export. Enables duplicate detection. Optional.",
    )
    backlog_upload = None
    if BACKLOG_OPTIONS[backlog_choice] == "__upload__":
        backlog_upload = st.file_uploader(
            "Upload backlog JSON", type=["json"],
            key="backlog_upload", label_visibility="collapsed",
        )

    # ---- Privacy ----
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

    # ---- Options ----
    st.markdown("### Options")
    dry_run = st.toggle(
        "Dry run (no API call)",
        value=False,
        help=(
            "Load and validate inputs, then stop before any Claude call. "
            "Shows what would be sent (char counts + ticket count) without "
            "spending API credit. Useful for verifying uploads."
        ),
    )

    # ---- Run button ----
    # Disable Synthesize until a usable transcript is selected. The other two
    # inputs are optional — the orchestrator skips agents on empty input.
    _transcript_ready = (
        TRANSCRIPT_OPTIONS[transcript_choice] != "__upload__"
        or transcript_upload is not None
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

    # ---- Recent runs ----
    # The history is just an in-memory list scoped to this browser session.
    # Surviving across tab refresh would need disk persistence; not worth
    # the complexity for a demo.
    if st.session_state.get("run_history"):
        st.markdown("### Recent runs")
        history_clicked = st.button(
            f"↻  View {len(st.session_state.run_history)} past run(s)",
            use_container_width=True,
            key="open_history",
        )
    else:
        history_clicked = False


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

# Live-progress region. Two placeholders so the callback can update them
# mid-run without touching the rest of the page. After a run, they hold
# the final state (all done / any failures).
_pipeline_placeholder = st.empty()
_progress_placeholder = st.empty()

# Default render: all stages idle. Repaints below during a run.
with _pipeline_placeholder.container():
    _render_pipeline(stage_states=st.session_state.get("stage_states"))


# -------------------------------------------------------- run handler

def _read_uploaded_text(uploaded) -> str:
    """Streamlit's uploaded files are bytes-like; route through input_loader for PDFs."""
    if uploaded is None:
        return ""
    name = uploaded.name
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        # Save to a temp path so pypdf / pdfplumber can read it.
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


if run_clicked:
    # ---- Resolve inputs ----
    # Each input independently resolves to one of:
    #   - a bundled sample file path → read from disk
    #   - an uploaded file → read from the Streamlit upload buffer
    #   - empty/skip → empty string or [] (orchestrator skips that agent)
    try:
        # Transcript
        t_choice_val = TRANSCRIPT_OPTIONS[transcript_choice]
        if t_choice_val == "__upload__":
            transcript_text = _read_uploaded_text(transcript_upload)
            source_label = transcript_upload.name if transcript_upload else "(uploaded)"
        else:
            transcript_text = load_text(str(t_choice_val))
            source_label = Path(t_choice_val).name

        # Constraints (optional)
        c_choice_val = CONSTRAINTS_OPTIONS[constraints_choice]
        if c_choice_val == "__upload__":
            constraint_text = _read_uploaded_text(constraints_upload)
        elif c_choice_val == "":
            constraint_text = ""
        else:
            constraint_text = load_text(str(c_choice_val))

        # Backlog (optional)
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

    # ---- Dry-run branch: validate inputs, skip the API call ----
    if dry_run:
        st.success(
            f"**Dry run** — inputs validated.\n\n"
            f"- Transcript: **{len(transcript_text):,}** chars from `{source_label}`\n"
            f"- Constraints: **{len(constraint_text):,}** chars\n"
            f"- Existing tickets: **{len(existing_tickets)}**\n"
            f"- PII redaction: **{'ON' if redact_pii else 'OFF'}**\n\n"
            f"No Claude calls were made and no `outputs/` directory was created. "
            f"Toggle Dry-run off in the sidebar to actually run the pipeline."
        )
        st.stop()

    # ---- Run synthesizer with live progress ----
    t0 = time.perf_counter()
    stage_states = ["idle"] * len(_STAGES)
    # Bump initial state so the first card shows "active" before the
    # callback fires (visual cue that we've started).
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

    # ---- Live progress callback ----
    # Called by the orchestrator at every agent boundary. Repaints the 5
    # stage cards + the status line beneath them.
    def _on_progress(stage_index: int, stage_name: str, event: str, detail: str):
        # Map orchestrator event names to the CSS state tokens.
        if event == "started":
            stage_states[stage_index] = "active"
        elif event == "completed":
            stage_states[stage_index] = "done"
        elif event == "failed":
            stage_states[stage_index] = "failed"
        elif event == "skipped":
            stage_states[stage_index] = "skipped"
        # Pretty-print agent name for the status line: snake_case → Title Case
        pretty_name = stage_name.replace("_", " ").title()
        line = (
            f'<div class="progress-status">'
            f'<strong>{pretty_name.upper()}</strong>{_esc(event)}'
            f'{(" · " + _esc(detail)) if detail else ""}</div>'
        )
        with _pipeline_placeholder.container():
            _render_pipeline(stage_states=stage_states)
        _progress_placeholder.markdown(line, unsafe_allow_html=True)

    try:
        result = orch.run(
            transcript_text=transcript_text,
            constraint_text=constraint_text,
            existing_tickets=existing_tickets,
            redact_pii=redact_pii,
            progress_callback=_on_progress,
        )
    except Exception as e:
        _progress_placeholder.error(f"Pipeline failed: {e}")
        st.stop()

    elapsed = time.perf_counter() - t0
    # Final paint — all stages reach their terminal state (done / failed /
    # skipped) before this point thanks to the callback. The status line
    # gets a final summary tag.
    n_done   = sum(1 for s in stage_states if s == "done")
    n_failed = sum(1 for s in stage_states if s == "failed")
    n_skipped = sum(1 for s in stage_states if s == "skipped")
    summary_tag = (
        f'<strong>DONE</strong>{n_done}/{len(_STAGES)} agents completed'
        + (f' · {n_failed} failed' if n_failed else '')
        + (f' · {n_skipped} skipped' if n_skipped else '')
        + f' · {elapsed:.1f}s'
    )
    with _pipeline_placeholder.container():
        _render_pipeline(stage_states=stage_states)
    _progress_placeholder.markdown(
        f'<div class="progress-status">{summary_tag}</div>',
        unsafe_allow_html=True,
    )

    # ---- Persist outputs (same files the CLI writes) ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "outputs" / stamp
    json_path, md_path = write_outputs(
        {k: v for k, v in result.items() if k != "audit_trail"},
        run_dir,
        source_label=source_label,
    )
    audit_path = run_dir / "audit_trail.md"
    audit_path.write_text(result["audit_trail"], encoding="utf-8")

    # ---- Tally tokens from the audit log so the cost panel has real data ----
    # We re-walk the audit trail markdown for `tokens_used: N` lines. Cheap
    # enough — the trail is small — and avoids changing the audit_log API.
    import re as _re_local
    tokens_total = sum(
        int(m.group(1))
        for m in _re_local.finditer(r"tokens_used`?:\s*`?(\d+)", result["audit_trail"])
    )
    # Rough USD estimate for Claude Sonnet 4.5 based on a 1:3 in:out token
    # mix (input is much larger than output for our prompts). Not an exact
    # bill — labeled as "approx" in the UI to make that clear.
    _IN_PER_M, _OUT_PER_M = 3.0, 15.0  # USD per million tokens
    cost_usd = (tokens_total * 0.75 * _IN_PER_M + tokens_total * 0.25 * _OUT_PER_M) / 1_000_000

    st.session_state.result = result
    st.session_state.run_dir = run_dir
    st.session_state.elapsed = elapsed
    st.session_state.source_label = source_label
    st.session_state.stage_states = stage_states  # freeze for post-run renders
    st.session_state.tokens_total = tokens_total
    st.session_state.cost_usd = cost_usd

    # ---- Append to history (newest first) ----
    epics = result.get("epics", []) or []
    st.session_state.run_history.insert(0, {
        "run_dir": str(run_dir),
        "source_label": source_label,
        "timestamp": stamp,
        "elapsed": elapsed,
        "n_epics": len(epics),
        "n_stories": sum(len(e.get("stories") or []) for e in epics),
        "n_gaps": len(result.get("gaps") or []),
        "n_conflicts": len(result.get("conflicts") or []),
        "n_dups": len(result.get("duplicates") or []),
        "tokens": tokens_total,
        "cost_usd": cost_usd,
        "result": result,  # cached so "view past run" doesn't re-read disk
    })
    # Cap history so memory doesn't grow unbounded in a long session.
    st.session_state.run_history = st.session_state.run_history[:20]


# -------------------------------------------------------- results / empty state

result = st.session_state.result

if result is None:
    # Compact empty state — show the user what they've already picked + the
    # current input previews so the "Synthesize" button isn't taking a leap
    # of faith.
    t_val = TRANSCRIPT_OPTIONS[transcript_choice]
    c_val = CONSTRAINTS_OPTIONS[constraints_choice]
    b_val = BACKLOG_OPTIONS[backlog_choice]

    def _row(label: str, choice: str, val, upload, kind: str) -> str:
        if val == "__upload__":
            if upload is None:
                state = '<span style="color: var(--text-faint);">awaiting upload</span>'
            else:
                state = f'<span style="color: var(--accent);">{_esc(upload.name)} (uploaded)</span>'
        elif val == "":
            state = '<span style="color: var(--text-faint);">skipped — agent will not run</span>'
        else:
            state = f'<span style="color: var(--green);">{_esc(Path(str(val)).name)}</span>'
        return (
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:0.5rem 0;border-bottom:1px solid var(--border);">'
            f'<span style="color:var(--text-muted);font-weight:600;">{_esc(label)}</span>'
            f'{state}</div>'
        )

    st.markdown(
        '<div style="margin-top:1rem;padding:1.2rem 1.4rem;background:var(--bg-elev-1);'
        'border:1px solid var(--border);border-radius:12px;">'
        '<div style="font-size:0.7rem;font-weight:700;letter-spacing:0.12em;'
        'text-transform:uppercase;color:var(--accent);margin-bottom:0.7rem;">'
        'Selected inputs</div>'
        + _row("Transcript", transcript_choice, t_val, transcript_upload, "t")
        + _row("Architecture / wiki", constraints_choice, c_val, constraints_upload, "c")
        + _row("Existing backlog", backlog_choice, b_val, backlog_upload, "b")
        + '<div style="margin-top:0.9rem;font-size:0.8rem;color:var(--text-muted);'
        'line-height:1.5;">Click <strong style="color:var(--accent);">▶ Synthesize</strong> '
        'in the sidebar to run the five-agent pipeline against the selected inputs. '
        'Runs take ~3 minutes and write to <code style="background:var(--bg-card);'
        'padding:0.1rem 0.35rem;border-radius:3px;">outputs/&lt;timestamp&gt;/</code>.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Mini "how it works" — kept tight so it doesn't dominate the canvas.
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
    # Run-meta strip
    elapsed = st.session_state.elapsed or 0
    st.markdown(
        f'<div class="run-meta">'
        f'<span><strong>Source:</strong>{_esc(st.session_state.source_label)}</span>'
        f'<span><strong>Elapsed:</strong>{elapsed:.1f} s</span>'
        f'<span><strong>Run:</strong>'
        f'{_esc(st.session_state.run_dir.name if st.session_state.run_dir else "")}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    _render_kpis(result)

    # Tabbed results
    tab_epics, tab_gaps, tab_conf, tab_dups, tab_audit = st.tabs([
        f"Epics ({sum(len(e.get('stories', []) or []) for e in (result.get('epics') or []))} stories)",
        f"Gaps ({len(result.get('gaps') or [])})",
        f"Conflicts ({len(result.get('conflicts') or [])})",
        f"Duplicates ({len(result.get('duplicates') or [])})",
        "Audit trail",
    ])

    with tab_epics:
        if result.get("summary"):
            st.markdown(f'<div class="finding-body" style="margin-bottom:1rem;">{_esc(result["summary"])}</div>',
                        unsafe_allow_html=True)
        _render_epics_tab(result)
    with tab_gaps:
        _render_findings_tab(result, "gaps")
    with tab_conf:
        _render_findings_tab(result, "conflicts")
    with tab_dups:
        _render_findings_tab(result, "duplicates")
    with tab_audit:
        st.markdown(result.get("audit_trail", "_No audit trail captured._"))

    # Download buttons
    st.markdown("### Downloads")
    run_dir: Path = st.session_state.run_dir
    cols = st.columns(3)
    with cols[0]:
        synth_md = (run_dir / "synthesis.md")
        if synth_md.exists():
            st.download_button(
                "↓  synthesis.md",
                synth_md.read_text(encoding="utf-8"),
                file_name=f"{run_dir.name}_synthesis.md",
                mime="text/markdown",
                use_container_width=True,
            )
    with cols[1]:
        synth_json = (run_dir / "synthesis.json")
        if synth_json.exists():
            st.download_button(
                "↓  synthesis.json",
                synth_json.read_bytes(),
                file_name=f"{run_dir.name}_synthesis.json",
                mime="application/json",
                use_container_width=True,
            )
    with cols[2]:
        audit_md = (run_dir / "audit_trail.md")
        if audit_md.exists():
            st.download_button(
                "↓  audit_trail.md",
                audit_md.read_text(encoding="utf-8"),
                file_name=f"{run_dir.name}_audit_trail.md",
                mime="text/markdown",
                use_container_width=True,
            )
    st.caption(f"All three artifacts also live on the server under `{run_dir.relative_to(ROOT)}/`.")
