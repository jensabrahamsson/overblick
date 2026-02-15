#!/usr/bin/env bash
# ============================================================================
# Överblick Setup — First-time onboarding wizard
#
# Launches a browser-based setup wizard that guides you through configuring
# your agent framework: identity, AI engine, channels, and personalities.
#
# Usage:
#   ./scripts/setup_overblick.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Check for virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -n "${VIRTUAL_ENV:-}" ]; then
    : # already in a venv
else
    echo "WARNING: No virtual environment found. Consider running:"
    echo "  python3 -m venv venv && source venv/bin/activate && pip install -e '.[dashboard]'"
    echo ""
fi

# Check dependencies
python3 -c "import fastapi, uvicorn, jinja2" 2>/dev/null || {
    echo "ERROR: Missing dependencies. Install with:"
    echo "  pip install -e '.[dashboard]'"
    exit 1
}

echo ""
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║       Överblick Setup Wizard              ║"
echo "  ║  Security-first agent framework           ║"
echo "  ╚═══════════════════════════════════════════╝"
echo ""

python3 -m overblick.setup
