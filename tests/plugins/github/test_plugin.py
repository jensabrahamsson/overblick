"""
Tests for the GitHub plugin — lifecycle, event processing, integration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.github.database import GitHubDB, MIGRATIONS
from overblick.plugins.github.models import (
    EventAction,
    EventType,
    GitHubEvent,
    PluginState,
)
from overblick.plugins.github.plugin import GitHubPlugin


class TestGitHubPluginSetup:
    """Test plugin initialization and configuration."""

    @pytest.mark.asyncio
    async def test_setup_creates_database(self, github_plugin_context):
        """setup() creates the SQLite database."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        db_path = github_plugin_context.data_dir / "github.db"
        assert db_path.exists()
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_setup_loads_config(self, github_plugin_context):
        """setup() loads configuration from identity."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        assert plugin._repos == ["moltbook/api"]
        assert plugin._bot_username == "anomal-bot"
        assert plugin._default_branch == "main"
        assert plugin._dry_run is False
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_setup_loads_token(self, github_plugin_context):
        """setup() loads github_token from secrets."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        assert plugin._client._token == "ghp_test_token_123"
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_setup_requires_identity(self, tmp_path, mock_audit_log):
        """setup() raises if no identity is set."""
        from overblick.core.plugin_base import PluginContext

        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            identity=None,
        )
        plugin = GitHubPlugin(ctx)

        with pytest.raises(RuntimeError, match="requires an identity"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_requires_repos(self, github_plugin_context, github_identity):
        """setup() raises if no repos are configured."""
        # Override identity to have empty repos
        from overblick.identities import Identity, LLMSettings, ScheduleSettings

        empty_identity = Identity(
            name="test",
            llm=LLMSettings(),
            schedule=ScheduleSettings(),
            raw_config={"github": {"repos": []}},
        )
        github_plugin_context.identity = empty_identity
        plugin = GitHubPlugin(github_plugin_context)

        with pytest.raises(RuntimeError, match="no repos"):
            await plugin.setup()


class TestGitHubPluginTick:
    """Test the main tick cycle."""

    @pytest.mark.asyncio
    async def test_tick_respects_interval(self, github_plugin_context):
        """tick() skips if interval hasn't elapsed."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        import time
        plugin._state.last_check = time.time()  # Just checked

        # Replace client with mock to verify no calls
        mock_client = AsyncMock()
        mock_client.rate_limit_remaining = 5000
        plugin._client = mock_client

        # This tick should be a no-op
        await plugin.tick()

        # Client shouldn't have been called
        assert mock_client.list_issues.call_count == 0
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_tick_respects_quiet_hours(self, github_plugin_context):
        """tick() skips during quiet hours."""
        github_plugin_context.quiet_hours_checker.is_quiet_hours.return_value = True

        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()
        plugin._state.last_check = None  # Force interval pass

        await plugin.tick()

        # Should have been blocked by quiet hours
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_tick_skips_without_llm(self, github_plugin_context):
        """tick() skips if no LLM pipeline is available."""
        github_plugin_context.llm_pipeline = None

        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()
        plugin._state.last_check = None

        await plugin.tick()
        await plugin.teardown()


class TestGitHubPluginProcessing:
    """Test event processing and response pipeline."""

    @pytest.mark.asyncio
    async def test_process_event_respond(self, github_plugin_context, sample_mention_event):
        """High-score events trigger response generation."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        # Mock the response generator
        plugin._response_gen.generate = AsyncMock(return_value="Here's my analysis...")
        plugin._client = AsyncMock()
        plugin._client.create_comment = AsyncMock(return_value={"id": 99})
        plugin._client.list_issue_comments = AsyncMock(return_value=[])
        plugin._client.rate_limit_remaining = 4000

        action = await plugin._process_event(sample_mention_event)

        assert action == "responded"
        assert plugin._state.comments_posted == 1

    @pytest.mark.asyncio
    async def test_process_event_dry_run(self, github_plugin_context, sample_mention_event):
        """Dry run mode logs but doesn't post comments."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()
        plugin._dry_run = True

        action = await plugin._process_event(sample_mention_event)

        assert action == "dry_run"
        # No comment should be posted
        assert plugin._state.comments_posted == 0

    @pytest.mark.asyncio
    async def test_process_event_skip(self, github_plugin_context):
        """Low-score events are skipped."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        low_score_event = GitHubEvent(
            event_id="test/skip/1",
            event_type=EventType.ISSUE_OPENED,
            repo="other/repo",
            issue_number=999,
            issue_title="Unrelated topic",
            body="Something about gardening",
            author="user",
            created_at="2026-02-20T12:00:00Z",
        )

        action = await plugin._process_event(low_score_event)
        assert action == "skipped"
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_process_event_notify(self, github_plugin_context, mock_telegram_notifier_github):
        """Medium-score events trigger notification."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        # Force notify threshold but not respond
        plugin._decision_engine._respond_threshold = 200  # Very high
        plugin._decision_engine._notify_threshold = 25

        event = GitHubEvent(
            event_id="test/notify/1",
            event_type=EventType.ISSUE_OPENED,
            repo="moltbook/api",
            issue_number=200,
            issue_title="Security issue in API auth",
            body="Found a vulnerability in the authentication",
            author="reporter",
            labels=["question"],
            created_at="2026-02-20T12:00:00Z",
        )

        action = await plugin._process_event(event)
        assert action == "notified"
        assert plugin._state.notifications_sent == 1
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_process_event_records_in_db(self, github_plugin_context, sample_event):
        """Events are always recorded in the database."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        await plugin._process_event(sample_event)

        # Verify event was recorded (dedup check should now return True)
        has_event = await plugin._db.has_event(sample_event.event_id)
        assert has_event is True
        await plugin.teardown()


class TestGitHubPluginStatus:
    """Test status reporting."""

    @pytest.mark.asyncio
    async def test_get_status(self, github_plugin_context):
        """get_status returns expected fields."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        status = plugin.get_status()

        assert status["plugin"] == "github"
        assert status["identity"] == "anomal"
        assert status["repos_monitored"] == 1
        assert "events_processed" in status
        assert "comments_posted" in status
        assert "rate_limit_remaining" in status
        assert "dry_run" in status
        assert "health" in status
        await plugin.teardown()


class TestGitHubPluginTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_closes_resources(self, github_plugin_context):
        """teardown() closes DB and HTTP session."""
        plugin = GitHubPlugin(github_plugin_context)
        await plugin.setup()

        await plugin.teardown()

        # Should be safe to call twice
        await plugin.teardown()
