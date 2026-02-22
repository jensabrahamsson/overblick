"""
Tests for ObservationCollector — world state gathering.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.plugins.github.models import CIStatus, VersionBumpType
from overblick.plugins.github.observation import (
    ObservationCollector,
    _parse_version_bump,
    _age_hours,
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
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
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
            client=mock_client, db=mock_db, bot_username="test-bot",
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
            client=mock_client, db=mock_db, bot_username="test-bot",
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
            client=mock_client, db=mock_db, bot_username="test-bot",
        )
        obs = await observer.observe("owner/repo")
        text = observer.format_for_planner(obs)

        assert "Repository: owner/repo" in text
        assert "PR #1" in text
        assert "Test PR" in text
