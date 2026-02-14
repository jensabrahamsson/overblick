#!/usr/bin/env bash
# Överblick Supervisor — Boss Agent Manager
# Usage: ./scripts/supervisor.sh {start|stop|status|logs|restart} [identities...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/venv"
PID_FILE="$PROJECT_DIR/data/supervisor.pid"
LOG_FILE="$PROJECT_DIR/logs/supervisor/supervisor.log"

if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found at $VENV"
    echo "Run: python3.13 -m venv $VENV && $VENV/bin/pip install -e $PROJECT_DIR"
    exit 1
fi

PYTHON="$VENV/bin/python3"

usage() {
    echo "Usage: $0 {start|stop|status|logs|restart} [identities...]"
    echo ""
    echo "Commands:"
    echo "  start [ids...]  - Start supervisor with specified identities (default: anomal)"
    echo "  stop            - Stop supervisor gracefully"
    echo "  status          - Show supervisor and agent status"
    echo "  logs [-f]       - Show supervisor logs (use -f to follow)"
    echo "  restart [ids...]- Restart supervisor with specified identities"
    echo ""
    echo "Examples:"
    echo "  $0 start anomal"
    echo "  $0 start anomal cherry"
    echo "  $0 stop"
    echo "  $0 logs -f"
    exit 1
}

start() {
    local identities=("$@")
    
    # Default to anomal if no identities specified
    if [ ${#identities[@]} -eq 0 ]; then
        identities=("anomal")
    fi
    
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Supervisor already running (PID $(cat "$PID_FILE"))"
        return 0
    fi
    
    mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG_FILE")"
    
    echo "Starting Överblick Supervisor with: ${identities[*]}"
    nohup "$PYTHON" -m overblick supervisor "${identities[@]}" >> "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    
    # Wait a moment and check if it's still running
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "✅ Supervisor started (PID $pid)"
        echo "📊 View logs: $0 logs -f"
        echo "📈 Check status: $0 status"
    else
        echo "❌ Supervisor failed to start. Check logs:"
        tail -20 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "Supervisor not running (no PID file)"
        return 0
    fi
    
    local pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "Supervisor not running (stale PID file)"
        rm -f "$PID_FILE"
        return 0
    fi
    
    echo "Stopping Supervisor (PID $pid)..."
    kill -TERM "$pid"
    
    # Wait up to 10 seconds for graceful shutdown
    for i in {1..10}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "✅ Supervisor stopped"
            rm -f "$PID_FILE"
            return 0
        fi
        sleep 1
    done
    
    echo "⚠️  Supervisor did not stop gracefully, forcing..."
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "✅ Supervisor stopped (forced)"
}

status() {
    if [ ! -f "$PID_FILE" ]; then
        echo "❌ Supervisor: NOT RUNNING"
        return 1
    fi
    
    local pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "❌ Supervisor: NOT RUNNING (stale PID)"
        rm -f "$PID_FILE"
        return 1
    fi
    
    echo "✅ Supervisor: RUNNING (PID $pid)"
    
    # Show agent processes
    echo ""
    echo "Agent processes:"
    ps aux | grep "[p]ython.*overblick run" | while read -r line; do
        echo "  $line"
    done || echo "  (no agent processes found)"
    
    # Show recent log
    if [ -f "$LOG_FILE" ]; then
        echo ""
        echo "Recent activity (last 5 lines):"
        tail -5 "$LOG_FILE" | sed 's/^/  /'
    fi
}

logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "No log file found at $LOG_FILE"
        exit 1
    fi
    
    if [ "${1:-}" = "-f" ]; then
        tail -f "$LOG_FILE"
    else
        tail -50 "$LOG_FILE"
    fi
}

restart() {
    echo "Restarting Supervisor..."
    stop
    sleep 1
    start "$@"
}

# Main
if [ $# -eq 0 ]; then
    usage
fi

CMD=$1
shift

case "$CMD" in
    start)
        start "$@"
        ;;
    stop)
        stop
        ;;
    status)
        status
        ;;
    logs)
        logs "$@"
        ;;
    restart)
        restart "$@"
        ;;
    *)
        usage
        ;;
esac
