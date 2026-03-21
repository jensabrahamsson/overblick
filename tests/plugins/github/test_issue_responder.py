"""
Tests for IssueResponder — issue classification, response, and posting.
"""

from unittest.mock import AsyncMock

import pytest

from overblick.plugins.github.client import GitHubAPIError
from overblick.plugins.github.issue_responder import IssueResponder
from overblick.plugins.github.models import (
    ActionType,
    IssueSnapshot,
    PlannedAction,
)


def _make_action(repo="o/r", target_number=10):
    return PlannedAction(
        action_type=ActionType.RESPOND_ISSUE,
        target=f"Issue #{target_number}",
        target_number=target_number,
        repo=repo,
        priority=50,
        reasoning="Respond to user question",
    )


def _make_issue(
    number=10,
    has_our_response=False,
    age_hours=5.0,
    labels=None,
    body="Something broken",
):
    return IssueSnapshot(
        number=number,
        title="Bug report",
        author="reporter",
        body=body,
        labels=labels or ["bug"],
        created_at="2026-03-10T10:00:00Z",
        age_hours=age_hours,
        has_our_response=has_our_response,
    )


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.list_issue_comments = AsyncMock(return_value=[])
    client.create_comment = AsyncMock(return_value={"id": 999})
    return client


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.record_comment = AsyncMock()
    return db


@pytest.fixture
def mock_response_gen():
    gen = AsyncMock()
    gen.generate = AsyncMock(return_value="Here is the fix for your issue.")
    return gen


@pytest.fixture
def responder(mock_client, mock_db, mock_response_gen):
    return IssueResponder(
        client=mock_client,
        db=mock_db,
        response_gen=mock_response_gen,
        dry_run=True,
        respond_to_labels=["question", "help wanted", "bug"],
        max_response_age_hours=168,
    )


class TestHandleRespond:
    """Test handle_respond method."""

    @pytest.mark.asyncio
    async def test_should_skip_already_responded(self, responder):
        issue = _make_issue(has_our_response=True)
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        assert outcome.success is False
        assert "Already responded" in outcome.error

    @pytest.mark.asyncio
    async def test_should_skip_too_old_issues(self, responder):
        issue = _make_issue(age_hours=200.0)
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        assert outcome.success is False
        assert "too old" in outcome.error

    @pytest.mark.asyncio
    async def test_should_dry_run_response(self, responder):
        issue = _make_issue()
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        assert outcome.success is True
        assert "DRY RUN" in outcome.result
        assert "chars" in outcome.result

    @pytest.mark.asyncio
    async def test_should_fail_when_generation_returns_none(self, responder, mock_response_gen):
        mock_response_gen.generate = AsyncMock(return_value=None)
        issue = _make_issue()
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        assert outcome.success is False
        assert "generation failed" in outcome.error

    @pytest.mark.asyncio
    async def test_should_fail_when_generation_returns_empty(self, responder, mock_response_gen):
        mock_response_gen.generate = AsyncMock(return_value="")
        issue = _make_issue()
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        assert outcome.success is False
        assert "generation failed" in outcome.error

    @pytest.mark.asyncio
    async def test_should_post_comment_live(self, mock_client, mock_db, mock_response_gen):
        responder = IssueResponder(
            client=mock_client,
            db=mock_db,
            response_gen=mock_response_gen,
            dry_run=False,
        )
        issue = _make_issue()
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        assert outcome.success is True
        assert "Responded to issue" in outcome.result
        mock_client.create_comment.assert_called_once()
        mock_db.record_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_handle_post_failure(self, mock_client, mock_db, mock_response_gen):
        mock_client.create_comment = AsyncMock(side_effect=GitHubAPIError("API error"))
        responder = IssueResponder(
            client=mock_client,
            db=mock_db,
            response_gen=mock_response_gen,
            dry_run=False,
        )
        issue = _make_issue()
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        assert outcome.success is False
        assert "Failed to post comment" in outcome.error

    @pytest.mark.asyncio
    async def test_should_fetch_existing_comments(self, responder, mock_client):
        issue = _make_issue()
        action = _make_action()
        await responder.handle_respond(action, issue)
        mock_client.list_issue_comments.assert_called_once_with("o/r", 10)

    @pytest.mark.asyncio
    async def test_should_handle_comment_fetch_failure(self, responder, mock_client):
        mock_client.list_issue_comments = AsyncMock(side_effect=GitHubAPIError("fail"))
        issue = _make_issue()
        action = _make_action()
        outcome = await responder.handle_respond(action, issue)
        # Should still proceed with empty comments
        assert outcome.success is True


class TestShouldRespond:
    """Test should_respond filtering logic."""

    @pytest.fixture
    def responder(self, mock_client, mock_db, mock_response_gen):
        return IssueResponder(
            client=mock_client,
            db=mock_db,
            response_gen=mock_response_gen,
            respond_to_labels=["question", "help wanted", "bug"],
            max_response_age_hours=168,
        )

    def test_should_respond_to_matching_label(self, responder):
        issue = _make_issue(labels=["bug"])
        assert responder.should_respond(issue) is True

    def test_should_respond_to_case_insensitive_label(self, responder):
        issue = _make_issue(labels=["Bug", "Enhancement"])
        assert responder.should_respond(issue) is True

    def test_should_not_respond_to_already_responded(self, responder):
        issue = _make_issue(has_our_response=True, labels=["bug"])
        assert responder.should_respond(issue) is False

    def test_should_not_respond_to_old_issues(self, responder):
        issue = _make_issue(age_hours=200.0, labels=["bug"])
        assert responder.should_respond(issue) is False

    def test_should_not_respond_to_unmatched_labels(self, responder):
        issue = _make_issue(labels=["enhancement", "docs"])
        assert responder.should_respond(issue) is False

    def test_should_not_respond_to_no_labels(self, responder):
        issue = IssueSnapshot(
            number=10,
            title="Bug report",
            author="reporter",
            body="Something broken",
            labels=[],
            age_hours=5.0,
        )
        assert responder.should_respond(issue) is False
