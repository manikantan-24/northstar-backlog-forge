#!/usr/bin/env bash
# =============================================================================
# Backlog Synthesizer — start everything in one command
#
# Usage:
#   chmod +x start.sh
#   ./start.sh
#
# What it does:
#   1. Starts Ollama (if installed and not already running)
#   2. Activates the Python 3.13 venv (has mcp + all deps)
#   3. Loads .env
#   4. Starts Streamlit on port 8501
#
# Press Ctrl+C to stop.
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info() { echo -e "${CYAN}[start]${NC} $*"; }
ok()   { echo -e "${GREEN}[start]${NC} $*"; }
warn() { echo -e "${YELLOW}[start]${NC} $*"; }

# ── 1. Ollama ────────────────────────────────────────────────────────────────
if command -v ollama &>/dev/null; then
    if curl -sf http://localhost:11434/api/tags &>/dev/null; then
        ok "Ollama already running"
    else
        info "Starting Ollama in background…"
        ollama serve &>/tmp/ollama.log &
        # Wait up to 10 seconds for it to be ready
        for i in $(seq 1 20); do
            if curl -sf http://localhost:11434/api/tags &>/dev/null; then
                ok "Ollama ready (${i} × 0.5s)"
                break
            fi
            sleep 0.5
        done
        if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
            warn "Ollama did not start in time — Local preset may fail. Check /tmp/ollama.log"
        fi
    fi
else
    warn "Ollama not installed — Local preset unavailable. Install from https://ollama.ai"
fi

# ── 2. Pick best available venv (highest Python version wins) ───────────────
# requirements.txt uses PEP 508 markers so mcp installs automatically on 3.10+.
# Priority: venv313 > venv312 > venv311 > venv310 > venv (3.9 fallback)
VENV=""
for _candidate in venv313 venv312 venv311 venv310 venv; do
    if [ -f "$ROOT/$_candidate/bin/activate" ]; then
        VENV="$ROOT/$_candidate"
        break
    fi
done

if [ -z "$VENV" ]; then
    echo "ERROR: No venv found."
    echo "Create one: python3.13 -m venv venv313 && source venv313/bin/activate && pip install -r requirements.txt"
    exit 1
fi

_PY_VER=$("$VENV/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "?")
if "$VENV/bin/python" -c "import mcp" 2>/dev/null; then
    ok "Using $VENV (Python $_PY_VER + MCP packages)"
else
    warn "Using $VENV (Python $_PY_VER) — MCP unavailable. Upgrade to Python 3.10+ for full MCP support."
fi

source "$VENV/bin/activate"
ok "Activated $VENV"

# ── 3. Load .env ─────────────────────────────────────────────────────────────
if [ -f "$ROOT/.env" ]; then
    set -a
    source "$ROOT/.env"
    set +a
    ok "Loaded .env"
else
    warn ".env not found — using system environment variables only"
fi

# ── 4. Start Streamlit ───────────────────────────────────────────────────────
PORT="${PORT:-8501}"
info "Starting Streamlit on http://localhost:${PORT} …"
echo ""
echo -e "  ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}  Backlog Synthesizer${NC}"
echo -e "  ${GREEN}  http://localhost:${PORT}${NC}"
echo -e "  ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

cd "$ROOT"
exec streamlit run app.py \
    --server.port "$PORT" \
    --server.headless true \
    --browser.gatherUsageStats false
