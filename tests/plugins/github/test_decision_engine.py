"""
Tests for the GitHub decision engine — event scoring and action determination.
"""

import pytest

from overblick.plugins.github.decision_engine import GitHubDecisionEngine
from overblick.plugins.github.models import (
    EventAction,
    EventType,
    GitHubEvent,
)


class TestDecisionEngine:
    """Test scoring factors and thresholds."""

    def _make_engine(self, **kwargs):
        defaults = {
            "bot_username": "anomal-bot",
            "respond_threshold": 50,
            "notify_threshold": 25,
            "interest_keywords": ["security", "api", "authentication"],
            "respond_labels": ["question", "help wanted"],
            "priority_repos": ["moltbook/api"],
            "max_issue_age_hours": 168,
        }
        defaults.update(kwargs)
        return GitHubDecisionEngine(**defaults)

    def test_mention_scores_high(self, sample_mention_event):
        """@mention of bot username adds +50."""
        engine = self._make_engine()
        result = engine.evaluate(sample_mention_event)

        assert result.factors.get("mention") == 50
        assert result.score >= 50

    def test_label_question_adds_score(self, sample_event):
        """Label 'help wanted' adds +30."""
        engine = self._make_engine()
        result = engine.evaluate(sample_event)

        assert "label_help wanted" in result.factors
        assert result.factors["label_help wanted"] == 30

    def test_keyword_match_adds_score(self):
        """Interest keyword in body adds +20."""
        engine = self._make_engine()
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=1,
            issue_title="Security vulnerability in auth",
            body="Found a potential authentication bypass.",
            author="reporter",
            created_at="2026-02-20T12:00:00Z",
        )
        result = engine.evaluate(event)

        assert result.factors.get("keyword_match") == 20

    def test_priority_repo_adds_score(self):
        """Event on a priority repo adds +15."""
        engine = self._make_engine()
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=1,
            issue_title="Some issue",
            body="Details here",
            author="user",
            created_at="2026-02-20T12:00:00Z",
        )
        result = engine.evaluate(event)

        assert result.factors.get("priority_repo") == 15

    def test_self_authored_always_skipped(self):
        """Events by the bot itself are always skipped."""
        engine = self._make_engine()
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=1,
            issue_title="My own issue",
            body="@anomal-bot test",
            author="anomal-bot",
            labels=["question", "help wanted"],
            created_at="2026-02-20T12:00:00Z",
        )
        result = engine.evaluate(event)

        assert result.action == EventAction.SKIP
        assert result.score == -100

    def test_already_responded_penalty(self, sample_event):
        """Already responding to an issue applies -50."""
        engine = self._make_engine()
        result = engine.evaluate(sample_event, already_responded=True)

        assert result.factors.get("already_responded") == -50

    def test_old_issue_penalty(self):
        """Very old issues get -20 penalty."""
        engine = self._make_engine(max_issue_age_hours=24)
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=1,
            issue_title="Ancient issue",
            body="This is very old",
            author="user",
            created_at="2020-01-01T00:00:00Z",
        )
        result = engine.evaluate(event)

        assert result.factors.get("old_issue") == -20

    def test_pull_request_skipped(self):
        """Pull requests are penalized -100."""
        engine = self._make_engine()
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=1,
            issue_title="Some PR",
            body="Changes here",
            author="user",
            is_pull_request=True,
            created_at="2026-02-20T12:00:00Z",
        )
        result = engine.evaluate(event)

        assert result.factors.get("pull_request") == -100
        assert result.action == EventAction.SKIP

    def test_respond_threshold(self, sample_mention_event):
        """Score >= respond_threshold triggers RESPOND."""
        engine = self._make_engine(respond_threshold=50)
        result = engine.evaluate(sample_mention_event)

        # mention(50) + label(30) + keyword(20) + priority(15) >= 50
        assert result.action == EventAction.RESPOND

    def test_notify_threshold(self):
        """Score between notify and respond triggers NOTIFY."""
        engine = self._make_engine(respond_threshold=100, notify_threshold=25)
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=1,
            issue_title="Help with security setup",
            body="How do I configure auth?",
            author="user",
            labels=["question"],
            created_at="2026-02-20T12:00:00Z",
        )
        result = engine.evaluate(event)

        # label(30) + keyword(20) + priority(15) = 65
        assert result.action == EventAction.NOTIFY

    def test_skip_below_notify_threshold(self):
        """Score below notify_threshold triggers SKIP."""
        engine = self._make_engine(respond_threshold=100, notify_threshold=50)
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="other/repo",
            issue_number=1,
            issue_title="Unrelated issue",
            body="Something about cooking",
            author="user",
            created_at="2026-02-20T12:00:00Z",
        )
        result = engine.evaluate(event)

        assert result.action == EventAction.SKIP

    def test_factors_dict_populated(self, sample_mention_event):
        """DecisionResult.factors contains all contributing factors."""
        engine = self._make_engine()
        result = engine.evaluate(sample_mention_event)

        assert isinstance(result.factors, dict)
        assert len(result.factors) > 0

    def test_combined_scoring(self):
        """Multiple factors combine correctly."""
        engine = self._make_engine()
        # mention(50) + label(30) + keyword(20) + priority(15) = 115
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=1,
            issue_title="@anomal-bot security question",
            body="How does the API authentication work?",
            author="user",
            labels=["question"],
            created_at="2026-02-20T12:00:00Z",
        )
        result = engine.evaluate(event)

        assert result.score == 115
        assert result.action == EventAction.RESPOND
