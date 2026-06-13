"""Streamlit page styling — the full CSS stylesheet for the app.

Extracted from app.py so the main entry script can stay focused on flow
and state. One big stylesheet rather than per-component <style> tags
because Streamlit re-renders top-to-bottom on every interaction; injecting
CSS once at the top is cheaper than re-inserting many small blocks on
each rerun.

The CSS tokens (colors, radii, fonts) match the target app's existing
visual style — dark navy palette, cyan + violet accents, IBM Plex Mono
for IDs / numbers, Inter for body. Additional rules ported from the V2
app cover: pipeline-wrap / pl-stage cards, summary-card, run-meta strip,
next-chip "what's next" row, dup-diff word-level highlight, and rh-*
history dialog cards.

Usage:
    from ui.styling import get_css
    st.markdown(get_css(), unsafe_allow_html=True)
"""

from __future__ import annotations

# --------------------------------------------------------------------- tokens
# Design tokens. Kept in a single :root block so any tweak (e.g. accent hue)
# propagates everywhere. Match the existing target palette.
_TOKENS_CSS = """
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
    --accent-strong: #06b6d4;
    --accent-glow: rgba(34, 211, 238, 0.16);
    --violet: #a78bfa;
    --violet-glow: rgba(167, 139, 250, 0.14);
    --accenture: #a100ff;   /* Accenture brand purple */
    --green: #34d399;
    --green-glow: rgba(52, 211, 153, 0.14);
    --amber: #fbbf24;
    --amber-glow: rgba(251, 191, 36, 0.14);
    --rose: #fb7185;
    --rose-glow: rgba(251, 113, 133, 0.14);
}
"""

# ----------------------------------------------------------------- base shell
_SHELL_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

.stApp {
    background:
      radial-gradient(ellipse 1100px 600px at 18% -10%, rgba(34,211,238,0.07), transparent 60%),
      radial-gradient(ellipse 800px 500px at 90% 110%, rgba(167,139,250,0.06), transparent 60%),
      var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
}

/* Hide ALL Streamlit chrome — deploy button, toolbar, hamburger, footer */
#MainMenu,
footer,
[data-testid="stDeployButton"],
[data-testid="stToolbar"],
[data-testid="stToolbarActions"],
[data-testid="stStatusWidget"],
[data-testid="stAppDeployButton"],
[data-testid="stMainBlockContainer"] > div > div > div > div > [data-testid="stVerticalBlock"] > div:last-child > [data-testid="stStatusWidget"],
button[title="View app in Streamlit Community Cloud"],
button[aria-label="Open app in Streamlit Community Cloud"],
.stDeployButton { display: none !important; visibility: hidden !important; height: 0 !important; }

/* ── Enterprise login page — Premium Dark ────────────────────────── */
.login-wrap {
    position:fixed; top:0; left:0; width:100vw; height:100vh; z-index:9999;
    display:flex; flex-direction:row; align-items:stretch;
    overflow:hidden; background:#060c1e;
}

/* Animated ambient orbs */
.login-orb { position:absolute; border-radius:50%; pointer-events:none; filter:blur(95px); }
.login-orb-1 {
    width:700px; height:700px; top:-250px; left:-130px;
    background:radial-gradient(circle,rgba(109,40,217,0.72),transparent 62%);
    animation:lorb1 22s ease-in-out infinite alternate;
}
.login-orb-2 {
    width:580px; height:580px; bottom:-220px; right:320px;
    background:radial-gradient(circle,rgba(30,58,175,0.62),transparent 62%);
    animation:lorb2 29s ease-in-out infinite alternate;
}
.login-orb-3 {
    width:400px; height:400px; top:36%; right:22%;
    background:radial-gradient(circle,rgba(161,0,255,0.38),transparent 62%);
    animation:lorb3 19s ease-in-out infinite alternate;
}
@keyframes lorb1 { 0%{transform:translate(0,0)} 100%{transform:translate(58px,40px)} }
@keyframes lorb2 { 0%{transform:translate(0,0)} 100%{transform:translate(-44px,-30px)} }
@keyframes lorb3 { 0%{transform:translate(0,-50%)scale(1)} 100%{transform:translate(-24px,-50%)scale(1.2)} }
/* Dot-grid overlay */
.login-wrap::before {
    content:''; position:absolute; inset:0; pointer-events:none;
    background-image:radial-gradient(rgba(99,60,255,0.11) 1px, transparent 1px);
    background-size:30px 30px;
}

