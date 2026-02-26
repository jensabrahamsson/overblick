"""
Multi-identity log scanner for the log agent.

Scans log files and audit databases across all configured identities.
Maintains byte offsets per file for incremental scanning (skip already-read data).
Handles file rotation gracefully.

Built on the same patterns as dev_agent's LogWatcher but generalized
for multi-identity scanning.
"""

import logging
import re
import time
from pathlib import Path
from typing import Optional

from overblick.plugins.log_agent.models import LogEntry, LogScanResult

logger = logging.getLogger(__name__)

# Regex for standard Python logging lines: "2026-02-26 03:15:42,123 - module - LEVEL - message"
_LOG_LINE_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.]?\d*)"  # timestamp
    r"\s*[-–]\s*(\S+)"                                       # module
    r"\s*[-–]\s*(ERROR|CRITICAL|WARNING)"                    # level
    r"\s*[-–]\s*(.+)$",                                      # message
    re.IGNORECASE,
)

# Traceback continuation: lines starting with whitespace or "Traceback"
_TRACEBACK_LINE = re.compile(r"^\s+|^Traceback \(")

# Maximum traceback length (characters)
_MAX_TRACEBACK_LEN = 2000

# Maximum number of entries per file scan (prevent runaway)
_MAX_ENTRIES_PER_SCAN = 100


class LogScanner:
    """
    Scans log files for ERROR and CRITICAL entries across multiple identities.

    Maintains per-file byte offsets to enable incremental scanning.
    """

    def __init__(
        self,
        base_log_dir: Path,
        identities: list[str],
        levels: tuple[str, ...] = ("ERROR", "CRITICAL"),
    ):
        self._base_log_dir = base_log_dir
        self._identities = identities
        self._levels = levels
        self._offsets: dict[str, int] = {}  # file_path → byte offset

    @property
    def identities(self) -> list[str]:
        return list(self._identities)

    def get_offset(self, file_path: str) -> int:
        """Get the current byte offset for a file."""
        return self._offsets.get(file_path, 0)

    def set_offset(self, file_path: str, offset: int) -> None:
        """Set the byte offset for a file (e.g. loaded from DB)."""
        self._offsets[file_path] = offset

    def get_log_files(self, identity: str) -> list[Path]:
        """Find all .log files for a given identity."""
        log_dir = self._base_log_dir / identity
        if not log_dir.is_dir():
            return []
        return sorted(log_dir.glob("*.log"))

    def scan_identity(self, identity: str) -> LogScanResult:
        """Scan all log files for one identity. Returns scan result."""
        start = time.time()
        all_entries: list[LogEntry] = []

        for log_file in self.get_log_files(identity):
            entries, new_offset = self.scan_file(log_file, identity)
            self._offsets[str(log_file)] = new_offset
            all_entries.extend(entries)

        # Deduplicate
        all_entries = self._deduplicate(all_entries)

        duration_ms = (time.time() - start) * 1000
        return LogScanResult(
            identity=identity,
            errors_found=sum(1 for e in all_entries if e.level == "ERROR"),
            criticals_found=sum(1 for e in all_entries if e.level == "CRITICAL"),
            entries=all_entries,
            scan_duration_ms=duration_ms,
        )

    def scan_file(
        self, file_path: Path, identity: str,
    ) -> tuple[list[LogEntry], int]:
        """
        Scan a log file from the stored byte offset.

        Returns (entries, new_offset). Handles file rotation
        (file shrunk since last read → reset to 0).
        """
        path_str = str(file_path)
        offset = self._offsets.get(path_str, 0)

        if not file_path.is_file():
            return [], offset

        file_size = file_path.stat().st_size
        if file_size < offset:
            # File rotation detected — reset to beginning
            logger.info("Log file rotated: %s (was %d, now %d)", path_str, offset, file_size)
            offset = 0

        if file_size == offset:
            return [], offset  # No new data

        entries: list[LogEntry] = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                current_entry: Optional[LogEntry] = None
                traceback_lines: list[str] = []
                line_number = 0

                for line_number_raw, line in enumerate(f, start=1):
                    # Approximate line number from offset
                    line_number = line_number_raw

                    match = _LOG_LINE_PATTERN.match(line.rstrip())
                    if match:
                        # Save previous entry if any
                        if current_entry:
                            if traceback_lines:
                                current_entry.traceback = "\n".join(traceback_lines)[:_MAX_TRACEBACK_LEN]
                            entries.append(current_entry)
                            if len(entries) >= _MAX_ENTRIES_PER_SCAN:
                                break

                        level = match.group(3).upper()
                        if level in self._levels:
                            current_entry = LogEntry(
                                identity=identity,
                                file_path=path_str,
                                line_number=line_number,
                                level=level,
                                message=match.group(4).strip(),
                                timestamp=match.group(1),
                            )
                            traceback_lines = []
                        else:
                            current_entry = None
                            traceback_lines = []
                    elif current_entry and _TRACEBACK_LINE.match(line):
                        traceback_lines.append(line.rstrip())

                # Don't forget the last entry (respect max limit)
                if current_entry and len(entries) < _MAX_ENTRIES_PER_SCAN:
                    if traceback_lines:
                        current_entry.traceback = "\n".join(traceback_lines)[:_MAX_TRACEBACK_LEN]
                    entries.append(current_entry)

                new_offset = f.tell()
        except OSError as e:
            logger.warning("Failed to read log file %s: %s", path_str, e)
            return [], offset

        return entries, new_offset

    def scan_all(self) -> list[LogScanResult]:
        """Scan all configured identities. Returns list of results."""
        results = []
        for identity in self._identities:
            result = self.scan_identity(identity)
            results.append(result)
        return results

    @staticmethod
    def _deduplicate(entries: list[LogEntry]) -> list[LogEntry]:
        """Remove duplicate entries by message content."""
        seen: set[str] = set()
        unique: list[LogEntry] = []
        for entry in entries:
            # Dedup key: level + message (ignore line number, as same error
            # may appear on different lines after restart)
            key = f"{entry.level}:{entry.message}"
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        return unique
