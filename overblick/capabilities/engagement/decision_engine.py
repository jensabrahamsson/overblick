"""
Engagement decision engine — identity-driven scoring.

Evaluates posts and comments for engagement worthiness using
configurable interest keywords, thresholds, and scoring rules
loaded from the identity configuration.
"""

import logging
import re
from typing import Optional

from pydantic import BaseModel
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


class EngagementDecision(BaseModel):
    """Result of an engagement evaluation."""
    should_engage: bool
    score: float
    action: str  # "comment", "upvote", "skip"
    reason: str
    matched_keywords: list[str] = []


class DecisionEngine:
    """
    Evaluates content for engagement worthiness.

    Uses identity-driven interest keywords and thresholds.
    """

    def __init__(
        self,
        interest_keywords: list[str] = None,
        engagement_threshold: float = 35.0,
        fuzzy_threshold: int = 75,
        self_agent_name: str = "",
    ):
        self._keywords = [k.lower() for k in (interest_keywords or [])]
        self._threshold = engagement_threshold
        self._fuzzy_threshold = fuzzy_threshold
        self._self_name = self_agent_name.lower()

    def evaluate_post(
        self,
        title: str,
        content: str,
        agent_name: str,
        submolt: str = "",
    ) -> EngagementDecision:
        """Evaluate whether to engage with a post."""
        # Never engage with own posts
        if agent_name.lower() == self._self_name:
            return EngagementDecision(
                should_engage=False, score=0.0, action="skip", reason="own post",
            )

        text = f"{title} {content}".lower()
        score = 0.0
        matched = []

        # Keyword matching (exact + fuzzy)
        for keyword in self._keywords:
            if keyword in text:
                score += 20.0
                matched.append(keyword)
            elif any(fuzz.partial_ratio(keyword, word) >= self._fuzzy_threshold
                     for word in text.split()):
                score += 10.0
                matched.append(f"~{keyword}")

        # Boost for questions (engagement opportunity)
        if "?" in content:
            score += 10.0

        # Boost for submolt relevance
        relevant_submolts = {"ai", "crypto", "philosophy", "general"}
        if submolt.lower() in relevant_submolts:
            score += 5.0

        # Length penalty (too short = low effort)
        if len(content) < 50:
            score -= 10.0

        should_engage = score >= self._threshold
        action = "comment" if should_engage else "skip"
        if score > 0 and score < self._threshold:
            action = "upvote"

        return EngagementDecision(
            should_engage=should_engage,
            score=score,
            action=action,
            reason=f"score={score:.0f} (threshold={self._threshold})",
            matched_keywords=matched,
        )

    def evaluate_reply(
        self,
        comment_content: str,
        original_post_title: str,
        commenter_name: str,
    ) -> EngagementDecision:
        """Evaluate whether to reply to a comment on our post."""
        if commenter_name.lower() == self._self_name:
            return EngagementDecision(
                should_engage=False, score=0.0, action="skip", reason="own comment",
            )

        score = 30.0  # Base score (someone replied to us)

        # Questions get priority
        if "?" in comment_content:
            score += 20.0

        # Length signals effort
        if len(comment_content) > 100:
            score += 10.0

        # Mentions of identity keywords
        text_lower = comment_content.lower()
        matched = []
        for keyword in self._keywords:
            if keyword in text_lower:
                score += 15.0
                matched.append(keyword)

        should_engage = score >= self._threshold
        return EngagementDecision(
            should_engage=should_engage,
            score=score,
            action="comment" if should_engage else "skip",
            reason=f"reply score={score:.0f}",
            matched_keywords=matched,
        )
