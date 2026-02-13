"""
Quiet hours — GPU bedroom mode.

Ported from anomal_moltbook/core/quiet_hours.py.
Parameterized from identity settings (start/end hour, timezone).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from overblick.personalities import QuietHoursSettings

logger = logging.getLogger(__name__)


class QuietHoursChecker:
    """
    Checks if we're in quiet hours (bedroom mode).

    During quiet hours, no LLM calls should be made to avoid
    heating up the GPU and making noise.
    """

    def __init__(self, settings: QuietHoursSettings):
        self.enabled = settings.enabled
        self.timezone = ZoneInfo(settings.timezone)
        self.start_hour = settings.start_hour
        self.end_hour = settings.end_hour
        self.mode = settings.mode

        logger.info(
            f"QuietHoursChecker: "
            f"{'enabled' if self.enabled else 'disabled'}, "
            f"{self.start_hour}:00-{self.end_hour}:00 {settings.timezone}"
        )

    def is_quiet_hours(self, now: Optional[datetime] = None) -> bool:
        """Check if we're currently in quiet hours."""
        if not self.enabled:
            return False

        if now is None:
            now = datetime.now(self.timezone)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=self.timezone)

        current_hour = now.hour

        if self.start_hour > self.end_hour:
            return current_hour >= self.start_hour or current_hour < self.end_hour
        else:
            return self.start_hour <= current_hour < self.end_hour

    def can_use_llm(self) -> bool:
        """True if LLM usage is allowed right now."""
        return not self.is_quiet_hours()

    def time_until_active(self) -> Optional[int]:
        """Seconds until quiet hours end, or None if not quiet."""
        if not self.is_quiet_hours():
            return None

        now = datetime.now(self.timezone)

        if now.hour >= self.start_hour:
            end_time = now.replace(
                hour=self.end_hour, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
        else:
            end_time = now.replace(
                hour=self.end_hour, minute=0, second=0, microsecond=0
            )

        return int((end_time - now).total_seconds())

    def get_status(self) -> dict:
        """Get current status dict."""
        now = datetime.now(self.timezone)
        is_quiet = self.is_quiet_hours(now)
        return {
            "enabled": self.enabled,
            "is_quiet_hours": is_quiet,
            "current_time": now.strftime("%H:%M"),
            "timezone": str(self.timezone),
            "quiet_window": f"{self.start_hour}:00-{self.end_hour}:00",
            "mode": self.mode,
            "can_use_llm": not is_quiet,
            "seconds_until_active": self.time_until_active(),
        }
