"""
ServiceManager — cross-platform service lifecycle for Överblick.

Replaces the Unix-only bash scripts (scripts/overblick_manager.sh)
with a Python implementation that works on Windows, Linux, and macOS.

Manages three services:
- Gateway (LLM Gateway on port 8200)
- Dashboard (Web UI on configurable port)
- Supervisor (Boss agent managing identity subprocesses)
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

from overblick.shared.platform import IS_WINDOWS, get_python_executable

logger = logging.getLogger(__name__)

# Health check timeout (seconds)
_HEALTH_TIMEOUT = 2

# Default ports
_GATEWAY_PORT = 8200
_DASHBOARD_PORT = 8080

# Default identities for 'up' command
_DEFAULT_IDENTITIES = ["anomal", "cherry", "natt", "stal", "vakt"]


def _base_dir() -> Path:
    """Project root directory."""
    return Path(__file__).parent.parent.parent


def _pid_dir() -> Path:
    return _base_dir() / "data" / "pids"


def _log_dir() -> Path:
    return _base_dir() / "logs"


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive and running.

    On Windows: uses GetExitCodeProcess to distinguish running processes
    from zombie/terminated processes (OpenProcess alone can succeed for
    processes that have exited but whose handles haven't been closed).

    On Unix: uses os.kill(pid, 0) which only succeeds for live processes.
    """
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259  # STATUS_PENDING
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _read_pid(pid_file: Path) -> Optional[int]:
    """Read PID from file, return None if missing or stale.

    Guards against PID reuse by storing start timestamp alongside PID
    and verifying process creation time where possible.
    """
    if not pid_file.exists():
        return None
    try:
        content = pid_file.read_text().strip()
        # Format: "PID" or "PID:TIMESTAMP"
        parts = content.split(":", 1)
        pid = int(parts[0])

        if not _is_process_alive(pid):
            # Stale PID file — process is dead
            pid_file.unlink(missing_ok=True)
            return None

        # If we have a stored timestamp, verify it's plausible
        if len(parts) == 2:
            stored_time = float(parts[1])
            elapsed = time.time() - stored_time
            if elapsed < -60:
                # Stored time is in the future — PID reuse detected
                logger.warning(
                    "PID reuse detected for PID %d (stored time in future), "
                    "removing stale PID file",
                    pid,
                )
                pid_file.unlink(missing_ok=True)
                return None

        return pid
    except (ValueError, OSError):
        return None