/* ── LEFT PANEL ──────────────────────────────────────────────── */
.login-left {
    flex:0 0 56%; position:relative; z-index:1;
    display:flex; flex-direction:column;
    padding:3rem 3.5rem 0;
    border-right:1px solid rgba(99,60,255,0.1);
}
/* NorthStar logo — login large */
.ns-logo-lg { display:flex; align-items:center; gap:12px; margin-bottom:3.2rem; }
.ns-logo-lg-mark {
    width:46px; height:46px; border-radius:12px;
    background:rgba(8,16,42,0.95); border:1.5px solid rgba(245,198,66,0.32);
    display:flex; align-items:center; justify-content:center; flex-shrink:0;
}
.ns-logo-lg-text { display:flex; flex-direction:column; line-height:1.05; }
.ns-logo-lg-name { font-size:1.2rem; font-weight:800; color:#f0f4ff; letter-spacing:-0.02em; }
.ns-logo-lg-sub {
    font-size:0.55rem; font-weight:700; letter-spacing:0.28em;
    text-transform:uppercase; color:rgba(245,198,66,0.72); margin-top:2px;
}
/* Hero block wrapper */
.login-hero-block { display:flex; flex-direction:column; }

/* Badge */
.login-badge {
    display:inline-flex; align-items:center; gap:7px;
    font-size:0.67rem; font-weight:700; letter-spacing:0.16em; text-transform:uppercase;
    color:#a78bfa; background:rgba(109,40,217,0.15); border:1px solid rgba(109,40,217,0.28);
    border-radius:999px; padding:0.28rem 0.85rem; margin-bottom:1.4rem;
}
.login-badge-dot {
    width:5px; height:5px; border-radius:50%; background:#a78bfa;
    animation:bdot 2.2s ease-in-out infinite;
}
@keyframes bdot { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.3;transform:scale(0.7)} }
/* Hero */
.login-h1 {
    font-size:3.5rem; font-weight:900; color:#f0f4ff;
    line-height:0.97; letter-spacing:-0.05em; margin-bottom:0.12rem;
}
.login-h1 span {
    background:linear-gradient(128deg,#c084fc 0%,#818cf8 52%,#60a5fa 100%);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
}
.login-h2 {
    font-size:3.5rem; font-weight:900; color:rgba(240,244,255,0.17);
    line-height:0.97; letter-spacing:-0.05em; margin-bottom:1.3rem;
}
.login-pitch {
    font-size:0.84rem; color:rgba(175,185,220,0.62); line-height:1.74;
    max-width:350px; margin-bottom:2.2rem;
}
/* Stats */
.login-stats {
    display:flex; gap:2.5rem; margin-bottom:2.6rem;
    padding-top:1.2rem; border-top:1px solid rgba(255,255,255,0.06);
}
.login-stat-num {
    font-size:1.55rem; font-weight:900; color:#f0f4ff;
    letter-spacing:-0.04em; line-height:1;
}
.login-stat-label {
    font-size:0.59rem; font-weight:600; letter-spacing:0.13em;
    text-transform:uppercase; color:rgba(175,185,220,0.42); margin-top:4px;
}
/* Neural network viz */
.login-viz { flex:0 0 auto; margin:0 -3.5rem; }
.login-viz svg { width:100%; height:auto; display:block; }
/* Node pulse animations */
.node-pulse   { animation:npulse 3.4s ease-in-out infinite; transform-origin:center; }
.node-pulse-2 { animation:npulse 3.4s ease-in-out 1.2s infinite; transform-origin:center; }
.hub-ring     { animation:hring 3.6s ease-in-out infinite; transform-origin:280px 100px; }
.hub-ring-2   { animation:hring 3.6s ease-in-out 1.7s infinite; transform-origin:280px 100px; }
@keyframes npulse { 0%,100%{opacity:0.55;transform:scale(1)} 50%{opacity:0.06;transform:scale(2.1)} }
@keyframes hring  { 0%,100%{opacity:0.22;transform:scale(1)} 50%{opacity:0.03;transform:scale(1.9)} }
/* Accenture footer */
.login-acc-footer {
    padding:1.1rem 0 1.6rem; font-size:0.63rem; font-weight:700;
    letter-spacing:0.18em; text-transform:uppercase; color:rgba(156,163,175,0.38);
    display:flex; align-items:center; gap:7px;
}
.login-acc-footer .login-acc-sep { color:rgba(161,0,255,0.55); }

/* ── RIGHT PANEL ─────────────────────────────────────────────── */
.login-right {
    flex:1; position:relative; z-index:1;
    display:flex; flex-direction:column;
    align-items:center; justify-content:center; padding:2.5rem;
}
.login-card {
    width:100%; max-width:358px;
    background:rgba(255,255,255,0.045); border:1px solid rgba(255,255,255,0.1);
    border-radius:22px; padding:2.6rem 2.2rem 2.2rem;
    box-shadow:0 0 80px rgba(109,40,217,0.1), 0 24px 64px rgba(0,0,0,0.5);
    backdrop-filter:blur(28px); -webkit-backdrop-filter:blur(28px);
}
.login-card-icon {
    width:52px; height:52px; border-radius:14px;
    background:linear-gradient(135deg,rgba(109,40,217,0.28),rgba(161,0,255,0.16));
    border:1px solid rgba(109,40,217,0.32);
    display:flex; align-items:center; justify-content:center;
    margin-bottom:1.6rem; box-shadow:0 0 26px rgba(109,40,217,0.2);
}
.login-card-client {
    font-size:0.61rem; font-weight:700; letter-spacing:0.2em;
    text-transform:uppercase; color:#a78bfa; margin-bottom:0.3rem;
}
.login-card-title {
    font-size:1.7rem; font-weight:800; color:#f0f4ff;
    letter-spacing:-0.035em; margin-bottom:0.45rem; line-height:1.1;
}
.login-card-sub {
    font-size:0.78rem; color:rgba(175,185,220,0.48); line-height:1.62;
}
.login-card-divider { height:1px; background:rgba(255,255,255,0.07); margin:1.8rem 0; }
/* MS sign-in button */
.ms-signin-btn {
    display:flex; align-items:center; justify-content:center; gap:11px;
    width:100%; padding:0.92rem 1.4rem;
    background:#0078d4; color:#fff !important;
    border-radius:12px; font-size:0.93rem; font-weight:600;
    text-decoration:none !important; border:none;
    box-shadow:0 4px 22px rgba(0,120,212,0.32); letter-spacing:0.01em;
    transition:background 0.14s, box-shadow 0.14s, transform 0.1s;
    margin-bottom:1rem; position:relative; overflow:hidden;
}
.ms-signin-btn::after {
    content:''; position:absolute; inset:0;
    background:linear-gradient(105deg,transparent 38%,rgba(255,255,255,0.13) 50%,transparent 62%);
    transform:translateX(-100%); transition:transform 0.48s ease;
}
.ms-signin-btn:hover { background:#106ebe; transform:translateY(-1px); box-shadow:0 8px 30px rgba(0,120,212,0.44); }
.ms-signin-btn:hover::after { transform:translateX(100%); }
.ms-logo { width:18px; height:18px; flex-shrink:0; }
.login-card-note { font-size:0.65rem; color:rgba(175,185,220,0.32); text-align:center; }
.login-sec-strip {
    display:flex; align-items:center; justify-content:center; gap:8px;
    margin-top:1.6rem; font-size:0.62rem; color:rgba(175,185,220,0.28); letter-spacing:0.04em;
}
.login-sec-dot { width:3px; height:3px; border-radius:50%; background:rgba(175,185,220,0.2); flex-shrink:0; }

/* Responsive */
@media (max-width:900px) {
    .login-wrap { flex-direction:column; }
    .login-left { flex:none; padding:2rem 2rem 0; }
    .login-right { padding:2rem 2rem 2.5rem; }
    .login-h1, .login-h2 { font-size:2.3rem; }
    .login-viz { max-height:200px; }
    .login-card { max-width:100%; }
}

/* Header gets squashed to zero height but kept in the DOM so the
   sidebar collapse/expand chevron (which lives inside it in modern
   Streamlit) stays clickable. Without this the sidebar can't be
   re-opened once Streamlit remembers a collapsed state. */
header[data-testid="stHeader"],
.stAppHeader {
    background: transparent !important;
    height: 0 !important;
    min-height: 0 !important;
    overflow: visible !important;   /* don't clip the floated expand control */
}
header[data-testid="stHeader"] > *:not([data-testid="stSidebarCollapseButton"]):not([data-testid="collapsedControl"]):not([data-testid="stSidebarCollapsedControl"]):not([data-testid="stExpandSidebarButton"]) {
    display: none !important;
}

/* The sidebar is PERMANENTLY visible — the collapse / expand toggle is
   removed entirely so the navigation can never be hidden (and can't get
   stuck collapsed). Cover every testid Streamlit has used across versions
   plus an aria-label fallback. */
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"],
button[aria-label*="sidebar" i] {
    display: none !important;
}

/* Sidebar width.
   Rules:
   - !important on transform/visibility/margin so collapsed state can't hide it.
   - min-width with !important prevents shrinking below 360px.
   - NO !important on width or max-width — Streamlit's JS resize handler
     sets style.width directly; !important on those properties overrides JS
     and makes the drag handle appear to do nothing.
   - The initial width is set on the inner div (no !important) so the first
     paint shows 360px but the outer section can still be dragged wider. */
section[data-testid="stSidebar"],
section[data-testid="stSidebar"][aria-expanded="false"] {
    transform: none !important;
    visibility: visible !important;
    min-width: 360px !important;
    margin-left: 0 !important;
}
/* Set starting width on the inner container so JS can still override the
   outer section's width via the drag handle */
section[data-testid="stSidebar"] > div:first-child {
    min-width: 360px;
    width: 360px;
}
section[data-testid="stSidebar"] > div {
    visibility: visible !important;
}
[data-testid="stAppViewContainer"] > .main,
[data-testid="stMain"] {
    padding-top: 0 !important;
}
.block-container {
    padding-top: 1.5rem !important;
}

section[data-testid="stSidebar"] {
    background: var(--bg-elev-1);
    border-right: 1px solid var(--border);
    overflow-x: hidden !important;
}
/* Sidebar widgets: ensure text wraps and doesn't overflow horizontally */
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stMultiSelect label,
section[data-testid="stSidebar"] .stTextInput label,
section[data-testid="stSidebar"] .stToggle label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] .stCaption {
    white-space: normal !important;
    word-wrap: break-word !important;
    overflow-wrap: break-word !important;
}
/* Selectbox option text — allow wrapping in the dropdown */
section[data-testid="stSidebar"] [data-baseweb="select"] span,
section[data-testid="stSidebar"] [data-baseweb="select"] div {
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: unset !important;
}
/* Compact the horizontal radio so all 4 presets fit on two rows of 2,
   with smaller font so Local / Free / Balanced / Premium don't overflow. */
section[data-testid="stSidebar"] [data-testid="stRadio"] > div {
    gap: 0.3rem !important;
}
section[data-testid="stSidebar"] [data-testid="stRadio"] label {
    font-size: 0.82rem !important;
    padding: 0.1rem 0 !important;
}
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] .stMarkdown h3 {
    color: var(--text);
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-top: 1.2rem;
    padding-left: 0.55rem;
    border-left: 2px solid var(--accent);
}
"""

# --------------------------------------------------------------- app header
_HEADER_CSS = """
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

