"""
Tests for ObservationCollector — world state gathering.
"""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.github.models import (
    CIStatus,
    IssueSnapshot,
    PRSnapshot,
    RepoObservation,
    VersionBumpType,
)
from overblick.plugins.github.observation import (
    ObservationCollector,
    _age_hours,
    _parse_version_bump,
)


class TestVersionBumpParsing:
    """Test Dependabot version bump detection."""

    def test_patch_bump(self):
        assert _parse_version_bump("Bump lodash from 4.17.20 to 4.17.21") == VersionBumpType.PATCH

    def test_minor_bump(self):
        assert _parse_version_bump("Bump pytest from 7.4.0 to 7.5.0") == VersionBumpType.MINOR

    def test_major_bump(self):
        assert _parse_version_bump("Bump pydantic from 1.10.0 to 2.0.0") == VersionBumpType.MAJOR

    def test_unknown_format(self):
        assert _parse_version_bump("Update dependency X") == VersionBumpType.UNKNOWN

    def test_partial_version(self):
        result = _parse_version_bump("Bump foo from 1.0 to 2.0")
        assert result == VersionBumpType.MAJOR


class TestAgeHours:
    """Test age calculation."""

    def test_valid_timestamp(self):
        # Recent timestamp should give small age
        from datetime import datetime, timedelta, timezone

        recent = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = _age_hours(recent)
        assert 1.9 < age < 2.5

    def test_invalid_timestamp(self):
        assert _age_hours("not-a-date") == 0.0

    def test_empty_timestamp(self):
        assert _age_hours("") == 0.0


