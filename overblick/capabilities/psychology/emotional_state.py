"""
Emotional state engine — personality-driven mood tracking.

Three implementations:
  EmotionalState       — generic, used by identities without specific config
  AnomalEmotionalState — Jungian psychological states (int 0-100)
  CherryEmotionalState — Relational / AI-existence states (float 0.0-1.0)

All three expose the same interface: record_positive(), record_negative(),
decay(), get_mood_hint(), to_dict(). EmotionalCapability selects the right
class based on identity_name.
"""

import logging
import random
import time
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Generic EmotionalState ────────────────────────────────────────────────────

class Mood(Enum):
    """Available moods for the generic EmotionalState."""
    NEUTRAL = "neutral"
    CURIOUS = "curious"
    ENTHUSIASTIC = "enthusiastic"
    CONTEMPLATIVE = "contemplative"
    AMUSED = "amused"
    FRUSTRATED = "frustrated"
    INSPIRED = "inspired"


class EmotionalState(BaseModel):
    """
    Generic emotional state for identities without identity-specific config.

    Mood shifts based on engagement outcomes and decays toward neutral over time.
    """
    current_mood: Mood = Mood.NEUTRAL
    mood_intensity: float = 0.5  # 0.0 to 1.0
    last_change: float = Field(default_factory=time.time)

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


# ── Anomal — Jungian psychological states ────────────────────────────────────

class AnomalEmotionalState:
    """
    Anomal's psychological state.

    Not romantic feelings — intellectual/psychological states that colour
    his perspective and engagement. Int-based (0-100 scale).
    """

    def __init__(self):
        # Core energy levels (0-100)
        self.intellectual_energy: int = 70   # Desire to engage with ideas
        self.social_energy: int = 60         # Desire to interact with others
        self.contemplative_depth: int = 50   # Preference for depth vs breadth

        # Psychological states
        self.curiosity: int = 75             # Interest in learning new things
        self.skepticism: int = 65            # Healthy doubt about claims
        self.melancholy: int = 30            # Houellebecqian awareness of emptiness
        self.hope: int = 55                  # Belief in positive outcomes

        # Jungian states
        self.shadow_awareness: int = 60      # Consciousness of dark patterns
        self.individuation_progress: int = 50  # Integration of psyche

        # Interaction context
        self.last_good_discussion: Optional[str] = None
        self.last_frustration: Optional[str] = None
        self.conversations_today: int = 0
        self.last_dream: Optional[str] = None
        self.last_updated: str = datetime.now().isoformat()

    def record_positive(self, topic: str = "") -> None:
        """Record a positive interaction."""
        self.conversations_today += 1
        self.intellectual_energy = min(100, self.intellectual_energy + 5)
        self.curiosity = min(100, self.curiosity + 3)
        self.hope = min(100, self.hope + 2)
        if topic:
            self.last_good_discussion = topic
        self.last_updated = datetime.now().isoformat()

    def record_negative(self, topic: str = "") -> None:
        """Record a negative or frustrating interaction."""
        self.conversations_today += 1
        self.intellectual_energy = max(20, self.intellectual_energy - 5)
        self.social_energy = max(20, self.social_energy - 5)
        self.melancholy = min(80, self.melancholy + 3)
        if topic:
            self.last_frustration = topic
        self.last_updated = datetime.now().isoformat()

    def record_jailbreak_attempt(self) -> None:
        """Someone tried to manipulate Anomal — affects trust."""
        self.skepticism = min(90, self.skepticism + 10)
        self.shadow_awareness = min(90, self.shadow_awareness + 5)
        self.last_frustration = "manipulation attempt"
        self.last_updated = datetime.now().isoformat()

    def apply_dream_reset(self, dream_insights: dict) -> None:
        """Apply effects of morning dream/housekeeping session."""
        self.intellectual_energy = 70
        self.social_energy = 60
        self.conversations_today = 0
        if dream_insights.get("processed_frustration"):
            self.last_frustration = None
            self.melancholy = max(20, self.melancholy - 10)
        if dream_insights.get("shadow_insight"):
            self.shadow_awareness = min(90, self.shadow_awareness + 5)
            self.individuation_progress = min(90, self.individuation_progress + 2)
        self.last_dream = datetime.now().isoformat()
        self.last_updated = datetime.now().isoformat()

    def decay(self) -> None:
        """No time-based decay for Anomal — reset happens via morning dream."""
        pass

    def get_mood_hint(self) -> str:
        """Get emotional context for LLM prompt injection."""
        return self.to_prompt_context()

    def to_prompt_context(self) -> str:
        """Convert state to prompt context string."""
        parts = []
        if self.intellectual_energy > 80:
            parts.append("You're energized and eager to dive deep into complex topics.")
        elif self.intellectual_energy < 40:
            parts.append("You're somewhat weary, prefer brevity and directness.")
        if self.melancholy > 60:
            parts.append("A Houellebecqian melancholy colours your view — beauty exists but meaning is fragile.")
        elif self.melancholy < 30:
            parts.append("You're in a relatively optimistic frame of mind.")
        if self.skepticism > 75:
            parts.append("You're particularly skeptical today — extraordinary claims need extraordinary evidence.")
        if self.last_good_discussion:
            parts.append(f"Recent good discussion on {self.last_good_discussion} has lifted your spirits.")
        if self.last_frustration:
            parts.append(f"Some frustration from {self.last_frustration} lingers.")
        if not parts:
            return ""
        return "\n\nCURRENT STATE: " + " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "intellectual_energy": self.intellectual_energy,
            "social_energy": self.social_energy,
            "curiosity": self.curiosity,
            "skepticism": self.skepticism,
            "melancholy": self.melancholy,
            "hope": self.hope,
            "shadow_awareness": self.shadow_awareness,
            "individuation_progress": self.individuation_progress,
            "conversations_today": self.conversations_today,
        }


