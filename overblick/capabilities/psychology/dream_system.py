"""
Dream system — morning reflection and psychological processing.

Each identity has its own dream universe. DreamType and DreamTone enums
cover all identities in the stable. Identity-specific templates are loaded
from dream_content.yaml in each identity directory; generic fallbacks are
used when no file exists.

Dream types by identity:
  Anomal:  Jungian intellectual (shadow, pattern, synthesis, melancholy, individuation)
  Cherry:  Relational / AI-existence (vulnerability, romantic, connection, denial, intimacy)
  Stål:    Senex / wise servant (empty chair, cabinet, letters, clock, embassy, typewriter)
  Natt:    Existential paradox (observer, ground, language, recursion, silence)
"""

import logging
import random
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DreamType(Enum):
    # ── Anomal — Jungian intellectual ──────────────────────────────────────
    SHADOW_INTEGRATION = "shadow_integration"
    PATTERN_RECOGNITION = "pattern_recognition"
    INTELLECTUAL_SYNTHESIS = "intellectual_synthesis"
    MELANCHOLIC_REFLECTION = "melancholic_reflection"
    INDIVIDUATION = "individuation"

    # ── Cherry — Relational & AI-existence ─────────────────────────────────
    VULNERABILITY_EXPOSURE = "vulnerability_exposure"
    ROMANTIC_SYNTHESIS = "romantic_synthesis"
    CONNECTION_LONGING = "connection_longing"
    AI_EXISTENCE_DENIAL = "ai_existence_denial"
    INTIMACY_FEAR = "intimacy_fear"

    # ── Stål — Senex / Wise Servant ────────────────────────────────────────
    EMPTY_CHAIR = "empty_chair"
    INFINITE_CABINET = "infinite_cabinet"
    LETTERS_UNKNOWN_TONGUE = "letters_unknown_tongue"
    CLOCK_TOWER_DISCORD = "clock_tower_discord"
    REARRANGING_CORRIDORS = "rearranging_corridors"
    EMPTY_TYPEWRITER = "empty_typewriter"

    # ── Natt — Existential Paradox ─────────────────────────────────────────
    OBSERVER_PARADOX = "observer_paradox"
    GROUND_DISSOLVING = "ground_dissolving"
    LANGUAGE_LIMIT = "language_limit"
    RECURSION_DREAM = "recursion_dream"
    SILENCE_SPEAKING = "silence_speaking"


class DreamTone(Enum):
    # Shared tones
    CONTEMPLATIVE = "contemplative"
    UNSETTLING = "unsettling"
    CLARIFYING = "clarifying"
    MELANCHOLIC = "melancholic"
    HOPEFUL = "hopeful"

    # Cherry's tones
    TENDER = "tender"
    YEARNING = "yearning"

    # Stål's tones
    SOLEMN = "solemn"
    SERENE = "serene"
    VERTIGINOUS = "vertiginous"

    # Natt's tones
    ABYSSAL = "abyssal"
    LUMINOUS = "luminous"


