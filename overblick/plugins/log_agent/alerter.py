"""
Alert formatter and sender for the log agent.

Formats log scan results into human-readable Telegram messages
and tracks alert deduplication to prevent spam.
"""

import logging
import time
from typing import Any, Optional

from overblick.plugins.log_agent.models import AlertSeverity, LogEntry, LogScanResult

logger = logging.getLogger(__name__)

# Minimum interval between alerts for the same error (seconds)
_ALERT_COOLDOWN = 3600  # 1 hour


class AlertFormatter:
    """Formats log entries into alert messages."""

    @staticmethod
    def format_scan_summary(results: list[LogScanResult]) -> Optional[str]:
        """
        Format a scan summary for Telegram notification.

        Returns None if there's nothing to report (no errors).
        """
        total_errors = sum(r.errors_found for r in results)
        total_criticals = sum(r.criticals_found for r in results)

        if total_errors == 0 and total_criticals == 0:
            return None

        lines = ["*Vakt Log Alert*\n"]

        if total_criticals > 0:
            lines.append(f"CRITICAL: {total_criticals} critical entries found")
        if total_errors > 0:
            lines.append(f"ERROR: {total_errors} error entries found")

        lines.append("")

        for result in results:
            if not result.entries:
                continue
            lines.append(f"*{result.identity}:*")
            for entry in result.entries[:5]:  # Max 5 per identity
                msg = entry.message[:120]
                lines.append(f"  [{entry.level}] {msg}")
            if len(result.entries) > 5:
                lines.append(f"  _...and {len(result.entries) - 5} more_")
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def format_critical_alert(entry: LogEntry) -> str:
        """Format a single critical entry as an urgent alert."""
        msg = entry.message[:200]
        text = (
            f"*CRITICAL ALERT — {entry.identity}*\n"
            f"{msg}\n"
        )
        if entry.traceback:
            tb = entry.traceback[:500]
            text += f"\n```\n{tb}\n```"
        return text

    @staticmethod
    def severity_from_results(results: list[LogScanResult]) -> AlertSeverity:
        """Determine overall severity from scan results."""
        if any(r.criticals_found > 0 for r in results):
            return AlertSeverity.CRITICAL
        if any(r.errors_found > 0 for r in results):
            return AlertSeverity.ERROR
        return AlertSeverity.INFO


class AlertDeduplicator:
    """
    Prevents alert spam by tracking recently sent alerts.

    Uses a cooldown per error key — same error won't trigger
    another alert within the cooldown period.
    """

    def __init__(self, cooldown_seconds: int = _ALERT_COOLDOWN):
        self._cooldown = cooldown_seconds
        self._sent: dict[str, float] = {}  # alert_key → last_sent_timestamp

    def should_alert(self, entry: LogEntry) -> bool:
        """
        Check if this entry should trigger an alert AND record it.

        For backwards compatibility, this both checks and records.
        Use would_alert() + record_sent() for explicit control.
        """
        if not self.would_alert(entry):
            return False
        self.record_sent(entry)
        return True

    def would_alert(self, entry: LogEntry) -> bool:
        """Pure check: would this entry trigger an alert? Does not record."""
        key = f"{entry.identity}:{entry.level}:{entry.message[:100]}"
        now = time.time()
        last_sent = self._sent.get(key)
        if last_sent and (now - last_sent) < self._cooldown:
            return False
        return True

    def record_sent(self, entry: LogEntry) -> None:
        """Record that an alert was successfully sent for this entry."""
        key = f"{entry.identity}:{entry.level}:{entry.message[:100]}"
        self._sent[key] = time.time()

    def cleanup(self) -> int:
        """Remove expired cooldown entries. Returns count removed."""
        now = time.time()
        expired = [k for k, t in self._sent.items() if (now - t) > self._cooldown]
        for k in expired:
            del self._sent[k]
        return len(expired)
