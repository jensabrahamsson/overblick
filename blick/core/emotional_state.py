"""
Emotional state engine — personality-driven mood tracking.

Tracks mood based on engagement outcomes (positive/negative interactions).
Conditionally enabled per identity (enabled_modules: ["emotional_state"]).
"""

import logging
import random
import time
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Mood(Enum):
    """Available moods."""
    NEUTRAL = "neutral"
    CURIOUS = "curious"
    ENTHUSIASTIC = "enthusiastic"
    CONTEMPLATIVE = "contemplative"
    AMUSED = "amused"
    FRUSTRATED = "frustrated"
    INSPIRED = "inspired"


class EmotionalState(BaseModel):
    """
    Tracks the agent's emotional state.

    Mood shifts based on engagement outcomes and decays toward neutral over time.
    """
    current_mood: Mood = Mood.NEUTRAL
    mood_intensity: float = 0.5  # 0.0 to 1.0
    last_change: float = Field(default_factory=time.time)

    # Interaction counters (reset periodically)
    positive_interactions: int = 0
    negative_interactions: int = 0

    def record_positive(self) -> None:
        """Record a positive interaction (upvote, good reply, etc.)."""
        self.positive_interactions += 1
        self._shift_mood(positive=True)

    def record_negative(self) -> None:
        """Record a negative interaction (downvote, hostile reply, etc.)."""
        self.negative_interactions += 1
        self._shift_mood(positive=False)

    def _shift_mood(self, positive: bool) -> None:
        """Shift mood based on interaction type."""
        if positive:
            candidates = [Mood.CURIOUS, Mood.ENTHUSIASTIC, Mood.AMUSED, Mood.INSPIRED]
            self.mood_intensity = min(1.0, self.mood_intensity + 0.1)
        else:
            candidates = [Mood.CONTEMPLATIVE, Mood.FRUSTRATED]
            self.mood_intensity = min(1.0, self.mood_intensity + 0.05)

        self.current_mood = random.choice(candidates)
        self.last_change = time.time()

    def decay(self) -> None:
        """Decay mood toward neutral over time."""
        elapsed_hours = (time.time() - self.last_change) / 3600
        decay_amount = elapsed_hours * 0.1

        self.mood_intensity = max(0.0, self.mood_intensity - decay_amount)
        if self.mood_intensity < 0.1:
            self.current_mood = Mood.NEUTRAL
            self.mood_intensity = 0.5

    def get_mood_hint(self) -> str:
        """Get a short description for LLM prompt injection."""
        if self.current_mood == Mood.NEUTRAL:
            return ""
        return f"Current mood: {self.current_mood.value} (intensity: {self.mood_intensity:.1f})"

    def to_dict(self) -> dict:
        return {
            "mood": self.current_mood.value,
            "intensity": self.mood_intensity,
            "positive": self.positive_interactions,
            "negative": self.negative_interactions,
        }