/* Accenture brand strip — styled wordmark, brand purple + chevron mark. */
.acc-brand {
    display: flex; flex-direction: column; gap: 0.2rem;
    padding: 0.3rem 0 1.05rem 0;
}
.acc-wordmark {
    font-size: 1.7rem; font-weight: 600; letter-spacing: -0.02em;
    color: var(--text); line-height: 1.05;
}
.acc-wordmark .acc-mark {
    color: var(--accenture); font-weight: 800; margin-left: 1px; font-size: 1.85rem;
}
.acc-eyebrow {
    font-size: 0.64rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--text-faint);
}
.acc-footer {
    margin-top: 1.6rem; padding-top: 0.9rem; border-top: 1px solid var(--border);
    font-size: 0.68rem; color: var(--text-faint); letter-spacing: 0.03em; line-height: 1.5;
}
.acc-footer .acc-mark { color: var(--accenture); font-weight: 800; }

/* Accumulating live run-log — every agent's lines stay visible (newest at
   the bottom) instead of being overwritten when the next stage starts. */
.progress-log {
    display: flex; flex-direction: column; gap: 4px;
    max-height: 340px; overflow-y: auto;
    padding: 10px 14px; margin-top: 8px;
    background: var(--bg-elev-1); border: 1px solid var(--border);
    border-radius: 10px; font-size: 0.86rem;
}
.log-line { color: var(--text-muted); line-height: 1.55; }
.log-line strong { color: var(--text); }
.log-icon { display: inline-block; width: 1.2em; color: var(--text-faint); }
.log-evt { color: var(--text-faint); text-transform: uppercase;
           font-size: 0.7rem; letter-spacing: 0.06em; }
