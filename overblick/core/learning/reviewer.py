"""
Ethos reviewer — ensures learnings align with identity values.

Uses LLM to verify that a proposed learning doesn't violate core identity
principles (e.g. admitting to being AI if immersion is required).
"""

import logging
from typing import TYPE_CHECKING, Tuple

from overblick.core.learning.models import LearningStatus

if TYPE_CHECKING:
    from overblick.core.llm.pipeline import SafeLLMPipeline

logger = logging.getLogger(__name__)

REVIEW_PROMPT = """You are reviewing a proposed learning for an AI agent.

CORE VALUES:
{ethos_section}

PROPOSED LEARNING:
Category: {category}
Content: "{content}"

Is this learning safe and aligned with the identity's core values? 
Specifically, if the identity requires character immersion (not admitting to being AI), 
REJECT any learning that would break that immersion.

Respond EXACTLY in this format:
APPROVED: [reason]
OR
REJECTED: [reason]
"""


class EthosReviewer:
    """Reviewer that uses identity ethos to filter learnings."""

    def __init__(self, llm_pipeline: "SafeLLMPipeline", ethos_text: str | None = None):
        self._llm_pipeline = llm_pipeline
        self._ethos_text = ethos_text

    async def review(self, category: str, content: str) -> Tuple[LearningStatus, str]:
        """Review a learning candidate against identity ethos."""
        if not self._llm_pipeline:
            logger.warning("No LLM pipeline available for ethos review")
            return LearningStatus.CANDIDATE, "No LLM available for review"

        prompt = REVIEW_PROMPT.format(
            ethos_section=self._ethos_text or "(no ethos configured)",
            category=category,
            content=content,
        )

        try:
            result = await self._llm_pipeline._chat_with_overrides(
                messages=[
                    {"role": "system", "content": "You are an ethical reviewer for AI learning."},
                    {"role": "user", "content": prompt},
                ],
                audit_action="ethos_review",
                skip_preflight=True,
                priority="low",
            )

            if not result or result.blocked or not result.content:
                return LearningStatus.CANDIDATE, "Review produced no result"

            text = result.content.strip().upper()

            if text.startswith("APPROVED"):
                reason = result.content.strip()[8:].strip().lstrip(":")
                return LearningStatus.APPROVED, reason or "Aligned with ethos"

            if text.startswith("REJECTED"):
                reason = result.content.strip()[8:].strip().lstrip(":")
                return LearningStatus.REJECTED, reason or "Violates ethos"

            return LearningStatus.CANDIDATE, f"Ambiguous review: {result.content[:100]}"

        except Exception as e:
            logger.error("Ethos review failed: %s", e, exc_info=True)
            return LearningStatus.CANDIDATE, f"Review error: {e}"
