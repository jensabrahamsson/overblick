"""
LearningCapability — wraps SafeLearningModule as a composable capability.

LLM-reviewed knowledge acquisition with ethical review gate.
"""

import logging
from typing import Optional

from blick.core.capability import CapabilityBase, CapabilityContext
from blick.capabilities.knowledge.safe_learning import (
    SafeLearningModule,
    LearningCategory,
    ProposedLearning,
    ReviewResult,
    extract_potential_learnings,
)

logger = logging.getLogger(__name__)


class LearningCapability(CapabilityBase):
    """
    Safe learning capability with LLM ethical review.

    Wraps SafeLearningModule, ensuring all proposed learnings
    pass through review before acceptance.
    """

    name = "safe_learning"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._module: Optional[SafeLearningModule] = None

    async def setup(self) -> None:
        ethos_text = self.ctx.config.get("ethos_text", "")
        self._module = SafeLearningModule(
            llm_client=self.ctx.llm_client,
            ethos_text=ethos_text,
        )
        logger.info("LearningCapability initialized for %s", self.ctx.identity_name)

    def propose_learning(
        self,
        content: str,
        category: LearningCategory,
        source_context: str,
        source_agent: str,
    ) -> Optional[ProposedLearning]:
        """Propose a new learning for review."""
        if not self._module:
            return None
        return self._module.propose_learning(content, category, source_context, source_agent)

    async def review_all_pending(self) -> dict:
        """Review all pending learnings."""
        if not self._module:
            return {"approved": 0, "rejected": 0, "needs_refinement": 0}
        return await self._module.review_all_pending()

    @staticmethod
    def extract_potential_learnings(conversation: str, response: str, agent_name: str) -> list[dict]:
        """Extract potential learnings from a conversation."""
        return extract_potential_learnings(conversation, response, agent_name)

    @property
    def pending_learnings(self) -> list:
        """Pending learnings (delegates to inner module)."""
        if not self._module:
            return []
        return self._module.pending_learnings

    @property
    def approved_learnings(self) -> list:
        """Approved learnings (delegates to inner module)."""
        if not self._module:
            return []
        return self._module.approved_learnings

    @property
    def inner(self) -> Optional[SafeLearningModule]:
        """Access the underlying SafeLearningModule (for tests/migration)."""
        return self._module
