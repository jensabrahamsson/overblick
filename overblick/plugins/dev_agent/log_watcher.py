"""
Log watcher for the dev agent.

Scans log files for ERROR patterns and tracebacks. Maintains
file offsets in the database to avoid re-processing old entries.
"""

import logging
import re
from pathlib import Path
from typing import Optional

from overblick.plugins.dev_agent.models import LogErrorEntry

logger = logging.getLogger(__name__)

# Patterns that indicate an error worth investigating
_ERROR_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}[\.,]?\d*)\s+"
    r"(ERROR|CRITICAL)\s+(.+)$",
    re.MULTILINE,
)

_TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)
_TRACEBACK_END = re.compile(r"^\w+Error:|^\w+Exception:", re.MULTILINE)


class LogWatcher:
    """
    Scans identity log files for errors and tracebacks.

    Reads from the last known byte offset to avoid re-processing.
    Offset state is managed by the caller (DevAgentDB).
    """

    def __init__(
        self,
        base_log_dir: Path,
        scan_identities: list[str],
        enabled: bool = True,
    ):
        self._base_dir = base_log_dir
        self._identities = scan_identities
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_log_files(self) -> list[tuple[str, Path]]:
        """
        Get all log files to scan.

        Returns list of (identity, file_path) tuples.
        """
        files = []
        for identity in self._identities:
            log_dir = self._base_dir / identity / "logs"
            if not log_dir.is_dir():
                # Try alternative: data/<identity>/logs/
                log_dir = self._base_dir.parent / "data" / identity / "logs"

            if log_dir.is_dir():
                for log_file in sorted(log_dir.glob("*.log")):
                    files.append((identity, log_file))

        return files

    def scan_file(
        self,
        file_path: Path,
        identity: str,
        offset: int = 0,
    ) -> tuple[list[LogErrorEntry], int]:
        """
        Scan a log file from the given byte offset.

        Returns (errors_found, new_offset).
        """
        if not self._enabled:
            return [], offset

        if not file_path.is_file():
            return [], offset

        try:
            file_size = file_path.stat().st_size
        except OSError:
            return [], offset

        # If file is smaller than offset, it was rotated — reset
        if file_size < offset:
            offset = 0

        # Nothing new to read
        if file_size <= offset:
            return [], offset

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                content = f.read()
                new_offset = f.tell()
        except (OSError, IOError) as e:
            logger.warning("Failed to read %s: %s", file_path, e)
            return [], offset

        errors = self._extract_errors(content, file_path, identity, offset)
        return errors, new_offset

    def _extract_errors(
        self,
        content: str,
        file_path: Path,
        identity: str,
        base_offset: int,
    ) -> list[LogErrorEntry]:
        """Extract error entries from log content."""
        errors: list[LogErrorEntry] = []
        lines = content.split("\n")

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check for ERROR/CRITICAL lines
            match = _ERROR_PATTERN.match(line)
            if match:
                timestamp = match.group(1)
                level = match.group(2)
                message = match.group(3)

                # Look ahead for traceback
                traceback_text = ""
                if i + 1 < len(lines) and _TRACEBACK_START.match(lines[i + 1]):
                    tb_lines = [lines[i + 1]]
                    j = i + 2
                    while j < len(lines) and j < i + 50:  # Cap at 50 lines
                        tb_lines.append(lines[j])
                        if _TRACEBACK_END.match(lines[j]):
                            break
                        j += 1
                    traceback_text = "\n".join(tb_lines)
                    i = j  # Skip past traceback

                errors.append(LogErrorEntry(
                    file_path=str(file_path),
                    line_number=base_offset + sum(len(l) + 1 for l in lines[:i]),
                    identity=identity,
                    level=level,
                    message=message.strip(),
                    traceback=traceback_text,
                    timestamp=timestamp,
                ))

            i += 1

        return errors

    @staticmethod
    def deduplicate_errors(errors: list[LogErrorEntry]) -> list[LogErrorEntry]:
        """
        Remove duplicate errors based on message + traceback signature.

        Keeps the first occurrence of each unique error.
        """
        seen: set[str] = set()
        unique: list[LogErrorEntry] = []

        for error in errors:
            # Use message + last line of traceback as signature
            tb_last = error.traceback.strip().split("\n")[-1] if error.traceback else ""
            sig = f"{error.message}|{tb_last}"
            if sig not in seen:
                seen.add(sig)
                unique.append(error)

        return unique