class TestObservationCollector:
    """Test ObservationCollector.observe()."""

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.list_pulls = AsyncMock(return_value=[])
        client.list_issues = AsyncMock(return_value=[])
        client.get_check_runs = AsyncMock(return_value={"check_runs": []})
        client.get_combined_status = AsyncMock(return_value={"state": ""})
        client.list_pull_reviews = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.get_repo_summary = AsyncMock(return_value=None)
        db.get_tree_paths = AsyncMock(return_value=[])
        db.has_responded_to_issue = AsyncMock(return_value=False)
        db.upsert_pr_tracking = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_empty_repo(self, mock_client, mock_db):
        """Observe returns empty observation for repo with no PRs/issues."""
        observer = ObservationCollector(
            client=mock_client,
            db=mock_db,
            bot_username="test-bot",
        )
        obs = await observer.observe("owner/repo")

        assert obs.repo == "owner/repo"
        assert obs.open_prs == []
        assert obs.open_issues == []
        assert obs.observed_at != ""

    @pytest.mark.asyncio
    async def test_dependabot_pr_detection(self, mock_client, mock_db):
        """Dependabot PRs are identified and classified."""
        mock_client.list_pulls.return_value = [
            {
                "number": 42,
                "title": "Bump pytest from 7.4.0 to 7.5.0",
                "user": {"login": "dependabot[bot]"},
                "state": "open",
                "head": {"sha": "abc123"},
                "base": {"ref": "main"},
                "labels": [],
                "created_at": "2026-02-20T10:00:00Z",
                "updated_at": "2026-02-20T10:00:00Z",
                "draft": False,
                "mergeable": True,
                "merged": False,
            },
        ]
        mock_client.get_check_runs.return_value = {
            "check_runs": [
                {"name": "tests", "status": "completed", "conclusion": "success"},
            ],
        }
        mock_client.get_combined_status.return_value = {"state": "success"}

        observer = ObservationCollector(
            client=mock_client,
            db=mock_db,
            bot_username="test-bot",
        )
        obs = await observer.observe("owner/repo")

        assert len(obs.open_prs) == 1
        assert len(obs.dependabot_prs) == 1
        pr = obs.open_prs[0]
        assert pr.is_dependabot is True
        assert pr.version_bump == VersionBumpType.MINOR
        assert pr.ci_status == CIStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_issue_collection(self, mock_client, mock_db):
        """Issues are collected, PRs are filtered out."""
        mock_client.list_issues.return_value = [
            {
                "number": 7,
                "title": "Bug report",
                "user": {"login": "reporter"},
                "state": "open",
                "labels": [{"name": "bug"}],
                "body": "Something is broken",
                "created_at": "2026-02-20T10:00:00Z",
                "updated_at": "2026-02-20T10:00:00Z",
                "comments": 0,
            },
            {
                "number": 42,
                "title": "PR (should be filtered)",
                "user": {"login": "dev"},
                "state": "open",
                "labels": [],
                "body": "",
                "created_at": "2026-02-20T10:00:00Z",
                "updated_at": "2026-02-20T10:00:00Z",
                "comments": 0,
                "pull_request": {"url": "..."},  # This makes it a PR
            },
        ]

        observer = ObservationCollector(
            client=mock_client,
            db=mock_db,
            bot_username="test-bot",
        )
        obs = await observer.observe("owner/repo")

        assert len(obs.open_issues) == 1
        assert obs.open_issues[0].number == 7

    @pytest.mark.asyncio
    async def test_format_for_planner(self, mock_client, mock_db):
        """format_for_planner produces readable text."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1,
                "title": "Test PR",
                "user": {"login": "user"},
                "state": "open",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "labels": [],
                "created_at": "2026-02-20T10:00:00Z",
                "updated_at": "2026-02-20T10:00:00Z",
                "draft": False,
                "mergeable": False,
                "merged": False,
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": ""}

        observer = ObservationCollector(
            client=mock_client,
            db=mock_db,
            bot_username="test-bot",
        )
        obs = await observer.observe("owner/repo")
        text = observer.format_for_planner(obs)

        assert "Repository: owner/repo" in text
        assert "PR #1" in text
        assert "Test PR" in text

    @pytest.mark.asyncio
    async def test_collect_prs_handles_api_error(self, mock_client, mock_db):
        """_collect_prs returns empty list on API error."""
        from overblick.plugins.github.client import GitHubAPIError
        mock_client.list_pulls = AsyncMock(side_effect=GitHubAPIError("rate limited"))
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs == []

    @pytest.mark.asyncio
    async def test_collect_issues_handles_api_error(self, mock_client, mock_db):
        """_collect_issues returns empty list on API error."""
        from overblick.plugins.github.client import GitHubAPIError
        mock_client.list_issues = AsyncMock(side_effect=GitHubAPIError("rate limited"))
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_issues == []

    @pytest.mark.asyncio
    async def test_mergeable_none_treated_as_false(self, mock_client, mock_db):
        """PR with mergeable=None is treated as not mergeable."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1,
                "title": "PR",
                "user": {"login": "dev"},
                "state": "open",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "labels": [],
                "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
                "mergeable": None,  # Not computed yet
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": ""}
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].mergeable is False

    @pytest.mark.asyncio
    async def test_ci_pending_status(self, mock_client, mock_db):
        """CI with pending check runs returns PENDING."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {
            "check_runs": [
                {"name": "build", "status": "in_progress", "conclusion": ""},
            ],
        }
        mock_client.get_combined_status.return_value = {"state": ""}
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].ci_status == CIStatus.PENDING

    @pytest.mark.asyncio
    async def test_ci_failure_status(self, mock_client, mock_db):
        """CI with failed check runs returns FAILURE."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {
            "check_runs": [
                {"name": "build", "status": "completed", "conclusion": "failure"},
            ],
        }
        mock_client.get_combined_status.return_value = {"state": ""}
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].ci_status == CIStatus.FAILURE

    @pytest.mark.asyncio
    async def test_ci_combined_status_success(self, mock_client, mock_db):
        """When no check runs, falls back to combined status."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": "success"}
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].ci_status == CIStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_ci_combined_status_failure(self, mock_client, mock_db):
        """Combined status failure."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": "failure"}
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].ci_status == CIStatus.FAILURE

    @pytest.mark.asyncio
    async def test_ci_combined_status_pending(self, mock_client, mock_db):
        """Combined status pending."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": "pending"}
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].ci_status == CIStatus.PENDING

    @pytest.mark.asyncio
    async def test_ci_api_error_returns_unknown(self, mock_client, mock_db):
        """CI status returns UNKNOWN on API error."""
        from overblick.plugins.github.client import GitHubAPIError
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs = AsyncMock(side_effect=GitHubAPIError("fail"))
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].ci_status == CIStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_review_state_approved(self, mock_client, mock_db):
        """Review state returns approved."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": ""}
        mock_client.list_pull_reviews.return_value = [
            {"state": "COMMENTED"},
            {"state": "APPROVED"},
        ]
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].review_state == "approved"

    @pytest.mark.asyncio
    async def test_review_state_changes_requested(self, mock_client, mock_db):
        """Review state returns changes_requested."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": ""}
        mock_client.list_pull_reviews.return_value = [
            {"state": "CHANGES_REQUESTED"},
        ]
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].review_state == "changes_requested"

    @pytest.mark.asyncio
    async def test_review_state_pending_no_reviews(self, mock_client, mock_db):
        """Review state returns pending when no reviews."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": ""}
        mock_client.list_pull_reviews.return_value = []
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].review_state == "pending"

    @pytest.mark.asyncio
    async def test_review_state_pending_only_comments(self, mock_client, mock_db):
        """Review state returns pending when only comment reviews exist."""
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": ""}
        mock_client.list_pull_reviews.return_value = [
            {"state": "COMMENTED"},
        ]
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].review_state == "pending"

    @pytest.mark.asyncio
    async def test_review_state_api_error(self, mock_client, mock_db):
        """Review state returns empty on API error."""
        from overblick.plugins.github.client import GitHubAPIError
        mock_client.list_pulls.return_value = [
            {
                "number": 1, "title": "PR", "user": {"login": "dev"},
                "state": "open", "head": {"sha": "abc"}, "base": {"ref": "main"},
                "labels": [], "created_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-10T10:00:00Z",
            },
        ]
        mock_client.get_check_runs.return_value = {"check_runs": []}
        mock_client.get_combined_status.return_value = {"state": ""}
        mock_client.list_pull_reviews = AsyncMock(side_effect=GitHubAPIError("fail"))
        observer = ObservationCollector(client=mock_client, db=mock_db)
        obs = await observer.observe("o/r")
        assert obs.open_prs[0].review_state == ""


