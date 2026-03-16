"""
Additional coverage tests for decision_engine module.

Covers uncovered lines:
- 156: _event_age_hours with no timezone
- 159-160: _event_age_hours with invalid timestamp
"""


from overblick.plugins.github.decision_engine import GitHubDecisionEngine
from overblick.plugins.github.models import EventType, GitHubEvent


class TestEventAgeHours:
    def test_should_handle_naive_timestamp(self):
        """Line 156: timestamp without timezone gets UTC applied."""
        engine = GitHubDecisionEngine()
        result = engine._event_age_hours("2020-01-01T00:00:00")
        assert result is not None
        assert result > 0

    def test_should_return_none_for_invalid_timestamp(self):
        """Lines 159-160: invalid timestamp returns None."""
        engine = GitHubDecisionEngine()
        result = engine._event_age_hours("not-a-date")
        assert result is None

    def test_should_return_none_for_empty_timestamp(self):
        """Lines 159-160: empty timestamp returns None."""
        engine = GitHubDecisionEngine()
        result = engine._event_age_hours("")
        assert result is None


class TestEvaluateNoCreatedAt:
    def test_should_skip_age_check_when_no_created_at(self):
        """Line 111: created_at is empty, age check is skipped."""
        engine = GitHubDecisionEngine(
            bot_username="bot",
            max_issue_age_hours=1,
        )
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="Test",
            body="Test",
            author="user",
            created_at="",
        )
        result = engine.evaluate(event)
        assert "old_issue" not in result.factors


class TestEvaluateNoBotUsername:
    def test_should_skip_mention_check_with_empty_username(self):
        """No bot_username means self-authored and mention checks skip."""
        engine = GitHubDecisionEngine(bot_username="")
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="@someone test",
            body="Hello",
            author="user",
        )
        result = engine.evaluate(event)
        assert "self_authored" not in result.factors
        assert "mention" not in result.factors
