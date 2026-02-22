"""
Tests for the GitHubAgentPlugin — lifecycle, configuration, status.
"""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.github.models import PluginState
from overblick.plugins.github.plugin import GitHubAgentPlugin


class TestGitHubAgentPluginSetup:
    """Test plugin initialization and configuration."""

    @pytest.mark.asyncio
    async def test_setup_creates_database(self, github_plugin_context):
        """setup() creates the SQLite database."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        db_path = github_plugin_context.data_dir / "github.db"
        assert db_path.exists()
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_setup_loads_config(self, github_plugin_context):
        """setup() loads configuration from identity."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        assert plugin._repos == ["moltbook/api"]
        assert plugin._dry_run is False
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_setup_loads_token(self, github_plugin_context):
        """setup() loads github_token from secrets."""
        plugin = GitHubAgentPlugin(github_plugin_context)
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
        plugin = GitHubAgentPlugin(ctx)

        with pytest.raises(RuntimeError, match="requires an identity"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_requires_repos(self, github_plugin_context):
        """setup() raises if no repos are configured."""
        from overblick.identities import Identity, LLMSettings, ScheduleSettings

        empty_identity = Identity(
            name="test",
            llm=LLMSettings(),
            schedule=ScheduleSettings(),
            raw_config={"github": {"repos": []}},
        )
        github_plugin_context.identity = empty_identity
        plugin = GitHubAgentPlugin(github_plugin_context)

        with pytest.raises(RuntimeError, match="no repos"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_creates_default_goals(self, github_plugin_context):
        """setup() creates default agent goals."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        goals = await plugin._db.get_goals(status="active")
        assert len(goals) >= 5
        goal_names = {g.name for g in goals}
        assert "merge_safe_dependabot" in goal_names
        assert "communicate_with_owner" in goal_names
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_setup_initializes_agent_loop(self, github_plugin_context):
        """setup() wires the agent loop."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        assert plugin._agent_loop is not None
        await plugin.teardown()


class TestGitHubAgentPluginTick:
    """Test the main tick cycle."""

    @pytest.mark.asyncio
    async def test_tick_respects_interval(self, github_plugin_context):
        """tick() skips if interval hasn't elapsed."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        plugin._state.last_check = time.time()  # Just checked

        # Mock agent loop to verify no calls
        plugin._agent_loop = MagicMock()
        plugin._agent_loop.tick = AsyncMock()

        await plugin.tick()

        assert plugin._agent_loop.tick.call_count == 0
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_tick_respects_quiet_hours(self, github_plugin_context):
        """tick() skips during quiet hours."""
        github_plugin_context.quiet_hours_checker.is_quiet_hours.return_value = True

        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()
        plugin._state.last_check = None

        plugin._agent_loop = MagicMock()
        plugin._agent_loop.tick = AsyncMock()

        await plugin.tick()

        assert plugin._agent_loop.tick.call_count == 0
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_tick_skips_without_llm(self, github_plugin_context):
        """tick() skips if no LLM pipeline is available."""
        github_plugin_context.llm_pipeline = None

        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()
        plugin._state.last_check = None

        await plugin.tick()
        await plugin.teardown()


class TestGitHubAgentPluginStatus:
    """Test status reporting."""

    @pytest.mark.asyncio
    async def test_get_status(self, github_plugin_context):
        """get_status returns expected fields."""
        plugin = GitHubAgentPlugin(github_plugin_context)
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


class TestGitHubAgentPluginTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_closes_resources(self, github_plugin_context):
        """teardown() closes DB and HTTP session."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        await plugin.teardown()

        # Should be safe to call twice
        await plugin.teardown()
