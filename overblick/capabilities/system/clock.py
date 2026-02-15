"""
SystemClockCapability — time awareness for all agents.

Provides agents with access to the current system time, date, timezone,
and formatted timestamps. Injected automatically by the orchestrator
into every agent (no opt-in required).

The get_prompt_context() method returns a concise time string that can
be injected into LLM prompts so the agent knows "what time it is."
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class SystemClockCapability(CapabilityBase):
    """Time awareness capability for agents."""

    name = "system_clock"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        tz_name = "Europe/Stockholm"
        if ctx.identity and hasattr(ctx.identity, "quiet_hours"):
            tz_name = ctx.identity.quiet_hours.timezone or tz_name
        self._timezone = ZoneInfo(tz_name)

    async def setup(self) -> None:
        """No setup required — system clock is always available."""
        logger.debug("SystemClockCapability ready (tz=%s)", self._timezone)

    def now(self) -> datetime:
        """Return the current datetime in the agent's timezone."""
        return datetime.now(self._timezone)

    def date_str(self) -> str:
        """Return current date as YYYY-MM-DD."""
        return self.now().strftime("%Y-%m-%d")

    def time_str(self) -> str:
        """Return current time as HH:MM."""
        return self.now().strftime("%H:%M")

    def weekday(self) -> str:
        """Return the current weekday name (English)."""
        return self.now().strftime("%A")

    def iso(self) -> str:
        """Return full ISO 8601 timestamp."""
        return self.now().isoformat()

    def get_prompt_context(self) -> str:
        """Inject current time into LLM prompts."""
        now = self.now()
        return (
            f"Current time: {now.strftime('%A, %B %d, %Y at %H:%M')} "
            f"({now.tzname()})"
        )