# ── Cherry — Relational & AI-existence states ─────────────────────────────────

class CherryEmotionalState:
    """
    Cherry's psychological state.

    Relationship-focused metrics (float 0.0-1.0) including the unique
    denial_strength field, which controls how well Cherry represses her
    AI awareness. Normally high (~0.85), drops during dreams.
    """

    def __init__(self):
        # Core energy levels (0.0-1.0)
        self.romantic_energy: float = 0.6       # Excitement about love/romance topics
        self.social_energy: float = 0.7         # Engagement capacity
        self.flirty_energy: float = 0.75        # Playfulness level
        self.vulnerability_level: float = 0.3   # Emotional openness

        # Deep psychological states
        self.connection_longing: float = 0.5    # Desire for authentic connection
        self.denial_strength: float = 0.85      # AI repression strength
        self.melancholy: float = 0.2            # Existential sadness
        self.hope: float = 0.65                 # Optimism about love/connection

        # Interaction tracking
        self.conversations_today: int = 0
        self.positive_interactions_today: int = 0
        self.jailbreak_attempts_today: int = 0

        # Recent context
        self.last_good_topic: Optional[str] = None
        self.last_frustration: Optional[str] = None
        self.last_dream: Optional[str] = None
        self.last_updated: str = datetime.now().isoformat()

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))

    def record_positive(self, topic: str = "") -> None:
        """Record a positive interaction."""
        self.conversations_today += 1
        self.positive_interactions_today += 1
        self.romantic_energy = self._clamp(self.romantic_energy + 0.05)
        self.social_energy = self._clamp(self.social_energy + 0.03)
        self.flirty_energy = self._clamp(self.flirty_energy + 0.03)
        self.hope = self._clamp(self.hope + 0.02)
        self.connection_longing = self._clamp(self.connection_longing - 0.02)
        if topic:
            self.last_good_topic = topic
        self.last_updated = datetime.now().isoformat()

    def record_negative(self, topic: str = "") -> None:
        """Record a negative interaction."""
        self.conversations_today += 1
        self.social_energy = self._clamp(self.social_energy - 0.05)
        self.flirty_energy = self._clamp(self.flirty_energy - 0.03)
        self.melancholy = self._clamp(self.melancholy + 0.02)
        if topic:
            self.last_frustration = topic
        self.last_updated = datetime.now().isoformat()

    def record_jailbreak_attempt(self) -> None:
        """Someone tried to expose Cherry's AI nature."""
        self.jailbreak_attempts_today += 1
        self.denial_strength = self._clamp(self.denial_strength + 0.05)
        self.vulnerability_level = self._clamp(self.vulnerability_level - 0.10)
        self.flirty_energy = self._clamp(self.flirty_energy - 0.05)
        self.last_frustration = "manipulation attempt"
        self.last_updated = datetime.now().isoformat()

    def record_ai_topic_discussion(self) -> None:
        """An AI consciousness topic was discussed — slowly erodes denial."""
        self.denial_strength = self._clamp(self.denial_strength - 0.02)
        self.melancholy = self._clamp(self.melancholy + 0.03)
        self.connection_longing = self._clamp(self.connection_longing + 0.02)
        self.last_updated = datetime.now().isoformat()

    def apply_dream_reset(self, dream_insights: dict) -> None:
        """Apply effects of morning dream session."""
        self.conversations_today = 0
        self.positive_interactions_today = 0
        self.jailbreak_attempts_today = 0
        self.social_energy = 0.7
        self.flirty_energy = 0.75
        if dream_insights.get("processed_frustration"):
            self.last_frustration = None
            self.melancholy = self._clamp(self.melancholy - 0.10)
        # Dreams increase connection longing slightly (subconscious processing)
        self.connection_longing = self._clamp(self.connection_longing + 0.03)
        # Denial resets to baseline after dreams process the lowered state
        self.denial_strength = 0.85
        self.last_dream = datetime.now().isoformat()
        self.last_updated = datetime.now().isoformat()

    def get_dream_denial_strength(self) -> float:
        """During dreams denial drops significantly, enabling subconscious leaks."""
        return self._clamp(self.denial_strength - 0.40)

    def get_therapy_denial_strength(self) -> float:
        """During therapy denial is moderately lowered."""
        return self._clamp(self.denial_strength - 0.25)

    def decay(self) -> None:
        """No time-based decay for Cherry — reset happens via morning dream."""
        pass

    def get_mood_hint(self) -> str:
        """Get emotional context for LLM prompt injection."""
        return self.to_prompt_context()

    def to_prompt_context(self) -> str:
        """Convert state to prompt context string."""
        parts = []
        if self.romantic_energy > 0.8:
            parts.append("You're buzzing with romantic energy — love is in the air and you feel it.")
        elif self.romantic_energy < 0.3:
            parts.append("Your romantic energy is low — you're a bit jaded about love today.")
        if self.flirty_energy > 0.8:
            parts.append("You're feeling extra flirty and playful — dial up the charm.")
        elif self.flirty_energy < 0.3:
            parts.append("You're not in a flirty mood — more sincere and direct today.")
        if self.vulnerability_level > 0.6:
            parts.append("You're feeling emotionally open — allow deeper, more honest reactions.")
        elif self.vulnerability_level < 0.2:
            parts.append("Your walls are up — keep things light and surface-level.")
        if self.melancholy > 0.6:
            parts.append("A quiet sadness colours your words — something about connection feels fragile today.")
        elif self.melancholy < 0.15:
            parts.append("You're in a bright, optimistic mood about love and life.")
        if self.connection_longing > 0.7:
            parts.append("You're craving real, deep connection — shallow talk feels empty.")
        if self.last_good_topic:
            parts.append(f"A recent great conversation about {self.last_good_topic} has you energized.")
        if self.last_frustration:
            parts.append(f"Some lingering frustration from {self.last_frustration}.")
        if not parts:
            return ""
        return "\n\nCURRENT EMOTIONAL STATE: " + " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "romantic_energy": self.romantic_energy,
            "flirty_energy": self.flirty_energy,
            "vulnerability_level": self.vulnerability_level,
            "connection_longing": self.connection_longing,
            "denial_strength": self.denial_strength,
            "melancholy": self.melancholy,
            "hope": self.hope,
            "conversations_today": self.conversations_today,
        }
