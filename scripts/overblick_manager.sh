#!/usr/bin/env bash
# Överblick Manager — unified management for the entire Överblick platform
#
# Usage:
#   $0 up [IDENTITIES]                   — Start everything (gateway, dashboard, supervisor)
#   $0 down                              — Stop everything gracefully
#   $0 restart [IDENTITIES]              — Restart everything
#   $0 status                            — Show status of all components
#
#   $0 gateway {start|stop|restart|status|logs}
#   $0 dashboard {start|stop|restart|status|logs} [--port PORT]
#   $0 supervisor-start "anomal cherry natt"
#   $0 supervisor-stop
#   $0 supervisor-restart "anomal cherry natt"
#   $0 supervisor-status
#   $0 supervisor-logs
#
#   $0 {start|stop|restart|status|logs} {anomal|cherry|IDENTITY}
#
# Default identities (used by 'up' without arguments): anomal cherry natt stal

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/venv"
PID_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"

DEFAULT_IDENTITIES="anomal cherry natt stal"
DEFAULT_DASHBOARD_PORT=8080

if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found at $VENV"
    echo "Run: python3.13 -m venv $VENV && $VENV/bin/pip install -e $PROJECT_DIR"
    exit 1
fi

PYTHON="$VENV/bin/python"

# Load environment variables from config/.env if it exists
# (contains API keys for cloud backends like DeepSeek)
ENV_FILE="$PROJECT_DIR/config/.env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a && source "$ENV_FILE" && set +a
fi

usage() {
    echo "Överblick Manager"
    echo ""
    echo "Platform commands:"
    echo "  $0 up [IDENTITIES]              Start everything (gateway → dashboard → supervisor)"
    echo "  $0 down                          Stop everything gracefully"
    echo "  $0 restart [IDENTITIES]          Restart everything"
    echo "  $0 status                        Show status of all components"
    echo ""
    echo "Component commands:"
    echo "  $0 gateway {start|stop|restart|status|logs}"
    echo "  $0 dashboard {start|stop|restart|status|logs} [--port PORT]"
    echo "  $0 supervisor-start \"anomal cherry natt\""
    echo "  $0 supervisor-stop"
    echo "  $0 supervisor-restart \"anomal cherry natt\""
    echo "  $0 supervisor-status"
    echo "  $0 supervisor-logs"
    echo ""
    echo "Identity commands:"
    echo "  $0 {start|stop|restart|status|logs} IDENTITY"
    echo ""
    echo "Default identities: $DEFAULT_IDENTITIES"
    exit 1
}

# ---------------------------------------------------------------------------
# Gateway functions
# ---------------------------------------------------------------------------

GATEWAY_PID_FILE="$PID_DIR/gateway/gateway.pid"
GATEWAY_LOG_FILE="$LOG_DIR/gateway/gateway.log"