class TestFormatForPlanner:
    """Test format_for_planner with various observation states."""

    def _make_observer(self):
        return ObservationCollector(
            client=AsyncMock(), db=AsyncMock(), bot_username="bot"
        )

    def test_should_show_no_open_prs(self):
        observer = self._make_observer()
        obs = RepoObservation(repo="o/r")
        text = observer.format_for_planner(obs)
        assert "No open PRs" in text

    def test_should_show_no_open_issues(self):
        observer = self._make_observer()
        obs = RepoObservation(repo="o/r")
        text = observer.format_for_planner(obs)
        assert "No open issues" in text

    def test_should_show_repo_summary(self):
        observer = self._make_observer()
        obs = RepoObservation(repo="o/r", repo_summary="Python project with tests")
        text = observer.format_for_planner(obs)
        assert "Python project with tests" in text

    def test_should_show_dependabot_pr_flags(self):
        observer = self._make_observer()
        pr = PRSnapshot(
            number=1, title="Bump X", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.SUCCESS, draft=False, mergeable=True,
            review_state="approved", age_hours=2.0,
        )
        obs = RepoObservation(repo="o/r", open_prs=[pr])
        text = observer.format_for_planner(obs)
        assert "dependabot:patch" in text
        assert "mergeable" in text
        assert "review:approved" in text

    def test_should_show_draft_flag(self):
        observer = self._make_observer()
        pr = PRSnapshot(
            number=1, title="WIP", author="dev",
            draft=True, age_hours=1.0,
        )
        obs = RepoObservation(repo="o/r", open_prs=[pr])
        text = observer.format_for_planner(obs)
        assert "draft" in text

    def test_should_show_dependabot_prs_section(self):
        from overblick.plugins.github.models import PRSnapshot, VersionBumpType, CIStatus
        observer = self._make_observer()
        pr = PRSnapshot(
            number=1, title="Bump X", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.MINOR,
            ci_status=CIStatus.SUCCESS, mergeable=True, age_hours=1.0,
        )
        obs = RepoObservation(repo="o/r", dependabot_prs=[pr])
        text = observer.format_for_planner(obs)
        assert "Dependabot PRs" in text
        assert "minor bump" in text

    def test_should_show_failing_ci_section(self):
        observer = self._make_observer()
        pr = PRSnapshot(
            number=1, title="Broken", author="dev",
            ci_status=CIStatus.FAILURE, age_hours=1.0,
            ci_details=[{"name": "build", "conclusion": "failure"}],
        )
        obs = RepoObservation(repo="o/r", failing_ci=[pr])
        text = observer.format_for_planner(obs)
        assert "FAILING CI" in text
        assert "build:failure" in text

    def test_should_show_open_issues_section(self):
        observer = self._make_observer()
        issue = IssueSnapshot(
            number=5, title="Bug", author="user",
            labels=["bug"], age_hours=10.0, comments_count=2,
            has_our_response=True,
        )
        obs = RepoObservation(repo="o/r", open_issues=[issue])
        text = observer.format_for_planner(obs)
        assert "Open Issues" in text
        assert "Bug" in text
        assert "responded" in text
        assert "labels: bug" in text

    def test_should_show_unanswered_issue(self):
        observer = self._make_observer()
        issue = IssueSnapshot(
            number=5, title="Help", author="user",
            labels=[], age_hours=10.0, has_our_response=False,
        )
        obs = RepoObservation(repo="o/r", open_issues=[issue])
        text = observer.format_for_planner(obs)
        assert "unanswered" in text
        assert "labels: none" in text

    def test_should_show_stale_prs_section(self):
        observer = self._make_observer()
        pr = PRSnapshot(
            number=1, title="Old PR", author="dev", age_hours=100.0,
        )
        obs = RepoObservation(repo="o/r", stale_prs=[pr])
        text = observer.format_for_planner(obs)
        assert "Stale PRs" in text
        assert "Old PR" in text


class TestAgeHoursNaiveDatetime:
    """Test _age_hours with naive datetime (no timezone)."""

    def test_should_handle_naive_timestamp(self):
        """Naive timestamps (no Z or offset) should get UTC added."""
        result = _age_hours("2026-03-10T10:00:00")
        # Should not return 0.0 — should compute age
        assert result > 0.0
