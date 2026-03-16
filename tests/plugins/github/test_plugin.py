"""
Tests for the GitHubAgentPlugin — lifecycle, configuration, status.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestGitHubAgentPluginNotify:
    """Test _notify_principal."""

    @pytest.mark.asyncio
    async def test_notify_principal_success(self, github_plugin_context):
        """_notify_principal sends notification via telegram."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        result = await plugin._notify_principal("test message")
        assert result is True
        assert plugin._state.notifications_sent == 1
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_notify_principal_no_notifier(self, github_plugin_context):
        """_notify_principal returns False when no notifier available."""
        github_plugin_context.capabilities = {}
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        result = await plugin._notify_principal("test")
        assert result is False
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_notify_principal_failure(self, github_plugin_context, mock_telegram_notifier_github):
        """_notify_principal returns False when notification fails."""
        mock_telegram_notifier_github.send_notification = AsyncMock(side_effect=Exception("send failed"))
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        result = await plugin._notify_principal("test")
        assert result is False
        await plugin.teardown()


class TestGitHubAgentPluginSetupNoToken:
    """Test setup without github_token."""

    @pytest.mark.asyncio
    async def test_setup_without_token_uses_read_only(self, github_plugin_context):
        """setup() warns but proceeds when no token is available."""
        github_plugin_context._secrets_getter = lambda key: None
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        assert plugin._client._token == ""
        await plugin.teardown()


class TestGitHubAgentPluginTickWithActions:
    """Test tick with agentic loop returning results."""

    @pytest.mark.asyncio
    async def test_tick_runs_full_cycle(self, github_plugin_context):
        """tick() runs the full agentic cycle and updates state."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()
        plugin._state.last_check = None

        # Mock the agentic_tick
        from overblick.core.agentic.models import TickLog
        mock_tick_log = TickLog(
            observations_count=3,
            actions_succeeded=2,
        )
        plugin.agentic_tick = AsyncMock(return_value=mock_tick_log)

        await plugin.tick()

        assert plugin._state.events_processed == 3
        assert plugin._state.comments_posted == 2
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_tick_clears_commands(self, github_plugin_context):
        """tick() clears command queue after execution."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()
        plugin._state.last_check = None

        plugin.agentic_tick = AsyncMock(return_value=None)

        # Add a pending command
        from overblick.plugins.github.owner_commands import OwnerCommand
        plugin._command_queue.pending_commands.append(
            OwnerCommand(verb="merge", repo="o/r", number=1)
        )

        await plugin.tick()

        assert len(plugin._command_queue.pending_commands) == 0
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_tick_updates_rate_limit(self, github_plugin_context):
        """tick() updates rate limit from client."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()
        plugin._state.last_check = None

        plugin.agentic_tick = AsyncMock(return_value=None)
        plugin._client._rate_limit_remaining = 3000

        await plugin.tick()

        assert plugin._state.rate_limit_remaining == 3000
        await plugin.teardown()


class TestGitHubAgentPluginExtraPlanningContext:
    """Test get_extra_planning_context."""

    @pytest.mark.asyncio
    async def test_returns_command_context(self, github_plugin_context):
        """get_extra_planning_context returns formatted commands."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        from overblick.plugins.github.owner_commands import OwnerCommand
        plugin._command_queue.pending_commands.append(
            OwnerCommand(verb="merge", repo="o/r", number=42)
        )

        context = plugin.get_extra_planning_context()
        assert "merge" in context
        assert "42" in context
        await plugin.teardown()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_commands(self, github_plugin_context):
        """get_extra_planning_context returns empty string when no commands."""
        plugin = GitHubAgentPlugin(github_plugin_context)
        await plugin.setup()

        context = plugin.get_extra_planning_context()
        assert context == ""
        await plugin.teardown()


class TestMultiRepoObserver:
    """Test _MultiRepoObserver."""

    @pytest.mark.asyncio
    async def test_observe_collects_from_all_repos(self, github_plugin_context):
        """observe() collects observations from all configured repos."""
        from overblick.plugins.github.plugin import _MultiRepoObserver
        from overblick.plugins.github.models import RepoObservation

        mock_observer = AsyncMock()
        mock_observer.observe = AsyncMock(return_value=RepoObservation(repo="o/r"))

        multi = _MultiRepoObserver(mock_observer, ["o/r", "o/r2"])
        result = await multi.observe()

        assert result is not None
        assert "o/r" in result

    @pytest.mark.asyncio
    async def test_observe_handles_errors(self):
        """observe() handles errors for individual repos."""
        from overblick.plugins.github.plugin import _MultiRepoObserver
        from overblick.plugins.github.models import RepoObservation

        mock_observer = AsyncMock()
        mock_observer.observe = AsyncMock(side_effect=Exception("fail"))

        multi = _MultiRepoObserver(mock_observer, ["o/r"])
        result = await multi.observe()

        assert result is None

    @pytest.mark.asyncio
    async def test_observe_partial_failure(self):
        """observe() returns successful repos even if some fail."""
        from overblick.plugins.github.plugin import _MultiRepoObserver
        from overblick.plugins.github.models import RepoObservation

        call_count = [0]

        async def mock_observe(repo):
            call_count[0] += 1
            if repo == "fail/repo":
                raise Exception("fail")
            return RepoObservation(repo=repo)

        mock_observer = AsyncMock()
        mock_observer.observe = mock_observe

        multi = _MultiRepoObserver(mock_observer, ["ok/repo", "fail/repo"])
        result = await multi.observe()

        assert result is not None
        assert "ok/repo" in result
        assert "fail/repo" not in result

    def test_format_for_planner_with_observations(self):
        """format_for_planner formats all repo observations."""
        from overblick.plugins.github.plugin import _MultiRepoObserver
        from overblick.plugins.github.models import RepoObservation

        mock_observer = MagicMock()
        mock_observer.format_for_planner = MagicMock(return_value="formatted text")

        multi = _MultiRepoObserver(mock_observer, ["o/r"])
        obs = {"o/r": RepoObservation(repo="o/r")}
        result = multi.format_for_planner(obs)

        assert "formatted text" in result

    def test_format_for_planner_empty_observation(self):
        """format_for_planner returns fallback for empty observation."""
        from overblick.plugins.github.plugin import _MultiRepoObserver

        mock_observer = MagicMock()
        multi = _MultiRepoObserver(mock_observer, ["o/r"])
        result = multi.format_for_planner(None)

        assert "No observations" in result

    def test_format_for_planner_non_dict_observation(self):
        """format_for_planner returns fallback for non-dict observation."""
        from overblick.plugins.github.plugin import _MultiRepoObserver

        mock_observer = MagicMock()
        multi = _MultiRepoObserver(mock_observer, ["o/r"])
        result = multi.format_for_planner("not a dict")

        assert "No observations" in result


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
