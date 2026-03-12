"""
Cross-platform utilities for Överblick.

Provides OS-aware helpers for file permissions, signal handling,
process management, and IPC transport selection.

Platform strategy:
- macOS/Linux: Unix domain sockets, POSIX signals, chmod permissions
- Windows: TCP localhost fallback, signal.signal(), NTFS ACLs
"""

import asyncio
import logging
import os
import signal
import sys
import sysconfig
import warnings
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Platform detection
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def set_restrictive_permissions(path: Path) -> None:
    """Set file to owner-read/write only (0o600 on Unix).

    On Windows: logs a warning since POSIX permissions are unavailable.
    Windows relies on NTFS ACLs inherited from the parent directory.
    For Överblick's security model, the data/ directory should have
    restrictive ACLs set during installation on Windows.
    """
    if IS_WINDOWS:
        logger.debug(
            "Skipping POSIX permissions on Windows for %s — ensure NTFS ACLs restrict access",
            path,
        )
        return
    os.chmod(str(path), 0o600)


def set_restrictive_dir_permissions(path: Path) -> None:
    """Set directory to owner-only access (0o700 on Unix).

    On Windows: logs a warning since POSIX permissions are unavailable.
    """
    if IS_WINDOWS:
        logger.debug(
            "Skipping POSIX directory permissions on Windows for %s — "
            "ensure NTFS ACLs restrict access",
            path,
        )
        return
    os.chmod(str(path), 0o700)


def verify_restrictive_permissions(
    path: Path, require_owner_only: bool = True, is_directory: bool = False
) -> bool:
    """Verify that a file or directory has restrictive permissions.

    On Unix: checks that permissions are owner-only.
    On Windows: always returns True (relies on NTFS ACLs).

    Args:
        path: Path to check
        require_owner_only: If True, requires exactly 0o600 (files) or
            0o700 (directories). If False, allows group/other read but not write.
        is_directory: If True, checks directory permissions (0o700).
            If False, checks file permissions (0o600).

    Returns:
        True if permissions are acceptable, False otherwise.
    """
    if IS_WINDOWS:
        return True  # Windows relies on NTFS ACLs

    if not path.exists():
        return True  # Non-existent path passes by default

    try:
        stat = os.stat(str(path))
        mode = stat.st_mode & 0o777

        if is_directory:
            if require_owner_only:
                # Must be exactly 0o700 (owner read+write+execute)
                return mode == 0o700
            else:
                # Allow owner read+write+execute, group/other read+execute but not write
                # Deny if group or others have write permission
                return (mode & 0o022) == 0
        else:
            if require_owner_only:
                # Must be exactly 0o600 (owner read+write)
                return mode == 0o600
            else:
                # Allow owner read+write, group/other read (0o644) but not write
                # Deny if group or others have write permission
                return (mode & 0o022) == 0
    except OSError:
        return False  # Cannot stat path


def enforce_restrictive_permissions(
    path: Path, require_owner_only: bool = True, is_directory: bool = False
) -> None:
    """Verify file or directory permissions and warn or raise if too permissive.

    Logs a WARNING if permissions are too permissive. In strict mode,
    raises PermissionError.

    Args:
        path: Path to check
        require_owner_only: If True, requires 0o600 (files) or 0o700 (directories).
        is_directory: If True, checks directory permissions.

    Raises:
        PermissionError: If permissions are too permissive and
            OVERBLICK_STRICT_PERMISSIONS=1 is set.
    """
    if not path.exists():
        return

    if not verify_restrictive_permissions(path, require_owner_only, is_directory):
        mode = os.stat(str(path)).st_mode & 0o777
        expected_mode = 0o700 if is_directory else 0o600
        if require_owner_only:
            expected_desc = oct(expected_mode)
        else:
            expected_desc = f"no group/other write (max {oct(expected_mode)})"

        warning_msg = (
            f"{'Directory' if is_directory else 'File'} {path} has overly permissive "
            f"permissions: {oct(mode)}. Expected {expected_desc}."
        )

        if os.environ.get("OVERBLICK_STRICT_PERMISSIONS") == "1":
            raise PermissionError(warning_msg)

        logger.warning(warning_msg)


def register_shutdown_signals(
    shutdown_event: asyncio.Event,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Register shutdown signal handlers to set the shutdown event.

    On Unix: uses loop.add_signal_handler() for SIGINT + SIGTERM.
    On Windows: uses signal.signal() for SIGINT + SIGBREAK.
      - SIGTERM is not registered on Windows because the OS never delivers
        it — processes are terminated via TerminateProcess() which cannot
        be caught. SIGBREAK (Ctrl+Break) is the Windows equivalent.
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    if IS_WINDOWS:

        def _handler(signum: int, frame: Any) -> None:
            loop.call_soon_threadsafe(shutdown_event.set)

        signal.signal(signal.SIGINT, _handler)
        # SIGBREAK is the Windows-specific graceful shutdown signal
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handler)  # type: ignore[attr-defined]
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown_event.set)


def get_python_executable() -> str:
    """Get the Python executable path, preferring the active venv.

    Returns the venv's python3 (Unix) or python.exe (Windows) if it exists,
    otherwise falls back to sys.executable.
    """
    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir is None:
        return sys.executable  # type: ignore[unreachable]

    if IS_WINDOWS:
        venv_python = os.path.join(scripts_dir, "python.exe")
    else:
        venv_python = os.path.join(scripts_dir, "python3")

    if os.path.exists(venv_python):
        return venv_python
    return sys.executable
