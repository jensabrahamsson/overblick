"""
OpeningCapability — wraps OpeningSelector as a composable capability.

Selects varied opening phrases for agent responses to avoid repetition.
"""

import logging
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.capabilities.social.opening_selector import OpeningSelector

logger = logging.getLogger(__name__)


class OpeningCapability(CapabilityBase):
    """
    Opening phrase selection capability.

    Wraps OpeningSelector, providing varied response openings
    based on identity configuration.
    """

    name = "openings"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._selector: Optional[OpeningSelector] = None

    async def setup(self) -> None:
        phrases = self.ctx.config.get("opening_phrases", None)
        history_size = self.ctx.config.get("history_size", 10)
        self._selector = OpeningSelector(phrases=phrases, history_size=history_size)
        logger.info("OpeningCapability initialized for %s", self.ctx.identity_name)

    def select(self) -> str:
        """Select a varied opening phrase."""
        if not self._selector:
            return ""
        return self._selector.select()

    @property
    def inner(self) -> Optional[OpeningSelector]:
        """Access the underlying OpeningSelector (for tests/migration)."""
        return self._selector