.log-started .log-icon   { color: var(--accent); }
.log-completed .log-icon,
.log-done .log-icon      { color: var(--green); }
.log-failed .log-icon    { color: var(--rose); }
.log-failed strong       { color: var(--rose); }
.log-skipped .log-icon   { color: var(--amber); }

/* Responsive guard — the 5-column pipeline grid doesn't fit narrower
   viewports. Stack the cards vertically below 900px. */
@media (max-width: 900px) {
    .pipeline { grid-template-columns: 1fr 1fr; }
}
.log-failover            { color: var(--amber); }
.log-failover .log-icon  { color: var(--amber); }
.log-failover strong     { color: var(--amber); }
.log-failover .log-evt   { color: var(--amber); }

/* Multi-select chips — subtle dark pills that match the dashboard, instead
   of the bright primary-colour fill baseweb applies by default. */
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
    background-color: var(--bg-elev-2) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 7px !important;
    color: var(--text) !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] span { color: var(--text) !important; }
[data-testid="stMultiSelect"] [data-baseweb="tag"] svg {
    color: var(--text-muted) !important; fill: var(--text-muted) !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"]:hover {
    border-color: var(--accent) !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"]:hover svg {
    color: var(--accent) !important; fill: var(--accent) !important;
}
"""

# ------------------------------------------------------------- pipeline cards
# Two variants live side-by-side in this stylesheet:
#   - `.pipeline / .stage` — the target's original 5-card timeline (kept).
#   - `.pipeline-wrap / .pl-stage` — the V2 dotted-rail style. Used for the
#     active-stage glow/pulse animation (feature 4). The target's original
#     `.stage.active` already animates; the new `.pl-stage.active` is the
#     V2 pattern, available for any code that wants to use it.
_PIPELINE_CSS = """
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
    animation: stage-glow 1.6s ease-in-out infinite;
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
@keyframes stage-glow {
    0%, 100% { box-shadow: 0 0 12px rgba(34,211,238,0.16); }
    50%      { box-shadow: 0 0 26px rgba(34,211,238,0.45); }
}

