#!/usr/bin/env bash
# Överblick Manager — start/stop/restart/status per identity or supervisor
# Usage:
#   ./overblick_manager.sh {start|stop|restart|status|logs} {anomal|cherry|all}
#   ./overblick_manager.sh supervisor-start "anomal cherry natt"
#   ./overblick_manager.sh supervisor-stop
#   ./overblick_manager.sh supervisor-restart "anomal cherry natt"
#   ./overblick_manager.sh supervisor-status
#   ./overblick_manager.sh supervisor-logs

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
    echo "Usage:"
    echo "  $0 {start|stop|restart|status|logs} {anomal|cherry|all}"
    echo "  $0 supervisor-start \"anomal cherry natt\""
    echo "  $0 supervisor-stop"
    echo "  $0 supervisor-restart \"anomal cherry natt\""
    echo "  $0 supervisor-status"
    echo "  $0 supervisor-logs"
    exit 1
}

# Kill ALL running supervisor processes (by process name), not just the one in PID file.
# This is the key guard against duplicate supervisors.
kill_all_supervisors() {
    local pids
    pids=$(pgrep -f "overblick supervisor" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "[supervisor] Killing stray supervisor processes: $pids"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 2
        pids=$(pgrep -f "overblick supervisor" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            # shellcheck disable=SC2086
            kill -9 $pids 2>/dev/null || true
        fi
    fi
    rm -f "$SUPERVISOR_PID_FILE"
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

# ---------------------------------------------------------------------------
# Supervisor functions
# ---------------------------------------------------------------------------

SUPERVISOR_PID_FILE="$PID_DIR/supervisor/supervisor.pid"
SUPERVISOR_LOG_FILE="$LOG_DIR/supervisor/overblick.log"

start_supervisor() {
    local identities="$1"

    # Check PID file first
    if [ -f "$SUPERVISOR_PID_FILE" ] && kill -0 "$(cat "$SUPERVISOR_PID_FILE")" 2>/dev/null; then
        echo "[supervisor] Already running (PID $(cat "$SUPERVISOR_PID_FILE"))"
        return 0
    fi

    # Also check for any stray supervisor processes not tracked by PID file
    local stray
    stray=$(pgrep -f "overblick supervisor" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        echo "[supervisor] ERROR: Stray supervisor process(es) detected: $stray"
        echo "[supervisor] Run 'supervisor-stop' first, or use 'supervisor-restart'."
        exit 1
    fi

    mkdir -p "$(dirname "$SUPERVISOR_PID_FILE")" "$(dirname "$SUPERVISOR_LOG_FILE")"

    echo "[supervisor] Starting with identities: $identities"
    # shellcheck disable=SC2086
    nohup "$PYTHON" -m overblick supervisor $identities >> "$SUPERVISOR_LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$SUPERVISOR_PID_FILE"
    echo "[supervisor] Started (PID $pid)"
}

stop_supervisor() {
    # Kill everything — both via PID file and via pgrep (catches strays)
    local found=0

    if [ -f "$SUPERVISOR_PID_FILE" ]; then
        local pid
        pid=$(cat "$SUPERVISOR_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "[supervisor] Stopping PID file process ($pid)..."
            found=1
        else
            echo "[supervisor] PID file is stale, cleaning up"
        fi
    fi

    local stray
    stray=$(pgrep -f "overblick supervisor" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        echo "[supervisor] Stopping all supervisor processes: $stray"
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        echo "[supervisor] Not running"
        rm -f "$SUPERVISOR_PID_FILE"
        return 0
    fi

    kill_all_supervisors

    # Verify
    if pgrep -f "overblick supervisor" > /dev/null 2>&1; then
        echo "[supervisor] ERROR: Could not stop all supervisor processes"
        exit 1
    fi
    echo "[supervisor] Stopped"
}

status_supervisor() {
    local pids
    pids=$(pgrep -f "overblick supervisor" 2>/dev/null || true)
    local pid_file_pid=""
    [ -f "$SUPERVISOR_PID_FILE" ] && pid_file_pid=$(cat "$SUPERVISOR_PID_FILE")

    if [ -z "$pids" ]; then
        echo "[supervisor] STOPPED"
        rm -f "$SUPERVISOR_PID_FILE"
        return 0
    fi

    local count
    count=$(echo "$pids" | wc -w | tr -d ' ')
    if [ "$count" -gt 1 ]; then
        echo "[supervisor] WARNING: $count instances running (PIDs: $pids) — run supervisor-stop then supervisor-start"
    else
        echo "[supervisor] RUNNING (PID $pids)"
    fi

    if [ -n "$pid_file_pid" ] && ! echo "$pids" | grep -qw "$pid_file_pid"; then
        echo "[supervisor] WARNING: PID file ($pid_file_pid) does not match running process(es)"
    fi
}

logs_supervisor() {
    if [ -f "$SUPERVISOR_LOG_FILE" ]; then
        tail -f "$SUPERVISOR_LOG_FILE"
    else
        echo "[supervisor] No log file found"
    fi
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

[ $# -lt 1 ] && usage

ACTION=$1

# Handle supervisor commands (single-argument actions)
case "$ACTION" in
    supervisor-start)
        [ $# -lt 2 ] && { echo "ERROR: supervisor-start requires identities"; usage; }
        start_supervisor "$2"
        exit 0
        ;;
    supervisor-stop)
        stop_supervisor
        exit 0
        ;;
    supervisor-restart)
        [ $# -lt 2 ] && { echo "ERROR: supervisor-restart requires identities"; usage; }
        echo "[supervisor] Restarting..."
        stop_supervisor
        sleep 1
        start_supervisor "$2"
        exit 0
        ;;
    supervisor-status)
        status_supervisor
        exit 0
        ;;
    supervisor-logs)
        logs_supervisor
        exit 0
        ;;
esac

# Identity commands require a second argument
[ $# -lt 2 ] && usage

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
