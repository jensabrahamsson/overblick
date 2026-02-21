"""
Decision engine for GitHub event scoring.

Pure heuristics — no LLM calls. Evaluates whether to respond,
notify, or skip a GitHub event based on configurable factors.
"""

import logging
import time
from typing import Optional

from overblick.plugins.github.models import (
    DecisionResult,
    EventAction,
    GitHubEvent,
)

logger = logging.getLogger(__name__)


class GitHubDecisionEngine:
    """
    Score GitHub events to decide plugin action.

    Factors (additive/subtractive):
        @mention of bot username     +50
        Label: question/help wanted  +30
        Keywords from interests      +20
        High-priority repo           +15
        Author is self               -100
        Issue older than max age     -20
        Already responded to issue   -50
    """

    def __init__(
        self,
        bot_username: str = "",
        respond_threshold: int = 50,
        notify_threshold: int = 25,
        interest_keywords: Optional[list[str]] = None,
        respond_labels: Optional[list[str]] = None,
        priority_repos: Optional[list[str]] = None,
        max_issue_age_hours: int = 168,
    ):
        self._bot_username = bot_username.lower()
        self._respond_threshold = respond_threshold
        self._notify_threshold = notify_threshold
        self._interest_keywords = [k.lower() for k in (interest_keywords or [])]
        self._respond_labels = [l.lower() for l in (respond_labels or ["question", "help wanted"])]
        self._priority_repos = [r.lower() for r in (priority_repos or [])]
        self._max_issue_age_hours = max_issue_age_hours

    def evaluate(
        self,
        event: GitHubEvent,
        already_responded: bool = False,
    ) -> DecisionResult:
        """
        Score a GitHub event and determine the action.

        Args:
            event: The event to evaluate
            already_responded: Whether we've already responded to this issue

        Returns:
            DecisionResult with score, action, and factor breakdown
        """
        factors: dict[str, int] = {}
        score = 0

        # Self-authored — never respond to own content
        if self._bot_username and event.author.lower() == self._bot_username:
            factors["self_authored"] = -100
            return DecisionResult(
                score=-100,
                action=EventAction.SKIP,
                factors=factors,
            )

        # @mention of bot username
        text = f"{event.issue_title} {event.body}".lower()
        if self._bot_username and f"@{self._bot_username}" in text:
            factors["mention"] = 50
            score += 50

        # Labels
        event_labels = [l.lower() for l in event.labels]
        for label in self._respond_labels:
            if label in event_labels:
                factors[f"label_{label}"] = 30
                score += 30
                break  # Only count once

        # Interest keywords
        keyword_match = False
        for keyword in self._interest_keywords:
            if keyword in text:
                keyword_match = True
                break
        if keyword_match:
            factors["keyword_match"] = 20
            score += 20

        # Priority repo
        if event.repo.lower() in self._priority_repos:
            factors["priority_repo"] = 15
            score += 15

        # Issue age penalty
        if event.created_at:
            age_hours = self._event_age_hours(event.created_at)
            if age_hours is not None and age_hours > self._max_issue_age_hours:
                factors["old_issue"] = -20
                score -= 20

        # Already responded penalty
        if already_responded:
            factors["already_responded"] = -50
            score -= 50

        # Skip pull requests for now
        if event.is_pull_request:
            factors["pull_request"] = -100
            score -= 100

        # Determine action
        if score >= self._respond_threshold:
            action = EventAction.RESPOND
        elif score >= self._notify_threshold:
            action = EventAction.NOTIFY
        else:
            action = EventAction.SKIP

        logger.debug(
            "GitHub decision: %s#%d score=%d action=%s factors=%s",
            event.repo, event.issue_number, score, action.value, factors,
        )

        return DecisionResult(score=score, action=action, factors=factors)

    @staticmethod
    def _event_age_hours(created_at: str) -> Optional[float]:
        """Parse ISO 8601 timestamp and return age in hours."""
        try:
            from datetime import datetime, timezone
            # GitHub uses ISO 8601 with Z suffix
            ts = created_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            return None