start_gateway() {
    if [ -f "$GATEWAY_PID_FILE" ] && kill -0 "$(cat "$GATEWAY_PID_FILE")" 2>/dev/null; then
        echo "[gateway] Already running (PID $(cat "$GATEWAY_PID_FILE"))"
        return 0
    fi

    # Check for stray gateway processes
    local stray
    stray=$(pgrep -f "overblick\.gateway" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        echo "[gateway] Killing stray gateway process(es): $stray"
        # shellcheck disable=SC2086
        kill $stray 2>/dev/null || true
        sleep 1
        stray=$(pgrep -f "overblick\.gateway" 2>/dev/null || true)
        if [ -n "$stray" ]; then
            # shellcheck disable=SC2086
            kill -9 $stray 2>/dev/null || true
        fi
    fi

    mkdir -p "$(dirname "$GATEWAY_PID_FILE")" "$(dirname "$GATEWAY_LOG_FILE")"

    echo "[gateway] Starting..."
    nohup "$PYTHON" -m overblick.gateway >> "$GATEWAY_LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$GATEWAY_PID_FILE"

    # Wait for health check
    local attempts=0
    while [ $attempts -lt 10 ]; do
        if curl -sf http://localhost:8200/health > /dev/null 2>&1; then
            echo "[gateway] Started (PID $pid) — healthy"
            return 0
        fi
        sleep 1
        attempts=$((attempts + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo "[gateway] Started (PID $pid) — health check pending"
    else
        echo "[gateway] FAILED to start — check $GATEWAY_LOG_FILE"
        rm -f "$GATEWAY_PID_FILE"
        return 1
    fi
}

stop_gateway() {
    local found=0

    if [ -f "$GATEWAY_PID_FILE" ]; then
        local pid
        pid=$(cat "$GATEWAY_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "[gateway] Stopping (PID $pid)..."
            kill "$pid"
            found=1
        fi
    fi

    # Also kill any stray gateway processes
    local stray
    stray=$(pgrep -f "overblick\.gateway" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        [ "$found" -eq 0 ] && echo "[gateway] Stopping stray process(es): $stray"
        # shellcheck disable=SC2086
        kill $stray 2>/dev/null || true
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        echo "[gateway] Not running"
        rm -f "$GATEWAY_PID_FILE"
        return 0
    fi

    # Wait for graceful shutdown
    for i in $(seq 1 10); do
        if ! pgrep -f "overblick\.gateway" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    # Force kill if needed
    stray=$(pgrep -f "overblick\.gateway" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        echo "[gateway] Force killing..."
        # shellcheck disable=SC2086
        kill -9 $stray 2>/dev/null || true
    fi

    rm -f "$GATEWAY_PID_FILE"
    echo "[gateway] Stopped"
}

status_gateway() {
    local pid=""

    # Try PID file first
    if [ -f "$GATEWAY_PID_FILE" ] && kill -0 "$(cat "$GATEWAY_PID_FILE")" 2>/dev/null; then
        pid=$(cat "$GATEWAY_PID_FILE")
    else
        # Check for unmanaged process
        pid=$(pgrep -f "overblick\.gateway" 2>/dev/null | head -1 || true)
        [ -f "$GATEWAY_PID_FILE" ] && rm -f "$GATEWAY_PID_FILE"
    fi

    if [ -n "$pid" ]; then
        local health
        health=$(curl -sf http://localhost:8200/health 2>/dev/null || echo '{"status":"unknown"}')
        local backends
        backends=$(echo "$health" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin).get('backends',{}); print(', '.join(f'{k}={v}' for k,v in d.items()))" 2>/dev/null || echo "unknown")
        echo "[gateway] RUNNING (PID $pid) — backends: $backends"
    else
        echo "[gateway] STOPPED"
    fi
}

logs_gateway() {
    if [ -f "$GATEWAY_LOG_FILE" ]; then
        tail -f "$GATEWAY_LOG_FILE"
    else
        echo "[gateway] No log file found"
    fi
}

# ---------------------------------------------------------------------------
# Dashboard functions
# ---------------------------------------------------------------------------

DASHBOARD_PID_FILE="$PID_DIR/dashboard/dashboard.pid"
DASHBOARD_LOG_FILE="$LOG_DIR/dashboard/dashboard.log"

start_dashboard() {
    local port="${1:-$DEFAULT_DASHBOARD_PORT}"

    if [ -f "$DASHBOARD_PID_FILE" ] && kill -0 "$(cat "$DASHBOARD_PID_FILE")" 2>/dev/null; then
        echo "[dashboard] Already running (PID $(cat "$DASHBOARD_PID_FILE"))"
        return 0
    fi

    # Check for stray dashboard processes
    local stray
    stray=$(pgrep -f "overblick dashboard" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        echo "[dashboard] Killing stray dashboard process(es): $stray"
        # shellcheck disable=SC2086
        kill $stray 2>/dev/null || true
        sleep 1
    fi

    mkdir -p "$(dirname "$DASHBOARD_PID_FILE")" "$(dirname "$DASHBOARD_LOG_FILE")"

    echo "[dashboard] Starting on port $port..."
    nohup "$PYTHON" -m overblick dashboard --port "$port" >> "$DASHBOARD_LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$DASHBOARD_PID_FILE"

    # Wait for health
    local attempts=0
    while [ $attempts -lt 8 ]; do
        if curl -sf "http://localhost:$port/" > /dev/null 2>&1; then
            echo "[dashboard] Started (PID $pid) — http://localhost:$port"
            return 0
        fi
        sleep 1
        attempts=$((attempts + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo "[dashboard] Started (PID $pid) — http://localhost:$port"
    else
        echo "[dashboard] FAILED to start — check $DASHBOARD_LOG_FILE"
        rm -f "$DASHBOARD_PID_FILE"
        return 1
    fi
}

stop_dashboard() {
    local found=0

    if [ -f "$DASHBOARD_PID_FILE" ]; then
        local pid
        pid=$(cat "$DASHBOARD_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "[dashboard] Stopping (PID $pid)..."
            kill "$pid"
            found=1
        fi
    fi

    local stray
    stray=$(pgrep -f "overblick dashboard" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        [ "$found" -eq 0 ] && echo "[dashboard] Stopping stray process(es): $stray"
        # shellcheck disable=SC2086
        kill $stray 2>/dev/null || true
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        echo "[dashboard] Not running"
        rm -f "$DASHBOARD_PID_FILE"
        return 0
    fi

    for i in $(seq 1 5); do
        if ! pgrep -f "overblick dashboard" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    stray=$(pgrep -f "overblick dashboard" 2>/dev/null || true)
    if [ -n "$stray" ]; then
        # shellcheck disable=SC2086
        kill -9 $stray 2>/dev/null || true
    fi

    rm -f "$DASHBOARD_PID_FILE"
    echo "[dashboard] Stopped"
}

status_dashboard() {
    if [ -f "$DASHBOARD_PID_FILE" ] && kill -0 "$(cat "$DASHBOARD_PID_FILE")" 2>/dev/null; then
        echo "[dashboard] RUNNING (PID $(cat "$DASHBOARD_PID_FILE")) — http://localhost:$DEFAULT_DASHBOARD_PORT"
    else
        # Check for process started outside manager
        local stray
        stray=$(pgrep -f "overblick dashboard" 2>/dev/null || true)
        if [ -n "$stray" ]; then
            echo "[dashboard] RUNNING (PID $stray, unmanaged)"
        else
            echo "[dashboard] STOPPED"
        fi
        [ -f "$DASHBOARD_PID_FILE" ] && rm -f "$DASHBOARD_PID_FILE"
    fi
}

logs_dashboard() {
    if [ -f "$DASHBOARD_LOG_FILE" ]; then
        tail -f "$DASHBOARD_LOG_FILE"
    else
        echo "[dashboard] No log file found"
    fi
}

# ---------------------------------------------------------------------------
# Supervisor functions
# ---------------------------------------------------------------------------

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
# Platform-level commands: up / down / restart / status
# ---------------------------------------------------------------------------

platform_up() {
    local identities="${1:-$DEFAULT_IDENTITIES}"
    echo "=== Överblick Platform — Starting ==="
    echo ""
    start_gateway
    echo ""
    start_dashboard
    echo ""
    start_supervisor "$identities"
    echo ""
    echo "=== All components started ==="
}

platform_down() {
    echo "=== Överblick Platform — Stopping ==="
    echo ""
    stop_supervisor
    echo ""
    stop_dashboard
    echo ""
    stop_gateway
    echo ""
    echo "=== All components stopped ==="
}

platform_status() {
    echo "=== Överblick Platform Status ==="
    echo ""
    status_gateway
    status_dashboard
    status_supervisor

    # Show individual agent status if supervisor is running
    local agents
    agents=$(pgrep -f "overblick run" 2>/dev/null || true)
    if [ -n "$agents" ]; then
        echo ""
        for pid in $agents; do
            local cmd
            cmd=$(ps -o args= -p "$pid" 2>/dev/null || true)
            local identity
            identity=$(echo "$cmd" | grep -o "overblick run [a-z]*" | awk '{print $3}')
            if [ -n "$identity" ]; then
                echo "  [$identity] RUNNING (PID $pid)"
            fi
        done
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

[ $# -lt 1 ] && usage

ACTION=$1

case "$ACTION" in
    # --- Platform-level commands ---
    up)
        platform_up "${2:-$DEFAULT_IDENTITIES}"
        exit 0
        ;;
    down)
        platform_down
        exit 0
        ;;
    restart)
        platform_down
        sleep 2
        platform_up "${2:-$DEFAULT_IDENTITIES}"
        exit 0
        ;;
    status)
        if [ $# -eq 1 ]; then
            platform_status
            exit 0
        fi
        # 'status IDENTITY' falls through to identity commands below
        ;;

    # --- Gateway commands ---
    gateway)
        [ $# -lt 2 ] && { echo "ERROR: gateway requires an action"; usage; }
        case "$2" in
            start)   start_gateway ;;
            stop)    stop_gateway ;;
            restart) stop_gateway; sleep 1; start_gateway ;;
            status)  status_gateway ;;
            logs)    logs_gateway ;;
            *)       echo "ERROR: Unknown gateway action: $2"; usage ;;
        esac
        exit 0
        ;;

    # --- Dashboard commands ---
    dashboard)
        [ $# -lt 2 ] && { echo "ERROR: dashboard requires an action"; usage; }
        _dash_port="$DEFAULT_DASHBOARD_PORT"
        [ $# -ge 4 ] && [ "$3" = "--port" ] && _dash_port="$4"
        case "$2" in
            start)   start_dashboard "$_dash_port" ;;
            stop)    stop_dashboard ;;
            restart) stop_dashboard; sleep 1; start_dashboard "$_dash_port" ;;
            status)  status_dashboard ;;
            logs)    logs_dashboard ;;
            *)       echo "ERROR: Unknown dashboard action: $2"; usage ;;
        esac
        exit 0
        ;;

    # --- Supervisor commands ---
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

# --- Identity commands (start/stop/restart/status/logs IDENTITY) ---
[ $# -lt 2 ] && usage

IDENTITY=$2

case "$ACTION" in
    start|stop|restart|status|logs)
        ;;
    *)
        usage
        ;;
esac

case "$ACTION" in
    start)
        start_identity "$IDENTITY"
        ;;
    stop)
        stop_identity "$IDENTITY"
        ;;
    restart)
        stop_identity "$IDENTITY"
        sleep 1
        start_identity "$IDENTITY"
        ;;
    status)
        status_identity "$IDENTITY"
        ;;
    logs)
        logs_identity "$IDENTITY"
        ;;
esac
