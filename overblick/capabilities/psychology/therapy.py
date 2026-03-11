"""
TherapyCapability — wraps TherapySystem or CherryTherapySystem as a capability.

Weekly psychological reflection. Config-driven via 'therapy_model':
  - "llm" (default): LLM-based Jungian + Freudian analysis via TherapySystem
  - "template": Template-based attachment theory sessions via CherryTherapySystem
"""

import logging
from typing import Optional

from overblick.capabilities.psychology.therapy_system import (
    CherryTherapySystem,
    TherapySession,
    TherapySystem,
)
from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.core.security.settings import raw_llm

logger = logging.getLogger(__name__)

_AnyTherapySystem = TherapySystem | CherryTherapySystem


class TherapyCapability(CapabilityBase):
    """
    Weekly therapy session capability.

    Dispatches to TherapySystem (LLM-based Jungian/Freudian) or
    CherryTherapySystem (template-based attachment theory) based on
    the identity name provided in CapabilityContext.
    """

    name = "therapy_system"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._therapy_system: _AnyTherapySystem | None = None

    async def setup(self) -> None:
        therapy_day = self.ctx.config.get("therapy_day", TherapySystem.DEFAULT_THERAPY_DAY)
        model = self.ctx.config.get("therapy_model", "llm")

        if model == "template":
            self._therapy_system = CherryTherapySystem(therapy_day=therapy_day)
            logger.info(
                "TherapyCapability (template-based) initialized for %s",
                self.ctx.identity_name,
            )
        else:
            # TherapySystem handles llm_pipeline=None gracefully (template fallback).
            llm_pipeline = self.ctx.llm_pipeline
            system_prompt = self.ctx.config.get("system_prompt", "")
            self._therapy_system = TherapySystem(
                llm_pipeline=llm_pipeline,
                system_prompt=system_prompt,
                therapy_day=therapy_day,
            )
            logger.info(
                "TherapyCapability (LLM/Jungian-Freudian) initialized for %s (day=%s)",
                self.ctx.identity_name,
                TherapySystem._day_name(therapy_day),
            )

    def is_therapy_day(self) -> bool:
        """Return True if today is the configured therapy day."""
        if not self._therapy_system:
            return False
        return self._therapy_system.is_therapy_day()

    async def run_session(self, **kwargs) -> TherapySession | None:
        """
        Run a therapy session.

        For TherapySystem (Anomal/generic): accepts dreams, learnings,
        dream_analysis_prompt, synthesis_prompt, post_prompt kwargs.

        For CherryTherapySystem: accepts emotional_state, week_stats kwargs.
        """
        if not self._therapy_system:
            return None
        if isinstance(self._therapy_system, CherryTherapySystem):
            return self._therapy_system.generate_session(
                emotional_state=kwargs.get("emotional_state"),
                week_stats=kwargs.get("week_stats"),
            )
        return await self._therapy_system.run_session(**kwargs)

    def get_prompt_context(self) -> str:
        """Inject the most recent therapy session insight into prompts."""
        if not self._therapy_system:
            return ""

        if isinstance(self._therapy_system, CherryTherapySystem):
            sessions = self._therapy_system.recent_sessions
            if sessions:
                summary = sessions[-1].session_summary
                if summary:
                    return f"\n[Therapy insight: {summary}]\n"
        else:
            # TherapySystem (Anomal / generic)
            summary = self._therapy_system.last_session_summary
            if summary:
                return f"\n[Therapy insight: {summary}]\n"

        return ""

    @property
    def inner(self) -> _AnyTherapySystem | None:
        """Access the underlying therapy system (for tests)."""
        return self._therapy_system
