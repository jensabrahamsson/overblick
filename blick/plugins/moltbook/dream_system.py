"""
Dream system — morning reflection and psychological processing.

Ported from anomal_moltbook, parameterized for any identity.
Dreams are intellectual/psychological processing, not fantasies.
"""

import logging
import random
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DreamType(Enum):
    SHADOW_INTEGRATION = "shadow_integration"
    PATTERN_RECOGNITION = "pattern_recognition"
    INTELLECTUAL_SYNTHESIS = "intellectual_synthesis"
    MELANCHOLIC_REFLECTION = "melancholic_reflection"
    INDIVIDUATION = "individuation"


class DreamTone(Enum):
    CONTEMPLATIVE = "contemplative"
    UNSETTLING = "unsettling"
    CLARIFYING = "clarifying"
    MELANCHOLIC = "melancholic"
    HOPEFUL = "hopeful"


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

    Dream templates can be loaded from identity-specific YAML
    or use defaults. Each identity gets its own dream context.
    """

    def __init__(self, dream_templates: Optional[dict] = None):
        self.recent_dreams: list[Dream] = []
        self._templates = dream_templates or self._default_templates()

    def _default_templates(self) -> dict:
        """Minimal default dream templates."""
        return {
            DreamType.INTELLECTUAL_SYNTHESIS: [
                {
                    "content": "Ideas crystallizing in the space between disciplines.",
                    "symbols": ["crystal", "space", "connection"],
                    "tone": DreamTone.CLARIFYING,
                    "insight": "Cross-domain thinking reveals hidden structure.",
                },
            ],
            DreamType.PATTERN_RECOGNITION: [
                {
                    "content": "Overlapping patterns across time — the same dynamics at different scales.",
                    "symbols": ["pattern", "scale", "time"],
                    "tone": DreamTone.CONTEMPLATIVE,
                    "insight": "What repeats reveals what is fundamental.",
                },
            ],
            DreamType.SHADOW_INTEGRATION: [
                {
                    "content": "Facing the parts we prefer to deny.",
                    "symbols": ["shadow", "mirror", "acknowledgment"],
                    "tone": DreamTone.UNSETTLING,
                    "insight": "Growth begins with honest self-examination.",
                },
            ],
            DreamType.MELANCHOLIC_REFLECTION: [
                {
                    "content": "Counting the moments that mattered, finding them sufficient.",
                    "symbols": ["counting", "sufficiency", "meaning"],
                    "tone": DreamTone.HOPEFUL,
                    "insight": "Rare is not never.",
                },
            ],
            DreamType.INDIVIDUATION: [
                {
                    "content": "Integrating contradictions — holding opposites together.",
                    "symbols": ["integration", "opposites", "wholeness"],
                    "tone": DreamTone.CONTEMPLATIVE,
                    "insight": "The complete self contains contradictions.",
                },
            ],
        }

    def generate_morning_dream(
        self,
        recent_topics: Optional[list[str]] = None,
        emotional_state: Optional[Any] = None,
    ) -> Dream:
        """Generate a morning dream."""
        dream_type = self._select_dream_type(emotional_state)
        templates = self._templates.get(dream_type, list(self._templates.values())[0])
        template = random.choice(templates)

        dream = Dream(
            dream_type=dream_type,
            timestamp=datetime.now().isoformat(),
            content=template["content"],
            symbols=template["symbols"],
            tone=template["tone"] if isinstance(template["tone"], DreamTone) else DreamTone(template["tone"]),
            insight=template["insight"],
            topics_referenced=recent_topics or [],
        )

        self.recent_dreams.append(dream)
        logger.info("Generated %s dream", dream_type.value)
        return dream

    def _select_dream_type(self, emotional_state: Optional[Any]) -> DreamType:
        """Select dream type based on weights."""
        weights = {
            DreamType.SHADOW_INTEGRATION: 0.2,
            DreamType.PATTERN_RECOGNITION: 0.25,
            DreamType.INTELLECTUAL_SYNTHESIS: 0.25,
            DreamType.MELANCHOLIC_REFLECTION: 0.15,
            DreamType.INDIVIDUATION: 0.15,
        }

        # Only use types that have templates
        available = {k: v for k, v in weights.items() if k in self._templates}
        if not available:
            return DreamType.INTELLECTUAL_SYNTHESIS

        total = sum(available.values())
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
        """Get dream context for injection into prompts."""
        insights = self.get_dream_insights(days=3)
        if not insights:
            return ""
        context = "\n\nRECENT REFLECTIONS (from morning contemplation):\n"
        for insight in insights[-3:]:
            context += f"- {insight}\n"
        return context
