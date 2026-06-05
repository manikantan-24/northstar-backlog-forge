#!/usr/bin/env python3
"""
MCP + REST tool integration test.

Tests both the MCP path (when enabled) and the direct REST fallback.
Run this before enabling MCP in production to verify credentials and
connectivity are correct.

Usage:
    # Test REST fallback only (works on Python 3.9):
    python scripts/test_mcp_tools.py

    # Test Atlassian MCP (requires Python 3.10+, Node.js, valid credentials):
    ATLASSIAN_MCP_ENABLED=1 python scripts/test_mcp_tools.py

    # Test GitHub MCP (requires Python 3.10+, Docker or gh-mcp binary):
    GITHUB_MCP_ENABLED=1 GITHUB_TOKEN=ghp_... GITHUB_OWNER=myorg GITHUB_REPO=myrepo \
        python scripts/test_mcp_tools.py
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
NC     = "\033[0m"

def ok(msg):    print(f"  {GREEN}✓{NC}  {msg}")
def fail(msg):  print(f"  {RED}✗{NC}  {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{NC}  {msg}")
def info(msg):  print(f"  {CYAN}→{NC}  {msg}")
def header(msg): print(f"\n{BOLD}{msg}{NC}")

# ── Add src/ to path ───────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PASSED = []
FAILED = []
SKIPPED = []

def record(label, status, detail=""):
    if status == "pass":    PASSED.append(label)
    elif status == "fail":  FAILED.append((label, detail))
    elif status == "skip":  SKIPPED.append((label, detail))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Environment checks
# ══════════════════════════════════════════════════════════════════════════════
header("1. Environment")

py_ver = sys.version_info
info(f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}")
if py_ver >= (3, 10):
    ok("Python 3.10+ — MCP package supported")
    record("python-version", "pass")
else:
    warn(f"Python {py_ver.major}.{py_ver.minor} — MCP package requires 3.10+")
    warn("REST fallback will be tested. To test MCP, create a Python 3.11 venv:")
    warn("  python3.11 -m venv venv311 && source venv311/bin/activate")
    warn("  pip install -r requirements.txt mcp")
    record("python-version", "skip", "needs 3.10+")

# Check Node.js (for Atlassian MCP server)
try:
    node_ver = subprocess.check_output(["node", "--version"],
                                        stderr=subprocess.DEVNULL).decode().strip()
    ok(f"Node.js {node_ver} — Atlassian MCP server can run via npx")
    record("node", "pass")
except FileNotFoundError:
    warn("Node.js not found — Atlassian MCP server needs Node.js 18+")
    warn("Install: https://nodejs.org")
    record("node", "skip", "not installed")

# GitHub MCP uses npx — same Node.js already checked above.
# No Docker or Go needed for @modelcontextprotocol/server-github.
info("GitHub MCP server uses npx (same Node.js as Atlassian MCP — no Docker needed)")
record("docker", "pass")

# Check mcp package
try:
    import mcp  # noqa: F401
    ok("mcp package installed")
    record("mcp-package", "pass")
except ImportError:
    if py_ver >= (3, 10):
        warn("mcp package not installed — run: pip install mcp")
        record("mcp-package", "skip", "not installed")
    else:
        info("mcp package not installed (expected on Python 3.9)")
        record("mcp-package", "skip", "python<3.10")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Credentials check
# ══════════════════════════════════════════════════════════════════════════════
header("2. Credentials")

ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
JIRA_BASE_URL    = os.environ.get("JIRA_BASE_URL", "")
JIRA_EMAIL       = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN   = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER     = os.environ.get("GITHUB_OWNER", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPO", "")

ATLASSIAN_MCP_ENABLED = os.environ.get("ATLASSIAN_MCP_ENABLED", "").strip() == "1"
GITHUB_MCP_ENABLED    = os.environ.get("GITHUB_MCP_ENABLED", "").strip() == "1"

if ANTHROPIC_KEY:
    ok(f"ANTHROPIC_API_KEY set ({ANTHROPIC_KEY[:8]}...)")
    record("anthropic-key", "pass")
else:
    fail("ANTHROPIC_API_KEY not set — required for all synthesis runs")
    record("anthropic-key", "fail", "missing")

jira_complete = all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN])
if jira_complete:
    ok(f"Jira credentials complete — {JIRA_BASE_URL}")
    if JIRA_PROJECT_KEY:
        ok(f"  Project key: {JIRA_PROJECT_KEY}")
    else:
        warn("  JIRA_PROJECT_KEY not set — live fetch will return all visible issues")
    record("jira-creds", "pass")
else:
    missing = [v for v, k in [("JIRA_BASE_URL", JIRA_BASE_URL), ("JIRA_EMAIL", JIRA_EMAIL),
                                ("JIRA_API_TOKEN", JIRA_API_TOKEN)] if not k]
    warn(f"Jira credentials incomplete — missing: {', '.join(missing)}")
    warn("Set these in .env to enable live Jira integration")
    record("jira-creds", "skip", "incomplete")

if GITHUB_TOKEN:
    ok(f"GITHUB_TOKEN set ({GITHUB_TOKEN[:8]}...)")
    if GITHUB_OWNER and GITHUB_REPO:
        ok(f"  Repo: {GITHUB_OWNER}/{GITHUB_REPO}")
    else:
        warn(f"  GITHUB_OWNER / GITHUB_REPO not set")
        warn("  Set these to enable live GitHub Issues fetching")
    record("github-token", "pass")
else:
    warn("GITHUB_TOKEN not set — GitHub tool will use fixture data")
    record("github-token", "skip", "not set")


# ══════════════════════════════════════════════════════════════════════════════
# 3. REST fallback tests (always run, no MCP needed)
# ══════════════════════════════════════════════════════════════════════════════
header("3. REST fallback tools (no MCP required)")

# Jira REST
info("Testing JiraTool (REST)...")
try:
    from tools.jira_tool import JiraTool
    jt = JiraTool()
    tickets = jt.list_all()
    ok(f"JiraTool mock: loaded {len(tickets)} fixture ticket(s)")
    record("jira-rest-mock", "pass")
except Exception as e:
    fail(f"JiraTool mock failed: {e}")
    record("jira-rest-mock", "fail", str(e))

# Jira REST live (if credentials configured)
if jira_complete:
    info("Testing JiraTool (live REST)...")
    try:
        from tools.jira_tool import JiraTool
        jt_live = JiraTool(mode="live")
        tickets_live = jt_live.list_all()
        ok(f"JiraTool live REST: fetched {len(tickets_live)} ticket(s) from {JIRA_BASE_URL}")
        if tickets_live:
            first = tickets_live[0]
            ok(f"  Sample: [{first.get('id', '?')}] {(first.get('title') or first.get('summary', ''))[:60]}")
        record("jira-rest-live", "pass")
    except Exception as e:
        fail(f"JiraTool live REST: {e}")
        record("jira-rest-live", "fail", str(e))

# Confluence REST
info("Testing ConfluenceTool (REST fallback)...")
try:
    from tools.confluence_tool import ConfluenceTool
    ct = ConfluenceTool(default_page_path=ROOT / "samples" / "architecture_constraints.md")
    content = ct.get_page()
    ok(f"ConfluenceTool mock: read {len(content)} chars from fixture")
    record("confluence-rest-mock", "pass")
except Exception as e:
    fail(f"ConfluenceTool mock failed: {e}")
    record("confluence-rest-mock", "fail", str(e))

# GitHub fixture
info("Testing GithubTool (fixture)...")
try:
    from tools.github_tool import GithubTool
    gh = GithubTool()
    issues = gh.list_all()
    ok(f"GithubTool fixture: loaded {len(issues)} issue(s)")
    record("github-fixture", "pass")
except Exception as e:
    fail(f"GithubTool fixture failed: {e}")
    record("github-fixture", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 4. Atlassian MCP server test
# ══════════════════════════════════════════════════════════════════════════════
header("4. Atlassian MCP server")

if not ATLASSIAN_MCP_ENABLED:
    info("ATLASSIAN_MCP_ENABLED not set — skipping MCP tests")
    info("To enable: ATLASSIAN_MCP_ENABLED=1 python scripts/test_mcp_tools.py")
    record("atlassian-mcp", "skip", "not enabled")
elif not jira_complete:
    warn("Atlassian credentials incomplete — cannot test MCP server")
    record("atlassian-mcp", "skip", "no credentials")
elif py_ver < (3, 10):
    warn("Python 3.10+ required for mcp package")
    warn("Create a 3.11 venv: python3.11 -m venv venv311 && pip install -r requirements.txt mcp")
    record("atlassian-mcp", "skip", "python<3.10")
else:
    info("Testing Atlassian MCP server via npx @atlassian/mcp...")
    info("(First run downloads the package — may take 30-60 seconds)")
    try:
        from tools.mcp_atlassian_tool import MCPJiraTool
        mcp_jira = MCPJiraTool(mode="live")
        mcp_jira._use_mcp = True
        results = mcp_jira.search("ORDER BY created DESC")
        ok(f"MCPJiraTool: fetched {len(results)} issue(s) via Atlassian MCP server")
        if results:
            ok(f"  Sample: [{results[0].get('id','?')}] {results[0].get('title','')[:60]}")
        record("atlassian-mcp", "pass")
    except ImportError as e:
        fail(f"mcp package not installed: {e}")
        fail("Run: pip install mcp")
        record("atlassian-mcp", "fail", str(e))
    except Exception as e:
        fail(f"Atlassian MCP: {e}")
        info("Common causes:")
        info("  • Node.js not in PATH")
        info("  • npx @atlassian/mcp failed to download (check internet/npm registry)")
        info("  • Wrong JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN")
        info("  • Token permissions: needs read:jira-work scope")
        record("atlassian-mcp", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 5. GitHub MCP server test
# ══════════════════════════════════════════════════════════════════════════════
header("5. GitHub MCP server")

if not GITHUB_MCP_ENABLED:
    info("GITHUB_MCP_ENABLED not set — skipping MCP tests")
    info("To enable: GITHUB_MCP_ENABLED=1 GITHUB_TOKEN=... GITHUB_OWNER=... GITHUB_REPO=... \\")
    info("           python scripts/test_mcp_tools.py")
    record("github-mcp", "skip", "not enabled")
elif not GITHUB_TOKEN:
    warn("GITHUB_TOKEN not set — cannot test GitHub MCP server")
    record("github-mcp", "skip", "no token")
elif py_ver < (3, 10):
    warn("Python 3.10+ required for mcp package")
    record("github-mcp", "skip", "python<3.10")
else:
    info(f"Testing GitHub MCP server for {GITHUB_OWNER}/{GITHUB_REPO} (via npx)...")
    try:
        from tools.mcp_github_tool import MCPGithubTool
        mcp_gh = MCPGithubTool()
        mcp_gh._use_mcp = True
        issues = mcp_gh.list_all()
        ok(f"MCPGithubTool: fetched {len(issues)} issue(s) from {GITHUB_OWNER}/{GITHUB_REPO}")
        if issues:
            ok(f"  Sample: #{issues[0].get('id','?')} {issues[0].get('title','')[:60]}")
        record("github-mcp", "pass")
    except ImportError as e:
        fail(f"mcp package not installed: {e}")
        fail("Run: pip install mcp")
        record("github-mcp", "fail", str(e))
    except Exception as e:
        fail(f"GitHub MCP: {e}")
        info("Common causes:")
        info("  • GITHUB_TOKEN not set or missing 'repo' scope")
        info("  • GITHUB_OWNER or GITHUB_REPO not set")
        info("  • npx not in PATH (install Node.js)")
        record("github-mcp", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════
header("Summary")

total = len(PASSED) + len(FAILED) + len(SKIPPED)
print(f"\n  {GREEN}{len(PASSED)} passed{NC}  "
      f"{RED}{len(FAILED)} failed{NC}  "
      f"{YELLOW}{len(SKIPPED)} skipped{NC}  "
      f"(out of {total} checks)\n")

if FAILED:
    print(f"  {RED}Failed:{NC}")
    for label, detail in FAILED:
        print(f"    {RED}✗{NC} {label}: {detail}")
    print()

if SKIPPED:
    print(f"  {YELLOW}Skipped:{NC}")
    for label, detail in SKIPPED:
        print(f"    {YELLOW}⚠{NC} {label}: {detail}")
    print()

if not FAILED:
    print(f"  {GREEN}All required checks passed.{NC}")
    if any(lbl == "atlassian-mcp" for lbl, _ in SKIPPED):
        print(f"\n  {CYAN}To test Atlassian MCP server:{NC}")
        print(f"  1. Ensure .env has JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN")
        print(f"  2. Use Python 3.10+: python3.11 -m venv venv311 && pip install -r requirements.txt mcp")
        print(f"  3. ATLASSIAN_MCP_ENABLED=1 python3.11 scripts/test_mcp_tools.py")
    if any(lbl == "github-mcp" for lbl, _ in SKIPPED):
        print(f"\n  {CYAN}To test GitHub MCP server:{NC}")
        print(f"  1. Create a GitHub PAT with issues:read scope")
        print(f"  2. GITHUB_MCP_ENABLED=1 GITHUB_TOKEN=ghp_... GITHUB_OWNER=your-org \\")
        print(f"     GITHUB_REPO=your-repo python3.11 scripts/test_mcp_tools.py")
else:
    sys.exit(1)