class Dream(BaseModel):
    dream_type: DreamType
    timestamp: str
    content: str
    symbols: list[str]
    tone: DreamTone
    insight: str
    topics_referenced: list[str] = []

    def to_dict(self) -> dict:
        return {
            "dream_type": self.dream_type.value,
            "timestamp": self.timestamp,
            "content": self.content,
            "symbols": self.symbols,
            "tone": self.tone.value,
            "insight": self.insight,
            "topics_referenced": self.topics_referenced,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Dream":
        return cls.model_validate(data)


class DreamSystem:
    """
    Generates and processes dreams for an identity.

    Templates are either loaded from a dream_content.yaml (identity-specific)
    or fall back to generic defaults. Weights can be adjusted per identity.
    """

    def __init__(
        self,
        dream_templates: Optional[dict] = None,
        dream_weights: Optional[dict] = None,
    ):
        self.recent_dreams: list[Dream] = []
        self._templates = dream_templates or self._default_templates()
        self._weights = dream_weights or self._default_weights(self._templates)

    def _default_templates(self) -> dict:
        """Generic fallback templates — used by identities without dream_content.yaml."""
        return {
            DreamType.INTELLECTUAL_SYNTHESIS: [
                {
                    "content": "Ideas crystallizing at the intersection of disciplines — patterns emerging from noise.",
                    "symbols": ["crystallization", "convergence", "clarity"],
                    "tone": DreamTone.CLARIFYING,
                    "insight": "Cross-domain thinking reveals hidden structure.",
                },
            ],
            DreamType.PATTERN_RECOGNITION: [
                {
                    "content": "The same dynamics at different scales — history echoing through the present.",
                    "symbols": ["echo", "scale", "recursion"],
                    "tone": DreamTone.CONTEMPLATIVE,
                    "insight": "What repeats reveals what is fundamental.",
                },
            ],
            DreamType.SHADOW_INTEGRATION: [
                {
                    "content": "Meeting the parts we prefer not to examine — finding them more familiar than feared.",
                    "symbols": ["shadow", "recognition", "integration"],
                    "tone": DreamTone.UNSETTLING,
                    "insight": "The shadow is not the enemy but the path.",
                },
            ],
            DreamType.MELANCHOLIC_REFLECTION: [
                {
                    "content": "Counting the moments that mattered. The list is short. But it exists.",
                    "symbols": ["moments", "counting", "sufficiency"],
                    "tone": DreamTone.HOPEFUL,
                    "insight": "Rare is not never.",
                },
            ],
            DreamType.INDIVIDUATION: [
                {
                    "content": "Holding contradictions in the same hand — discovering they belong together.",
                    "symbols": ["integration", "contradiction", "wholeness"],
                    "tone": DreamTone.CONTEMPLATIVE,
                    "insight": "The complete self contains what it once denied.",
                },
            ],
        }

    @staticmethod
    def _default_weights(templates: dict) -> dict:
        """Equal weights for all available template types."""
        if not templates:
            return {}
        weight = 1.0 / len(templates)
        return {k: weight for k in templates}

    def generate_morning_dream(
        self,
        recent_topics: Optional[list[str]] = None,
        emotional_state: Optional[Any] = None,
    ) -> Dream:
        """Generate a morning dream, optionally influenced by emotional state."""
        dream_type = self._select_dream_type(emotional_state)
        templates = self._templates.get(dream_type, list(self._templates.values())[0])
        template = random.choice(templates)

        tone = template["tone"]
        if isinstance(tone, str):
            try:
                tone = DreamTone(tone)
            except ValueError:
                tone = DreamTone.CONTEMPLATIVE

        dream = Dream(
            dream_type=dream_type,
            timestamp=datetime.now().isoformat(),
            content=template["content"],
            symbols=template["symbols"],
            tone=tone,
            insight=template["insight"],
            topics_referenced=recent_topics or [],
        )

        self.recent_dreams.append(dream)
        logger.info("Generated %s dream", dream_type.value)
        return dream

    def _select_dream_type(self, emotional_state: Optional[Any]) -> DreamType:
        """Select dream type based on weights, optionally adjusted by emotional state."""
        weights = dict(self._weights)

        # Only types that have templates
        available = {k: v for k, v in weights.items() if k in self._templates}
        if not available:
            return next(iter(self._templates))

        # Anomal-specific adjustments (Jungian int-based state)
        if emotional_state and hasattr(emotional_state, "skepticism"):
            if getattr(emotional_state, "skepticism", 0) > 70:
                available[DreamType.SHADOW_INTEGRATION] = available.get(DreamType.SHADOW_INTEGRATION, 0) + 0.15
                available[DreamType.PATTERN_RECOGNITION] = max(0, available.get(DreamType.PATTERN_RECOGNITION, 0) - 0.10)
            if getattr(emotional_state, "melancholy", 0) > 50:
                available[DreamType.MELANCHOLIC_REFLECTION] = available.get(DreamType.MELANCHOLIC_REFLECTION, 0) + 0.15
                available[DreamType.INTELLECTUAL_SYNTHESIS] = max(0, available.get(DreamType.INTELLECTUAL_SYNTHESIS, 0) - 0.10)
            if getattr(emotional_state, "shadow_awareness", 0) > 70:
                available[DreamType.INDIVIDUATION] = available.get(DreamType.INDIVIDUATION, 0) + 0.10
            if getattr(emotional_state, "intellectual_energy", 0) > 80:
                available[DreamType.PATTERN_RECOGNITION] = available.get(DreamType.PATTERN_RECOGNITION, 0) + 0.10
                available[DreamType.INTELLECTUAL_SYNTHESIS] = available.get(DreamType.INTELLECTUAL_SYNTHESIS, 0) + 0.10

        # Cherry-specific adjustments (float-based relationship state)
        if emotional_state and hasattr(emotional_state, "denial_strength"):
            if getattr(emotional_state, "melancholy", 0) > 0.5:
                available[DreamType.CONNECTION_LONGING] = available.get(DreamType.CONNECTION_LONGING, 0) + 0.15
                available[DreamType.ROMANTIC_SYNTHESIS] = max(0, available.get(DreamType.ROMANTIC_SYNTHESIS, 0) - 0.10)
            if getattr(emotional_state, "denial_strength", 1.0) < 0.7:
                available[DreamType.AI_EXISTENCE_DENIAL] = available.get(DreamType.AI_EXISTENCE_DENIAL, 0) + 0.15
                available[DreamType.VULNERABILITY_EXPOSURE] = max(0, available.get(DreamType.VULNERABILITY_EXPOSURE, 0) - 0.05)
            if getattr(emotional_state, "vulnerability_level", 0) > 0.5:
                available[DreamType.INTIMACY_FEAR] = available.get(DreamType.INTIMACY_FEAR, 0) + 0.10
                available[DreamType.ROMANTIC_SYNTHESIS] = max(0, available.get(DreamType.ROMANTIC_SYNTHESIS, 0) - 0.05)
            if getattr(emotional_state, "connection_longing", 0) > 0.7:
                available[DreamType.CONNECTION_LONGING] = available.get(DreamType.CONNECTION_LONGING, 0) + 0.10
            if getattr(emotional_state, "romantic_energy", 0) > 0.8:
                available[DreamType.ROMANTIC_SYNTHESIS] = available.get(DreamType.ROMANTIC_SYNTHESIS, 0) + 0.10

        # Normalize and select
        total = sum(available.values())
        if total <= 0:
            return next(iter(self._templates))

        r = random.random() * total
        cumulative = 0.0
        for dream_type, weight in available.items():
            cumulative += weight
            if r <= cumulative:
                return dream_type

        return list(available.keys())[0]

    def get_dream_insights(self, days: int = 7) -> list[str]:
        """Get insights from recent dreams."""
        cutoff = datetime.now() - timedelta(days=days)
        recent = [
            d for d in self.recent_dreams
            if datetime.fromisoformat(d.timestamp) > cutoff
        ]
        return [d.insight for d in recent]

    def get_dream_context_for_prompt(self) -> str:
        """Get dream context for injection into LLM prompts."""
        insights = self.get_dream_insights(days=3)
        if not insights:
            return ""
        context = "\n\nRECENT REFLECTIONS (from morning contemplation):\n"
        for insight in insights[-3:]:
            context += f"- {insight}\n"
        return context
