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
import queue as _queue
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class _PipelineCancelled(Exception):
    """Raised inside the run thread when the user clicks Cancel."""

import streamlit as st
from dotenv import load_dotenv

# -------------------------------------------------------- bootstrap

ROOT = Path(__file__).resolve().parent
# Respect OUTPUTS_DIR env var so Azure deployments write synthesis outputs
# to the mounted Azure Files share instead of the ephemeral container layer.
OUTPUTS_BASE = Path(os.environ.get("OUTPUTS_DIR", str(ROOT / "outputs")))
# .env is for local development only. In production, set env vars via the
# deployment platform (Fly.io secrets, AWS Secrets Manager, etc.).
load_dotenv(ROOT / ".env")

# Same Atlassian tenant — same credentials work for Jira and Confluence.
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
from startup_check import check_required_secrets, get_configured_integrations, check_python_version  # noqa: E402
from rate_limiter import check_rate_limit, get_usage_summary, RateLimitError  # noqa: E402
from feature_flags import FeatureFlags  # noqa: E402

# Load feature flags once per session. Cached in session_state so editing
# them in the Admin panel and clicking Save triggers a reload via st.rerun().
if "feature_flags" not in st.session_state:
    st.session_state.feature_flags = FeatureFlags.load()
_ff: FeatureFlags = st.session_state.feature_flags

# -------------------------------------------------------- startup validation
# Runs once per Streamlit session. Hard-fails on missing ANTHROPIC_API_KEY;
# surfaces warnings for partial optional configs as an info banner.
_startup_warnings: list[str] = check_python_version()
try:
    _startup_warnings += check_required_secrets()
except RuntimeError as _startup_err:
    st.error(f"**Configuration error:** {_startup_err}")
    st.info("Set the required environment variables and restart the app. See `.env.example`.")
    st.stop()

# -------------------------------------------------------- Ollama auto-start
# Start Ollama in the background if any stage uses a local model and the
# server isn't already running. Runs once per session — idempotent.
if "ollama_started" not in st.session_state:
    import shutil as _shutil_startup
    if _shutil_startup.which("ollama"):
        try:
            from ollama_manager import ensure_running as _ensure_ollama
            _ok, _msg = _ensure_ollama(timeout=30)
            st.session_state.ollama_started = _ok
            st.session_state.ollama_msg = _msg
        except Exception:  # noqa: BLE001
            st.session_state.ollama_started = False
            st.session_state.ollama_msg = "Ollama manager unavailable."
    else:
        st.session_state.ollama_started = False
        st.session_state.ollama_msg = "Ollama not installed."

