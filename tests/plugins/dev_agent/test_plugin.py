"""Tests for dev agent plugin lifecycle."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from overblick.plugins.dev_agent.models import ActionType
from overblick.plugins.dev_agent.plugin import DevAgentPlugin


@pytest.fixture
def mock_identity():
    """Create a mock identity with dev_agent config."""
    identity = MagicMock()
    identity.raw_config = {
        "dev_agent": {
            "repo_url": "https://github.com/test/repo.git",
            "workspace_dir": "workspace/test",
            "default_branch": "main",
            "dry_run": True,
            "max_fix_attempts": 3,
            "max_actions_per_tick": 3,
            "tick_interval_minutes": 30,
            "opencode": {
                "model": "test-model",
                "timeout_seconds": 60,
            },
            "log_watcher": {
                "enabled": False,
                "scan_identities": [],
            },
        },
    }
    return identity


@pytest.fixture
def mock_ctx(tmp_path, mock_identity):
    """Create a mock PluginContext."""
    ctx = MagicMock()
    ctx.identity = mock_identity
    ctx.identity_name = "smed"
    ctx.data_dir = tmp_path / "data" / "smed"
    ctx.data_dir.mkdir(parents=True)
    ctx.llm_pipeline = MagicMock()
    ctx.get_secret = MagicMock(return_value=None)
    ctx.get_capability = MagicMock(return_value=None)
    ctx.quiet_hours_checker = None
    ctx.load_identity = MagicMock(return_value=mock_identity)
    ctx.build_system_prompt = MagicMock(return_value="You are Smed.")

    # Mock ipc_server as missing (not all contexts have it)
    ctx.ipc_server = None

    return ctx


class TestDevAgentPlugin:
    def test_plugin_name(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        assert plugin.name == "dev_agent"

    @pytest.mark.asyncio
    async def test_setup(self, mock_ctx):
        """Test that setup initializes all components."""
        plugin = DevAgentPlugin(mock_ctx)

        # Mock the agentic loop setup since it needs LLM
        with patch.object(plugin, "setup_agentic_loop", new_callable=AsyncMock) as mock_loop:
            await plugin.setup()

            assert plugin._db is not None
            assert plugin._workspace is not None
            assert plugin._opencode is not None
            assert plugin._test_runner is not None
            assert plugin._pr_creator is not None
            assert plugin._log_watcher is not None
            assert plugin._observer is not None
            assert plugin._dry_run is True

            mock_loop.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_no_identity(self):
        """Test that setup fails without identity."""
        ctx = MagicMock()
        ctx.identity = None
        plugin = DevAgentPlugin(ctx)

        with pytest.raises(RuntimeError, match="requires an identity"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_no_repo_url(self, mock_ctx, mock_identity):
        """Test that setup fails without repo_url."""
        mock_identity.raw_config["dev_agent"]["repo_url"] = ""
        plugin = DevAgentPlugin(mock_ctx)

        with pytest.raises(RuntimeError, match="no repo_url"):
            with patch.object(plugin, "setup_agentic_loop", new_callable=AsyncMock):
                await plugin.setup()

    def test_get_default_goals(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        goals = plugin.get_default_goals()
        assert len(goals) == 4
        names = {g.name for g in goals}
        assert "fix_bugs" in names
        assert "fix_log_errors" in names
        assert "maintain_test_health" in names
        assert "keep_workspace_clean" in names

    def test_get_valid_action_types(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        types = plugin.get_valid_action_types()
        assert types == {a.value for a in ActionType}
        assert len(types) == 7

    def test_get_learning_categories(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        cats = plugin.get_learning_categories()
        assert "bug_analysis" in cats
        assert "code_fixes" in cats

    def test_get_planning_prompt_config(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        config = plugin.get_planning_prompt_config()
        assert "Smed" in config.agent_role
        assert "analyze_bug" in config.available_actions
        assert "NEVER commit" in config.safety_rules

    def test_get_status(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        status = plugin.get_status()
        assert status["plugin"] == "dev_agent"
        assert status["identity"] == "smed"

    @pytest.mark.asyncio
    async def test_tick_interval_guard(self, mock_ctx):
        """Test that tick respects the interval guard."""
        plugin = DevAgentPlugin(mock_ctx)
        plugin._last_check = 9999999999.0  # Far future
        plugin._check_interval = 1800

        # Should return immediately without running
        await plugin.tick()

    @pytest.mark.asyncio
    async def test_tick_no_llm(self, mock_ctx):
        """Test that tick skips if no LLM pipeline."""
        mock_ctx.llm_pipeline = None
        plugin = DevAgentPlugin(mock_ctx)
        plugin._last_check = None  # Force past interval guard

        await plugin.tick()
        # Should not crash

    @pytest.mark.asyncio
    async def test_teardown(self, mock_ctx):
        """Test teardown closes DB."""
        plugin = DevAgentPlugin(mock_ctx)
        plugin._db = AsyncMock()

        await plugin.teardown()
        plugin._db.close.assert_called_once()


class TestIPCHandling:
    @pytest.mark.asyncio
    async def test_handle_bug_report(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        plugin._observer = MagicMock()

        msg = MagicMock()
        msg.payload = {"title": "Test bug", "ref": "issue#1"}

        await plugin._handle_ipc_bug_report(msg)
        plugin._observer.enqueue_ipc_message.assert_called_once_with(
            "bug_report", msg.payload,
        )

    @pytest.mark.asyncio
    async def test_handle_log_alert(self, mock_ctx):
        plugin = DevAgentPlugin(mock_ctx)
        plugin._observer = MagicMock()

        msg = MagicMock()
        msg.payload = {"message": "Error", "identity": "anomal"}

        await plugin._handle_ipc_log_alert(msg)
        plugin._observer.enqueue_ipc_message.assert_called_once_with(
            "log_alert", msg.payload,
        )


class TestPluginRegistry:
    def test_dev_agent_in_registry(self):
        from overblick.core.plugin_registry import _DEFAULT_PLUGINS
        assert "dev_agent" in _DEFAULT_PLUGINS
        module_path, class_name = _DEFAULT_PLUGINS["dev_agent"]
        assert module_path == "overblick.plugins.dev_agent.plugin"
        assert class_name == "DevAgentPlugin"
