#!/usr/bin/env bash
# Överblick Manager — start/stop/restart/status per identity
# Usage: ./overblick_manager.sh {start|stop|restart|status|logs} {anomal|cherry|all}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/venv"
PID_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"

if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found at $VENV"
    echo "Run: python3.13 -m venv $VENV && $VENV/bin/pip install -e $PROJECT_DIR"
    exit 1
fi

PYTHON="$VENV/bin/python"

usage() {
    echo "Usage: $0 {start|stop|restart|status|logs} {anomal|cherry|all}"
    exit 1
}

get_pid_file() {
    echo "$PID_DIR/$1/overblick.pid"
}

get_log_file() {
    echo "$LOG_DIR/$1/overblick.log"
}

start_identity() {
    local identity=$1
    local pid_file=$(get_pid_file "$identity")
    local log_file=$(get_log_file "$identity")

    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        echo "[$identity] Already running (PID $(cat "$pid_file"))"
        return 0
    fi

    mkdir -p "$(dirname "$pid_file")" "$(dirname "$log_file")"

    echo "[$identity] Starting..."
    nohup "$PYTHON" -m overblick run "$identity" >> "$log_file" 2>&1 &
    local pid=$!
    echo "$pid" > "$pid_file"
    echo "[$identity] Started (PID $pid)"
}

stop_identity() {
    local identity=$1
    local pid_file=$(get_pid_file "$identity")

    if [ ! -f "$pid_file" ]; then
        echo "[$identity] Not running (no PID file)"
        return 0
    fi

    local pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        echo "[$identity] Stopping (PID $pid)..."
        kill "$pid"
        # Wait up to 10s for graceful shutdown
        for i in $(seq 1 10); do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "[$identity] Force killing..."
            kill -9 "$pid" 2>/dev/null || true
        fi
        echo "[$identity] Stopped"
    else
        echo "[$identity] Not running (stale PID file)"
    fi
    rm -f "$pid_file"
}

status_identity() {
    local identity=$1
    local pid_file=$(get_pid_file "$identity")

    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        echo "[$identity] RUNNING (PID $(cat "$pid_file"))"
    else
        echo "[$identity] STOPPED"
        [ -f "$pid_file" ] && rm -f "$pid_file"
    fi
}

logs_identity() {
    local identity=$1
    local log_file=$(get_log_file "$identity")

    if [ -f "$log_file" ]; then
        tail -f "$log_file"
    else
        echo "[$identity] No log file found"
    fi
}

[ $# -lt 2 ] && usage

ACTION=$1
IDENTITY=$2

case "$ACTION" in
    start|stop|restart|status|logs)
        ;;
    *)
        usage
        ;;
esac

if [ "$IDENTITY" = "all" ]; then
    IDENTITIES="anomal cherry"
else
    IDENTITIES="$IDENTITY"
fi

for id in $IDENTITIES; do
    case "$ACTION" in
        start)
            start_identity "$id"
            ;;
        stop)
            stop_identity "$id"
            ;;
        restart)
            stop_identity "$id"
            sleep 1
            start_identity "$id"
            ;;
        status)
            status_identity "$id"
            ;;
        logs)
            logs_identity "$id"
            ;;
    esac
done
