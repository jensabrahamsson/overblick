"""
Cross-platform utilities for Överblick.

Provides OS-aware helpers for file permissions, signal handling,
process management, and IPC transport selection.

Platform strategy:
- macOS/Linux: Unix domain sockets, POSIX signals, chmod permissions
- Windows: TCP localhost fallback, signal.signal(), no-op permissions
"""

import asyncio
import logging
import os
import signal
import sys
import sysconfig
from pathlib import Path

logger = logging.getLogger(__name__)

# Platform detection
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def set_restrictive_permissions(path: Path) -> None:
    """Set file to owner-read/write only (0o600 on Unix, no-op on Windows).

    Windows relies on NTFS ACLs inherited from the parent directory.
    For Överblick's security model, the data/ directory should have
    restrictive ACLs set during installation on Windows.
    """
    if IS_WINDOWS:
        return
    os.chmod(str(path), 0o600)


def set_restrictive_dir_permissions(path: Path) -> None:
    """Set directory to owner-only access (0o700 on Unix, no-op on Windows)."""
    if IS_WINDOWS:
        return
    os.chmod(str(path), 0o700)


def register_shutdown_signals(
    shutdown_event: asyncio.Event,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Register SIGINT/SIGTERM handlers to set the shutdown event.

    On Unix: uses loop.add_signal_handler() (safe for asyncio).
    On Windows: uses signal.signal() with call_soon_threadsafe().
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    if IS_WINDOWS:
        def _handler(signum, frame):
            loop.call_soon_threadsafe(shutdown_event.set)

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown_event.set)


def get_python_executable() -> str:
    """Get the Python executable path, preferring the active venv.

    Returns the venv's python3 (Unix) or python.exe (Windows) if it exists,
    otherwise falls back to sys.executable.
    """
    scripts_dir = sysconfig.get_path("scripts")
    if IS_WINDOWS:
        venv_python = os.path.join(scripts_dir, "python.exe")
    else:
        venv_python = os.path.join(scripts_dir, "python3")

    if os.path.exists(venv_python):
        return venv_python
    return sys.executable
