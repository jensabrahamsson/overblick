"""
EmotionalCapability — wraps EmotionalState as a composable capability.

Tracks agent mood based on interaction outcomes and provides
mood hints for LLM prompt injection.

Identity-aware: Anomal gets AnomalEmotionalState (Jungian int-based),
Cherry gets CherryEmotionalState (float-based relationship metrics),
all others get the generic EmotionalState.
"""

import logging
from typing import Optional, Union

from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.capabilities.psychology.emotional_state import (
    EmotionalState,
    AnomalEmotionalState,
    CherryEmotionalState,
)

logger = logging.getLogger(__name__)

_AnyState = Union[EmotionalState, AnomalEmotionalState, CherryEmotionalState]


class EmotionalCapability(CapabilityBase):
    """
    Emotional state tracking capability.

    Wraps the appropriate EmotionalState variant, providing mood tracking
    through the standard capability lifecycle. Mood decays toward neutral
    over time via tick().
    """

    name = "emotional_state"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._state: Optional[_AnyState] = None

    async def setup(self) -> None:
        """Initialize identity-specific emotional state."""
        identity = self.ctx.identity_name.lower()

        if identity == "anomal":
            self._state = AnomalEmotionalState()
            logger.info("EmotionalCapability initialized for Anomal (Jungian state)")
        elif identity == "cherry":
            self._state = CherryEmotionalState()
            logger.info("EmotionalCapability initialized for Cherry (relational state)")
        else:
            self._state = EmotionalState()
            logger.info("EmotionalCapability initialized for %s (generic state)", identity)

    async def tick(self) -> None:
        """Decay mood toward neutral over time."""
        if self._state:
            self._state.decay()

    async def on_event(self, event: str, **kwargs) -> None:
        """React to interaction events."""
        if not self._state:
            return
        topic = kwargs.get("topic", "")
        if event == "interaction_positive":
            self._state.record_positive(topic) if topic else self._state.record_positive()
        elif event == "interaction_negative":
            self._state.record_negative(topic) if topic else self._state.record_negative()
        elif event == "jailbreak_attempt":
            if hasattr(self._state, "record_jailbreak_attempt"):
                self._state.record_jailbreak_attempt()
        elif event == "ai_topic_discussed":
            if hasattr(self._state, "record_ai_topic_discussion"):
                self._state.record_ai_topic_discussion()

    def get_prompt_context(self) -> str:
        """Return mood hint for injection into LLM prompts."""
        if not self._state:
            return ""
        return self._state.get_mood_hint()

    def record_positive(self, topic: str = "") -> None:
        """Record a positive interaction."""
        if self._state:
            self._state.record_positive(topic) if topic else self._state.record_positive()

    def record_negative(self, topic: str = "") -> None:
        """Record a negative interaction."""
        if self._state:
            self._state.record_negative(topic) if topic else self._state.record_negative()

    @property
    def inner(self) -> Optional[_AnyState]:
        """Access the underlying state (for tests)."""
        return self._state
