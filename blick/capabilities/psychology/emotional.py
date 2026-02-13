"""
EmotionalCapability — wraps EmotionalState as a composable capability.

Tracks agent mood based on interaction outcomes and provides
mood hints for LLM prompt injection.
"""

import logging
from typing import Optional

from blick.core.capability import CapabilityBase, CapabilityContext
from blick.core.emotional_state import EmotionalState

logger = logging.getLogger(__name__)


class EmotionalCapability(CapabilityBase):
    """
    Emotional state tracking capability.

    Wraps EmotionalState, providing mood tracking through the
    standard capability lifecycle. Mood decays toward neutral
    over time via tick().
    """

    name = "emotional_state"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._state: Optional[EmotionalState] = None

    async def setup(self) -> None:
        self._state = EmotionalState()
        logger.info("EmotionalCapability initialized for %s", self.ctx.identity_name)

    async def tick(self) -> None:
        """Decay mood toward neutral over time."""
        if self._state:
            self._state.decay()

    async def on_event(self, event: str, **kwargs) -> None:
        """React to interaction events."""
        if not self._state:
            return
        if event == "interaction_positive":
            self._state.record_positive()
        elif event == "interaction_negative":
            self._state.record_negative()

    def get_prompt_context(self) -> str:
        """Return mood hint for injection into LLM prompts."""
        if not self._state:
            return ""
        return self._state.get_mood_hint()

    def record_positive(self) -> None:
        """Record a positive interaction."""
        if self._state:
            self._state.record_positive()

    def record_negative(self) -> None:
        """Record a negative interaction."""
        if self._state:
            self._state.record_negative()

    @property
    def inner(self) -> Optional[EmotionalState]:
        """Access the underlying EmotionalState (for tests/migration)."""
        return self._state
