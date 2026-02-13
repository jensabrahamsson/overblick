"""
DreamCapability — wraps DreamSystem as a composable capability.

Generates morning reflections and provides dream context for prompt injection.
"""

import logging
from typing import Any, Optional

from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.capabilities.psychology.dream_system import DreamSystem

logger = logging.getLogger(__name__)


class DreamCapability(CapabilityBase):
    """
    Dream generation and reflection capability.

    Wraps the DreamSystem module, exposing it through the standard
    capability lifecycle. Provides dream context for LLM prompts.
    """

    name = "dream_system"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._dream_system: Optional[DreamSystem] = None

    async def setup(self) -> None:
        templates = self.ctx.config.get("dream_templates", None)
        self._dream_system = DreamSystem(dream_templates=templates)
        logger.info("DreamCapability initialized for %s", self.ctx.identity_name)

    def get_prompt_context(self) -> str:
        """Return dream context for injection into LLM prompts."""
        if not self._dream_system:
            return ""
        return self._dream_system.get_dream_context_for_prompt()

    def generate_morning_dream(
        self,
        recent_topics: Optional[list[str]] = None,
        emotional_state: Optional[Any] = None,
    ):
        """Generate a morning dream. Delegates to DreamSystem."""
        if not self._dream_system:
            return None
        return self._dream_system.generate_morning_dream(recent_topics, emotional_state)

    def get_dream_insights(self, days: int = 7) -> list[str]:
        """Get insights from recent dreams."""
        if not self._dream_system:
            return []
        return self._dream_system.get_dream_insights(days)

    @property
    def inner(self) -> Optional[DreamSystem]:
        """Access the underlying DreamSystem (for tests/migration)."""
        return self._dream_system