/* When a stage flips from active → done, give the checkmark a brief
   scale-pop so the eye notices the transition. Runs once, then settles. */
.stage.done .stage-glyph {
    animation: stage-done-pop 0.45s cubic-bezier(0.34, 1.56, 0.64, 1) 1;
}
@keyframes stage-done-pop {
    0%   { transform: scale(0.5); opacity: 0; }
    60%  { transform: scale(1.25); opacity: 1; }
    100% { transform: scale(1); opacity: 1; }
}

/* Active stage gets a thin animated progress bar across its bottom edge
   so the user can see "this stage is doing work" even before its
   completed event arrives. Subtle — same accent color, low opacity. */
.stage.active::after {
    content: "";
    position: absolute;
    left: 0; right: 0; bottom: 0;
    height: 2px;
    background: linear-gradient(
        90deg,
        transparent 0%,
        var(--accent) 50%,
        transparent 100%
    );
    border-radius: 0 0 10px 10px;
    background-size: 200% 100%;
    animation: stage-progress-sweep 1.4s linear infinite;
}
@keyframes stage-progress-sweep {
    0%   { background-position: -100% 0; }
    100% { background-position: 100% 0; }
}

/* Every stage card softly slides in on first render. Cheap polish that
   makes the pipeline feel responsive on the first page load. */
.stage {
    animation: stage-fade-in 0.35s ease-out both;
}
.stage:nth-child(1) { animation-delay: 0.00s; }
.stage:nth-child(2) { animation-delay: 0.06s; }
.stage:nth-child(3) { animation-delay: 0.12s; }
.stage:nth-child(4) { animation-delay: 0.18s; }
.stage:nth-child(5) { animation-delay: 0.24s; }
@keyframes stage-fade-in {
    0%   { opacity: 0; transform: translateY(4px); }
    100% { opacity: 1; transform: translateY(0); }
}

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
    font-size: 1rem; font-weight: 700; color: var(--text);
    line-height: 1.25;
}
.stage-sub {
    font-size: 0.72rem; color: var(--text-muted); margin-top: 0.25rem;
    line-height: 1.4;
}
.stage-model {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    margin-top: 0.55rem;
    padding: 0.22rem 0.55rem;
    background: rgba(34, 211, 238, 0.12);
    border: 1px solid rgba(34, 211, 238, 0.35);
    border-radius: 999px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem;
    color: var(--accent);
    letter-spacing: 0.02em;
    width: fit-content;
    font-weight: 600;
}
.stage-model-dot {
    width: 5px; height: 5px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 6px var(--accent);
}
.stage-tokens {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.35rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.6rem;
    color: var(--text-muted);
}
.stage-tokens-in  { color: var(--text-muted); }
.stage-tokens-out { color: var(--green); }
.stage.done .stage-model {
    background: rgba(74, 222, 128, 0.07);
    border-color: rgba(74, 222, 128, 0.2);
    color: var(--green);
}
.stage.done .stage-model-dot { background: var(--green); box-shadow: 0 0 6px var(--green); }

/* V2 dotted-rail pipeline (`pipeline-wrap` / `pl-stage`). Available for
   any future use; kept in case the app wants to swap timelines later. */