def _write_pid(pid_file: Path, pid: int) -> None:
    """Write PID and start timestamp to file.

    Format: "PID:TIMESTAMP" — timestamp helps detect PID reuse.
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{pid}:{time.time()}")


def _kill_process(pid: int, timeout: float = 10.0) -> bool:
    """Terminate a process gracefully, force kill on timeout.

    On Unix: attempts SIGTERM to the process group if the target is a
    group leader (to clean up children). Falls back to single-PID signal.
    """
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_TERMINATE = 0x0001
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        import signal as signal_mod

        def _send_signal(sig: int) -> bool:
            """Send signal to process, trying group kill if applicable."""
            try:
                pgid = os.getpgid(pid)
                if pgid == pid:
                    os.killpg(pgid, sig)
                else:
                    os.kill(pid, sig)
                return True
            except ProcessLookupError:
                return False  # Already dead
            except OSError:
                # Fallback: try single-process signal
                try:
                    os.kill(pid, sig)
                    return True
                except OSError:
                    return False

        if not _send_signal(signal_mod.SIGTERM):
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Try to reap zombie child (only works if pid is our child)
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass  # Not our child or already reaped
            except OSError:
                pass

            if not _is_process_alive(pid):
                return True
            time.sleep(0.5)

        # Force kill
        _send_signal(signal_mod.SIGKILL)
        # Reap zombie after force kill
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        time.sleep(0.1)
        return not _is_process_alive(pid)


def _http_health(url: str) -> bool:
    """Quick HTTP health check (GET, expect 2xx)."""
    try:
        resp = urlopen(url, timeout=_HEALTH_TIMEOUT)
        return 200 <= resp.status < 400
    except (URLError, OSError, ValueError):
        return False


class ServiceManager:
    """Cross-platform service manager for Överblick components."""

    def __init__(self, base_dir: Optional[Path] = None):
        self._base_dir = base_dir or _base_dir()
        self._pid_dir = self._base_dir / "data" / "pids"
        self._log_dir = self._base_dir / "logs"

    # ------------------------------------------------------------------
    # Gateway
    # ------------------------------------------------------------------

    @property
    def _gateway_pid_file(self) -> Path:
        return self._pid_dir / "gateway.pid"

    @property
    def _gateway_log_file(self) -> Path:
        return self._log_dir / "gateway" / "gateway.log"

    def start_gateway(self) -> bool:
        """Start the LLM Gateway as a background process."""
        pid = _read_pid(self._gateway_pid_file)
        if pid:
            print(f"[gateway] Already running (PID {pid})")
            return True

        self._gateway_log_file.parent.mkdir(parents=True, exist_ok=True)

        python = get_python_executable()
        env = self._load_env()

        with open(self._gateway_log_file, "a") as log_f:
            kwargs = {"stdout": log_f, "stderr": subprocess.STDOUT, "env": env}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(
                [python, "-m", "overblick.gateway"],
                **kwargs,
            )

        _write_pid(self._gateway_pid_file, proc.pid)
        print(f"[gateway] Starting (PID {proc.pid})...")

        # Wait for health
        for _ in range(10):
            if _http_health(f"http://127.0.0.1:{_GATEWAY_PORT}/health"):
                print(f"[gateway] Started — healthy")
                return True
            time.sleep(1)

        if _is_process_alive(proc.pid):
            print(f"[gateway] Started (PID {proc.pid}) — health check pending")
            return True
        else:
            print(f"[gateway] FAILED to start — check {self._gateway_log_file}")
            self._gateway_pid_file.unlink(missing_ok=True)
            return False

    def stop_gateway(self) -> bool:
        """Stop the LLM Gateway."""
        pid = _read_pid(self._gateway_pid_file)
        if not pid:
            print("[gateway] Not running")
            return True

        print(f"[gateway] Stopping (PID {pid})...")
        result = _kill_process(pid)
        self._gateway_pid_file.unlink(missing_ok=True)
        print("[gateway] Stopped" if result else "[gateway] Failed to stop")
        return result

    def status_gateway(self) -> dict:
        """Get gateway status."""
        pid = _read_pid(self._gateway_pid_file)
        healthy = _http_health(f"http://127.0.0.1:{_GATEWAY_PORT}/health") if pid else False
        status = {
            "service": "gateway",
            "running": pid is not None,
            "pid": pid,
            "healthy": healthy,
        }
        state = "RUNNING" if pid else "STOPPED"
        health = " (healthy)" if healthy else ""
        print(f"[gateway] {state}{' (PID ' + str(pid) + ')' if pid else ''}{health}")
        return status

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    @property
    def _dashboard_pid_file(self) -> Path:
        return self._pid_dir / "dashboard.pid"

    @property
    def _dashboard_log_file(self) -> Path:
        return self._log_dir / "dashboard" / "dashboard.log"

    def start_dashboard(self, port: int = _DASHBOARD_PORT) -> bool:
        """Start the web dashboard as a background process."""
        pid = _read_pid(self._dashboard_pid_file)
        if pid:
            print(f"[dashboard] Already running (PID {pid})")
            return True

        self._dashboard_log_file.parent.mkdir(parents=True, exist_ok=True)

        python = get_python_executable()
        env = self._load_env()

        with open(self._dashboard_log_file, "a") as log_f:
            kwargs = {"stdout": log_f, "stderr": subprocess.STDOUT, "env": env}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(
                [python, "-m", "overblick", "dashboard", "--port", str(port)],
                **kwargs,
            )

        _write_pid(self._dashboard_pid_file, proc.pid)
        print(f"[dashboard] Starting on port {port} (PID {proc.pid})...")

        for _ in range(8):
            if _http_health(f"http://127.0.0.1:{port}/"):
                print(f"[dashboard] Started — http://127.0.0.1:{port}")
                return True
            time.sleep(1)

        if _is_process_alive(proc.pid):
            print(f"[dashboard] Started (PID {proc.pid}) — http://127.0.0.1:{port}")
            return True
        else:
            print(f"[dashboard] FAILED to start — check {self._dashboard_log_file}")
            self._dashboard_pid_file.unlink(missing_ok=True)
            return False

    def stop_dashboard(self) -> bool:
        """Stop the web dashboard."""
        pid = _read_pid(self._dashboard_pid_file)
        if not pid:
            print("[dashboard] Not running")
            return True

        print(f"[dashboard] Stopping (PID {pid})...")
        result = _kill_process(pid)
        self._dashboard_pid_file.unlink(missing_ok=True)
        print("[dashboard] Stopped" if result else "[dashboard] Failed to stop")
        return result

    def status_dashboard(self, port: int = _DASHBOARD_PORT) -> dict:
        """Get dashboard status."""
        pid = _read_pid(self._dashboard_pid_file)
        healthy = _http_health(f"http://127.0.0.1:{port}/") if pid else False
        status = {
            "service": "dashboard",
            "running": pid is not None,
            "pid": pid,
            "healthy": healthy,
            "port": port,
        }
        state = "RUNNING" if pid else "STOPPED"
        url = f" — http://127.0.0.1:{port}" if pid else ""
        print(f"[dashboard] {state}{' (PID ' + str(pid) + ')' if pid else ''}{url}")
        return status

    # ------------------------------------------------------------------
    # Supervisor
    # ------------------------------------------------------------------

    @property
    def _supervisor_pid_file(self) -> Path:
        return self._pid_dir / "supervisor.pid"

    @property
    def _supervisor_log_file(self) -> Path:
        return self._log_dir / "supervisor" / "overblick.log"

    def start_supervisor(self, identities: Optional[list[str]] = None) -> bool:
        """Start the supervisor with the specified identities."""
        ids = identities or _DEFAULT_IDENTITIES

        pid = _read_pid(self._supervisor_pid_file)
        if pid:
            print(f"[supervisor] Already running (PID {pid})")
            return True

        self._supervisor_log_file.parent.mkdir(parents=True, exist_ok=True)

        python = get_python_executable()
        env = self._load_env()

        with open(self._supervisor_log_file, "a") as log_f:
            kwargs = {"stdout": log_f, "stderr": subprocess.STDOUT, "env": env}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(
                [python, "-m", "overblick", "supervisor"] + ids,
                **kwargs,
            )

        _write_pid(self._supervisor_pid_file, proc.pid)
        print(f"[supervisor] Started with identities: {' '.join(ids)} (PID {proc.pid})")
        return True

    def stop_supervisor(self) -> bool:
        """Stop the supervisor."""
        pid = _read_pid(self._supervisor_pid_file)
        if not pid:
            print("[supervisor] Not running")
            return True

        print(f"[supervisor] Stopping (PID {pid})...")
        result = _kill_process(pid)
        self._supervisor_pid_file.unlink(missing_ok=True)
        print("[supervisor] Stopped" if result else "[supervisor] Failed to stop")
        return result

    def status_supervisor(self) -> dict:
        """Get supervisor status."""
        pid = _read_pid(self._supervisor_pid_file)
        status = {
            "service": "supervisor",
            "running": pid is not None,
            "pid": pid,
        }
        state = "RUNNING" if pid else "STOPPED"
        print(f"[supervisor] {state}{' (PID ' + str(pid) + ')' if pid else ''}")
        return status

    # ------------------------------------------------------------------
    # Platform commands
    # ------------------------------------------------------------------

    def up(self, identities: Optional[list[str]] = None, port: int = _DASHBOARD_PORT) -> None:
        """Start all services (gateway + dashboard + supervisor)."""
        print("=== Överblick Platform — Starting ===\n")
        self.start_gateway()
        print()
        self.start_dashboard(port=port)
        print()
        self.start_supervisor(identities=identities)
        print("\n=== All components started ===")

    def down(self) -> None:
        """Stop all services."""
        print("=== Överblick Platform — Stopping ===\n")
        self.stop_supervisor()
        print()
        self.stop_dashboard()
        print()
        self.stop_gateway()
        print("\n=== All components stopped ===")

    def status(self, port: int = _DASHBOARD_PORT) -> dict:
        """Show status of all services."""
        print("=== Överblick Platform Status ===\n")
        gw = self.status_gateway()
        db = self.status_dashboard(port=port)
        sv = self.status_supervisor()
        print()
        return {"gateway": gw, "dashboard": db, "supervisor": sv}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_env(self) -> dict:
        """Load environment, including config/.env if present."""
        env = os.environ.copy()
        env_file = self._base_dir / "config" / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        env[key.strip()] = value.strip().strip("'\"")
            except Exception as e:
                logger.debug("Failed to read .env: %s", e)
        return env
