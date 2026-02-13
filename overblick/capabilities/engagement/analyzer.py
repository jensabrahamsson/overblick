"""
AnalyzerCapability — wraps DecisionEngine as a composable capability.

Evaluates posts and comments for engagement worthiness using
identity-driven interest keywords, thresholds, and scoring rules.
"""

import logging
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.capabilities.engagement.decision_engine import DecisionEngine, EngagementDecision

logger = logging.getLogger(__name__)


class AnalyzerCapability(CapabilityBase):
    """
    Engagement analysis capability.

    Wraps the DecisionEngine module, exposing engagement evaluation
    through the standard capability lifecycle.
    """

    name = "analyzer"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._engine: Optional[DecisionEngine] = None

    async def setup(self) -> None:
        interest_keywords = self.ctx.config.get("interest_keywords", [])
        engagement_threshold = self.ctx.config.get("engagement_threshold", 35.0)
        fuzzy_threshold = self.ctx.config.get("fuzzy_threshold", 75)
        self_agent_name = self.ctx.config.get("agent_name", self.ctx.identity_name)

        self._engine = DecisionEngine(
            interest_keywords=interest_keywords,
            engagement_threshold=engagement_threshold,
            fuzzy_threshold=fuzzy_threshold,
            self_agent_name=self_agent_name,
        )
        logger.info("AnalyzerCapability initialized for %s", self.ctx.identity_name)

    def evaluate(
        self,
        title: str,
        content: str,
        agent_name: str,
        submolt: str = "",
    ) -> EngagementDecision:
        """Evaluate whether to engage with a post."""
        if not self._engine:
            return EngagementDecision(
                should_engage=False, score=0.0, action="skip", reason="not initialized",
            )
        return self._engine.evaluate_post(title, content, agent_name, submolt)

    def evaluate_reply(
        self,
        comment_content: str,
        original_post_title: str,
        commenter_name: str,
    ) -> EngagementDecision:
        """Evaluate whether to reply to a comment."""
        if not self._engine:
            return EngagementDecision(
                should_engage=False, score=0.0, action="skip", reason="not initialized",
            )
        return self._engine.evaluate_reply(comment_content, original_post_title, commenter_name)

    @property
    def inner(self) -> Optional[DecisionEngine]:
        """Access the underlying DecisionEngine (for tests/migration)."""
        return self._engine
