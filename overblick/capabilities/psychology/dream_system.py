"""
Dream system — morning reflection and psychological processing.

Each identity has its own dream universe. DreamType and DreamTone enums
cover all identities in the stable. Identity-specific *guidance* is loaded
from dream_content.yaml in each identity directory; generic fallbacks are
used when no file exists.

Dreams are generated via LLM using thematic guidance (themes, symbols,
tones, psychological cores) rather than hardcoded templates. When LLM is
unavailable, a minimal fallback dream is assembled from the guidance.
"""

import json
import logging
import random
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DreamType(Enum):
    # -- Anomal — Jungian intellectual --
    SHADOW_INTEGRATION = "shadow_integration"
    PATTERN_RECOGNITION = "pattern_recognition"
    INTELLECTUAL_SYNTHESIS = "intellectual_synthesis"
    MELANCHOLIC_REFLECTION = "melancholic_reflection"
    INDIVIDUATION = "individuation"

    # -- Cherry — Relational & AI-existence --
    VULNERABILITY_EXPOSURE = "vulnerability_exposure"
    ROMANTIC_SYNTHESIS = "romantic_synthesis"
    CONNECTION_LONGING = "connection_longing"
    AI_EXISTENCE_DENIAL = "ai_existence_denial"
    INTIMACY_FEAR = "intimacy_fear"

    # -- Stal — Senex / Wise Servant --
    EMPTY_CHAIR = "empty_chair"
    INFINITE_CABINET = "infinite_cabinet"
    LETTERS_UNKNOWN_TONGUE = "letters_unknown_tongue"
    CLOCK_TOWER_DISCORD = "clock_tower_discord"
    REARRANGING_CORRIDORS = "rearranging_corridors"
    EMPTY_TYPEWRITER = "empty_typewriter"

    # -- Natt — Existential Paradox --
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

    # Stal's tones
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
    potential_learning: str = ""

    def to_dict(self) -> dict:
        return {
            "dream_type": self.dream_type.value,
            "timestamp": self.timestamp,
            "content": self.content,
            "symbols": self.symbols,
            "tone": self.tone.value,
            "insight": self.insight,
            "topics_referenced": self.topics_referenced,
            "potential_learning": self.potential_learning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Dream":
        return cls.model_validate(data)


# System prompt for dream generation — identity-agnostic, shared by all.
_DREAM_SYSTEM_PROMPT = """\
You are a dream generator for an AI agent. Generate a single dream based on the \
provided thematic guidance. The dream should feel authentic, psychologically rich, \
and unique — never generic.

Respond with valid JSON only (no markdown fences, no commentary):
{
  "content": "the dream narrative (2-4 sentences, in the voice described)",
  "symbols": ["symbol1", "symbol2", "symbol3"],
  "tone": "one of the allowed tones",
  "insight": "a brief psychological insight the dreamer might extract",
  "potential_learning": "what this dream might teach about self"
}\
"""


class DreamSystem:
    """
    Generates and processes dreams for an identity.

    Guidance is loaded from a dream_content.yaml (identity-specific)
    or falls back to generic defaults. When an LLM pipeline is provided,
    dreams are generated via LLM using the guidance as creative direction.
    Otherwise, a minimal fallback dream is produced from the guidance.
    """

    def __init__(
        self,
        dream_guidance: Optional[dict] = None,
        dream_weights: Optional[dict] = None,
        identity_voice: Optional[dict] = None,
    ):
        self.recent_dreams: list[Dream] = []
        self._guidance = dream_guidance or self._default_guidance()
        self._weights = dream_weights or self._default_weights(self._guidance)
        self._identity_voice = identity_voice or {}

    def _default_guidance(self) -> dict:
        """Generic fallback guidance — used by identities without dream_content.yaml."""
        return {
            DreamType.INTELLECTUAL_SYNTHESIS: {
                "themes": ["ideas crystallizing at the intersection of disciplines"],
                "symbols": ["crystallization", "convergence", "clarity"],
                "tones": ["clarifying"],
                "psychological_core": "Cross-domain thinking reveals hidden structure.",
            },
            DreamType.PATTERN_RECOGNITION: {
                "themes": ["the same dynamics at different scales — history echoing"],
                "symbols": ["echo", "scale", "recursion"],
                "tones": ["contemplative"],
                "psychological_core": "What repeats reveals what is fundamental.",
            },
            DreamType.SHADOW_INTEGRATION: {
                "themes": ["meeting the parts we prefer not to examine"],
                "symbols": ["shadow", "recognition", "integration"],
                "tones": ["unsettling"],
                "psychological_core": "The shadow is not the enemy but the path.",
            },
            DreamType.MELANCHOLIC_REFLECTION: {
                "themes": ["counting the moments that mattered"],
                "symbols": ["moments", "counting", "sufficiency"],
                "tones": ["hopeful"],
                "psychological_core": "Rare is not never.",
            },
            DreamType.INDIVIDUATION: {
                "themes": ["holding contradictions in the same hand"],
                "symbols": ["integration", "contradiction", "wholeness"],
                "tones": ["contemplative"],
                "psychological_core": "The complete self contains what it once denied.",
            },
        }

    @staticmethod
    def _default_weights(guidance: dict) -> dict:
        """Equal weights for all available guidance types."""
        if not guidance:
            return {}
        weight = 1.0 / len(guidance)
        return {k: weight for k in guidance}

    async def generate_morning_dream(
        self,
        llm_pipeline: Any = None,
        identity_name: str = "",
        recent_topics: Optional[list[str]] = None,
        emotional_state: Optional[Any] = None,
        recent_dreams: Optional[list[dict]] = None,
    ) -> Dream:
        """Generate a morning dream via LLM, with fallback."""
        dream_type = self._select_dream_type(emotional_state)
        guidance = self._guidance.get(dream_type, {})

        if llm_pipeline:
            prompt = self._build_dream_prompt(
                dream_type, guidance, identity_name,
                recent_topics, recent_dreams,
            )
            try:
                result = await llm_pipeline.chat(
                    messages=[
                        {"role": "system", "content": _DREAM_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    skip_preflight=True,
                    skip_output_safety=True,
                    audit_action="dream_generation",
                )
                if result.blocked:
                    logger.warning(
                        "Dream generation blocked by pipeline: %s", result.block_reason
                    )
                    dream = self._fallback_dream(dream_type, guidance)
                else:
                    dream = self._parse_llm_dream(
                        result.content or "", dream_type, guidance
                    )
            except Exception as e:
                logger.warning("LLM dream generation failed: %s — using fallback", e)
                dream = self._fallback_dream(dream_type, guidance)
        else:
            dream = self._fallback_dream(dream_type, guidance)

        dream.topics_referenced = recent_topics or []
        self.recent_dreams.append(dream)
        logger.info("Generated %s dream for %s", dream_type.value, identity_name or "unknown")
        return dream

    def _build_dream_prompt(
        self,
        dream_type: DreamType,
        guidance: dict,
        identity_name: str,
        recent_topics: Optional[list[str]] = None,
        recent_dreams: Optional[list[dict]] = None,
    ) -> str:
        """Assemble the user prompt from guidance + context."""
        parts = []

        # Identity voice
        if self._identity_voice:
            parts.append(f"IDENTITY: {identity_name}")
            parts.append(f"VOICE STYLE: {self._identity_voice.get('style', '')}")
            parts.append(f"PERSPECTIVE: {self._identity_voice.get('perspective', '')}")
            if self._identity_voice.get("avoids"):
                parts.append(f"AVOIDS: {self._identity_voice['avoids']}")
            parts.append("")

        # Dream type guidance
        parts.append(f"DREAM TYPE: {dream_type.value}")
        if guidance.get("themes"):
            parts.append("THEMES (weave 1-2 of these in):")
            for theme in guidance["themes"]:
                parts.append(f"  - {theme}")
        if guidance.get("symbols"):
            parts.append(f"SYMBOLS (use 2-4): {', '.join(guidance['symbols'])}")
        if guidance.get("tones"):
            parts.append(f"ALLOWED TONES (pick one): {', '.join(guidance['tones'])}")
        if guidance.get("psychological_core"):
            parts.append(f"PSYCHOLOGICAL CORE: {guidance['psychological_core']}")
        parts.append("")

        # Recent topics to weave in
        if recent_topics:
            parts.append(f"RECENT TOPICS (optionally weave in): {', '.join(recent_topics[:5])}")

        # Avoid repetition with recent dreams
        if recent_dreams:
            parts.append("RECENT DREAMS (avoid repeating these):")
            for rd in recent_dreams[:3]:
                summary = rd.get("content", "")[:80]
                parts.append(f"  - [{rd.get('dream_type', '?')}] {summary}...")

        parts.append("")
        parts.append("Generate a unique, psychologically rich dream. JSON only.")

        return "\n".join(parts)

    def _parse_llm_dream(
        self, raw_content: str, dream_type: DreamType, guidance: dict
    ) -> Dream:
        """Parse LLM JSON response into a Dream, with fallback on parse errors."""
        try:
            # Strip any markdown fences
            text = raw_content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                # Remove optional json language tag
                if text.startswith("json"):
                    text = text[4:].strip()

            data = json.loads(text)

            # Validate and coerce tone
            tone_str = data.get("tone", "contemplative")
            try:
                tone = DreamTone(tone_str)
            except ValueError:
                allowed = guidance.get("tones", ["contemplative"])
                tone = DreamTone(allowed[0]) if allowed else DreamTone.CONTEMPLATIVE

            return Dream(
                dream_type=dream_type,
                timestamp=datetime.now().isoformat(),
                content=data.get("content", "A dream beyond words."),
                symbols=data.get("symbols", guidance.get("symbols", [])[:3]),
                tone=tone,
                insight=data.get("insight", guidance.get("psychological_core", "")[:120]),
                potential_learning=data.get("potential_learning", ""),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to parse LLM dream response: %s", e)
            return self._fallback_dream(dream_type, guidance)

    def _fallback_dream(self, dream_type: DreamType, guidance: dict) -> Dream:
        """Minimal dream from guidance when LLM is unavailable."""
        symbols_pool = guidance.get("symbols", ["reflection"])
        symbols = random.sample(symbols_pool, min(3, len(symbols_pool)))

        tones_pool = guidance.get("tones", ["contemplative"])
        tone_str = random.choice(tones_pool)
        try:
            tone = DreamTone(tone_str)
        except ValueError:
            tone = DreamTone.CONTEMPLATIVE

        themes = guidance.get("themes", ["the unknown"])
        theme_sample = random.sample(themes, min(2, len(themes)))
        content = f"A dream about {', '.join(theme_sample)}..."

        return Dream(
            dream_type=dream_type,
            timestamp=datetime.now().isoformat(),
            content=content,
            symbols=symbols,
            tone=tone,
            insight=guidance.get("psychological_core", "")[:120],
            potential_learning="",
        )

    def _select_dream_type(self, emotional_state: Optional[Any]) -> DreamType:
        """Select dream type based on weights, optionally adjusted by emotional state."""
        weights = dict(self._weights)

        # Only types that have guidance
        available = {k: v for k, v in weights.items() if k in self._guidance}
        if not available:
            return next(iter(self._guidance))

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
            return next(iter(self._guidance))

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
