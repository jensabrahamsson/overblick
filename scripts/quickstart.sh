#!/usr/bin/env bash
# quickstart.sh — One-command setup for Överblick
#
# Idempotent: safe to run multiple times. Skips steps that are already done.
# Usage: ./scripts/quickstart.sh

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

echo -e "${BOLD}"
echo "  ◈ Överblick Quickstart"
echo "  ─────────────────────────────────"
echo -e "${NC}"

# Resolve project root (script lives in scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# ── Step 1: Check Python ──

info "Checking Python version..."
PYTHON=""
for candidate in python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.10+ is required but not found.\n  Install it: brew install python@3.13 (macOS) or apt install python3.13 (Linux)"
fi
ok "Found $PYTHON ($version)"

# ── Step 2: Virtual environment ──

info "Checking virtual environment..."
if [ -d "venv" ] && [ -f "venv/bin/python3" ]; then
    ok "Virtual environment exists"
else
    info "Creating virtual environment..."
    "$PYTHON" -m venv venv
    ok "Virtual environment created"
fi

# Activate venv for subsequent commands
# shellcheck disable=SC1091
source venv/bin/activate

# ── Step 3: Install dependencies ──

info "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[gateway,dashboard,dev]"
ok "Dependencies installed"

# ── Step 4: Check Ollama ──

info "Checking Ollama..."
OLLAMA_RUNNING=false
if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_RUNNING=true
    ok "Ollama is running"
else
    warn "Ollama is not running."
    echo ""
    echo -e "  ${BOLD}To install and start Ollama:${NC}"
    echo "    brew install ollama        # macOS"
    echo "    ollama serve               # start the server"
    echo ""
    echo "  Then re-run this script."
    echo ""
fi

# ── Step 5: Pull required models (only if Ollama is running) ──

if [ "$OLLAMA_RUNNING" = true ]; then
    info "Checking required models..."

    MODELS_JSON=$(curl -sf http://127.0.0.1:11434/api/tags)

    for model in "qwen3:8b" "nomic-embed-text"; do
        if echo "$MODELS_JSON" | grep -q "\"$model\""; then
            ok "Model $model is available"
        else
            info "Pulling $model (this may take a few minutes)..."
            ollama pull "$model"
            ok "Model $model pulled"
        fi
    done
fi

# ── Step 6: Start gateway + dashboard ──

if [ "$OLLAMA_RUNNING" = true ]; then
    info "Starting Överblick (gateway + dashboard)..."
    "$PYTHON" -m overblick start &
    START_PID=$!

    # Wait for health endpoints
    info "Waiting for services to become healthy..."
    HEALTHY=false
    for i in $(seq 1 30); do
        GW_OK=false
        DASH_OK=false
        if curl -sf http://127.0.0.1:8200/health >/dev/null 2>&1; then
            GW_OK=true
        fi
        if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
            DASH_OK=true
        fi
        if [ "$GW_OK" = true ] && [ "$DASH_OK" = true ]; then
            HEALTHY=true
            break
        fi
        sleep 1
    done

    if [ "$HEALTHY" = true ]; then
        ok "Gateway and dashboard are running"
    else
        warn "Services did not become healthy within 30s."
        warn "Check logs for errors. You can start manually:"
        echo "    python -m overblick start"
    fi

    # ── Step 7: Open the wizard ──

    if [ "$HEALTHY" = true ]; then
        WIZARD_URL="http://127.0.0.1:8080/settings/"
        info "Opening setup wizard..."
        if command -v open &>/dev/null; then
            open "$WIZARD_URL"
        elif command -v xdg-open &>/dev/null; then
            xdg-open "$WIZARD_URL"
        else
            info "Open in your browser: $WIZARD_URL"
        fi
    fi
else
    echo ""
    info "Skipping service startup (Ollama not running)."
    info "Once Ollama is running, start everything with:"
    echo ""
    echo "    source venv/bin/activate"
    echo "    python -m overblick start"
    echo ""
fi

echo ""
echo -e "${BOLD}  ◈ Quickstart complete${NC}"
echo ""
