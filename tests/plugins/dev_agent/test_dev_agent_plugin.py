"""
Tests for DevAgentPlugin — covers lifecycle, tick guards, IPC handlers, and helpers.
"""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.dev_agent.plugin import DevAgentPlugin, _DEFAULT_GOALS


def _make_ctx(tmp_path, has_identity=True, has_repo=True, dry_run=True):
    ctx = MagicMock()
    ctx.identity_name = "smed"
    ctx.data_dir = tmp_path / "data" / "smed"
    ctx.data_dir.mkdir(parents=True, exist_ok=True)
    ctx.llm_pipeline = AsyncMock()
    ctx.quiet_hours_checker = MagicMock()
    ctx.quiet_hours_checker.is_quiet_hours = MagicMock(return_value=False)
    ctx.audit_log = MagicMock()
    ctx.ipc_server = None
    ctx.get_secret = MagicMock(return_value="")
    ctx.get_capability = MagicMock(return_value=None)

    if has_identity:
        identity = MagicMock()
        identity.raw_config = {
            "dev_agent": {
                "repo_url": "https://github.com/test/repo.git" if has_repo else "",
                "workspace_dir": "workspace/test",
                "default_branch": "main",
                "dry_run": dry_run,
                "max_fix_attempts": 3,
                "max_actions_per_tick": 2,
                "tick_interval_minutes": 30,
                "opencode": {"model": "test-model", "timeout_seconds": 60},
                "log_watcher": {"enabled": False, "scan_identities": []},
                "git_config": {"author_name": "Test", "author_email": "test@test.com"},
            }
        }
        ctx.identity = identity
    else:
        ctx.identity = None

    return ctx


class TestDevAgentPluginSetup:
    @pytest.mark.asyncio
    async def test_should_raise_without_identity(self, tmp_path):
        ctx = _make_ctx(tmp_path, has_identity=False)
        plugin = DevAgentPlugin(ctx)
        with pytest.raises(RuntimeError, match="requires an identity"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_should_raise_without_repo_url(self, tmp_path):
        ctx = _make_ctx(tmp_path, has_repo=False)
        plugin = DevAgentPlugin(ctx)
        with pytest.raises(RuntimeError, match="no repo_url"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_should_setup_successfully(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        # Mock agentic loop setup to avoid complex dependency chain
        plugin.setup_agentic_loop = AsyncMock()

        await plugin.setup()

        assert plugin._db is not None
        assert plugin._workspace is not None
        assert plugin._dry_run is True
        plugin.setup_agentic_loop.assert_awaited_once()


class TestDevAgentPluginTick:
    @pytest.mark.asyncio
    async def test_should_skip_within_interval(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._last_check = time.time()
        plugin._check_interval = 9999

        plugin.agentic_tick = AsyncMock()
        await plugin.tick()
        plugin.agentic_tick.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_skip_during_quiet_hours(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.quiet_hours_checker.is_quiet_hours.return_value = True
        plugin = DevAgentPlugin(ctx)
        plugin._last_check = 0
        plugin._check_interval = 0

        plugin.agentic_tick = AsyncMock()
        await plugin.tick()
        plugin.agentic_tick.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_skip_without_llm_pipeline(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.llm_pipeline = None
        plugin = DevAgentPlugin(ctx)
        plugin._last_check = 0
        plugin._check_interval = 0

        plugin.agentic_tick = AsyncMock()
        await plugin.tick()
        plugin.agentic_tick.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_run_agentic_tick_when_ready(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._last_check = 0
        plugin._check_interval = 0

        plugin.agentic_tick = AsyncMock()
        await plugin.tick()
        plugin.agentic_tick.assert_awaited_once()


class TestDevAgentPluginAbstractMethods:
    @pytest.mark.asyncio
    async def test_create_observer_returns_observer(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._observer = MagicMock()
        result = await plugin.create_observer()
        assert result is plugin._observer

    def test_get_action_handlers(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._handlers = {"analyze": MagicMock()}
        assert plugin.get_action_handlers() == plugin._handlers

    def test_get_planning_prompt_config(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        config = plugin.get_planning_prompt_config()
        assert config is not None

    def test_get_default_goals(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        goals = plugin.get_default_goals()
        assert len(goals) == len(_DEFAULT_GOALS)

    def test_get_learning_categories(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        cats = plugin.get_learning_categories()
        assert "bug_analysis" in cats

    def test_get_valid_action_types(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        types = plugin.get_valid_action_types()
        assert isinstance(types, set)
        assert len(types) > 0


class TestDevAgentIPCHandlers:
    @pytest.mark.asyncio
    async def test_handle_ipc_bug_report(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._observer = MagicMock()

        msg = MagicMock()
        msg.payload = {"title": "Bug #1"}
        await plugin._handle_ipc_bug_report(msg)
        plugin._observer.enqueue_ipc_message.assert_called_once_with("bug_report", {"title": "Bug #1"})

    @pytest.mark.asyncio
    async def test_handle_ipc_log_alert(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._observer = MagicMock()

        msg = MagicMock()
        msg.payload = {"message": "Error found"}
        await plugin._handle_ipc_log_alert(msg)
        plugin._observer.enqueue_ipc_message.assert_called_once_with("log_alert", {"message": "Error found"})

    @pytest.mark.asyncio
    async def test_handle_ipc_bug_report_no_observer(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._observer = None

        msg = MagicMock()
        msg.payload = {"title": "Bug #1"}
        await plugin._handle_ipc_bug_report(msg)  # Should not raise


class TestDevAgentNotify:
    @pytest.mark.asyncio
    async def test_notify_principal_success(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        notifier = AsyncMock()
        notifier.send_notification = AsyncMock(return_value=True)
        ctx.get_capability = MagicMock(return_value=notifier)

        plugin = DevAgentPlugin(ctx)
        result = await plugin._notify_principal("Bug fixed!")
        assert result is True

    @pytest.mark.asyncio
    async def test_notify_principal_no_notifier(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.get_capability = MagicMock(return_value=None)

        plugin = DevAgentPlugin(ctx)
        result = await plugin._notify_principal("Bug fixed!")
        assert result is False

    @pytest.mark.asyncio
    async def test_notify_principal_exception(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        notifier = AsyncMock()
        notifier.send_notification = AsyncMock(side_effect=RuntimeError("fail"))
        ctx.get_capability = MagicMock(return_value=notifier)

        plugin = DevAgentPlugin(ctx)
        result = await plugin._notify_principal("Bug fixed!")
        assert result is False


class TestDevAgentGetStatus:
    def test_should_return_status_dict(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        status = plugin.get_status()
        assert status["plugin"] == "dev_agent"
        assert status["identity"] == "smed"


class TestDevAgentTeardown:
    @pytest.mark.asyncio
    async def test_should_close_db(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._db = AsyncMock()
        await plugin.teardown()
        plugin._db.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_handle_no_db(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = DevAgentPlugin(ctx)
        plugin._db = None
        await plugin.teardown()  # Should not raise


class TestDevAgentRegisterIPC:
    def test_should_register_handlers_when_ipc_server_available(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.ipc_server = MagicMock()
        ctx.ipc_server.on = MagicMock()

        plugin = DevAgentPlugin(ctx)
        plugin._register_ipc_handlers()

        assert ctx.ipc_server.on.call_count == 2

    def test_should_noop_without_ipc_server(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.ipc_server = None

        plugin = DevAgentPlugin(ctx)
        plugin._register_ipc_handlers()  # Should not raise
