"""
TherapyCapability — wraps TherapySystem as a composable capability.

Weekly psychological reflection through Jungian/Freudian frameworks.
"""

import logging
from typing import Optional

from blick.core.capability import CapabilityBase, CapabilityContext
from blick.plugins.moltbook.therapy_system import TherapySystem, TherapySession

logger = logging.getLogger(__name__)


class TherapyCapability(CapabilityBase):
    """
    Weekly therapy session capability.

    Wraps the TherapySystem module. Uses the LLM client from
    CapabilityContext for analysis and synthesis.
    """

    name = "therapy_system"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._therapy_system: Optional[TherapySystem] = None

    async def setup(self) -> None:
        therapy_day = self.ctx.config.get("therapy_day", TherapySystem.DEFAULT_THERAPY_DAY)
        system_prompt = self.ctx.config.get("system_prompt", "")
        self._therapy_system = TherapySystem(
            llm_client=self.ctx.llm_client,
            system_prompt=system_prompt,
            therapy_day=therapy_day,
        )
        logger.info("TherapyCapability initialized for %s (day=%d)", self.ctx.identity_name, therapy_day)

    def is_therapy_day(self) -> bool:
        """Check if today is therapy day."""
        if not self._therapy_system:
            return False
        return self._therapy_system.is_therapy_day()

    async def run_session(self, **kwargs) -> Optional[TherapySession]:
        """Run a complete therapy session. Delegates to TherapySystem."""
        if not self._therapy_system:
            return None
        return await self._therapy_system.run_session(**kwargs)

    @property
    def inner(self) -> Optional[TherapySystem]:
        """Access the underlying TherapySystem (for tests/migration)."""
        return self._therapy_system