.pipeline-wrap {
    background: var(--bg-elev-1);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1.5rem;
    transition: opacity 0.25s ease;
}
.pipeline-wrap.is-idle {
    opacity: 0.55;
    background: var(--bg-elev-2);
}
.pl-stage {
    position: relative;
    z-index: 1;
    text-align: center;
}
.pl-stage .pl-dot {
    width: 32px; height: 32px; border-radius: 50%;
    background: var(--bg-elev-2);
    border: 2px solid var(--border-strong);
    margin: 0 auto 0.6rem auto;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.72rem; font-weight: 700; color: var(--text-muted);
}
.pl-stage.active .pl-dot {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--bg);
    box-shadow: 0 0 0 4px var(--accent-glow), 0 0 24px var(--accent-glow);
    animation: pl-pulse 1.4s ease-in-out infinite;
}
.pl-stage.done .pl-dot {
    background: var(--bg-elev-2);
    border-color: var(--accent);
    color: var(--accent);
}
@keyframes pl-pulse {
    0%, 100% { box-shadow: 0 0 0 4px var(--accent-glow), 0 0 18px var(--accent-glow); }
    50%      { box-shadow: 0 0 0 6px var(--accent-glow), 0 0 30px var(--accent-glow); }
}
.pl-label {
    font-size: 0.82rem; font-weight: 600; color: var(--text);
    margin-bottom: 0.2rem;
}
.pl-sub {
    font-size: 0.72rem; color: var(--text-faint);
}
"""

# --------------------------------------------------------------- KPI / cards
_KPI_CSS = """
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
.kpi.amber  .kpi-value { color: var(--amber); }
.kpi.rose   .kpi-value { color: var(--rose); }
.kpi.green  .kpi-value { color: var(--green); }
"""

# --------------------------------------------------------------- empty state
_EMPTY_CSS = """
.empty-state {
    padding: 2.5rem 2rem;
    background: var(--bg-elev-1);
    border: 1px dashed var(--border-strong);
    border-radius: 14px;
    text-align: center;  /* overridden to left by the explainer-card variant below */
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

/* ===== Home-screen explainer card (replaces the pre-filled
   "Selected inputs" block) — ported from UI-smart-backlog-assistant.
   These classes intentionally use the `empty-state-*` namespace so they
   don't collide with the legacy `.empty-step*` rules above; both ship
   so either layout can be invoked from app.py. */
.empty-state.explainer-card {
    margin-top: 0.5rem;
    margin-bottom: 1.5rem;
    padding: 1.6rem 1.8rem 1.4rem;
    background: linear-gradient(135deg, var(--bg-elev-1) 0%, var(--bg-elev-2) 100%);
    border: 1px solid var(--border);
    border-radius: 16px;
    text-align: left;
}
.empty-state-eyebrow {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 0.6rem;
}
.empty-state-title {
    font-family: 'Inter', sans-serif;
    font-size: 1.35rem;
    font-weight: 700;
    color: var(--text);
    line-height: 1.25;
    margin-bottom: 0.35rem;
}
.empty-state-subtitle {
    font-size: 0.9rem;
    color: var(--text-muted);
    line-height: 1.5;
    margin-bottom: 1.3rem;
}
.empty-state-subtitle strong {
    color: var(--accent);
}
.empty-step-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 0.85rem;
}
/* Re-style step cards to match the reference (transition + hover). */
.empty-step {
    transition: border-color 0.15s ease;
}
.empty-step:hover {
    border-color: var(--border-strong);
}

/* ===== Main-canvas primary CTA (Synthesize / ANALYZE) ===== */
.main-cta-wrap {
    margin: 1.4rem 0 0.6rem 0;
}
.main-cta-wrap .stButton button {
    background: linear-gradient(135deg, var(--accent) 0%, #4f8fff 100%) !important;
    color: #0a0e1a !important;
    border: 1px solid var(--accent) !important;
    font-weight: 800 !important;
    font-size: 1.2rem !important;
    letter-spacing: 0.08em !important;
    padding: 1.2rem 2rem !important;
    border-radius: 14px !important;
    box-shadow: 0 8px 32px var(--accent-glow), 0 0 0 1px var(--accent) !important;
    transition: all 0.18s ease !important;
    min-height: 3.4rem !important;
}
.main-cta-wrap .stButton button:hover:not(:disabled) {
    transform: translateY(-1px) !important;
    box-shadow: 0 14px 40px var(--accent-glow), 0 0 0 1px var(--accent) !important;
}
.main-cta-wrap .stButton button:disabled {
    opacity: 0.4 !important;
    cursor: not-allowed !important;
}
"""

# ---------------------------------------------------- epic / story cards
_STORY_CSS = """
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
.pri-high   {
    background: var(--rose-glow);  color: var(--rose);
    border: 1px solid rgba(251,113,133,.4);
    position: relative;
}
/* Small pulsing dot next to High priority. Pure visual cue — draws the
   eye to the must-ship items without changing layout. */
.pri-high::before {
    content: "";
    display: inline-block;
    width: 5px; height: 5px; border-radius: 999px;
    background: var(--rose);
    margin-right: 0.35rem;
    vertical-align: middle;
    box-shadow: 0 0 6px var(--rose);
    animation: pri-pulse 2.2s ease-in-out infinite;
}
@keyframes pri-pulse {
    0%, 100% { opacity: 0.85; }
    50%      { opacity: 0.35; }
}
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

/* Summary card (V2 pattern — used above the epics list). */
.summary-card {
    background: linear-gradient(180deg, var(--bg-elev-1) 0%, var(--bg-elev-2) 100%);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 12px;
    padding: 1.1rem 1.35rem;
    margin-bottom: 1.2rem;
    font-size: 0.92rem;
    line-height: 1.55;
    color: var(--text);
}
.summary-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--accent);
    margin-bottom: 0.5rem;
}
"""

# ---------------------------------------------- findings (gap/conflict/dup)
_FINDING_CSS = """
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
"""

# --------------------------------------------------------------- run meta
_RUN_META_CSS = """
.run-meta {
    display: flex; flex-wrap: wrap; gap: 0.5rem 1.1rem; align-items: center;
    padding: 0.55rem 0.85rem;
    background: var(--bg-elev-1);
    border: 1px solid var(--border); border-radius: 10px;
    font-size: 0.8rem; color: var(--text);
    margin-bottom: 1rem;
}
.run-meta-item {
    display: inline-flex; align-items: center; gap: 0.4rem;
}
.run-meta-icon {
    font-size: 0.95rem; line-height: 1;
    color: var(--accent);
}
.run-meta-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-faint);
    margin-right: 0.25rem;
}
.run-meta-sep { color: var(--text-faint); opacity: 0.55; }
.run-meta strong {
    color: var(--text);
    margin-right: 0.35rem;
    font-weight: 600;
}
"""

# ------------------------------------------------------------- what's-next
_NEXT_CSS = """
.next-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.85rem;
    margin: 0.4rem 0 1.1rem 0;
    padding: 0.7rem 1rem;
    background: var(--bg-elev-2);
    border: 1px solid var(--border);
    border-radius: 12px;
}
.next-strip-label {
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-faint);
}
.next-strip-items {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    flex: 1;
}
.next-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.32rem 0.7rem;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 999px;
    font-size: 0.76rem;
    color: var(--text);
}
.next-chip-violet {
    color: var(--violet);
    border-color: rgba(167, 139, 250, 0.4);
    background: var(--violet-glow);
    font-weight: 600;
}
.next-chip-amber {
    color: var(--amber);
    border-color: rgba(251, 191, 36, 0.4);
    background: var(--amber-glow);
    font-weight: 600;
}
.next-chip-icon {
    font-size: 0.85rem;
    line-height: 1;
    opacity: 0.85;
}