st.set_page_config(
    page_title="Backlog Synthesizer · Accenture",
    page_icon="🟣",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Inject CSS immediately after page config so the login page is styled ──
# This must happen BEFORE the auth check which may call st.stop().
st.markdown(get_css(), unsafe_allow_html=True)

# -------------------------------------------------------- authentication
# Priority order:
#   1. AUTH_DISABLED=1              → skip auth entirely (local dev)
#   2. ENTRA_TENANT_ID set          → Microsoft Entra ID SSO (enterprise)
#   3. config/auth.yaml exists      → streamlit-authenticator (username/password fallback)

_auth_disabled = os.environ.get("AUTH_DISABLED", "").strip() == "1"

# Guard: AUTH_DISABLED must never be set alongside real Entra credentials —
# that combination would leave a production deployment fully open.
if _auth_disabled and os.environ.get("ENTRA_TENANT_ID", "").strip():
    st.error(
        "**Misconfiguration:** `AUTH_DISABLED=1` cannot be set when "
        "`ENTRA_TENANT_ID` is also present. Remove `AUTH_DISABLED` in production."
    )
    st.stop()
_current_user: str = "local"
_current_role: str = "admin"
_authenticator = None

sys.path.insert(0, str(ROOT / "src"))
from entra_auth import is_enabled as _entra_enabled, get_auth_url, exchange_code_for_token, parse_user  # noqa: E402

if not _auth_disabled:

    if _entra_enabled():
        # ── Entra ID SSO path ─────────────────────────────────────────────────
        # OAuth2 authorization code flow via MSAL.
        # Step 1: check if Microsoft just redirected back with ?code=
        _query = st.query_params
        _auth_code = _query.get("code", "")
        _auth_error = _query.get("error", "")

        if _auth_error:
            st.error(f"Microsoft login error: {_query.get('error_description', _auth_error)}")
            st.stop()

        if _auth_code and "entra_user" not in st.session_state:
            # Exchange the one-time code for a token
            with st.spinner("Signing in with Microsoft…"):
                _token_result = exchange_code_for_token(_auth_code)
            if "error" in _token_result:
                st.error(f"Token exchange failed: {_token_result.get('error_description', _token_result['error'])}")
                if st.button("Try again"):
                    st.query_params.clear()
                    st.rerun()
                st.stop()
            # Store user info in session state and clear the ?code= from URL
            st.session_state["entra_user"] = parse_user(_token_result)
            st.query_params.clear()
            st.rerun()

        if "entra_user" not in st.session_state:
            _login_url = get_auth_url()
            st.markdown("""
            <style>
            .stApp, [data-testid="stAppViewContainer"] {
                background-color: #060c1e !important;
                background-image: none !important;
            }
            section[data-testid="stSidebar"] { display:none !important; }
            header[data-testid="stHeader"] { display:none !important; }
            .main .block-container, [data-testid="stMainBlockContainer"] {
                padding:0 !important; max-width:100% !important;
            }
            @keyframes card-border {
                0%,100% { background-position:0% 50%; }
                50%      { background-position:100% 50%; }
            }
            </style>
            """, unsafe_allow_html=True)

            _ns_star_sm = (
                '<svg viewBox="0 0 28 28" fill="none" width="18" height="18">'
                '<polygon points="14,2 17.2,10.4 26,10.8 19.4,15.8 21.6,24.4 14,19.8 6.4,24.4 8.6,15.8 2,10.8 10.8,10.4"'
                ' fill="none" stroke="#F5A623" stroke-width="1.8" stroke-linejoin="round"/>'
                '</svg>'
            )
            _ns_star_card = (
                '<svg viewBox="0 0 28 28" fill="none" width="24" height="24">'
                '<polygon points="14,2 17.2,10.4 26,10.8 19.4,15.8 21.6,24.4 14,19.8 6.4,24.4 8.6,15.8 2,10.8 10.8,10.4"'
                ' fill="none" stroke="#a78bfa" stroke-width="1.8" stroke-linejoin="round"/>'
                '</svg>'
            )

            # ── radial neural-brain SVG (3 rings, center 170,170, viewBox 340x340) ─
            # Inline transform-origin on animation elements so they use this SVG's centre.
            _brain = (
                '<svg viewBox="0 0 340 340" fill="none" xmlns="http://www.w3.org/2000/svg" '
                'style="width:min(75%,440px);height:auto;display:block;">'
                '<defs>'
                '<filter id="bf-g" x="-60%" y="-60%" width="220%" height="220%">'
                '<feGaussianBlur stdDeviation="6" result="b"/>'
                '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
                '</filter>'
                '<filter id="bf-gs" x="-80%" y="-80%" width="260%" height="260%">'
                '<feGaussianBlur stdDeviation="2.8" result="b"/>'
                '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
                '</filter>'
                '<radialGradient id="bf-hub" cx="50%" cy="50%" r="50%">'
                '<stop offset="0%" stop-color="#f0f4ff"/><stop offset="100%" stop-color="#c084fc"/>'
                '</radialGradient>'
                '</defs>'
                # outer-ring adjacents (faint, dim purple)
                '<line x1="325" y1="170" x2="304" y2="248" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="248" y1="304" x2="170" y2="325" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="93" y1="304" x2="36" y2="248" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="15" y1="170" x2="36" y2="92" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="93" y1="36" x2="170" y2="15" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="248" y1="36" x2="304" y2="92" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                # mid→outer
                '<line x1="267" y1="210" x2="325" y2="170" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="267" y1="210" x2="304" y2="248" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="210" y1="267" x2="304" y2="248" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="210" y1="267" x2="248" y2="304" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="130" y1="267" x2="170" y2="325" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="130" y1="267" x2="93" y2="304" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="73" y1="210" x2="93" y2="304" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="73" y1="210" x2="36" y2="248" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="73" y1="130" x2="15" y2="170" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="73" y1="130" x2="36" y2="92" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="130" y1="73" x2="36" y2="92" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="130" y1="73" x2="93" y2="36" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="210" y1="73" x2="170" y2="15" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="210" y1="73" x2="248" y2="36" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="267" y1="130" x2="304" y2="92" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                '<line x1="267" y1="130" x2="325" y2="170" stroke="rgba(109,40,217,0.22)" stroke-width="0.8"/>'
                # mid-ring adjacents
                '<line x1="267" y1="210" x2="210" y2="267" stroke="rgba(167,139,250,0.25)" stroke-width="0.9"/>'
                '<line x1="130" y1="267" x2="73" y2="210" stroke="rgba(167,139,250,0.25)" stroke-width="0.9"/>'
                '<line x1="73" y1="130" x2="130" y2="73" stroke="rgba(167,139,250,0.25)" stroke-width="0.9"/>'
                '<line x1="210" y1="73" x2="267" y2="130" stroke="rgba(167,139,250,0.25)" stroke-width="0.9"/>'
                # inner→mid
                '<line x1="225" y1="170" x2="267" y2="130" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="225" y1="170" x2="267" y2="210" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="198" y1="218" x2="267" y2="210" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="198" y1="218" x2="210" y2="267" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="143" y1="218" x2="210" y2="267" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="143" y1="218" x2="130" y2="267" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="115" y1="170" x2="73" y2="210" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="115" y1="170" x2="73" y2="130" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="143" y1="122" x2="73" y2="130" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="143" y1="122" x2="130" y2="73" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="198" y1="122" x2="210" y2="73" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                '<line x1="198" y1="122" x2="267" y2="130" stroke="rgba(167,139,250,0.32)" stroke-width="1"/>'
                # inner hexagon ring
                '<line x1="225" y1="170" x2="198" y2="218" stroke="rgba(167,139,250,0.48)" stroke-width="1.1"/>'
                '<line x1="198" y1="218" x2="143" y2="218" stroke="rgba(167,139,250,0.48)" stroke-width="1.1"/>'
                '<line x1="143" y1="218" x2="115" y2="170" stroke="rgba(167,139,250,0.48)" stroke-width="1.1"/>'
                '<line x1="115" y1="170" x2="143" y2="122" stroke="rgba(167,139,250,0.48)" stroke-width="1.1"/>'
                '<line x1="143" y1="122" x2="198" y2="122" stroke="rgba(167,139,250,0.48)" stroke-width="1.1"/>'
                '<line x1="198" y1="122" x2="225" y2="170" stroke="rgba(167,139,250,0.48)" stroke-width="1.1"/>'
                # hub spokes (brightest)
                '<line x1="170" y1="170" x2="225" y2="170" stroke="rgba(167,139,250,0.7)" stroke-width="1.4"/>'
                '<line x1="170" y1="170" x2="198" y2="218" stroke="rgba(167,139,250,0.7)" stroke-width="1.4"/>'
                '<line x1="170" y1="170" x2="143" y2="218" stroke="rgba(167,139,250,0.7)" stroke-width="1.4"/>'
                '<line x1="170" y1="170" x2="115" y2="170" stroke="rgba(167,139,250,0.7)" stroke-width="1.4"/>'
                '<line x1="170" y1="170" x2="143" y2="122" stroke="rgba(167,139,250,0.7)" stroke-width="1.4"/>'
                '<line x1="170" y1="170" x2="198" y2="122" stroke="rgba(167,139,250,0.7)" stroke-width="1.4"/>'
                # outer ring nodes
                '<circle cx="325" cy="170" r="2.5" fill="rgba(109,40,217,0.48)"/>'
                '<circle cx="304" cy="248" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="248" cy="304" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="170" cy="325" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="93" cy="304" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="36" cy="248" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="15" cy="170" r="2.5" fill="rgba(109,40,217,0.48)"/>'
                '<circle cx="36" cy="92" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="93" cy="36" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="170" cy="15" r="2.5" fill="rgba(109,40,217,0.48)"/>'
                '<circle cx="248" cy="36" r="2.5" fill="rgba(109,40,217,0.42)"/>'
                '<circle cx="304" cy="92" r="2.5" fill="rgba(109,40,217,0.48)"/>'
                # mid ring nodes
                '<circle cx="267" cy="210" r="3.5" fill="rgba(109,40,217,0.8)" filter="url(#bf-gs)"/>'
                '<circle cx="210" cy="267" r="3.5" fill="rgba(109,40,217,0.78)" filter="url(#bf-gs)"/>'
                '<circle cx="130" cy="267" r="3.5" fill="rgba(109,40,217,0.75)" filter="url(#bf-gs)"/>'
                '<circle cx="73" cy="210" r="3.5" fill="rgba(109,40,217,0.75)" filter="url(#bf-gs)"/>'
                '<circle cx="73" cy="130" r="3.5" fill="rgba(109,40,217,0.75)" filter="url(#bf-gs)"/>'
                '<circle cx="130" cy="73" r="3.5" fill="rgba(109,40,217,0.75)" filter="url(#bf-gs)"/>'
                '<circle cx="210" cy="73" r="3.5" fill="rgba(109,40,217,0.78)" filter="url(#bf-gs)"/>'
                '<circle cx="267" cy="130" r="3.5" fill="rgba(109,40,217,0.8)" filter="url(#bf-gs)"/>'
                # inner ring nodes (bright violet, larger)
                '<circle cx="225" cy="170" r="5.5" fill="rgba(167,139,250,0.95)" filter="url(#bf-gs)"/>'
                '<circle cx="198" cy="218" r="5.5" fill="rgba(167,139,250,0.92)" filter="url(#bf-gs)"/>'
                '<circle cx="143" cy="218" r="5.5" fill="rgba(167,139,250,0.92)" filter="url(#bf-gs)"/>'
                '<circle cx="115" cy="170" r="5.5" fill="rgba(167,139,250,0.95)" filter="url(#bf-gs)"/>'
                '<circle cx="143" cy="122" r="5.5" fill="rgba(167,139,250,0.92)" filter="url(#bf-gs)"/>'
                '<circle cx="198" cy="122" r="5.5" fill="rgba(167,139,250,0.92)" filter="url(#bf-gs)"/>'
                # animated pulse rings (inline transform-origin so scale works from node centre)
                '<circle cx="225" cy="170" r="5.5" fill="none" stroke="rgba(167,139,250,0.85)" stroke-width="1.2" '
                'style="animation:npulse 3.4s ease-in-out infinite;transform-origin:225px 170px;"/>'
                '<circle cx="115" cy="170" r="5.5" fill="none" stroke="rgba(167,139,250,0.75)" stroke-width="1.2" '
                'style="animation:npulse 3.4s ease-in-out 1.2s infinite;transform-origin:115px 170px;"/>'
                '<circle cx="198" cy="122" r="5.5" fill="none" stroke="rgba(167,139,250,0.7)" stroke-width="1" '
                'style="animation:npulse 3.4s ease-in-out 0.7s infinite;transform-origin:198px 122px;"/>'
                '<circle cx="143" cy="218" r="5.5" fill="none" stroke="rgba(167,139,250,0.65)" stroke-width="1" '
                'style="animation:npulse 3.4s ease-in-out 1.9s infinite;transform-origin:143px 218px;"/>'
                # hub glow rings (inline transform-origin = brain centre)
                '<circle cx="170" cy="170" r="34" fill="rgba(167,139,250,0.06)" '
                'style="animation:hring 3.6s ease-in-out infinite;transform-origin:170px 170px;"/>'
                '<circle cx="170" cy="170" r="22" fill="rgba(167,139,250,0.1)" '
                'style="animation:hring 3.6s ease-in-out 1.8s infinite;transform-origin:170px 170px;"/>'
                # hub node
                '<circle cx="170" cy="170" r="12" fill="url(#bf-hub)" filter="url(#bf-g)"/>'
                '<circle cx="170" cy="170" r="6.5" fill="rgba(255,255,255,0.97)"/>'
                '</svg>'
            )

            # ── Layout: ALL inline styles on position:fixed wrapper + children ─────
            # Inline styles on position:fixed bypass Streamlit's block container,
            # guaranteeing the flex row renders correctly.
            _PAGE = ('position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:9999;'
                     'background:#060c1e;overflow:hidden;'
                     'display:flex;flex-direction:row;align-items:stretch;')
            _LEFT = ('flex:0 0 58%;display:flex;flex-direction:column;'
                     'padding:40px 40px 28px 60px;position:relative;'
                     'border-right:1px solid rgba(167,139,250,0.09);')
            _RIGHT = 'flex:1;display:flex;align-items:center;justify-content:center;padding:0 48px;'
            # gradient-border wrapper: 1.5px animated gradient + inner solid card
            _CARD_WRAP = ('width:100%;max-width:400px;padding:1.5px;border-radius:22px;'
                          'background:linear-gradient(135deg,#6d28d9,#818cf8,#a78bfa,#4f46e5,#6d28d9);'
                          'background-size:300% 300%;'
                          'animation:card-border 5s ease infinite;'
                          'box-shadow:0 8px 64px rgba(109,40,217,0.28);')
            _CARD  = ('width:100%;padding:38px 38px 34px;'
                      'background:rgba(7,13,32,0.97);backdrop-filter:blur(32px);'
                      'border-radius:21px;')
            _BTN   = ('display:flex;align-items:center;justify-content:center;gap:10px;'
                      'width:100%;padding:13px 0;margin:4px 0 0;border-radius:10px;'
                      'background:linear-gradient(135deg,#6d28d9,#4f46e5);'
                      'color:#fff;font-size:14px;font-weight:600;letter-spacing:0.02em;'
                      'text-decoration:none;cursor:pointer;'
                      'box-shadow:0 4px 18px rgba(109,40,217,0.45);')
            _DOT   = 'display:inline-block;width:3px;height:3px;border-radius:50%;background:rgba(167,139,250,0.4);'

            st.markdown(
                f'<div style="{_PAGE}">'

                # ambient orbs — CSS animation classes work inside position:fixed
                f'<div class="login-orb login-orb-1"></div>'
                f'<div class="login-orb login-orb-2"></div>'
                f'<div class="login-orb login-orb-3"></div>'

                # ── LEFT PANEL ─────────────────────────────────────────────────────
                f'<div style="{_LEFT}">'

                # logo row
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:24px;">'
                f'{_ns_star_sm}'
                f'<span style="color:#fff;font-size:14px;font-weight:700;letter-spacing:0.02em;">NorthStar Retail</span>'
                f'<span style="color:rgba(255,255,255,0.18);margin:0 6px;font-size:13px;">|</span>'
                f'<span style="color:#a78bfa;font-size:12px;font-weight:700;letter-spacing:0.06em;">accenture</span>'
                f'</div>'

                # AI badge
                f'<div style="display:inline-flex;align-items:center;gap:7px;'
                f'background:rgba(109,40,217,0.18);border:1px solid rgba(167,139,250,0.28);'
                f'border-radius:20px;padding:5px 14px;margin-bottom:14px;width:fit-content;">'
                f'<span style="width:6px;height:6px;border-radius:50%;background:#a78bfa;'
                f'display:inline-block;animation:bdot 2.2s ease-in-out infinite;flex-shrink:0;"></span>'
                f'<span style="color:#c4b5fd;font-size:11px;font-weight:600;letter-spacing:0.08em;'
                f'text-transform:uppercase;">AI&#8209;Powered Enterprise Platform</span>'
                f'</div>'

                # main title — 58px for impact
                f'<div style="font-size:58px;font-weight:800;line-height:1.05;'
                f'letter-spacing:-0.04em;color:#f0f4ff;margin-bottom:14px;">'
                f'Backlog<br>'
                f'<span style="background:linear-gradient(135deg,#a78bfa 0%,#818cf8 45%,#c084fc 100%);'
                f'-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;">'
                f'Synthesizer</span>'
                f'</div>'

                # technical bullet lines — scannable by a tech architect in 5 seconds
                f'<div style="margin-bottom:20px;display:flex;flex-direction:column;gap:9px;">'
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<span style="color:#a78bfa;font-size:14px;flex-shrink:0;font-weight:700;">›</span>'
                f'<span style="font-size:12.5px;color:rgba(255,255,255,0.52);line-height:1.4;">'
                f'5&#8209;agent pipeline &nbsp;&middot;&nbsp; Parser &#8594; Constraint &#8594; Story &#8594; Epic &#8594; Gap'
                f'</span></div>'
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<span style="color:#a78bfa;font-size:14px;flex-shrink:0;font-weight:700;">›</span>'
                f'<span style="font-size:12.5px;color:rgba(255,255,255,0.52);line-height:1.4;">'
                f'MCP&#8209;native &nbsp;&middot;&nbsp; Jira + Confluence + GitHub live sync'
                f'</span></div>'
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<span style="color:#a78bfa;font-size:14px;flex-shrink:0;font-weight:700;">›</span>'
                f'<span style="font-size:12.5px;color:rgba(255,255,255,0.52);line-height:1.4;">'
                f'RS256 Entra SSO &nbsp;&middot;&nbsp; OTel traces &nbsp;&middot;&nbsp; SHA&#8209;256 audit chain'
                f'</span></div>'
                f'</div>'

                # stats
                f'<div style="display:flex;align-items:center;gap:0;margin-bottom:0;">'
                f'<div style="padding-right:24px;">'
                f'<div style="font-size:26px;font-weight:700;color:#a78bfa;line-height:1;">10x</div>'
                f'<div style="font-size:10px;color:rgba(255,255,255,0.38);letter-spacing:0.07em;'
                f'text-transform:uppercase;margin-top:3px;">Faster Backlog</div></div>'
                f'<div style="width:1px;height:36px;background:rgba(167,139,250,0.18);flex-shrink:0;"></div>'
                f'<div style="padding:0 24px;">'
                f'<div style="font-size:26px;font-weight:700;color:#a78bfa;line-height:1;">5</div>'
                f'<div style="font-size:10px;color:rgba(255,255,255,0.38);letter-spacing:0.07em;'
                f'text-transform:uppercase;margin-top:3px;">AI Agents</div></div>'
                f'<div style="width:1px;height:36px;background:rgba(167,139,250,0.18);flex-shrink:0;"></div>'
                f'<div style="padding-left:24px;">'
                f'<div style="font-size:26px;font-weight:700;color:#a78bfa;line-height:1;">100%</div>'
                f'<div style="font-size:10px;color:rgba(255,255,255,0.38);letter-spacing:0.07em;'
                f'text-transform:uppercase;margin-top:3px;">Auditable</div></div>'
                f'</div>'

                # neural brain SVG — fills remaining space, centred horizontally
                f'<div style="flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding-top:16px;">'
                f'{_brain}'
                f'</div>'

                # bottom footer
                f'<div style="font-size:11px;color:rgba(255,255,255,0.25);'
                f'letter-spacing:0.04em;padding-top:16px;flex-shrink:0;">'
                f'accenture &rsaquo; AI&#8209;First Agentic Solutions'
                f'</div>'

                f'</div>'  # end left panel

                # vertical divider line
                f'<div style="width:1px;flex-shrink:0;align-self:stretch;'
                f'background:linear-gradient(180deg,transparent 5%,rgba(167,139,250,0.14) 30%,'
                f'rgba(167,139,250,0.14) 70%,transparent 95%);"></div>'

                # ── RIGHT PANEL ────────────────────────────────────────────────────
                f'<div style="{_RIGHT}">'
                # animated gradient border wrapper
                f'<div style="{_CARD_WRAP}">'
                f'<div style="{_CARD}">'

                f'<div style="text-align:center;margin-bottom:8px;">{_ns_star_card}</div>'
                f'<div style="text-align:center;font-size:11px;font-weight:600;letter-spacing:0.14em;'
                f'color:rgba(167,139,250,0.7);text-transform:uppercase;margin-bottom:18px;">NorthStar Retail</div>'
                f'<div style="text-align:center;font-size:26px;font-weight:700;color:#f0f4ff;'
                f'letter-spacing:-0.02em;margin-bottom:8px;">Welcome back</div>'
                f'<div style="text-align:center;font-size:13px;color:rgba(255,255,255,0.44);'
                f'line-height:1.6;margin-bottom:24px;">'
                f'Sign in with your NorthStar Retail account<br>to access the backlog intelligence workspace.'
                f'</div>'
                f'<div style="height:1px;background:linear-gradient(90deg,transparent,'
                f'rgba(167,139,250,0.2),transparent);margin-bottom:22px;"></div>'

                f'<a href="{_login_url}" target="_self" style="{_BTN}">'
                f'<svg width="18" height="18" viewBox="0 0 21 21" xmlns="http://www.w3.org/2000/svg">'
                f'<rect x="1" y="1" width="9" height="9" fill="#f25022"/>'
                f'<rect x="11" y="1" width="9" height="9" fill="#7fba00"/>'
                f'<rect x="1" y="11" width="9" height="9" fill="#00a4ef"/>'
                f'<rect x="11" y="11" width="9" height="9" fill="#ffb900"/>'
                f'</svg>Continue with Microsoft'
                f'</a>'

                f'<div style="text-align:center;margin-top:14px;font-size:11px;'
                f'color:rgba(255,255,255,0.26);">For authorised NorthStar Retail employees only</div>'

                f'<div style="display:flex;align-items:center;justify-content:center;gap:8px;'
                f'margin-top:14px;font-size:11px;color:rgba(167,139,250,0.5);letter-spacing:0.03em;">'
                f'<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
                f'<path d="M12 2L20 7V12C20 17 16 21.4 12 23C8 21.4 4 17 4 12V7Z"/></svg>'
                f'Microsoft Entra ID<span style="{_DOT}"></span>Enterprise SSO<span style="{_DOT}"></span>Zero Trust'
                f'</div>'

                # tech stack badges — the things a tech architect immediately notices
                f'<div style="margin-top:20px;padding-top:16px;'
                f'border-top:1px solid rgba(167,139,250,0.08);">'
                f'<div style="text-align:center;font-size:10px;color:rgba(255,255,255,0.2);'
                f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;">Powered by</div>'
                f'<div style="display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap;">'
                f'<span style="background:rgba(109,40,217,0.18);border:1px solid rgba(167,139,250,0.22);'
                f'border-radius:6px;padding:3px 9px;font-size:10px;font-weight:600;color:#c4b5fd;'
                f'letter-spacing:0.03em;">Claude</span>'
                f'<span style="background:rgba(109,40,217,0.18);border:1px solid rgba(167,139,250,0.22);'
                f'border-radius:6px;padding:3px 9px;font-size:10px;font-weight:600;color:#c4b5fd;'
                f'letter-spacing:0.03em;">Gemini</span>'
                f'<span style="background:rgba(109,40,217,0.18);border:1px solid rgba(167,139,250,0.22);'
                f'border-radius:6px;padding:3px 9px;font-size:10px;font-weight:600;color:#c4b5fd;'
                f'letter-spacing:0.03em;">MCP Protocol</span>'
                f'</div>'
                f'</div>'

                f'</div>'  # end inner card
                f'</div>'  # end gradient border wrapper
                f'</div>'  # end right panel

                # demo disclaimer
                f'<div style="position:absolute;bottom:14px;left:0;right:0;text-align:center;'
                f'font-size:11px;color:rgba(255,255,255,0.2);letter-spacing:0.02em;">'
                f'Demo environment &mdash; NorthStar Retail Corp is a fictional client created for demonstration purposes only.'
                f'</div>'

                f'</div>',  # end page wrapper
                unsafe_allow_html=True,
            )
            st.stop()

        # User is signed in via Entra ID
        _entra_user   = st.session_state["entra_user"]
        _current_user = _entra_user.get("email", "unknown")
        _current_role = _entra_user.get("role", "viewer")
        _display_name = _entra_user.get("name") or _current_user

    else:
        # ── streamlit-authenticator fallback (username/password) ──────────────
        _auth_config_path = ROOT / "config" / "auth.yaml"
        if not _auth_config_path.exists():
            st.error(
                "Authentication config missing: `config/auth.yaml` not found. "
                "Set `AUTH_DISABLED=1` for local dev or configure Entra ID."
            )
            st.stop()
        try:
            import yaml
            import streamlit_authenticator as stauth
            with open(_auth_config_path) as _f:
                _auth_cfg = yaml.safe_load(_f)

            _all_passwords = [
                v.get("password", "")
                for v in (_auth_cfg.get("credentials", {}).get("usernames") or {}).values()
            ]
            if any(str(p).startswith("CHANGE_ME_") for p in _all_passwords):
                st.error("**Auth not configured:** `config/auth.yaml` has placeholder passwords.")
                st.info("Set `AUTH_DISABLED=1` for local dev or set `ENTRA_TENANT_ID` for SSO.")
                st.stop()

            _authenticator = stauth.Authenticate(
                _auth_cfg["credentials"],
                _auth_cfg["cookie"]["name"],
                _auth_cfg["cookie"]["key"],
                _auth_cfg["cookie"]["expiry_days"],
                auto_hash=False,
            )
            _login_result = _authenticator.login(location="main")
            if _login_result is not None:
                _auth_name, _auth_status, _auth_username = _login_result
            else:
                _auth_name     = st.session_state.get("name")
                _auth_status   = st.session_state.get("authentication_status")
                _auth_username = st.session_state.get("username")

            if _auth_status is False:
                st.error("Incorrect username or password.")
                st.stop()
            elif not _auth_status:
                st.info("Please log in to use the Backlog Synthesizer.")
                st.stop()

            _current_user = _auth_username or "unknown"
            _user_config  = ((_auth_cfg.get("credentials") or {})
                             .get("usernames", {}).get(_current_user, {}))
            _current_role = _user_config.get("role", "viewer")

        except ImportError:
            st.error(
                "**Dependency missing:** `streamlit-authenticator` is not installed. "
                "Run `pip install streamlit-authenticator` or configure Entra ID SSO."
            )
            st.stop()


def _is_admin() -> bool:
    return _current_role == "admin"


def _is_contributor() -> bool:
    return _current_role in ("admin", "contributor")


def _can_run() -> bool:
    """Contributors and admins can run synthesis; viewers cannot."""
    return _is_contributor()


def _can_push_jira() -> bool:
    # Contributors and admins can push to Jira.
    # Contributors always go through the mandatory approval gate;
    # admins can optionally bypass the confirmation checkbox.
    return _is_contributor()


def _can_use_premium_models() -> bool:
    return _is_admin()


def _can_use_live_atlassian() -> bool:
    return _is_admin()


# CSS already injected above (before auth) — no duplicate needed here.

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
    def _chars_of(selected, options: dict, upload) -> int:
        labels = selected if isinstance(selected, list) else ([selected] if selected else [])
        total = 0
        for lbl in labels:
            val = options.get(lbl)
            if val and val != "__upload__":
                try:
                    total += Path(str(val)).stat().st_size
                except OSError:
                    pass
        # Uploads are always combined with the selected samples now.
        ups = upload if isinstance(upload, list) else ([upload] if upload else [])
        total += sum(int(getattr(u, "size", 0) or 0) for u in ups)
        return total

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
                # Filter LLM placeholder values — "...", "null", etc. — so they
                # never reach the UI regardless of when the run was stored.
                _ph = {"...", "…", "null", "none", "n/a", "tbd", "unknown", "—", "-"}
                if quote.lower() in _ph:
                    quote = ""
                if speaker.lower() in _ph:
                    speaker = ""
                if quote:
                    attribution = f" — {_esc(speaker)}" if speaker else ""
                    # Collapsed by default — click "Evidence" label to expand.
                    # The quote is still there for audit/review; it just doesn't
                    # clutter the card in normal daily use.
                    ep_html.append(
                        f'<details style="margin:6px 0;">'
                        f'<summary style="cursor:pointer;font-size:11px;'
                        f'letter-spacing:0.06em;text-transform:uppercase;'
                        f'color:#64748b;list-style:none;display:flex;'
                        f'align-items:center;gap:4px;">'
                        f'<span style="font-size:9px;">▶</span>'
                        f'Evidence{attribution}'
                        f'</summary>'
                        f'<div style="border-left:3px solid #94a3b8;'
                        f'padding:6px 10px;margin:4px 0 0 0;color:#475569;'
                        f'font-style:italic;background:#f8fafc;border-radius:4px;">'
                        f'"{_esc(quote)}"'
                        f'</div>'
                        f'</details>'
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


def _gap_to_story(gap: dict) -> None:
    """Convert a gap into a user story and inject it into the session result."""
    res = st.session_state.get("result")
    if not res:
        return
    title = gap.get("title") or gap.get("description") or "Untitled gap"
    desc  = gap.get("description") or ""
    evid  = gap.get("evidence") or ""
    new_story = {
        "id":    f"ST-gap-{uuid.uuid4().hex[:4]}",
        "title": title,
        "user_story": f"As a product team, I want to address the identified gap — {title} — so that the product is complete and competitive.",
        "acceptance_criteria": [
            f"The gap is resolved: {desc}" if desc else f"Story addresses: {title}",
            "Acceptance criteria reviewed and approved by the squad lead.",
        ],
        "priority":       "Medium",
        "tags":           ["gap-identified"],
        "source_topic_id": "",
        "evidence":       [{"raw_quote": evid}] if evid else [],
    }
    epics = res.get("epics") or []
    gap_epic = next((e for e in epics if e.get("title") == "Gaps & Improvements"), None)
    if gap_epic is None:
        gap_epic = {
            "title":       "Gaps & Improvements",
            "description": "Stories created from detected gaps — capabilities implied by requirements but not in the existing backlog.",
            "stories":     [],
        }
        epics.append(gap_epic)
        res["epics"] = epics
    gap_epic.setdefault("stories", []).append(new_story)
    st.session_state.result = res
    st.session_state.epics_original = json.loads(json.dumps(res.get("epics") or []))


def _render_findings_tab(result: dict, kind: str) -> None:
    items = result.get(kind, []) or []
    if not items:
        kind_label = {"gaps": "gaps", "conflicts": "conflicts", "duplicates": "duplicates"}[kind]
        st.info(f"No {kind_label} detected for this run.")
        return
    css = {"gaps": "finding-gap", "conflicts": "finding-conflict", "duplicates": "finding-dup"}[kind]
    kind_label = {"gaps": "GAP", "conflicts": "CONFLICT", "duplicates": "DUPLICATE"}[kind]
    for idx, item in enumerate(items):
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

        if kind == "gaps" and _can_run():
            _gap_key = f"gap_to_story_{idx}_{item.get('id') or idx}"
            _already = any(
                s.get("id", "").startswith("ST-gap-") and
                (item.get("title") or item.get("description") or "") in (s.get("title") or "")
                for e in (st.session_state.get("result") or {}).get("epics", [])
                for s in e.get("stories", [])
            )
            if _already:
                st.caption("✓ Story created from this gap")
            elif st.button("＋ Create Story from this gap", key=_gap_key, use_container_width=False):
                _gap_to_story(item)
                st.rerun()


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


def _user_runs_dir(user_id: str) -> Path:
    """Per-user run history directory: logs/runs/<safe_user_id>/"""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (user_id or "anonymous"))
    return RUNS_DIR / safe


def _save_run_to_disk(summary: dict[str, Any]) -> Path:
    """Write summary JSON scoped to the current user: logs/runs/<user_id>/<stamp>_<id>.json"""
    user_id = summary.get("user_id", "anonymous")
    user_dir = _user_runs_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    short_id = uuid.uuid4().hex[:6]
    stamp = summary.get("timestamp") or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = user_dir / f"{stamp}_{short_id}.json"
    try:
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as e:
        st.warning(f"Could not save run history: {e}")
    return path


def _load_run_history() -> list[dict[str, Any]]:
    """Load run history scoped by role:
    - admin: sees ALL users' runs
    - contributor/viewer: sees only their own runs
    """
    if not RUNS_DIR.exists():
        return []
    entries: list[dict[str, Any]] = []

    # Admins scan all user subdirectories; others scan only their own
    try:
        current_role = st.session_state.get("entra_user", {}).get("role") \
            or st.session_state.get("authentication_status") and \
            __import__("yaml").safe_load(
                (ROOT / "config" / "auth.yaml").read_text()
            ).get("credentials", {}).get("usernames", {}) \
               .get(st.session_state.get("username", ""), {}).get("role", "viewer") \
            or "viewer"
    except Exception:  # noqa: BLE001
        current_role = "viewer"

    is_admin_user = (current_role == "admin")

    if is_admin_user:
        # Admins see all runs across all users
        search_dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir()] if RUNS_DIR.exists() else []
        search_dirs += [RUNS_DIR]  # also legacy flat structure
    else:
        # Non-admins see only their own runs
        current_uid = (st.session_state.get("entra_user") or {}).get("email") \
            or st.session_state.get("username") or "anonymous"
        search_dirs = [_user_runs_dir(current_uid)]

    for d in search_dirs:
        if not d.exists():
            continue
        for p in d.glob("*.json"):
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

    _cost_chip = (
        f'<div class="rh-summary-chip"><span>Total est. cost</span>${total_cost:.4f}</div>'
        if _is_admin() else ""
    )
    st.markdown(
        '<div style="display:flex;gap:0.6rem;margin-bottom:0.85rem;">'
        f'<div class="rh-summary-chip"><span>Runs</span>{len(history)}</div>'
        f'<div class="rh-summary-chip"><span>Stories drafted</span>{total_stories}</div>'
        f'{_cost_chip}'
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
        if _is_admin():
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
    # Search all user subdirectories for the run file
    deleted = 0
    if not RUNS_DIR.exists():
        st.toast(f"No metadata file found for run {run_id}", icon="⚠️")
        return
    search_dirs = [RUNS_DIR] + [d for d in RUNS_DIR.iterdir() if d.is_dir()]
    for d in search_dirs:
        for p in d.glob(f"{run_id}*.json"):
            try:
                p.unlink()
                deleted += 1
            except OSError:
                pass
    if deleted:
        st.toast(f"Deleted run metadata · {deleted} file(s)", icon="🗑️")
    else:
        st.toast(f"No metadata file found for run {run_id}", icon="⚠️")


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
    "local": {
        # Free local models (Ollama) for the mechanical stages;
        # Claude for the two reasoning-heavy stages.
        # Requires: ollama serve + ollama pull llama3.2:3b
        "parser":          "ollama/llama3.2:3b",
        "constraint":      "ollama/llama3.2:3b",
        "story_writer":    "claude-sonnet-4-5",
        "epic_decomposer": "ollama/llama3.2:3b",
        "gap_detector":    "claude-sonnet-4-5",
    },
    "free": {
        "parser":          "gemini-2.5-flash",
        "constraint":      "gemini-2.5-flash",
        "story_writer":    "gemini-2.5-flash",
        "epic_decomposer": "gemini-2.5-flash",
        "gap_detector":    "gemini-2.5-flash",
    },
    "balanced": {
        # Gemini Flash for mechanical extraction stages (fast, cheap);
        # Claude Sonnet for the two reasoning-heavy stages.
        # Story Writer needs nuanced judgment for priority + AC quality.
        # Gap Detector needs strong reasoning to detect constraint conflicts.
        "parser":          "gemini-2.5-flash",
        "constraint":      "gemini-2.5-flash",
        "story_writer":    "claude-sonnet-4-5",
        "epic_decomposer": "gemini-2.5-flash",
        "gap_detector":    "claude-sonnet-4-5",
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
    "local":    "Local (Ollama) · ~$0  —  needs ollama serve",
    "free":     "Free tier (Gemini) · ~$0",
    "balanced": "~$0.01 per run",   # now 2× Claude stages (Story Writer + Gap Detector)
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
    "ollama/llama3.2:3b",
    "ollama/llama3.1",
    "ollama/mistral",
    "ollama/phi3",
    "ollama/gemma2",
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


def _default_multi(saved_key: str, options_dict: dict) -> list[str]:
    """Default selection for a multi-select source picker.

    Accepts persisted state that's either a list (new) or a single string
    (older single-select state), filters to currently-valid options, and
    falls back to the first concrete (non-upload) sample."""
    saved = _persisted_ui.get(saved_key)
    keys = list(options_dict.keys())
    if isinstance(saved, str):
        saved = [saved]
    if isinstance(saved, list):
        valid = [s for s in saved if s in keys]
        if valid:
            return valid
    for k, v in options_dict.items():
        if v not in ("__upload__", ""):
            return [k]
    return []


if "models" not in st.session_state:
    # Default = Balanced (or persisted preset). Per-key overrides go into
    # this dict from the advanced expander; preset buttons replace it.
    _saved_preset = _persisted_ui.get("active_preset", "balanced")
    if _saved_preset not in MODEL_PRESETS and _saved_preset != "custom":
        _saved_preset = "balanced"
    # BUG FIX: always load models from the saved preset on first init.
    # Previously a stale persisted preset (e.g. "free") would silently
    # override whatever the user selected, because models are loaded here
    # before the radio renders.
    base_preset = _saved_preset if _saved_preset in MODEL_PRESETS else "balanced"
    st.session_state.models = dict(MODEL_PRESETS[base_preset])
    # If the saved preset was "custom", restore the saved per-stage map.
    # BUG FIX: widen the validity check to accept any recognised prefix
    # (claude-*, gemini-*, ollama/*) so Ollama model IDs are not silently
    # dropped when restoring a custom map.
    saved_custom = _persisted_ui.get("models") or {}
    if _saved_preset == "custom" and isinstance(saved_custom, dict):
        _valid_prefixes = ("claude-", "gemini-", "ollama/")
        for k, v in saved_custom.items():
            if k in STAGE_KEYS and (
                v in MODEL_OPTIONS
                or any(str(v).startswith(p) for p in _valid_prefixes)
            ):
                st.session_state.models[k] = v
if "active_preset" not in st.session_state:
    st.session_state.active_preset = _persisted_ui.get("active_preset", "balanced")
    if st.session_state.active_preset not in (*MODEL_PRESETS.keys(), "custom"):
        st.session_state.active_preset = "balanced"


# -------------------------------------------------------- sidebar

SAMPLES_DIR = ROOT / "samples"
GOLDEN_TRANSCRIPTS_DIR = ROOT / "evaluation" / "golden_dataset" / "transcripts"

TRANSCRIPT_OPTIONS = {
    "Q3 Planning — Meeting notes":
        SAMPLES_DIR / "meeting_notes.txt",
    "Q3 Strategy doc":
        SAMPLES_DIR / "product_strategy.md",
    "Pharmacy refill escalation":
        GOLDEN_TRANSCRIPTS_DIR / "case_02_pharmacy_escalation.txt",
    "Mobile Slack standup":
        GOLDEN_TRANSCRIPTS_DIR / "case_03_mobile_standup.txt",
    "Customer support note (negative)":
        GOLDEN_TRANSCRIPTS_DIR / "case_04_support_note.txt",
}
CONSTRAINTS_OPTIONS = {
    "Architecture constraints":
        SAMPLES_DIR / "architecture_constraints.md",
    "Product strategy doc":
        SAMPLES_DIR / "product_strategy.md",
}
BACKLOG_OPTIONS = {
    "JIRA backlog (30 tickets)":
        SAMPLES_DIR / "jira_backlog.json",
    "GitHub issues (6 tickets)":
        SAMPLES_DIR / "github_issues.json",
}

# Bundled sample images for the vision input — selectable directly so a
# whiteboard demo needs no upload. Maps label → image path.
VISION_SAMPLE_OPTIONS = {
    "Whiteboard — sprint planning sketch": SAMPLES_DIR / "whiteboard_sprint_planning.png",
}

def _expander_label(title: str, choices: list, empty_hint: str = "") -> str:
    """Compact expander label that stays on ONE line inside the narrow sidebar.
    Shows a ✓ badge + count when something is selected, empty hint otherwise."""
    if not choices:
        return f"{title}  {empty_hint}"
    count = f"  ·  {len(choices)} selected" if len(choices) > 1 else "  ✓"
    return f"{title}{count}"


with st.sidebar:
    # ── Brand ──────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="display:flex;align-items:center;gap:7px;margin-bottom:10px;">'
        '<svg viewBox="0 0 28 28" fill="none" width="24" height="24">'
        '<polygon points="14,2 17.2,10.4 26,10.8 19.4,15.8 21.6,24.4 14,19.8 6.4,24.4 8.6,15.8 2,10.8 10.8,10.4"'
        ' fill="none" stroke="#F5A623" stroke-width="1.8" stroke-linejoin="round"/>'
        '</svg>'
        '<span style="color:#f0f4ff;font-size:16px;font-weight:700;letter-spacing:0.02em;">NorthStar Retail</span>'
        '</div>'
        '<div class="acc-brand">'
        '<span class="acc-wordmark">accenture<span class="acc-mark">&gt;</span></span>'
        '<span class="acc-eyebrow">AI-First Agentic Solutions</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="app-header">'
        '<span class="app-mark">◆</span>'
        '<div><div class="app-title">Backlog Synthesizer</div>'
        '<div class="app-tagline">Multi-agent · five specialists · audited</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Profile panel — expandable like Azure Portal / Microsoft 365 ──────────
    if not _auth_disabled and (_entra_enabled() or _authenticator is not None):
        _role_color  = {"admin": "var(--rose)", "contributor": "var(--accent)", "viewer": "var(--text-faint)"}.get(_current_role, "var(--text-faint)")
        _show_name   = _display_name if _entra_enabled() else _current_user
        _show_email  = _current_user if _entra_enabled() else ""
        _org         = "NorthStar Retail Corp" if _entra_enabled() else "Local"
        _auth_method = "Microsoft Entra ID" if _entra_enabled() else "Username / Password"
        _role_desc   = {
            "admin": "Full access · Live integrations · Admin settings",
            "contributor": "Run synthesis · Push to Jira · View history",
            "viewer": "View results · Download exports only",
        }.get(_current_role, "")

        # Profile header — always visible, click to expand/collapse
        _profile_open = st.session_state.get("_profile_open", False)
        _chevron = "▲" if _profile_open else "▼"

        if st.button(
            f"👤  {_esc(_show_name)}   {_chevron}",
            key="profile_toggle_btn",
            use_container_width=True,
            help="Click to view your profile",
        ):
            st.session_state["_profile_open"] = not _profile_open
            st.rerun()

        # Role badge sits below the button
        st.markdown(
            f'<div style="text-align:right;margin:-6px 2px 4px;font-size:0.62rem;'
            f'font-weight:700;letter-spacing:0.12em;text-transform:uppercase;'
            f'color:{_role_color};">{_current_role}</div>',
            unsafe_allow_html=True,
        )

        # Expanded profile panel
        if _profile_open:
            st.markdown(
                f'<div style="background:var(--bg-elev-2);border:1px solid var(--border);'
                f'border-radius:10px;padding:1rem;margin-bottom:0.5rem;font-size:0.8rem;">'

                # Avatar circle with initials
                f'<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.9rem;">'
                f'<div style="width:40px;height:40px;border-radius:50%;background:{_role_color};'
                f'display:flex;align-items:center;justify-content:center;font-weight:700;'
                f'font-size:1rem;color:white;flex-shrink:0;">'
                f'{_esc(_show_name[0].upper()) if _show_name else "?"}'
                f'</div>'
                f'<div>'
                f'<div style="font-weight:700;color:var(--text);font-size:0.88rem;">{_esc(_show_name)}</div>'
                f'<div style="color:var(--text-faint);font-size:0.72rem;margin-top:1px;">{_esc(_show_email)}</div>'
                f'</div>'
                f'</div>'

                # Details rows
                f'<div style="display:flex;flex-direction:column;gap:0.45rem;'
                f'border-top:1px solid var(--border);padding-top:0.75rem;">'

                f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
                f'<span style="color:var(--text-faint);font-size:0.7rem;">Role</span>'
                f'<span style="color:{_role_color};font-weight:700;font-size:0.7rem;'
                f'letter-spacing:0.1em;text-transform:uppercase;">{_current_role}</span>'
                f'</div>'

                f'<div style="font-size:0.7rem;color:var(--text-faint);'
                f'background:var(--bg-elev-1);border-radius:6px;padding:4px 8px;">'
                f'{_esc(_role_desc)}'
                f'</div>'

                f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:4px;">'
                f'<span style="color:var(--text-faint);font-size:0.7rem;">Organisation</span>'
                f'<span style="color:var(--text-muted);font-size:0.72rem;">{_esc(_org)}</span>'
                f'</div>'

                f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
                f'<span style="color:var(--text-faint);font-size:0.7rem;">Signed in via</span>'
                f'<span style="color:var(--text-muted);font-size:0.72rem;">{_esc(_auth_method)}</span>'
                f'</div>'

                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Sign out inside the panel
            if _entra_enabled():
                if st.button("↩  Sign out", key="profile_signout_btn", use_container_width=True, type="secondary"):
                    st.session_state.pop("entra_user", None)
                    st.session_state.pop("_profile_open", None)
                    st.query_params.clear()
                    st.rerun()
            elif _authenticator is not None:
                _authenticator.logout(button_name="↩  Sign out", location="sidebar", key="sidebar_logout")

        elif not _profile_open:
            # Compact logout when panel is closed
            if _entra_enabled():
                if st.button("Log out", key="sidebar_logout", use_container_width=False):
                    st.session_state.pop("entra_user", None)
                    st.query_params.clear()
                    st.rerun()
            elif _authenticator is not None:
                _authenticator.logout(button_name="Log out", location="sidebar", key="sidebar_logout")

    # ── Usage meter (rate limit) ────────────────────────────────────────────
    if _can_run():
        try:
            _usage = get_usage_summary(_current_user)
            _hr_pct = min(100, int(100 * _usage["runs_last_hour"] / max(1, _usage["max_runs_per_hour"])))
            _hr_color = "var(--rose)" if _hr_pct >= 80 else "var(--accent)"
            if _is_admin():
                _day_pct = min(100, int(100 * _usage["cost_today_usd"] / max(0.01, _usage["max_cost_per_day_usd"])))
                _day_color = "var(--rose)" if _day_pct >= 80 else "var(--accent)"
                _cost_row = (
                    f'<div style="display:flex;justify-content:space-between;color:var(--text-faint);">'
                    f'<span>Cost today</span><span style="color:{_day_color};">'
                    f'${_usage["cost_today_usd"]:.3f}/${_usage["max_cost_per_day_usd"]:.2f}</span></div>'
                    f'<div style="height:3px;background:var(--border);border-radius:2px;margin-top:0.25rem;">'
                    f'<div style="height:3px;width:{_day_pct}%;background:{_day_color};border-radius:2px;"></div></div>'
                )
            else:
                _cost_row = ""
            st.markdown(
                f'<div style="padding:0.4rem 0.6rem;background:var(--bg-elev-1);'
                f'border:1px solid var(--border);border-radius:8px;margin-bottom:0.5rem;font-size:0.75rem;">'
                f'<div style="display:flex;justify-content:space-between;color:var(--text-faint);margin-bottom:0.25rem;">'
                f'<span>Runs/hr</span><span style="color:{_hr_color};">'
                f'{_usage["runs_last_hour"]}/{_usage["max_runs_per_hour"]}</span></div>'
                f'<div style="height:3px;background:var(--border);border-radius:2px;margin-bottom:0.3rem;">'
                f'<div style="height:3px;width:{_hr_pct}%;background:{_hr_color};border-radius:2px;"></div></div>'
                f'{_cost_row}'
                f'</div>',
                unsafe_allow_html=True,
            )
        except Exception:  # noqa: BLE001 — usage meter must never block the UI
            pass

    # ── Admin: Feature Flags settings button ───────────────────────────────
    # Note: show_admin_settings_dialog is defined LATER in the file. We set a
    # session state flag here and call the dialog after its definition below.
    if _is_admin():
        if st.button("⚙  Admin Settings", use_container_width=True,
                     key="admin_settings_btn", help="Configure what contributors can do"):
            st.session_state["_trigger_admin_settings"] = True

    # ── Show startup warnings if any ───────────────────────────────────────
    for _w in _startup_warnings:
        st.warning(_w, icon="⚠️")

    # ── ROLE-GATED SIDEBAR CONTENT ─────────────────────────────────────────
    # Viewer:      read-only panel + browse history only
    # Contributor: inputs + models (Free/Balanced) + synthesize + advanced
    # Admin:       everything above + Live Atlassian + Local/Premium + per-stage override

    if not _can_run():
        # ── VIEWER ───────────────────────────────────────────────────────────
        st.markdown(
            '<div style="padding:0.8rem 1rem;background:var(--bg-elev-1);'
            'border:1px solid var(--border);border-left:3px solid var(--text-faint);'
            'border-radius:8px;margin-bottom:0.7rem;">'
            '<div style="font-size:0.62rem;font-weight:700;letter-spacing:0.14em;'
            'text-transform:uppercase;color:var(--text-faint);margin-bottom:0.35rem;">'
            'View-only access</div>'
            '<div style="font-size:0.82rem;color:var(--text-muted);line-height:1.5;">'
            'Your role (<strong style="color:var(--text);">viewer</strong>) lets you '
            'read results and download exports.<br><br>'
            'To run synthesis or push to Jira, ask an admin to upgrade your role to '
            '<strong>contributor</strong>.</div></div>',
            unsafe_allow_html=True,
        )
        if st.button("⌕  Browse run history", use_container_width=True, key="viewer_history_btn"):
            show_run_history_dialog()
        # Safe defaults — referenced by the run handler below the sidebar.
        transcript_choice = []
        transcript_upload = None
        constraints_choice = []
        constraints_upload = None
        backlog_choice = []
        backlog_upload = None
        vision_samples = []
        vision_uploads = []
        run_clicked = False
        redact_pii = True
        dry_run = False
        auto_switch = False
        compare_enabled = False
        compare_with_preset = "free"
        use_live_confluence = False
        use_live_jira = False
        live_confluence_page_id = ""

    else:
        # ── CONTRIBUTOR / ADMIN: INPUTS ───────────────────────────────────────
        _saved_transcript = _default_multi("transcript_choice", TRANSCRIPT_OPTIONS)
        with st.expander(
            _expander_label("📝 Transcript", _saved_transcript, empty_hint="pick a source"),
            expanded=not bool(_saved_transcript),
        ):
            transcript_choice = st.multiselect(
                "Transcript",
                options=list(TRANSCRIPT_OPTIONS.keys()),
                default=_saved_transcript,
                label_visibility="collapsed",
                key="transcript_choice",
                help="Pick one or more bundled transcripts — combined into one source.",
            )
            transcript_upload = st.file_uploader(
                "↑ Upload (txt / md / pdf)", type=["txt", "md", "pdf"],
                accept_multiple_files=True, key="transcript_upload",
                help="Optional — combined with any samples selected above.",
            )
            if _ff.is_enabled(_current_role, "vision_input"):
                st.caption("**📷 Whiteboard / vision**")
                vision_samples = st.multiselect(
                    "Vision samples",
                    options=list(VISION_SAMPLE_OPTIONS.keys()),
                    default=_default_multi("vision_samples", VISION_SAMPLE_OPTIONS) if _persisted_ui.get("vision_samples") else [],
                    key="vision_samples", label_visibility="collapsed",
                    help="Bundled whiteboard images — fed directly to the Parser.",
                )
                vision_uploads = st.file_uploader(
                    "↑ Upload whiteboard (PNG / JPG)", type=["png", "jpg", "jpeg", "webp"],
                    accept_multiple_files=True, key="vision_uploads",
                    help="Vision-capable models only.",
                )
            else:
                vision_samples = []
                vision_uploads = []

        _saved_constraints = _default_multi("constraints_choice", CONSTRAINTS_OPTIONS)
        with st.expander(
            _expander_label("📐 Wiki", _saved_constraints, empty_hint="optional"),
            expanded=False,
        ):
            constraints_choice = st.multiselect(
                "Wiki",
                options=list(CONSTRAINTS_OPTIONS.keys()),
                default=_saved_constraints,
                label_visibility="collapsed",
                key="constraints_choice",
                help="Pick one or more wiki pages. Leave empty to skip the Constraint Extractor.",
            )
            constraints_upload = st.file_uploader(
                "↑ Upload wiki (md / txt)", type=["md", "txt"],
                accept_multiple_files=True, key="constraints_upload",
                help="Combined with any wiki samples selected above.",
            )

        _saved_backlog = _default_multi("backlog_choice", BACKLOG_OPTIONS)
        with st.expander(
            _expander_label("🗂 Backlog", _saved_backlog, empty_hint="optional"),
            expanded=False,
        ):
            backlog_choice = st.multiselect(
                "Backlog",
                options=list(BACKLOG_OPTIONS.keys()),
                default=_saved_backlog,
                label_visibility="collapsed",
                key="backlog_choice",
                help="Ticket exports merged for duplicate detection. Leave empty to skip.",
            )
            backlog_upload = st.file_uploader(
                "↑ Upload backlog (JSON)", type=["json"],
                accept_multiple_files=True, key="backlog_upload",
                help="Merged with any backlog samples selected above.",
            )

        # ── ADMIN ONLY: Live Atlassian ────────────────────────────────────────
        # Completely hidden for contributors unless live_jira_read flag is on.
        use_live_confluence = False
        use_live_jira = False
        live_confluence_page_id = ""
        _contributor_live_jira = _ff.is_enabled(_current_role, "live_jira_read")
        if _is_admin() or _contributor_live_jira:
            _live_conf_active = bool(st.session_state.get("use_live_confluence"))
            _live_jira_active = bool(st.session_state.get("use_live_jira"))
            _live_label = "☁ Live Atlassian" + (" — active" if (_live_conf_active or _live_jira_active) else "")
            with st.expander(_live_label, expanded=False):
                use_live_confluence = st.toggle(
                    "Pull constraints from live Confluence",
                    value=False,
                    help="Fetches a Confluence page by ID. Overrides the wiki selector above.",
                    key="use_live_confluence",
                )
                live_confluence_page_id = ""
                if use_live_confluence:
                    live_confluence_page_id = st.text_input(
                        "Confluence page ID",
                        value=os.environ.get("CONFLUENCE_PAGE_ID", ""),
                        placeholder="e.g. 65830",
                        key="live_confluence_page_id",
                    )
                use_live_jira = st.toggle(
                    "Pull backlog from live Jira",
                    value=False,
                    help=f"Fetches issues from project `{os.environ.get('JIRA_PROJECT_KEY') or '?'}`. Overrides the backlog selector above.",
                    key="use_live_jira",
                )

        # ── MODELS ────────────────────────────────────────────────────────────
        st.markdown("### Models")
        _label_to_key = {"Local": "local", "Free": "free", "Balanced": "balanced", "Premium": "premium"}
        _key_to_label = {v: k for k, v in _label_to_key.items()}
        # Allowed presets come from feature_flags for contributor; admin always gets all four.
        _allowed_preset_keys = _ff.allowed_presets(_current_role)
        # Hide "Local" preset when Ollama binary is not installed (e.g. on Azure).
        import shutil as _shutil
        _ollama_installed = bool(_shutil.which("ollama"))
        _preset_labels = [
            lbl for lbl, key in _label_to_key.items()
            if key in _allowed_preset_keys
            and (key != "local" or _ollama_installed)
        ] or ["Free", "Balanced"]

        _active = st.session_state.active_preset
        if _active not in [_label_to_key[l] for l in _preset_labels] and _active != "custom":
            _active = "balanced"
            st.session_state.active_preset = "balanced"
            st.session_state.models = dict(MODEL_PRESETS["balanced"])

        # Check which providers are actually available right now.
        _has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
        _has_google    = bool(os.environ.get("GOOGLE_API_KEY", "").strip())

        # For Ollama: do a live check so we pick up the server starting after
        # the initial page load (the session-state flag only captures startup).
        try:
            from ollama_manager import is_running as _ollama_is_running, ensure_running as _ollama_ensure
            _ollama_ok = _ollama_is_running()
            if _ollama_ok:
                st.session_state.ollama_started = True
        except Exception:
            _ollama_ok = st.session_state.get("ollama_started", False)


        # Each preset requires specific providers — derive its ready status.
        # We check env-var presence (fast); actual API validity is caught at run time.
        def _preset_status(key: str) -> tuple[bool, str]:
            """Return (ready, reason_if_not_ready) for a preset key."""
            if key == "free":
                return (_has_google, "needs GOOGLE_API_KEY")
            if key == "balanced":
                if not _has_google and not _has_anthropic:
                    return (False, "needs GOOGLE_API_KEY + ANTHROPIC_API_KEY")
                if not _has_google:
                    return (False, "needs GOOGLE_API_KEY")
                if not _has_anthropic:
                    return (False, "needs ANTHROPIC_API_KEY")
                return (True, "")
            if key == "premium":
                return (_has_anthropic, "needs ANTHROPIC_API_KEY")
            if key == "local":
                if not _ollama_ok:
                    return (False, "Ollama offline")
                if not _has_anthropic:
                    return (False, "Ollama ok but needs ANTHROPIC_API_KEY for reasoning stages")
                return (True, "")
            return (True, "")

        # ── Colored dot status row ────────────────────────────────────────────
        # Green dot = every model in the preset is available right now.
        # Red dot   = at least one model is missing a dependency.
        # Hovering a red dot shows the tooltip explaining what's missing.
        _dot_chips = []
        for _lbl in _preset_labels:
            _pkey = _label_to_key[_lbl]
            _ready, _reason = _preset_status(_pkey)
            _dot_color = "#34d399" if _ready else "#fb7185"   # green / red
            _dot_glow  = "0 0 6px rgba(52,211,153,0.5)" if _ready else "0 0 6px rgba(251,113,133,0.5)"
            _is_active_chip = (_active == _pkey)
            _chip_bg     = "rgba(52,211,153,0.1)"  if _is_active_chip and _ready  else \
                           "rgba(251,113,133,0.1)" if _is_active_chip and not _ready else \
                           "var(--bg-elev-1)"
            _chip_border = _dot_color if _is_active_chip else "var(--border)"
            _tooltip = (
                f"All models in {_lbl} preset are available"
                if _ready else
                f"{_lbl} preset unavailable: {_reason}"
            )
            _dot_chips.append(
                f'<span title="{_esc(_tooltip)}" style="'
                f'display:inline-flex;align-items:center;gap:5px;'
                f'padding:3px 10px;border-radius:20px;font-size:0.78rem;'
                f'color:var(--text);background:{_chip_bg};'
                f'border:1px solid {_chip_border};white-space:nowrap;">'
                f'<span style="width:9px;height:9px;border-radius:50%;'
                f'background:{_dot_color};flex-shrink:0;'
                f'box-shadow:{_dot_glow};display:inline-block;"></span>'
                f'{_esc(_lbl)}'
                f'</span>'
            )
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:4px;">'
            + "".join(_dot_chips)
            + '</div>'
            + '<div style="display:flex;gap:12px;margin-bottom:6px;font-size:0.68rem;color:var(--text-faint);">'
            + '<span style="display:flex;align-items:center;gap:4px;">'
            + '<span style="width:7px;height:7px;border-radius:50%;background:#34d399;display:inline-block;"></span>All models online</span>'
            + '<span style="display:flex;align-items:center;gap:4px;">'
            + '<span style="width:7px;height:7px;border-radius:50%;background:#fb7185;display:inline-block;"></span>Unavailable — hover for details</span>'
            + '</div>',
            unsafe_allow_html=True,
        )

        # ── Selectbox — clean names only, no status text ──────────────────────
        _radio_index = (
            _preset_labels.index(_key_to_label[_active])
            if _active in _key_to_label and _key_to_label[_active] in _preset_labels
            else min(1, len(_preset_labels) - 1)
        )
        _picked_label = st.selectbox(
            "Model preset",
            options=_preset_labels,
            index=_radio_index,
            label_visibility="collapsed",
            key="preset_radio",
            help=(
                "Free: all Gemini Flash · free tier.  "
                "Balanced: Gemini Flash + Claude Sonnet for Story Writer & Gap Detector."
                + ("  Local: Ollama + Claude for reasoning · run ./start.sh to auto-start."
                   "  Premium: all Claude Sonnet."
                   if _is_admin() else "")
            ),
        )
        _picked_key = _label_to_key.get(_picked_label, "balanced")

        def _apply_preset(key: str) -> None:
            st.session_state.models = dict(MODEL_PRESETS[key])
            st.session_state.active_preset = key
            st.session_state["_preset_radio_last"] = key
            # Also turn off per-stage override when preset changes — user is
            # explicitly picking a preset, so override should reset cleanly.
            st.session_state["_stage_override_enabled"] = False
            # Clear per-stage widget state so the override selects reflect the
            # new preset. Without this, Streamlit keeps the old selectbox value
            # even after the preset changes (stale widget state bug).
            for _s in STAGE_KEYS:
                st.session_state.pop(f"model_pick_{_s}", None)
            _save_ui_state({
                "transcript_choice":   transcript_choice,
                "constraints_choice":  constraints_choice,
                "backlog_choice":      backlog_choice,
                "active_preset":       key,
                "models":              dict(MODEL_PRESETS[key]),
            })
            st.rerun()

        # Track the last preset the user explicitly picked in the selectbox.
        # This lets us distinguish between:
        #   (a) user changed the dropdown → apply the new preset
        #   (b) active_preset became "custom" due to per-stage override,
        #       but the dropdown still shows the old preset → do NOT reset,
        #       or the override would be wiped on every rerun.
        _last_explicit_preset = st.session_state.get("_preset_radio_last", _active)

        if _picked_key != _last_explicit_preset:
            # User explicitly changed the preset dropdown — apply it.
            st.session_state["_preset_radio_last"] = _picked_key
            _apply_preset(_picked_key)
        elif _picked_key != _active and _active != "custom":
            # Drift between displayed preset and active (not a custom override) — sync.
            st.session_state["_preset_radio_last"] = _picked_key
            _apply_preset(_picked_key)

        # If the selected preset is not ready, show a clear actionable error.
        _sel_ready, _sel_reason = _preset_status(_picked_key)
        if not _sel_ready:
            if _picked_key == "local" and not _ollama_ok:
                # Auto-start Ollama instead of just showing an error.
                with st.spinner("Starting Ollama…"):
                    try:
                        _ok2, _msg2 = _ollama_ensure(timeout=30)
                    except Exception as _e:
                        _ok2, _msg2 = False, str(_e)
                if _ok2:
                    st.session_state.ollama_started = True
                    st.rerun()
                else:
                    st.error(
                        f"**Could not start Ollama** — {_msg2}  \n"
                        "Install from https://ollama.ai and run `ollama pull llama3.2:3b`, "
                        "or switch to the **Balanced** preset."
                    )
            else:
                _fix_hint = {
                    "free":     "Add `GOOGLE_API_KEY=...` to your `.env` file.",
                    "balanced": "Add the missing API key(s) to your `.env` file.",
                    "premium":  "Add `ANTHROPIC_API_KEY=...` to your `.env` file.",
                }.get(_picked_key, "Check your `.env` file.")
                st.error(
                    f"**{_picked_label} preset not available** — {_sel_reason}. {_fix_hint}"
                )

        # ── ADMIN ONLY: Per-stage model override ──────────────────────────────
        if _is_admin():
            with st.expander("⚙ Per-stage override", expanded=False):
                # Toggle guards the selects so the admin can't accidentally
                # change a stage while just browsing what the preset uses.
                _override_enabled = st.toggle(
                    "Enable per-stage override",
                    value=bool(st.session_state.get("_stage_override_enabled", False)),
                    key="_stage_override_enabled",
                    help="Turn on to customise individual stage models. "
                         "Turn off to lock all stages back to the selected preset.",
                )
                if not _override_enabled:
                    # Reset models to preset AND clear widget state so turning
                    # override back ON starts fresh from the preset, not stale values.
                    _cur_active = st.session_state.active_preset
                    if _cur_active in MODEL_PRESETS:
                        st.session_state.models = dict(MODEL_PRESETS[_cur_active])
                    for _s in STAGE_KEYS:
                        st.session_state.pop(f"model_pick_{_s}", None)

                    # Show a read-only summary so the user can confirm what each
                    # stage will use before running.
                    _stage_names = {
                        "parser": "Parser", "constraint": "Constraint Extractor",
                        "story_writer": "Story Writer", "epic_decomposer": "Epic Decomposer",
                        "gap_detector": "Gap Detector",
                    }
                    _rows = "".join(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'padding:2px 0;font-size:0.78rem;">'
                        f'<span style="color:var(--text-faint);">{_stage_names[_s]}</span>'
                        f'<span style="color:var(--text);font-family:\'IBM Plex Mono\',monospace;">'
                        f'{_esc(st.session_state.models.get(_s,"—"))}</span></div>'
                        for _s in STAGE_KEYS
                    )
                    st.markdown(
                        f'<div style="background:var(--bg-elev-2);border:1px solid var(--border);'
                        f'border-radius:8px;padding:8px 12px;margin:4px 0;">'
                        f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:0.1em;'
                        f'text-transform:uppercase;color:var(--text-faint);margin-bottom:6px;">'
                        f'{_esc(_cur_active.title())} preset — all stages</div>'
                        + _rows + '</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    _stage_labels = {
                        "parser": "Parser", "constraint": "Constraint Extractor",
                        "story_writer": "Story Writer", "epic_decomposer": "Epic Decomposer",
                        "gap_detector": "Gap Detector",
                    }

                    # Build labelled model options with live availability badges.
                    # Checks: Anthropic key, Google key, Ollama running + model pulled.
                    try:
                        from ollama_manager import list_models as _list_ollama
                        _pulled = {f"ollama/{m}" for m in _list_ollama()}
                    except Exception:
                        _pulled = {"ollama/llama3.2:3b"} if _ollama_ok else set()

                    def _model_label(mid: str) -> str:
                        """Return display label with plain-text availability tag."""
                        if mid.startswith("claude"):
                            tag = "[ready]" if _has_anthropic else "[no ANTHROPIC key]"
                        elif mid.startswith("gemini"):
                            if not _has_google:
                                tag = "[no GOOGLE key]"
                            elif mid == "gemini-2.5-pro":
                                tag = "[ready - paid tier]"
                            else:
                                tag = "[ready]"
                        elif mid.startswith("ollama"):
                            if not _ollama_ok:
                                tag = "[Ollama offline]"
                            elif mid in _pulled:
                                tag = "[ready]"
                            else:
                                model_name = mid.replace("ollama/", "")
                                tag = f"[not pulled - run: ollama pull {model_name}]"
                        else:
                            tag = "[ready]"
                        return f"{mid}  {tag}"

                    # Build the labelled list and a reverse mapping to raw model id.
                    _labelled_opts = [_model_label(m) for m in MODEL_OPTIONS]
                    _label_to_model = {lbl: mid for lbl, mid in zip(_labelled_opts, MODEL_OPTIONS)}

                    for _stage in STAGE_KEYS:
                        _cur = st.session_state.models.get(_stage, MODEL_PRESETS["balanced"][_stage])
                        # Find the labelled version of the current model.
                        _cur_lbl = next(
                            (lbl for lbl, mid in _label_to_model.items() if mid == _cur),
                            _labelled_opts[0],
                        )
                        _spicked_lbl = st.selectbox(
                            _stage_labels[_stage],
                            options=_labelled_opts,
                            index=_labelled_opts.index(_cur_lbl),
                            key=f"model_pick_{_stage}",
                        )
                        _spicked = _label_to_model.get(_spicked_lbl, _cur)
                        # Warn if the picked model is not available.
                        if "[ready]" not in _spicked_lbl:
                            _issue = _spicked_lbl.split("  [")[1].rstrip("]")
                            st.caption(f"⚠  {_issue}")
                        if _spicked != _cur:
                            st.session_state.models[_stage] = _spicked
                            _matches = next(
                                (n for n, mp in MODEL_PRESETS.items() if mp == st.session_state.models),
                                None,
                            )
                            st.session_state.active_preset = _matches or "custom"

                    # ── Active configuration summary ──────────────────────────
                    # Shows every stage's current model. Stages that differ from
                    # the base preset are highlighted so the admin can quickly
                    # confirm which overrides are active before running.
                    st.divider()
                    _base_preset_key = st.session_state.active_preset
                    _base = dict(MODEL_PRESETS.get(_base_preset_key, MODEL_PRESETS["balanced"]))
                    _stage_display_names = {
                        "parser": "Parser", "constraint": "Constraint Extractor",
                        "story_writer": "Story Writer", "epic_decomposer": "Epic Decomposer",
                        "gap_detector": "Gap Detector",
                    }
                    _summary_rows = ""
                    for _s in STAGE_KEYS:
                        _active_m  = st.session_state.models.get(_s, _base.get(_s, "—"))
                        _preset_m  = _base.get(_s, "—")
                        _changed   = _active_m != _preset_m
                        _color     = "var(--accent)" if _changed else "var(--text-muted)"
                        _badge     = (
                            f' <span style="font-size:0.62rem;color:var(--text-faint);">'
                            f'← was {_esc(_preset_m)}</span>'
                        ) if _changed else ""
                        _summary_rows += (
                            f'<div style="display:flex;align-items:baseline;'
                            f'justify-content:space-between;padding:3px 0;font-size:0.78rem;">'
                            f'<span style="color:var(--text-faint);">{_esc(_stage_display_names[_s])}</span>'
                            f'<span style="color:{_color};font-family:\'IBM Plex Mono\',monospace;">'
                            f'{_esc(_active_m)}{_badge}</span></div>'
                        )
                    _n_changed = sum(
                        1 for _s in STAGE_KEYS
                        if st.session_state.models.get(_s) != _base.get(_s)
                    )
                    _header_color = "var(--accent)" if _n_changed else "var(--text-faint)"
                    _header_label = (
                        f"Custom — {_n_changed} stage(s) overridden"
                        if _n_changed else "No overrides — same as preset"
                    )
                    st.markdown(
                        f'<div style="background:var(--bg-elev-2);border:1px solid var(--border);'
                        f'border-radius:8px;padding:8px 12px;margin-top:4px;">'
                        f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:0.1em;'
                        f'text-transform:uppercase;color:{_header_color};margin-bottom:6px;">'
                        f'{_esc(_header_label)}</div>'
                        + _summary_rows + '</div>',
                        unsafe_allow_html=True,
                    )

                    # Summary of what's ready on this machine.
                    _ready_providers = []
                    if _has_anthropic: _ready_providers.append("Claude")
                    if _has_google:    _ready_providers.append("Gemini")
                    if _ollama_ok:     _ready_providers.append(f"Ollama ({len(_pulled)} model pulled)")
                    st.caption("Available: " + " · ".join(_ready_providers) if _ready_providers else "No providers configured.")

        # ── ESTIMATED RUN COST ────────────────────────────────────────────────
        _vision_present = bool(vision_samples) or bool(vision_uploads)
        _transcript_ready = bool(transcript_choice) or bool(transcript_upload) or _vision_present

        _pre_cost_usd, _pre_in_tokens, _pre_out_tokens = _estimate_pre_run_cost(
            transcript_choice=transcript_choice, transcript_upload=transcript_upload,
            constraints_choice=constraints_choice, constraints_upload=constraints_upload,
            backlog_choice=backlog_choice, backlog_upload=backlog_upload,
            models=st.session_state.models,
        )
        if _transcript_ready and (_pre_in_tokens > 0 or _pre_out_tokens > 0):
            if _is_admin():
                _cost_line = (
                    f"≈ <strong style='color:var(--accent)'>${_pre_cost_usd:.4f}</strong> "
                    f"<span style='color:var(--text-faint)'>·</span> "
                    f"<span style='color:var(--text-muted)'>{_pre_in_tokens // 1000}k in, ~{_pre_out_tokens // 1000}k out</span>"
                )
                st.markdown(
                    "<div style='padding:0.45rem 0.8rem;background:var(--bg-elev-1);"
                    "border:1px solid var(--border);border-left:3px solid var(--accent);"
                    "border-radius:8px;font-size:0.82rem;margin-bottom:0.5rem;'>"
                    "<span style='font-size:0.62rem;font-weight:700;letter-spacing:0.12em;"
                    "text-transform:uppercase;color:var(--accent);display:block;margin-bottom:0.2rem;'>"
                    "Estimated run cost</span>"
                    f"{_cost_line}</div>",
                    unsafe_allow_html=True,
                )
        else:
            _transcript_ready = False

        # ── SYNTHESIZE ────────────────────────────────────────────────────────
        run_clicked = st.button(
            "▶  Synthesize", type="primary", use_container_width=True,
            disabled=not _transcript_ready,
        )
        if not _transcript_ready:
            st.caption("↑ Pick a transcript source first.")

        # ── JIRA PUSH — gated by jira_write_back feature flag ────────────────
        # Approval dialog is always shown regardless; this flag controls visibility.
        _sb_jira_ready = bool(
            os.environ.get("JIRA_BASE_URL") and os.environ.get("JIRA_EMAIL")
            and os.environ.get("JIRA_API_TOKEN") and os.environ.get("JIRA_PROJECT_KEY")
        )
        _jira_write_allowed = _ff.is_enabled(_current_role, "jira_write_back")
        if _sb_jira_ready and _jira_write_allowed and st.session_state.get("result"):
            if st.button(f"⤴  Push to Jira ({os.environ.get('JIRA_PROJECT_KEY')})",
                         use_container_width=True, key="sidebar_jira_btn"):
                st.session_state["_trigger_jira"] = True  # called after definition below
        elif _sb_jira_ready and _jira_write_allowed:
            st.caption("Run a synthesis first, then push to Jira.")

        # ── ADVANCED OPTIONS — admin only ─────────────────────────────────────
        # Contributors get safe defaults silently; no expander shown to them.
        if _is_admin():
            with st.expander("⚙ Advanced options", expanded=False):
                redact_pii = st.toggle(
                    "Mask personal & sensitive info", value=True,
                    help="Replace PII with stable placeholders before the LLM sees input. Un-redacted in output.",
                )
                dry_run = st.toggle(
                    "Dry run (preview prompts only)", value=False,
                    help="Build prompts but skip LLM calls — zero API spend.",
                )
                auto_switch = st.toggle(
                    "Auto-switch model on failure / vision", value=False, key="auto_switch",
                    help="On failure, retry on the other provider. Bumps Parser to Claude when an image is attached.",
                )
                compare_enabled = st.toggle(
                    "Compare two presets side-by-side", value=False, key="compare_enabled",
                    help="Runs the pipeline twice and shows a side-by-side summary. Doubles cost and time.",
                )
                compare_with_preset = "free"
                if compare_enabled:
                    compare_with_preset = st.selectbox(
                        "Compare against",
                        options=list(MODEL_PRESETS.keys()),
                        index=list(MODEL_PRESETS.keys()).index("free"),
                        key="compare_with_preset",
                    )
        else:
            # Contributor defaults — PII always on, no dry-run, no compare
            redact_pii = True
            dry_run = False
            auto_switch = False
            compare_enabled = False
            compare_with_preset = "free"

    # ── end role-gated block ──────────────────────────────────────────────────
    # Ensure _transcript_ready is always defined (viewer path doesn't set it).
    if not _can_run():
        _transcript_ready = False

    # ── FOOTER ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="acc-footer">'
        '<span class="acc-mark">accenture&gt;</span> · AI-First Agentic Solutions<br>'
        'Demonstration on mock data — fictional client <strong>NorthStar Retail</strong>. '
        'Jira / Confluence run in mock mode by default; live Atlassian is optional.'
        '</div>',
        unsafe_allow_html=True,
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

# ---- Admin Settings dialog ----
@st.dialog("Admin Settings — Feature Flags", width="large")
def show_admin_settings_dialog() -> None:
    """Admin-only panel to view and edit contributor feature flags live."""
    st.markdown(
        "Edit what **contributors** can do. Changes save to `config/feature_flags.yaml` "
        "and take effect immediately after saving. Admins always have full access."
    )

    flags = _ff.to_dict()
    c = flags.get("contributor", {})

    st.markdown("#### Model presets")
    _all_preset_keys = ["free", "balanced", "premium", "local"]
    _preset_labels_map = {"free": "Free (Gemini Flash)", "balanced": "Balanced (Gemini + Claude)",
                          "premium": "Premium (all Claude)", "local": "Local (Ollama + Claude)"}
    current_allowed = set(c.get("allowed_presets") or [])
    new_allowed = []
    cols = st.columns(4)
    for i, key in enumerate(_all_preset_keys):
        with cols[i]:
            checked = st.checkbox(_preset_labels_map[key], value=(key in current_allowed),
                                  key=f"ff_preset_{key}")
            if checked:
                new_allowed.append(key)
    c["allowed_presets"] = new_allowed

    st.markdown("#### Per-stage model locks")
    st.caption("Lock a stage to a specific model — contributor UI still shows their selection, "
               "but the orchestrator uses the locked model. Leave blank to let contributors choose freely.")
    _stage_display = {
        "parser": "Parser", "constraint": "Constraint Extractor",
        "story_writer": "Story Writer", "epic_decomposer": "Epic Decomposer",
        "gap_detector": "Gap Detector",
    }
    _lock_options = ["(none — contributor chooses)", "claude-sonnet-4-5", "claude-haiku-4-5",
                     "gemini-2.5-flash", "gemini-2.5-pro", "ollama/llama3.2:3b"]
    locks = dict(c.get("stage_model_locks") or {})
    lcols = st.columns(2)
    for i, (stage, label) in enumerate(_stage_display.items()):
        with lcols[i % 2]:
            current_lock = locks.get(stage) or _lock_options[0]
            if current_lock not in _lock_options:
                current_lock = _lock_options[0]
            picked = st.selectbox(label, options=_lock_options,
                                  index=_lock_options.index(current_lock),
                                  key=f"ff_lock_{stage}")
            locks[stage] = None if picked == _lock_options[0] else picked
    c["stage_model_locks"] = locks

    st.markdown("#### Rate limits")
    rl_c1, rl_c2 = st.columns(2)
    with rl_c1:
        c["max_runs_per_hour"] = st.number_input(
            "Max runs / hour", min_value=1, max_value=100,
            value=int(c.get("max_runs_per_hour", 10)), key="ff_runs_hr")
    with rl_c2:
        c["max_cost_per_day_usd"] = st.number_input(
            "Max cost / day (USD)", min_value=0.5, max_value=50.0,
            value=float(c.get("max_cost_per_day_usd", 5.0)),
            step=0.5, format="%.2f", key="ff_cost_day")

    st.markdown("#### Feature toggles")
    _toggles = [
        ("compare_mode",    "A/B Compare mode",           "Doubles cost and time"),
        ("vision_input",    "Vision / whiteboard upload",  "Allow photo inputs"),
        ("dry_run_allowed", "Dry-run mode",                "Preview prompts without LLM calls"),
        ("jira_write_back", "Jira write-back",             "Push to Jira (approval gate always shown)"),
        ("live_jira_read",  "Live Jira read",              "Pull live backlog as input"),
        ("pii_override",    "PII masking override",        "Allow turning off PII masking"),
    ]
    t_cols = st.columns(2)
    for i, (key, label, hint) in enumerate(_toggles):
        with t_cols[i % 2]:
            c[key] = st.toggle(label, value=bool(c.get(key, False)),
                               help=hint, key=f"ff_toggle_{key}")

    flags["contributor"] = c
    st.divider()
    if st.button("💾  Save feature flags", type="primary", use_container_width=True,
                 key="ff_save_btn"):
        try:
            FeatureFlags.save(flags)
            # Reload so changes take effect immediately for this session.
            st.session_state.feature_flags = FeatureFlags.load()
            st.success("Saved. Feature flags updated — contributors will see the new settings on their next action.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Save failed: {e}")

    st.caption(
        "Saving writes `config/feature_flags.yaml`. In a multi-replica deployment, "
        "all replicas read the same file so the change propagates on their next page load."
    )


# Deferred dialog triggers — called here (after definition) based on sidebar button clicks.
if st.session_state.pop("_trigger_admin_settings", False):
    show_admin_settings_dialog()


# ---- Top-nav dialogs ----
@st.dialog("How it works", width="large")
def show_how_it_works_dialog() -> None:
    st.markdown(
        "**Backlog Synthesizer** runs five specialized agents in sequence — each does "
        "one job and writes to a shared, audited memory:\n\n"
        "1. **Parser** — extracts the distinct topics from the transcript.\n"
        "2. **Constraint Extractor** — reads the wiki for `must` / `forbidden` rules.\n"
        "3. **Story Writer** — drafts a user story (Given/When/Then AC + priority) per topic.\n"
        "4. **Epic Decomposer** — groups stories into epics and breaks each into tasks.\n"
        "5. **Gap Detector** — local embeddings flag duplicates against your backlog; the "
        "LLM flags conflicts vs. constraints and missing gaps.\n\n"
        "Every step is captured in an **audit trail**, and you can push the result straight "
        "into **Jira** as Epic → Story → Sub-task."
    )
    st.caption("Pick sources on the left (or upload your own / a whiteboard image), then Synthesize.")


@st.dialog("Export", width="large")
def show_export_dialog() -> None:
    res = st.session_state.get("result")
    if not res:
        st.info("Run a synthesis first — then export it here.")
        return
    from output_formatter import _render_markdown as _bm  # local import
    rd = st.session_state.get("run_dir")
    stem = rd.name if rd else datetime.now().strftime("%Y%m%d_%H%M%S")
    md = _bm(res, source_label=st.session_state.get("source_label", ""))
    js = json.dumps({k: v for k, v in res.items() if k != "audit_trail"}, indent=2)
    st.download_button("↓  synthesis.md", md, file_name=f"{stem}_synthesis.md",
                       mime="text/markdown", use_container_width=True)
    st.download_button("↓  synthesis.json", js, file_name=f"{stem}_synthesis.json",
                       mime="application/json", use_container_width=True)
    st.download_button("↓  audit_trail.md", res.get("audit_trail", ""),
                       file_name=f"{stem}_audit_trail.md", mime="text/markdown",
                       use_container_width=True)


@st.dialog("Create in Jira", width="large")
def show_jira_dialog() -> None:
    res = st.session_state.get("result")
    if not res:
        st.info("Run a synthesis first — then publish it to Jira here.")
        return
    if not _can_push_jira():
        st.error("Your account does not have permission to push to Jira.")
        return
    _ready = all(os.environ.get(k) for k in
                 ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"))
    if not _ready:
        st.warning("Set JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN / JIRA_PROJECT_KEY in `.env` to enable.")
        return
    _proj = os.environ.get("JIRA_PROJECT_KEY")

    # ---- Human-in-the-loop approval gate ----
    # Show a full review of what will be created BEFORE the button is active.
    # Contributors always see this gate; admins also see it (good hygiene).
    _epics = res.get("epics") or []
    _n_epics = len(_epics)
    _n_stories = sum(len(e.get("stories") or []) for e in _epics)
    _n_tasks = sum(len(s.get("tasks") or []) for e in _epics for s in (e.get("stories") or []))
    _n_conflicts = len(res.get("conflicts") or [])
    _n_gaps = len(res.get("gaps") or [])
    _guardrail_errors = sum(1 for f in (res.get("guardrail_findings") or []) if f.get("severity") == "error")

    st.markdown(
        f'<div style="padding:0.8rem 1rem;background:var(--bg-elev-1);border:1px solid var(--border);'
        f'border-left:3px solid var(--accent);border-radius:8px;margin-bottom:0.8rem;">'
        f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;'
        f'color:var(--accent);margin-bottom:0.4rem;">Review before publishing</div>'
        f'<div style="font-size:0.85rem;display:grid;grid-template-columns:1fr 1fr;gap:0.3rem 1.2rem;">'
        f'<span>📦 <strong>{_n_epics}</strong> epic(s)</span>'
        f'<span>📝 <strong>{_n_stories}</strong> story(ies)</span>'
        f'<span>✅ <strong>{_n_tasks}</strong> sub-task(s)</span>'
        f'<span>⚠ <strong>{_n_conflicts}</strong> conflict(s) flagged</span>'
        f'<span>🔍 <strong>{_n_gaps}</strong> gap(s) detected</span>'
        f'<span style="color:{"var(--rose)" if _guardrail_errors else "var(--text-muted)"};">'
        f'🛡 <strong>{_guardrail_errors}</strong> guardrail error(s)</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    if _guardrail_errors > 0:
        st.warning(
            f"**{_guardrail_errors} guardrail error(s) detected.** "
            "Review the Guardrails tab before publishing — these stories may have "
            "missing grounding or unresolvable constraint conflicts."
        )

    _subs = st.checkbox("Also create sub-tasks", value=True, key="jira_dlg_subtasks")

    # ---- Squad / team assignment ----
    st.markdown(
        '<div style="font-size:0.62rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;'
        'color:var(--text-muted);margin:0.7rem 0 0.3rem;">Team Assignment</div>',
        unsafe_allow_html=True,
    )
    _SQUADS = [
        "— Unassigned —",
        "Platform Engineering",
        "Mobile",
        "Backend",
        "Frontend",
        "Data & Analytics",
        "QA / Testing",
        "DevOps / Infrastructure",
        "Product",
    ]
    col_sq, col_comp = st.columns(2)
    with col_sq:
        _squad = st.selectbox("Squad", _SQUADS, index=0, key="jira_dlg_squad")
    with col_comp:
        _component = st.text_input("Jira Component (optional)", placeholder="e.g. loyalty-api", key="jira_dlg_component")

    _squad_label = _squad if _squad != "— Unassigned —" else ""

    st.markdown(
        f'<div style="padding:0.5rem 0.8rem;background:rgba(251,113,133,.08);'
        f'border:1px solid rgba(251,113,133,.3);border-radius:6px;margin:0.5rem 0;font-size:0.82rem;">'
        f'⚠ This will create <strong>{_n_epics} epic(s)</strong>, '
        f'<strong>{_n_stories} story(ies)</strong>, and up to '
        f'<strong>{_n_tasks} sub-task(s)</strong> as <em>real issues</em> in '
        f'<strong>{_esc(_proj)}</strong>. This action cannot be automatically undone.</div>',
        unsafe_allow_html=True,
    )

    # Mandatory confirmation for contributors; admins can also confirm (same gate).
    _confirmed = st.checkbox(
        f"I confirm: create {_n_epics} epic(s), {_n_stories} story(ies), "
        f"up to {_n_tasks} sub-task(s) in **{_proj}**",
        value=False,
        key="jira_dlg_confirm",
    )

    if st.button(
        f"⤴  Create in Jira ({_proj})",
        type="primary",
        use_container_width=True,
        key="jira_dlg_go",
        disabled=not _confirmed,
    ):
        with st.spinner(f"Creating issues in {_proj}…"):
            try:
                from tools.jira_tool import JiraTool
                _pub_label = "-".join(filter(None, ["backlog-synth", _squad_label.lower().replace(" ", "-")])) if _squad_label else "backlog-synth"
                _pub_result = JiraTool(mode="live").publish_synthesis(
                    res, create_subtasks=_subs, label=_pub_label)
                st.session_state["jira_publish_result"] = _pub_result
                if not _pub_result.get("error"):
                    try:
                        from alerts import post_jira_push_notification
                        _c = _pub_result.get("counts", {})
                        post_jira_push_notification(
                            user=_current_user or "",
                            project=_proj or "",
                            epic_count=_c.get("epics", 0),
                            story_count=_c.get("stories", 0),
                            subtask_count=_c.get("subtasks", 0),
                        )
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                st.session_state["jira_publish_result"] = {"error": str(e)}

    if not _confirmed:
        st.caption("Tick the confirmation checkbox above to enable the Create button.")

    _pub = st.session_state.get("jira_publish_result")
    if _pub:
        if _pub.get("error"):
            st.error(f"Jira publish failed: {_pub['error']}")
        else:
            _c = _pub["counts"]
            st.success(f"Created {_c['epics']} epic(s), {_c['stories']} story(ies), "
                       f"{_c['subtasks']} sub-task(s) in {_pub['project']}.")
            for _it in _pub["created"]:
                if _it["level"] in ("epic", "story"):
                    _pad = "" if _it["level"] == "epic" else "&nbsp;&nbsp;&nbsp;&nbsp;↳ "
                    st.markdown(f'{_pad}<a href="{_it["url"]}" target="_blank">{_it["key"]}</a> — {_it["summary"]}',
                                unsafe_allow_html=True)

            # ── Two-way sync: read back current Jira status ───────────────────
            st.divider()
            if st.button("🔄  Sync status from Jira", key="jira_sync_btn",
                         use_container_width=True,
                         help="Read back current status, assignee and priority from live Jira"):
                with st.spinner("Fetching current status from Jira…"):
                    try:
                        from tools.jira_tool import JiraTool as _JT2
                        _sync_statuses = _JT2(mode="live").sync_published_stories(_pub)
                        st.session_state["jira_sync_statuses"] = _sync_statuses
                    except Exception as _se:
                        st.error(f"Sync failed: {_se}")

            _sync = st.session_state.get("jira_sync_statuses")
            if _sync:
                st.markdown("**Current Jira status:**")
                _status_colors = {
                    "To Do": "#64748b", "In Progress": "#f59e0b",
                    "Done": "#22c55e", "Closed": "#22c55e",
                    "In Review": "#8b5cf6", "Blocked": "#ef4444",
                }
                for _s in _sync:
                    _sc = _status_colors.get(_s["status"], "#94a3b8")
                    _assignee = _s["assignee"] or "Unassigned"
                    st.markdown(
                        f'<div style="display:flex;align-items:center;justify-content:space-between;'
                        f'padding:6px 10px;background:var(--bg-elev-1);border-radius:6px;'
                        f'margin-bottom:4px;font-size:0.8rem;">'
                        f'<span><a href="{_s["url"]}" target="_blank" style="color:var(--accent);'
                        f'text-decoration:none;font-weight:600;">{_esc(_s["key"])}</a>'
                        f' &nbsp;<span style="color:var(--text-muted);">{_esc(_s["summary"][:50])}</span></span>'
                        f'<span style="display:flex;gap:8px;align-items:center;">'
                        f'<span style="font-size:0.68rem;color:{_sc};font-weight:700;'
                        f'background:{_sc}22;padding:2px 8px;border-radius:10px;">{_esc(_s["status"])}</span>'
                        f'<span style="color:var(--text-faint);font-size:0.72rem;">{_esc(_assignee)}</span>'
                        f'</span></div>',
                        unsafe_allow_html=True,
                    )


if st.session_state.pop("_trigger_jira", False):
    show_jira_dialog()


# ---- Header + adaptive top-right nav ----
_result_exists = bool(st.session_state.get("result"))
_hdr_left, _hdr_right = st.columns(([5, 5] if _result_exists else [6, 3]),
                                   vertical_alignment="center")
with _hdr_left:
    st.markdown(
        '<div class="app-header">'
        '<span class="app-mark">◆</span>'
        '<div><div class="app-title">Synthesize epics, stories and tasks</div>'
        '<div class="app-tagline">'
        "Turn a meeting transcript, an architecture wiki, and your live backlog into an "
        "audited, conflict-checked sprint backlog — in one ~30-second multi-agent pass."
        "</div></div></div>",
        unsafe_allow_html=True,
    )
with _hdr_right:
    _navs = [("home", "⌂ Home"), ("history", "⌕ History"), ("help", "❓ Help")]
    if _result_exists:
        _navs += [("export", "↓ Export"), ("jira", "⤴ Jira")]
    _nav_cols = st.columns(len(_navs))
    _nav_clicked: dict[str, bool] = {}
    _primary_nav = {"jira", "export"}
    for _col, (_key, _label) in zip(_nav_cols, _navs):
        with _col:
            _nav_clicked[_key] = st.button(
                _label, key=f"nav_{_key}",
                use_container_width=True,
                type="primary" if _key in _primary_nav else "secondary",
            )

if _nav_clicked.get("home"):
    for _k in ("result", "run_dir", "dry_run_result", "jira_publish_result"):
        st.session_state[_k] = None
    st.rerun()
if _nav_clicked.get("history"):
    show_run_history_dialog()
if _nav_clicked.get("help"):
    show_how_it_works_dialog()
if _nav_clicked.get("export"):
    show_export_dialog()
if _nav_clicked.get("jira"):
    show_jira_dialog()

_pipeline_placeholder = st.empty()
_progress_placeholder = st.empty()

with _pipeline_placeholder.container():
    # Always use the CURRENT sidebar selection (st.session_state.models) for the
    # pre-run pipeline cards so per-stage overrides are reflected immediately.
    # Only use result["models"] AFTER a run to show what was actually used —
    # but even then, show the current selection if it has changed since the run.
    _last_run_models = (st.session_state.get("result") or {}).get("models") or {}
    _current_models  = dict(st.session_state.get("models") or {})
    _display_models  = _current_models if _current_models else _last_run_models
    _render_pipeline(
        stage_states=st.session_state.get("stage_states"),
        model=st.session_state.get("model_used") or None,
        token_usage=st.session_state.get("token_usage") or None,
        models_per_stage=_display_models,
    )


# -------------------------------------------------------- run handler

def _as_upload_list(uploaded) -> list:
    """Normalise the uploader return (None / single / list) to a clean list."""
    if uploaded is None:
        return []
    if isinstance(uploaded, list):
        return [u for u in uploaded if u is not None]
    return [uploaded]


def _read_one_text(uploaded) -> str:
    name = uploaded.name
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        tmp = ROOT / "logs" / f"_upload_{int(time.time() * 1000)}_{name}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(uploaded.getvalue())
        try:
            return load_text(str(tmp))
        finally:
            tmp.unlink(missing_ok=True)
    return uploaded.getvalue().decode("utf-8", errors="replace")


def _read_uploaded_text(uploaded) -> str:
    """Read one or more uploaded text/pdf files into a single string.

    Multiple files are concatenated with a labelled separator so the Parser
    sees one combined source while still being able to tell the documents
    apart (e.g. several meeting transcripts, or a transcript + a Slack export)."""
    files = _as_upload_list(uploaded)
    if not files:
        return ""
    if len(files) == 1:
        return _read_one_text(files[0])
    parts = [f"===== {f.name} =====\n{_read_one_text(f)}" for f in files]
    return "\n\n".join(parts)


def _read_uploaded_tickets(uploaded) -> list[dict]:
    """Read and MERGE tickets from one or more uploaded JSON backlog files.

    Each file may be a list of tickets or a `{"items": [...]}` wrapper.
    All tickets across files are concatenated into one backlog."""
    merged: list[dict] = []
    for f in _as_upload_list(uploaded):
        raw = f.getvalue().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise InputError(f"Backlog JSON parse error in {f.name}: {e}") from e
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        if not isinstance(data, list):
            raise InputError(f"Backlog JSON in {f.name} must be a list of tickets (or an {{\"items\": [...]}} object).")
        merged.extend(data)
    return merged


def _resolve_text(selected, options: dict, uploaded):
    """Combine all selected sample docs + any uploaded files into one text
    source. Returns (combined_text, [source_names]). Multiple sources are
    concatenated with a labelled separator so the Parser can still tell them
    apart; a single source is returned as-is."""
    pairs: list[tuple[str, str]] = []  # (name, text)
    for lbl in (selected or []):
        val = options.get(lbl)
        if val and val != "__upload__":
            pairs.append((Path(str(val)).name, load_text(str(val))))
    for f in _as_upload_list(uploaded):
        pairs.append((f.name, _read_one_text(f)))
    if not pairs:
        return "", []
    if len(pairs) == 1:
        return pairs[0][1], [pairs[0][0]]
    combined = "\n\n".join(f"===== {n} =====\n{t}" for n, t in pairs)
    return combined, [n for n, _ in pairs]


def _resolve_tickets(selected, options: dict, uploaded) -> list[dict]:
    """Merge tickets from all selected sample backlogs + uploaded JSON files."""
    merged: list[dict] = []
    for lbl in (selected or []):
        val = options.get(lbl)
        if val and val != "__upload__":
            merged.extend(load_tickets(str(val)))
    merged.extend(_read_uploaded_tickets(uploaded))
    return merged


# The sidebar Synthesize button and the home-screen ANALYZE button both
# trigger the same pipeline. The latter sets `_pending_run` and reruns
# (because the button isn't bound at script-init time), so we consume
# the flag here and clear it before invoking the pipeline.
_main_canvas_run = bool(st.session_state.pop("_pending_run", False))

if run_clicked or _main_canvas_run:
    # ---- Rate limit check ----
    try:
        check_rate_limit(_current_user)
    except RateLimitError as _rle:
        st.error(f"**Rate limit reached:** {_rle}")
        st.stop()

    # ---- Resolve inputs ----
    # Each picker is multi-select: combine every chosen sample (+ uploads)
    # into one source. Transcripts/wikis are concatenated; backlogs merged.
    try:
        transcript_text, _t_names = _resolve_text(
            transcript_choice, TRANSCRIPT_OPTIONS, transcript_upload)
        if not _t_names:
            source_label = "(uploaded)"
        elif len(_t_names) == 1:
            source_label = _t_names[0]
        else:
            source_label = (
                f"{len(_t_names)} sources: " + ", ".join(_t_names[:3])
                + ("…" if len(_t_names) > 3 else "")
            )

        constraint_text, _ = _resolve_text(
            constraints_choice, CONSTRAINTS_OPTIONS, constraints_upload)

        existing_tickets = _resolve_tickets(
            backlog_choice, BACKLOG_OPTIONS, backlog_upload)
    except InputError as e:
        st.error(f"Could not load inputs: {e}")
        st.stop()

    # ---- Default-on PII redaction for uploaded content ----
    # Any user-uploaded file may contain real customer/employee PII.
    # Regardless of the sidebar toggle, force redaction when the user
    # uploaded files rather than selecting bundled samples.
    # Admins can override by setting redact_pii=False in the sidebar
    # (the toggle is disabled for non-admins so they always land here).
    _has_user_uploads = bool(
        _as_upload_list(transcript_upload)
        or _as_upload_list(constraints_upload)
        or _as_upload_list(backlog_upload)
    )
    if _has_user_uploads and not _is_admin():
        redact_pii = True  # enforce for contributor + viewer uploads

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

    # Cancel support — a threading.Event lets the progress callback signal
    # the run thread to abort cleanly between stages.
    _cancel_event = threading.Event()

    try:
        orch = Orchestrator()
    except Exception as e:
        _progress_placeholder.error(f"Orchestrator init failed: {e}")
        st.stop()

    # Per-stage start timestamps so completed/failed events can report
    # how long the stage actually took. Reset on every pipeline run.
    stage_started_at: dict[int, float] = {}
    # Running log of every agent event. We APPEND to this (rather than
    # overwriting the placeholder per event) so each agent's lines stay
    # visible as the next stage runs.
    progress_log: list[str] = []

    # Data source info is shown inline in each stage that actually fetches data
    # (Constraint Extractor for Confluence, Gap Detector for Jira + GitHub).
    # No pre-pipeline "Data sources" banner — only show it when it's used.
    # Track failovers / failures so we can show an end-of-run summary, a toast,
    # and a persistent badge — nothing changes provider silently.
    _events_seen = {"failover": [], "failed": []}

    def _render_log():
        _progress_placeholder.markdown(
            '<div class="progress-log">' + "".join(progress_log) + "</div>",
            unsafe_allow_html=True,
        )

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
        elif event == "failover":
            stage_states[stage_index] = "active"  # still working, just on the other provider

        pretty_name = stage_name.replace("_", " ").title()
        if event == "failover":
            _events_seen["failover"].append(pretty_name)
        elif event == "failed":
            _events_seen["failed"].append(pretty_name)

        # Build an "elapsed" suffix once the stage finishes so reviewers
        # can see which agent dominates wall time.
        elapsed_suffix = ""
        if event in ("completed", "failed") and stage_index in stage_started_at:
            secs = now - stage_started_at[stage_index]
            elapsed_suffix = f" · {secs:.1f}s"

        st.session_state["current_stage"] = stage_index
        icon = {"started": "▸", "completed": "✓", "failed": "✗",
                "skipped": "–", "failover": "⚠"}.get(event, "·")
        evt_label = "FAILOVER" if event == "failover" else _esc(event)
        entry = (
            f'<div class="log-line log-{_esc(event)}">'
            f'<span class="log-icon">{icon}</span>'
            f'<strong>{_esc(pretty_name)}</strong> '
            f'<span class="log-evt">{evt_label}</span>'
            f'{(" · " + _esc(detail)) if detail else ""}{elapsed_suffix}</div>'
        )
        progress_log.append(entry)
        # NOTE: no UI calls here — the main thread polls and renders every
        # 300 ms via the loop below. Calling st.* from a background thread
        # works inconsistently across Streamlit versions; the polling loop
        # is the reliable approach that actually live-streams to the browser.

        # Check for user-initiated cancel between stages. Only interrupt on
        # "completed" or "skipped" events so we never cut a stage mid-call.
        if event in ("completed", "skipped") and _cancel_event.is_set():
            raise _PipelineCancelled("Run cancelled by user.")

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
    _vision_sample_labels = st.session_state.get("vision_samples") or []
    if _vision_files or _vision_sample_labels:
        try:
            from tools.base import VisionAttachment
            _vision_atts = []
            # Bundled sample images selected directly from the dropdown.
            for _lbl in _vision_sample_labels:
                _p = VISION_SAMPLE_OPTIONS.get(_lbl)
                if _p:
                    _vision_atts.append(VisionAttachment.from_path(_p))
            # Plus any user uploads.
            for f in _vision_files:
                _vision_atts.append(
                    VisionAttachment.from_bytes(
                        f.getvalue(),
                        media_type=getattr(f, "type", "image/png"),
                        label=getattr(f, "name", "upload"),
                    )
                )
            if not _vision_atts:
                _vision_atts = None
        except Exception as e:  # noqa: BLE001
            st.warning(f"Skipping vision attachments: {e}")
            _vision_atts = None

    # Capture all session-state values NOW (main thread) before starting
    # the background thread. st.session_state is NOT thread-safe — reading
    # it from a daemon thread causes "has no attribute 'models'" errors.
    # Apply any admin-configured stage model locks for this role.
    # e.g. if story_writer is locked to "claude-sonnet-4-5", that overrides
    # whatever preset the contributor selected.
    _thread_models = _ff.apply_stage_locks(
        _current_role, dict(st.session_state.get("models") or {})
    )
    _thread_is_compare   = bool(st.session_state.get("compare_enabled"))
    _thread_compare_pset = st.session_state.get("compare_with_preset", "free")
    _thread_active_pset  = (st.session_state.get("active_preset") or "balanced").title()
    _thread_auto_switch  = bool(st.session_state.get("auto_switch"))

    # Thread the pipeline so the Cancel button stays responsive. The run
    # executes in a daemon thread; the main thread polls a result queue.
    # _PipelineCancelled raised in the progress callback propagates out of
    # orch.run() and is caught in the thread, put on the queue as an error.
    _result_q: _queue.Queue = _queue.Queue()

    def _run_pipeline():
        try:
            if _thread_is_compare:
                secondary_models = dict(MODEL_PRESETS.get(
                    _thread_compare_pset, MODEL_PRESETS["free"]
                ))
                cmp = orch.run_compare(
                    primary_models=_thread_models,
                    secondary_models=secondary_models,
                    primary_label=_thread_active_pset,
                    secondary_label=_thread_compare_pset.title(),
                    progress_callback=_on_progress,
                    transcript_text=transcript_text,
                    constraint_text=constraint_text,
                    existing_tickets=existing_tickets,
                    redact_pii=redact_pii,
                    live_confluence_page_id=_live_conf_pid or None,
                    live_jira=_use_live_jira,
                    vision_attachments=_vision_atts,
                    auto_switch=_thread_auto_switch,
                )
                r = cmp["primary"]
                r["_compare_secondary"] = cmp["secondary"]
                r["_compare_summary"] = cmp["comparison"]
                r["_compare_labels"] = cmp["labels"]
                _result_q.put(("ok", r))
            else:
                r = orch.run(
                    transcript_text=transcript_text,
                    constraint_text=constraint_text,
                    existing_tickets=existing_tickets,
                    redact_pii=redact_pii,
                    progress_callback=_on_progress,
                    models=_thread_models,
                    live_confluence_page_id=_live_conf_pid or None,
                    live_jira=_use_live_jira,
                    vision_attachments=_vision_atts,
                    auto_switch=_thread_auto_switch,
                    run_metadata={
                        "user_id":      _current_user,
                        "role":         _current_role,
                        "preset":       st.session_state.get("active_preset", "unknown"),
                        "source_label": source_label,
                        "auth_disabled": _auth_disabled,
                    },
                )
                _result_q.put(("ok", r))
        except _PipelineCancelled:
            _result_q.put(("cancelled", None))
        except Exception as exc:  # noqa: BLE001
            _result_q.put(("error", exc))

    # Cancel button — visible during the run above the progress log.
    # Graceful cancel: sets _cancel_event which the progress callback checks
    # between stages. The run thread raises _PipelineCancelled on the next
    # completed/skipped event so the current stage always finishes cleanly.
    _cancel_col1, _cancel_col2 = st.columns([3, 1])
    with _cancel_col2:
        _cancel_placeholder = st.empty()
        if _cancel_placeholder.button(
            "✕  Cancel run",
            key="cancel_run_btn",
            use_container_width=True,
            type="secondary",
            help="Stops the pipeline between stages. Results up to this point are preserved.",
        ):
            _cancel_event.set()

    _thread = threading.Thread(target=_run_pipeline, daemon=True)
    _thread.start()

    # Live-stream the progress log to the browser.
    # _thread.join() was used before but it blocks the main Streamlit thread,
    # preventing the browser from receiving any WebSocket updates until the
    # entire run finishes. The polling loop below lets the main thread render
    # every 300 ms so each stage appears as it completes.
    while _thread.is_alive():
        with _pipeline_placeholder.container():
            _render_pipeline(
                stage_states=list(stage_states),  # snapshot to avoid race
                model=st.session_state.get("model_used") or None,
                token_usage=None,
                models_per_stage=_thread_models,
            )
        _render_log()
        time.sleep(0.3)

    _thread.join()  # ensure thread is fully done before reading results
    _cancel_placeholder.empty()  # remove cancel button once run finishes

    _status, _payload = _result_q.get()
    if _status == "cancelled":
        progress_log.append(
            '<div class="log-line log-failed"><span class="log-icon">✕</span>'
            '<strong>Run cancelled by user</strong></div>'
        )
        _render_log()
        st.warning("Run cancelled — partial results (if any) were not saved.")
        st.stop()
    elif _status == "error":
        _progress_placeholder.error(f"Pipeline failed: {_payload}")
        try:
            from alerts import post_pipeline_failure_notification
            post_pipeline_failure_notification(
                user=_current_user or "",
                source_label=source_label,
                error=str(_payload),
            )
        except Exception:  # noqa: BLE001
            pass
        st.stop()

    result = _payload
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
    progress_log.append(f'<div class="log-line log-done"><span class="log-icon">✓</span>{summary_tag}</div>')

    # ---- Failover / failure summary (so nothing is missed) ----
    _fo = _events_seen["failover"]
    _fl = _events_seen["failed"]
    st.session_state["failover_count"] = len(_fo)
    st.session_state["failed_count"] = len(_fl)
    if _fo:
        progress_log.append(
            '<div class="log-line log-failover"><span class="log-icon">⚠</span>'
            f'<strong>Provider failover</strong> · {len(_fo)} stage(s) switched provider: '
            f'{_esc(", ".join(_fo))}</div>'
        )
    if _fl:
        progress_log.append(
            '<div class="log-line log-failed"><span class="log-icon">✗</span>'
            f'<strong>Failed</strong> · {len(_fl)} stage(s): {_esc(", ".join(_fl))}</div>'
        )
        try:
            from alerts import post_pipeline_failure_notification
            post_pipeline_failure_notification(
                user=_current_user or "",
                source_label=source_label,
                error=f"{len(_fl)} stage(s) failed: {', '.join(_fl)}",
                partial=True,
            )
        except Exception:  # noqa: BLE001
            pass
    _render_log()
    try:
        if _fl:
            st.toast(f"⚠ {len(_fl)} stage(s) failed: {', '.join(_fl)}", icon="⚠️")
        elif _fo:
            st.toast(f"⚠ {len(_fo)} stage(s) failed over to the other provider", icon="⚠️")
    except Exception:  # noqa: BLE001 — toast is best-effort
        pass

    # ---- Persist outputs ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Scope outputs to the current user — outputs/<user_id>/<timestamp>/
    _safe_uid = "".join(c if c.isalnum() or c in "-_." else "_" for c in (_current_user or "anonymous"))
    run_dir = OUTPUTS_BASE / _safe_uid / stamp
    # `result` from the orchestrator now includes `token_usage` and `model`.
    # write_outputs reads the synthesis content fields; the extras are
    # carried through to the JSON dump as well — useful downstream.
    synth_payload = {k: v for k, v in result.items() if k != "audit_trail"}
    json_path, md_path = write_outputs(synth_payload, run_dir, source_label=source_label)
    audit_path = run_dir / "audit_trail.md"
    audit_path.write_text(result["audit_trail"], encoding="utf-8")

    # Persist original uploaded files so every run has a complete input→output record.
    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(exist_ok=True)
    for _uf in _as_upload_list(transcript_upload):
        (inputs_dir / _uf.name).write_bytes(_uf.getvalue())
    for _uf in _as_upload_list(constraints_upload):
        (inputs_dir / _uf.name).write_bytes(_uf.getvalue())
    for _uf in _as_upload_list(backlog_upload):
        (inputs_dir / _uf.name).write_bytes(_uf.getvalue())

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
        "user_id": _current_user,
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

    # Fire Slack/Teams synthesis-complete notification (SLACK_WEBHOOK_URL).
    try:
        from alerts import post_synthesis_notification
        post_synthesis_notification(
            run_id=history_summary["run_id"],
            user=_current_user or "anonymous",
            source_label=source_label,
            epic_count=len(epics),
            story_count=n_stories,
            gap_count=len(result.get("gaps") or []),
            conflict_count=len(result.get("conflicts") or []),
            elapsed_seconds=elapsed,
            cost_usd=cost_usd,
        )
    except Exception:  # noqa: BLE001 — notification must never fail a run
        pass


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
        <div class="empty-state explainer-card">
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
    # sidebar's Synthesize button. Placed below the explainer card so it's
    # the last thing the eye lands on before clicking.
    st.markdown("<div style='height:0.8rem'/>", unsafe_allow_html=True)
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

else:
    # ---- Run-meta strip ----
    elapsed = st.session_state.elapsed or 0
    tokens = st.session_state.tokens_total or 0
    cost = st.session_state.cost_usd or 0.0
    model = st.session_state.model_used or "—"

    _cost_meta = ""
    if _is_admin():
        cost_label = f"${cost:.4f}" if cost > 0 else "—"
        _cost_meta = (
            '<span class="run-meta-sep">·</span>'
            f'<span class="run-meta-item"><span class="run-meta-icon">$</span>'
            f'<span class="run-meta-label">Cost</span>{cost_label}</span>'
        )

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
        f'{_cost_meta}'
        '</div>',
        unsafe_allow_html=True,
    )

    _render_kpis(result)

    # Persistent failover / failure badge so it's visible even after the live
    # log scrolls away (the audit trail has the full detail).
    _fo_n = int(st.session_state.get("failover_count") or 0)
    _fl_n = int(st.session_state.get("failed_count") or 0)
    if _fo_n or _fl_n:
        _bits = []
        if _fl_n:
            _bits.append(f"✗ {_fl_n} stage(s) failed")
        if _fo_n:
            _bits.append(f"⚠ {_fo_n} stage(s) failed over to the other provider")
        st.warning(" · ".join(_bits) + " — see the **Audit trail** tab for details.")

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
    # Jira push — most important CTA, shown as primary button when ready
    _jira_cta_ready = bool(
        _can_push_jira()
        and os.environ.get("JIRA_BASE_URL")
        and os.environ.get("JIRA_API_TOKEN")
        and os.environ.get("JIRA_PROJECT_KEY")
    )
    if _jira_cta_ready and n_stories > 0:
        actions.append((
            "push_jira",
            f"⤴  Push to Jira ({os.environ.get('JIRA_PROJECT_KEY')})",
            "primary",
        ))

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
                    st.toast("Edit mode on — open the Epics tab to edit", icon="✏️")
                elif akey == "review":
                    st.session_state.stories_edit_mode = False
                    st.toast("Stories are in the Epics tab below", icon="📋")
                elif akey == "export":
                    st.toast("Download buttons are inside each tab", icon="⬇️")
                elif akey == "push_jira":
                    show_jira_dialog()
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

    # Jira write-back is available via the "⤴ Jira" top-nav button.
    # No duplicate inline section — keeps the Downloads area clean.
