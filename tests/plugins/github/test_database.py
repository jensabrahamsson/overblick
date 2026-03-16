"""Tests for GitHubDB — database layer for GitHub plugin.

Covers all uncovered lines: delegated agentic methods, event deduplication,
comment tracking, file tree/content cache, PR tracking, repo summaries.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.models import (
    CachedFile,
    CommentRecord,
    EventRecord,
    FileTreeEntry,
)


@pytest.fixture
def mock_backend():
    """Mock DatabaseBackend."""
    backend = AsyncMock()
    backend.connect = AsyncMock()
    backend.close = AsyncMock()
    backend.execute = AsyncMock()
    backend.execute_returning_id = AsyncMock(return_value=42)
    backend.fetch_one = AsyncMock(return_value=None)
    backend.fetch_all = AsyncMock(return_value=[])
    backend.fetch_scalar = AsyncMock(return_value=0)
    return backend


@pytest.fixture
def db(mock_backend):
    """Create a GitHubDB with mocked backend."""
    return GitHubDB(mock_backend)


class TestSetup:
    @pytest.mark.asyncio
    async def test_setup_connects_and_migrates(self, db, mock_backend):
        await db.setup()
        mock_backend.connect.assert_awaited_once()


class TestAgenticDelegation:
    """Test all delegated agentic methods."""

    @pytest.mark.asyncio
    async def test_upsert_goal(self, db):
        from overblick.core.agentic.models import AgentGoal

        goal = AgentGoal(name="test", description="test goal")
        await db.upsert_goal(goal)

    @pytest.mark.asyncio
    async def test_get_goal_by_name(self, db):
        await db.get_goal_by_name("test-goal")

    @pytest.mark.asyncio
    async def test_log_action(self, db):
        await db.log_action(1, MagicMock())

    @pytest.mark.asyncio
    async def test_get_recent_actions(self, db):
        await db.get_recent_actions(10)

    @pytest.mark.asyncio
    async def test_add_learning(self, db):
        await db.add_learning(MagicMock())

    @pytest.mark.asyncio
    async def test_get_learnings(self, db):
        await db.get_learnings(5)

    @pytest.mark.asyncio
    async def test_log_tick(self, db):
        await db.log_tick(MagicMock())

    @pytest.mark.asyncio
    async def test_get_tick_count(self, db):
        await db.get_tick_count()

    @pytest.mark.asyncio
    async def test_get_goals(self, db):
        await db.get_goals("active")


class TestEventDeduplication:
    @pytest.mark.asyncio
    async def test_has_event_true(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=1)
        result = await db.has_event("evt-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_has_event_false(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=0)
        result = await db.has_event("evt-999")
        assert result is False

    @pytest.mark.asyncio
    async def test_has_event_none_count(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=None)
        result = await db.has_event("evt-nil")
        assert result is False

    @pytest.mark.asyncio
    async def test_record_event(self, db, mock_backend):
        record = EventRecord(
            event_id="evt-1",
            event_type="issue_opened",
            repo="test/repo",
            issue_number=42,
            author="alice",
            score=75,
            action_taken="respond",
        )
        row_id = await db.record_event(record)
        assert row_id == 42
        mock_backend.execute_returning_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_event_returns_zero_when_none(self, db, mock_backend):
        mock_backend.execute_returning_id = AsyncMock(return_value=None)
        record = EventRecord(
            event_id="evt-dup",
            event_type="issue_comment",
            repo="test/repo",
            issue_number=1,
        )
        row_id = await db.record_event(record)
        assert row_id == 0

    @pytest.mark.asyncio
    async def test_has_responded_to_issue_true(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=3)
        result = await db.has_responded_to_issue("test/repo", 42)
        assert result is True

    @pytest.mark.asyncio
    async def test_has_responded_to_issue_false(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=0)
        result = await db.has_responded_to_issue("test/repo", 42)
        assert result is False

    @pytest.mark.asyncio
    async def test_has_responded_to_issue_none(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=None)
        result = await db.has_responded_to_issue("test/repo", 42)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_response_count(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=5)
        count = await db.get_response_count("test/repo", 42)
        assert count == 5

    @pytest.mark.asyncio
    async def test_get_response_count_none(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=None)
        count = await db.get_response_count("test/repo", 42)
        assert count == 0


class TestCommentTracking:
    @pytest.mark.asyncio
    async def test_record_comment(self, db, mock_backend):
        record = CommentRecord(
            github_comment_id=12345,
            repo="test/repo",
            issue_number=42,
            content_hash="abc123",
        )
        row_id = await db.record_comment(record)
        assert row_id == 42

    @pytest.mark.asyncio
    async def test_record_comment_returns_zero_when_none(self, db, mock_backend):
        mock_backend.execute_returning_id = AsyncMock(return_value=None)
        record = CommentRecord(
            github_comment_id=99999,
            repo="test/repo",
            issue_number=1,
        )
        row_id = await db.record_comment(record)
        assert row_id == 0


class TestPRTracking:
    @pytest.mark.asyncio
    async def test_upsert_pr_tracking(self, db, mock_backend):
        await db.upsert_pr_tracking(
            "test/repo",
            42,
            title="Bump dep",
            author="dependabot",
            is_dependabot=True,
            version_bump="patch",
            ci_status="success",
            merged=True,
            auto_merged=True,
        )
        mock_backend.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_pr_tracking_defaults(self, db, mock_backend):
        """Test with no kwargs — all defaults."""
        await db.upsert_pr_tracking("test/repo", 99)
        mock_backend.execute.assert_awaited_once()
        args = mock_backend.execute.call_args[0][1]
        # is_dependabot=0, merged=0, auto_merged=0
        assert args[4] == 0  # is_dependabot
        assert args[7] == 0  # merged
        assert args[8] == 0  # auto_merged

    @pytest.mark.asyncio
    async def test_was_pr_auto_merged_true(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=1)
        result = await db.was_pr_auto_merged("test/repo", 42)
        assert result is True

    @pytest.mark.asyncio
    async def test_was_pr_auto_merged_false(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=0)
        result = await db.was_pr_auto_merged("test/repo", 42)
        assert result is False

    @pytest.mark.asyncio
    async def test_was_pr_auto_merged_none(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=None)
        result = await db.was_pr_auto_merged("test/repo", 42)
        assert result is False


class TestFileTreeCache:
    @pytest.mark.asyncio
    async def test_get_tree_meta_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(
            return_value={"repo": "test/repo", "root_sha": "abc", "last_refreshed": "2026-01-01"}
        )
        result = await db.get_tree_meta("test/repo")
        assert result is not None
        assert result["root_sha"] == "abc"

    @pytest.mark.asyncio
    async def test_get_tree_meta_not_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(return_value=None)
        result = await db.get_tree_meta("unknown/repo")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_tree_meta_insert(self, db, mock_backend):
        """When no existing meta, should INSERT."""
        mock_backend.fetch_one = AsyncMock(return_value=None)
        await db.update_tree_meta("test/repo", "sha123")
        # Should have called execute for INSERT
        assert mock_backend.execute.await_count >= 1

    @pytest.mark.asyncio
    async def test_update_tree_meta_update(self, db, mock_backend):
        """When existing meta, should UPDATE."""
        mock_backend.fetch_one = AsyncMock(
            return_value={"repo": "test/repo", "root_sha": "old"}
        )
        await db.update_tree_meta("test/repo", "new_sha")
        assert mock_backend.execute.await_count >= 1

    @pytest.mark.asyncio
    async def test_upsert_tree_entry(self, db, mock_backend):
        entry = FileTreeEntry(path="src/main.py", sha="abc123", size=500)
        await db.upsert_tree_entry("test/repo", entry)
        mock_backend.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_tree_paths(self, db, mock_backend):
        mock_backend.fetch_all = AsyncMock(
            return_value=[{"path": "a.py"}, {"path": "b.py"}]
        )
        paths = await db.get_tree_paths("test/repo")
        assert paths == ["a.py", "b.py"]

    @pytest.mark.asyncio
    async def test_clear_tree(self, db, mock_backend):
        await db.clear_tree("test/repo")
        mock_backend.execute.assert_awaited_once()


class TestFileContentCache:
    @pytest.mark.asyncio
    async def test_get_cached_file_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(
            return_value={
                "repo": "test/repo",
                "path": "src/main.py",
                "sha": "abc",
                "content": "def main(): pass",
                "cached_at": "2026-01-01",
            }
        )
        result = await db.get_cached_file("test/repo", "abc")
        assert result is not None
        assert isinstance(result, CachedFile)
        assert result.content == "def main(): pass"

    @pytest.mark.asyncio
    async def test_get_cached_file_not_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(return_value=None)
        result = await db.get_cached_file("test/repo", "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_file_by_path_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(
            return_value={
                "repo": "test/repo",
                "path": "src/main.py",
                "sha": "def456",
                "content": "# code",
                "cached_at": "2026-01-01",
            }
        )
        result = await db.get_cached_file_by_path("test/repo", "src/main.py")
        assert result is not None
        assert result.sha == "def456"

    @pytest.mark.asyncio
    async def test_get_cached_file_by_path_not_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(return_value=None)
        result = await db.get_cached_file_by_path("test/repo", "missing.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_file(self, db, mock_backend):
        await db.cache_file("test/repo", "src/main.py", "sha1", "content here")
        mock_backend.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_file_sha_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(return_value={"sha": "abc123"})
        result = await db.get_file_sha("test/repo", "src/main.py")
        assert result == "abc123"

    @pytest.mark.asyncio
    async def test_get_file_sha_not_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(return_value=None)
        result = await db.get_file_sha("test/repo", "missing.py")
        assert result is None


class TestRepoSummaries:
    @pytest.mark.asyncio
    async def test_get_repo_summary_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(
            return_value={"repo": "test/repo", "summary": "A test repo", "file_count": 10}
        )
        result = await db.get_repo_summary("test/repo")
        assert result is not None
        assert result["summary"] == "A test repo"

    @pytest.mark.asyncio
    async def test_get_repo_summary_not_found(self, db, mock_backend):
        mock_backend.fetch_one = AsyncMock(return_value=None)
        result = await db.get_repo_summary("unknown/repo")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_repo_summary(self, db, mock_backend):
        await db.upsert_repo_summary("test/repo", "A summary", file_count=100, primary_language="Python")
        mock_backend.execute.assert_awaited_once()


class TestStats:
    @pytest.mark.asyncio
    async def test_get_stats(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(side_effect=[5, 3, 2])
        stats = await db.get_stats()
        assert stats["events_processed"] == 5
        assert stats["comments_posted"] == 3
        assert stats["repos_tracked"] == 2

    @pytest.mark.asyncio
    async def test_get_stats_none_values(self, db, mock_backend):
        mock_backend.fetch_scalar = AsyncMock(return_value=None)
        stats = await db.get_stats()
        assert stats["events_processed"] == 0
        assert stats["comments_posted"] == 0
        assert stats["repos_tracked"] == 0


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self, db, mock_backend):
        await db.close()
        mock_backend.close.assert_awaited_once()


class TestAgenticProperty:
    def test_agentic_property(self, db):
        assert db.agentic is db._agentic