/* Sidebar preset buttons (Free / Balanced / Premium) live in a narrow
   3-col block. Force single-line labels and visible dark-on-dark styling
   so all three are clearly readable. */
section[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    padding: 0.5rem 0.5rem !important;
    font-size: 0.82rem !important;
    min-width: 0 !important;
}
/* Make inactive (secondary) preset buttons clearly visible against the
   sidebar's near-black background. */
section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"] {
    background: rgba(255, 255, 255, 0.04) !important;
    border: 1px solid rgba(255, 255, 255, 0.18) !important;
    color: var(--text) !important;
}
section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"]:hover {
    background: rgba(255, 255, 255, 0.08) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
}

/* Hallucination guardrail PASS card — shown when a deliberately off-topic
   source (named `hallucination_check.*`) correctly produces zero stories. */
.guardrail-pass {
    margin: 0.5rem 0 1.2rem 0;
    padding: 1.1rem 1.3rem;
    background: rgba(52, 211, 153, 0.10);
    border: 1px solid rgba(52, 211, 153, 0.35);
    border-left: 4px solid #34d399;
    border-radius: 12px;
    color: var(--text);
}
.guardrail-pass-tag {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #34d399;
    margin-bottom: 0.55rem;
}
.guardrail-pass-title {
    font-size: 0.98rem;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 0.55rem;
}
.guardrail-pass-body {
    font-size: 0.85rem;
    color: var(--text-muted);
    line-height: 1.55;
}

/* "What's next" action row — styled label above the Streamlit button row. */
.next-strip-label-row {
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-faint);
    margin: 0.6rem 0 0.45rem 0;
    padding: 0 0.1rem;
}
.next-action-row { display: none; }

/* -------- Global Streamlit button theming (dark-on-dark) ---------
   Streamlit secondary buttons default to a light-theme palette that's
   invisible against our dark background. We re-skin all buttons to the
   project palette. Primary buttons keep the accent. */
div[data-testid="stButton"] > button,
div[data-testid="stDownloadButton"] > button {
    background: var(--bg-card) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    font-weight: 500 !important;
    transition: all 0.15s ease !important;
    box-shadow: none !important;
}
div[data-testid="stButton"] > button:hover,
div[data-testid="stDownloadButton"] > button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--bg-elev-2) !important;
    transform: translateY(-1px);
}
div[data-testid="stButton"] > button:focus,
div[data-testid="stDownloadButton"] > button:focus {
    box-shadow: 0 0 0 2px rgba(110, 168, 254, 0.35) !important;
    outline: none !important;
}

/* Primary buttons — violet pill (Compare in chips row, anything else
   that uses type="primary"). */
div[data-testid="stButton"] > button[kind="primary"] {
    background: rgba(167, 139, 250, 0.16) !important;
    border: 1px solid rgba(167, 139, 250, 0.5) !important;
    color: var(--violet) !important;
    font-weight: 600 !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    background: rgba(167, 139, 250, 0.24) !important;
    border-color: rgba(167, 139, 250, 0.7) !important;
    color: var(--violet) !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(167, 139, 250, 0.2) !important;
}

/* Pill shape for the "What's next" chip buttons specifically. The label
   row sits directly above the horizontal block of buttons; this rule
   matches buttons inside the very next sibling stHorizontalBlock. */
.next-strip-label-row + div[data-testid="stVerticalBlock"] div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button,
.next-strip-label-row ~ div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button {
    border-radius: 999px !important;
    padding: 0.5rem 1rem !important;
    font-size: 0.82rem !important;
}
"""

# -------------------------------------------------- duplicate diff modal
_DUP_DIFF_CSS = """
.dup-pair {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 1rem;
    align-items: stretch;
    margin: 0.8rem 0;
}
.dup-side {
    background: var(--bg-elev-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0.95rem 1.05rem;
}
.dup-side.new      { border-left: 3px solid var(--accent); }
.dup-side.existing { border-left: 3px solid var(--violet); }
.dup-side-label {
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-weight: 600;
    color: var(--text-faint);
    margin-bottom: 0.4rem;
}
.dup-side.new .dup-side-label      { color: var(--accent); }
.dup-side.existing .dup-side-label { color: var(--violet); }
.dup-side-title {
    font-size: 0.96rem;
    font-weight: 600;
    line-height: 1.3;
    margin-bottom: 0.45rem;
    color: var(--text);
}
.dup-side-desc {
    font-size: 0.82rem;
    color: var(--text-muted);
    line-height: 1.5;
}
.dup-side-missing {
    font-size: 0.82rem;
    color: var(--text-faint);
    font-style: italic;
}

/* Word-level diff highlight badges. Green for added (only in new),
   amber for removed (only in existing). Matches V2's diff palette. */
.dup-diff-add {
    background: rgba(52, 211, 153, 0.18);
    color: #6ee7b7;
    border-radius: 3px;
    padding: 0 2px;
}
.dup-diff-del {
    background: rgba(251, 191, 36, 0.18);
    color: #fcd34d;
    border-radius: 3px;
    padding: 0 2px;
    text-decoration: line-through;
    text-decoration-color: rgba(252, 211, 77, 0.6);
}
.dup-diff-legend {
    display: flex; flex-wrap: wrap; gap: 1rem;
    margin: 0 0 0.75rem 0;
    font-size: 0.74rem; color: var(--text-muted);
}
.dup-diff-legend-item {
    display: inline-flex; align-items: center; gap: 0.4rem;
}
.dup-vs {
    align-self: center;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-faint);
}
.dup-reason {
    background: var(--bg-elev-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.7rem 0.95rem;
    font-size: 0.84rem;
    color: var(--text);
    margin-bottom: 1.4rem;
    line-height: 1.5;
}
.dup-reason .conf-tag {
    display: inline-block;
    padding: 0.12rem 0.55rem;
    border-radius: 999px;
    font-size: 0.65rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    background: var(--violet-glow);
    color: var(--violet);
    margin-right: 0.5rem;
}
"""

# ---------------------------------------------------- run-history dialog
_HISTORY_CSS = """
.rh-card {
    background: var(--bg-elev-2);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0.85rem 1rem;
    margin-bottom: 0.55rem;
}
.rh-card-top {
    display: flex; justify-content: space-between; align-items: center;
    gap: 0.85rem;
}
.rh-card-date {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.74rem;
    color: var(--text-faint);
    letter-spacing: 0.06em;
}
.rh-card-source {
    font-size: 0.92rem; font-weight: 600; color: var(--text);
    line-height: 1.3;
    margin-top: 0.15rem;
}
.rh-card-meta {
    display: flex; flex-wrap: wrap; gap: 0.4rem;
    margin-top: 0.45rem;
}
.rh-chip {
    display: inline-flex; align-items: center;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.66rem; font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.16rem 0.55rem;
}
.rh-chip-accent {
    color: var(--accent);
    border-color: rgba(34, 211, 238, 0.35);
    background: var(--accent-glow);
}
.rh-chip-current {
    color: var(--violet);
    border-color: rgba(167, 139, 250, 0.45);
    background: var(--violet-glow);
}
.rh-card-current {
    border-color: var(--violet) !important;
    box-shadow: 0 0 0 1px rgba(167, 139, 250, 0.25);
}
/* Small fact strip at the top of the history dialog */
.rh-summary-chip {
    flex: 1;
    background: var(--bg-elev-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0.55rem 0.75rem;
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text);
    text-align: center;
}
.rh-summary-chip span {
    display: block;
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-faint);
    margin-bottom: 0.15rem;
}
"""

# ----------------------------------------------------------------- assembly


def get_css() -> str:
    """Return the full CSS payload wrapped in a <style> tag.

    Call once at the top of app.py:
        from ui.styling import get_css
        st.markdown(get_css(), unsafe_allow_html=True)
    """
    parts = [
        _TOKENS_CSS,
        _SHELL_CSS,
        _HEADER_CSS,
        _PIPELINE_CSS,
        _KPI_CSS,
        _EMPTY_CSS,
        _STORY_CSS,
        _FINDING_CSS,
        _RUN_META_CSS,
        _NEXT_CSS,
        _DUP_DIFF_CSS,
        _HISTORY_CSS,
    ]
    return "<style>\n" + "\n".join(parts) + "\n</style>"
