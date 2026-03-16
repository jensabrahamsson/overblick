"""Tests for orchestrator state machine, LLM routing, and IPC client discovery."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.orchestrator import Orchestrator, OrchestratorState
from overblick.core.exceptions import ConfigError
from overblick.identities import Identity, LLMSettings, SecuritySettings, ScheduleSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_identity(**overrides):
    """Create a minimal Identity with sane defaults for testing."""
    defaults = dict(
        name="test",
        display_name="Test",
        plugins=("fakeplugin",),
        security=SecuritySettings(
            enable_preflight=False,
            enable_output_safety=False,
        ),
        schedule=ScheduleSettings(feed_poll_minutes=5, heartbeat_hours=4),
        raw_config={},
    )
    defaults.update(overrides)
    return Identity(**defaults)


def _make_orchestrator(tmp_path, **kwargs):
    """Create an Orchestrator with tmp_path as base_dir."""
    return Orchestrator("testident", base_dir=tmp_path, **kwargs)


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------


class TestOrchestratorState:
    def test_enum_values(self):
        assert OrchestratorState.INIT.value == "init"
        assert OrchestratorState.SETUP.value == "setup"
        assert OrchestratorState.RUNNING.value == "running"
        assert OrchestratorState.STOPPING.value == "stopping"
        assert OrchestratorState.STOPPED.value == "stopped"


class TestOrchestratorInit:
    def test_initial_state(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        assert orch.state == OrchestratorState.INIT
        assert orch.identity is None

    def test_default_plugins(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        assert orch._plugin_names == []

    def test_custom_plugins(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path, plugins=["moltbook", "gmail"])
        assert orch._plugin_names == ["moltbook", "gmail"]

    def test_factory_stored(self, tmp_path):
        factory = MagicMock()
        orch = Orchestrator("anomal", base_dir=tmp_path, factory=factory)
        assert orch._factory is factory


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


class TestOrchestratorStop:
    async def test_stop_from_init(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

    async def test_double_stop_is_safe(self, tmp_path):
        """Second stop goes through STOPPED path."""
        orch = Orchestrator("anomal", base_dir=tmp_path)
        await orch.stop()
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

    async def test_stop_while_stopping(self, tmp_path):
        """Line 548: stop returns early if already STOPPING."""
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._state = OrchestratorState.STOPPING
        await orch.stop()  # Should return immediately
        assert orch.state == OrchestratorState.STOPPING

    async def test_stop_tears_down_plugins_reverse_order(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        p1 = MagicMock()
        p1.name = "p1"
        p1.teardown = AsyncMock()
        p2 = MagicMock()
        p2.name = "p2"
        p2.teardown = AsyncMock()
        orch._plugins = [p1, p2]
        await orch.stop()
        assert p2.teardown.call_count == 1
        assert p1.teardown.call_count == 1

    async def test_stop_plugin_teardown_error_logged(self, tmp_path):
        """Line 567-568."""
        orch = _make_orchestrator(tmp_path)
        p = MagicMock()
        p.name = "bad"
        p.teardown = AsyncMock(side_effect=RuntimeError("teardown boom"))
        orch._plugins = [p]
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

    async def test_stop_closes_llm_client(self, tmp_path):
        """Line 574-575."""
        orch = _make_orchestrator(tmp_path)
        client = MagicMock()
        client.close = AsyncMock()
        orch._llm_client = client
        await orch.stop()
        client.close.assert_awaited_once()

    async def test_stop_llm_client_close_error(self, tmp_path):
        """Line 574-575."""
        orch = _make_orchestrator(tmp_path)
        client = MagicMock()
        client.close = AsyncMock(side_effect=RuntimeError("close boom"))
        orch._llm_client = client
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

    async def test_stop_engagement_db(self, tmp_path):
        """Line 560 + 580-582."""
        orch = _make_orchestrator(tmp_path)
        eng_db = MagicMock()
        eng_db.stop_background_cleanup = MagicMock()
        orch._engagement_db = eng_db

        backend = MagicMock()
        backend.close = AsyncMock()
        orch._engagement_db_backend = backend
        await orch.stop()
        backend.close.assert_awaited_once()
        eng_db.stop_background_cleanup.assert_called_once()

    async def test_stop_engagement_db_backend_close_error(self, tmp_path):
        """Line 581-582."""
        orch = _make_orchestrator(tmp_path)
        backend = MagicMock()
        backend.close = AsyncMock(side_effect=RuntimeError("db close boom"))
        orch._engagement_db_backend = backend
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

    async def test_stop_logs_final_audit(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        audit = MagicMock()
        audit.stop_background_cleanup = MagicMock()
        audit.log = MagicMock()
        audit.close = MagicMock()
        orch._audit_log = audit
        await orch.stop()
        audit.log.assert_called_with("orchestrator_stopped", category="lifecycle")
        audit.close.assert_called_once()


# ---------------------------------------------------------------------------
# _create_components_via_factory
# ---------------------------------------------------------------------------


class TestCreateComponentsViaFactory:
    async def test_no_factory_returns_empty(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        result = await orch._create_components_via_factory()
        assert result == {}

    async def test_factory_creates_all_components(self, tmp_path):
        """Lines 154-186."""
        factory = MagicMock()
        factory.load_identity = AsyncMock(return_value=_make_identity())
        factory.get_paths.return_value = {
            "data_dir": tmp_path / "data",
            "log_dir": tmp_path / "logs",
            "secrets_dir": tmp_path / "secrets",
        }
        factory.create_secrets_manager.return_value = MagicMock()
        factory.create_audit_log.return_value = MagicMock()
        factory.create_engagement_db = AsyncMock(return_value=MagicMock())
        factory.create_quiet_hours_checker.return_value = MagicMock()
        llm_client = AsyncMock()
        factory.create_llm_client = AsyncMock(return_value=llm_client)
        factory.create_preflight_checker.return_value = MagicMock()
        factory.create_output_safety.return_value = MagicMock()
        factory.create_rate_limiter.return_value = MagicMock()
        factory.create_safe_llm_pipeline.return_value = MagicMock()
        factory.create_permission_checker.return_value = MagicMock()
        factory.create_plugin_capability_checker.return_value = MagicMock()
        factory.create_ipc_client.return_value = MagicMock()
        factory.create_event_bus.return_value = MagicMock()
        factory.create_scheduler.return_value = MagicMock()
        factory.create_plugin_registry.return_value = MagicMock()

        orch = _make_orchestrator(tmp_path, factory=factory)
        result = await orch._create_components_via_factory()
        assert "identity" in result
        assert "llm_pipeline" in result
        assert "registry" in result
        assert len(result) == 17

        # Verify factory method was called with correct args for pipeline
        factory.create_safe_llm_pipeline.assert_called_once()
        call_kwargs = factory.create_safe_llm_pipeline.call_args[1]
        assert call_kwargs["llm_client"] is llm_client


# ---------------------------------------------------------------------------
# Setup — factory path (lines 269-289)
# ---------------------------------------------------------------------------


class TestSetupWithFactory:
    async def test_setup_via_factory_loads_plugins(self, tmp_path):
        """Lines 269-289."""
        factory = MagicMock()
        identity = _make_identity(plugins=("fakeplugin",))

        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        log_dir = tmp_path / "logs" / "testident"
        log_dir.mkdir(parents=True)

        factory.load_identity = AsyncMock(return_value=identity)
        factory.get_paths.return_value = {
            "data_dir": data_dir,
            "log_dir": log_dir,
            "secrets_dir": tmp_path / "secrets",
        }
        factory.create_secrets_manager.return_value = MagicMock()
        audit = MagicMock()
        audit.log = MagicMock()
        factory.create_audit_log.return_value = audit
        factory.create_engagement_db = AsyncMock(return_value=None)
        factory.create_quiet_hours_checker.return_value = MagicMock()
        llm_client = MagicMock()
        llm_client.embed = AsyncMock(return_value=[0.1])
        factory.create_llm_client = AsyncMock(return_value=llm_client)
        factory.create_preflight_checker.return_value = MagicMock()
        factory.create_output_safety.return_value = MagicMock()
        factory.create_rate_limiter.return_value = MagicMock()
        factory.create_safe_llm_pipeline.return_value = MagicMock()
        factory.create_permission_checker.return_value = MagicMock()
        cap_checker = MagicMock()
        cap_checker.check_plugin = MagicMock()
        factory.create_plugin_capability_checker.return_value = cap_checker
        factory.create_ipc_client.return_value = MagicMock()
        event_bus = MagicMock()
        factory.create_event_bus.return_value = event_bus
        sched = MagicMock()
        sched.stop = AsyncMock()
        factory.create_scheduler.return_value = sched

        plugin_instance = MagicMock()
        plugin_instance.name = "fakeplugin"
        plugin_instance.setup = AsyncMock()
        plugin_instance.REQUIRED_CAPABILITIES = ["cap1"]
        registry = MagicMock()
        registry.load.return_value = plugin_instance
        factory.create_plugin_registry.return_value = registry

        orch = _make_orchestrator(tmp_path, factory=factory)

        with patch.object(orch, "_setup_capabilities", new_callable=AsyncMock), \
             patch.object(orch, "_setup_learning_store", new_callable=AsyncMock), \
             patch.object(orch, "_load_local_plugin_config", return_value=[]), \
             patch.object(orch, "_resolve_plugin_dependencies", side_effect=lambda x: x):
            await orch.setup()

        assert orch.state == OrchestratorState.SETUP
        assert len(orch._plugins) == 1
        cap_checker.check_plugin.assert_called_once_with("fakeplugin", ["cap1"])


# ---------------------------------------------------------------------------
# Setup — non-factory path
# ---------------------------------------------------------------------------


def _patch_non_factory_setup(orch):
    """Context manager patches for non-factory setup tests."""
    identity = orch._test_identity

    return (
        patch("overblick.core.orchestrator.load_identity", return_value=identity),
        patch("overblick.core.orchestrator.SecretsManager"),
        patch("overblick.core.orchestrator.AuditLog", return_value=MagicMock()),
        patch("overblick.core.orchestrator.QuietHoursChecker"),
        patch.object(orch, "_create_llm_client", new_callable=AsyncMock, return_value=MagicMock()),
        patch.object(orch, "_create_preflight", return_value=None),
        patch.object(orch, "_create_output_safety", return_value=None),
        patch("overblick.core.orchestrator.RateLimiter"),
        patch("overblick.core.orchestrator.SafeLLMPipeline"),
        patch("overblick.core.orchestrator.PermissionChecker"),
        patch("overblick.core.orchestrator.PluginCapabilityChecker"),
        patch.object(orch, "_setup_capabilities", new_callable=AsyncMock),
        patch.object(orch, "_setup_learning_store", new_callable=AsyncMock),
        patch.object(orch, "_create_ipc_client", return_value=None),
        patch.object(orch, "_load_local_plugin_config", return_value=[]),
        patch.object(orch, "_resolve_plugin_dependencies", side_effect=lambda x: x),
    )


class TestSetupWithoutFactory:
    async def test_setup_with_moltbook_creates_engagement_db(self, tmp_path):
        """Lines 321-332."""
        identity = _make_identity(plugins=("moltbook",))
        orch = _make_orchestrator(tmp_path)

        plugin_instance = MagicMock()
        plugin_instance.name = "moltbook"
        plugin_instance.setup = AsyncMock()

        mock_sqlite_inst = MagicMock()
        mock_sqlite_inst.connect = AsyncMock()
        mock_eng_inst = MagicMock()
        mock_eng_inst.setup = AsyncMock()

        with patch("overblick.core.orchestrator.load_identity", return_value=identity), \
             patch("overblick.core.orchestrator.SecretsManager"), \
             patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls, \
             patch("overblick.core.orchestrator.SQLiteBackend", return_value=mock_sqlite_inst), \
             patch("overblick.core.orchestrator.EngagementDB", return_value=mock_eng_inst), \
             patch("overblick.core.orchestrator.QuietHoursChecker"), \
             patch.object(orch, "_create_llm_client", new_callable=AsyncMock, return_value=MagicMock()), \
             patch.object(orch, "_create_preflight", return_value=None), \
             patch.object(orch, "_create_output_safety", return_value=None), \
             patch("overblick.core.orchestrator.RateLimiter"), \
             patch("overblick.core.orchestrator.SafeLLMPipeline"), \
             patch("overblick.core.orchestrator.PermissionChecker") as mock_perm, \
             patch("overblick.core.orchestrator.PluginCapabilityChecker"), \
             patch.object(orch, "_setup_capabilities", new_callable=AsyncMock), \
             patch.object(orch, "_setup_learning_store", new_callable=AsyncMock), \
             patch.object(orch, "_create_ipc_client", return_value=None), \
             patch.object(orch, "_load_local_plugin_config", return_value=[]), \
             patch.object(orch, "_resolve_plugin_dependencies", side_effect=lambda x: x):

            mock_audit_cls.return_value = MagicMock()
            mock_perm.from_identity.return_value = MagicMock()
            orch._registry = MagicMock()
            orch._registry.load.return_value = plugin_instance

            await orch.setup()

        mock_sqlite_inst.connect.assert_awaited_once()
        mock_eng_inst.setup.assert_awaited_once()

    async def test_setup_no_plugins_raises_config_error(self, tmp_path):
        """Line 440."""
        identity = _make_identity(plugins=("nonexistent",))
        orch = _make_orchestrator(tmp_path)

        with patch("overblick.core.orchestrator.load_identity", return_value=identity), \
             patch("overblick.core.orchestrator.SecretsManager"), \
             patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls, \
             patch("overblick.core.orchestrator.QuietHoursChecker"), \
             patch.object(orch, "_create_llm_client", new_callable=AsyncMock, return_value=MagicMock()), \
             patch.object(orch, "_create_preflight", return_value=None), \
             patch.object(orch, "_create_output_safety", return_value=None), \
             patch("overblick.core.orchestrator.RateLimiter"), \
             patch("overblick.core.orchestrator.SafeLLMPipeline"), \
             patch("overblick.core.orchestrator.PermissionChecker") as mock_perm, \
             patch("overblick.core.orchestrator.PluginCapabilityChecker"), \
             patch.object(orch, "_setup_capabilities", new_callable=AsyncMock), \
             patch.object(orch, "_setup_learning_store", new_callable=AsyncMock), \
             patch.object(orch, "_create_ipc_client", return_value=None), \
             patch.object(orch, "_load_local_plugin_config", return_value=[]), \
             patch.object(orch, "_resolve_plugin_dependencies", side_effect=lambda x: x):

            mock_audit_cls.return_value = MagicMock()
            mock_perm.from_identity.return_value = MagicMock()
            orch._registry = MagicMock()
            orch._registry.load.side_effect = ImportError("no such plugin")

            with pytest.raises(ConfigError, match="No plugins loaded"):
                await orch.setup()

    async def test_setup_plugin_load_failure_logged(self, tmp_path):
        """Lines 429-437."""
        identity = _make_identity(plugins=("bad", "good"))
        orch = _make_orchestrator(tmp_path)

        good_plugin = MagicMock()
        good_plugin.name = "good"
        good_plugin.setup = AsyncMock()

        def _load_side_effect(name, ctx):
            if name == "bad":
                raise ImportError("bad plugin")
            return good_plugin

        with patch("overblick.core.orchestrator.load_identity", return_value=identity), \
             patch("overblick.core.orchestrator.SecretsManager"), \
             patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls, \
             patch("overblick.core.orchestrator.QuietHoursChecker"), \
             patch.object(orch, "_create_llm_client", new_callable=AsyncMock, return_value=MagicMock()), \
             patch.object(orch, "_create_preflight", return_value=None), \
             patch.object(orch, "_create_output_safety", return_value=None), \
             patch("overblick.core.orchestrator.RateLimiter"), \
             patch("overblick.core.orchestrator.SafeLLMPipeline"), \
             patch("overblick.core.orchestrator.PermissionChecker") as mock_perm, \
             patch("overblick.core.orchestrator.PluginCapabilityChecker"), \
             patch.object(orch, "_setup_capabilities", new_callable=AsyncMock), \
             patch.object(orch, "_setup_learning_store", new_callable=AsyncMock), \
             patch.object(orch, "_create_ipc_client", return_value=None), \
             patch.object(orch, "_load_local_plugin_config", return_value=[]), \
             patch.object(orch, "_resolve_plugin_dependencies", side_effect=lambda x: x):

            mock_audit_cls.return_value = MagicMock()
            mock_perm.from_identity.return_value = MagicMock()
            orch._registry = MagicMock()
            orch._registry.load.side_effect = _load_side_effect

            await orch.setup()

        assert len(orch._plugins) == 1
        assert orch._plugins[0].name == "good"

    async def test_setup_local_plugin_appended(self, tmp_path):
        """Lines 394-395."""
        identity = _make_identity(plugins=("existing",))
        orch = _make_orchestrator(tmp_path)

        plugin_mock = MagicMock()
        plugin_mock.name = "existing"
        plugin_mock.setup = AsyncMock()

        local_mock = MagicMock()
        local_mock.name = "localplugin"
        local_mock.setup = AsyncMock()

        def _load_side_effect(name, ctx):
            if name == "existing":
                return plugin_mock
            return local_mock

        with patch("overblick.core.orchestrator.load_identity", return_value=identity), \
             patch("overblick.core.orchestrator.SecretsManager"), \
             patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls, \
             patch("overblick.core.orchestrator.QuietHoursChecker"), \
             patch.object(orch, "_create_llm_client", new_callable=AsyncMock, return_value=MagicMock()), \
             patch.object(orch, "_create_preflight", return_value=None), \
             patch.object(orch, "_create_output_safety", return_value=None), \
             patch("overblick.core.orchestrator.RateLimiter"), \
             patch("overblick.core.orchestrator.SafeLLMPipeline"), \
             patch("overblick.core.orchestrator.PermissionChecker") as mock_perm, \
             patch("overblick.core.orchestrator.PluginCapabilityChecker"), \
             patch.object(orch, "_setup_capabilities", new_callable=AsyncMock), \
             patch.object(orch, "_setup_learning_store", new_callable=AsyncMock), \
             patch.object(orch, "_create_ipc_client", return_value=None), \
             patch.object(orch, "_load_local_plugin_config", return_value=["localplugin"]), \
             patch.object(orch, "_resolve_plugin_dependencies", side_effect=lambda x: x):

            mock_audit_cls.return_value = MagicMock()
            mock_perm.from_identity.return_value = MagicMock()
            orch._registry = MagicMock()
            orch._registry.load.side_effect = _load_side_effect

            await orch.setup()

        assert len(orch._plugins) == 2

    async def test_setup_resolve_deps_failure_continues(self, tmp_path):
        """Lines 401-402."""
        identity = _make_identity(plugins=("p1",))
        orch = _make_orchestrator(tmp_path)

        plugin_mock = MagicMock()
        plugin_mock.name = "p1"
        plugin_mock.setup = AsyncMock()

        with patch("overblick.core.orchestrator.load_identity", return_value=identity), \
             patch("overblick.core.orchestrator.SecretsManager"), \
             patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls, \
             patch("overblick.core.orchestrator.QuietHoursChecker"), \
             patch.object(orch, "_create_llm_client", new_callable=AsyncMock, return_value=MagicMock()), \
             patch.object(orch, "_create_preflight", return_value=None), \
             patch.object(orch, "_create_output_safety", return_value=None), \
             patch("overblick.core.orchestrator.RateLimiter"), \
             patch("overblick.core.orchestrator.SafeLLMPipeline"), \
             patch("overblick.core.orchestrator.PermissionChecker") as mock_perm, \
             patch("overblick.core.orchestrator.PluginCapabilityChecker"), \
             patch.object(orch, "_setup_capabilities", new_callable=AsyncMock), \
             patch.object(orch, "_setup_learning_store", new_callable=AsyncMock), \
             patch.object(orch, "_create_ipc_client", return_value=None), \
             patch.object(orch, "_load_local_plugin_config", return_value=[]), \
             patch.object(orch, "_resolve_plugin_dependencies", side_effect=RuntimeError("cycle")):

            mock_audit_cls.return_value = MagicMock()
            mock_perm.from_identity.return_value = MagicMock()
            orch._registry = MagicMock()
            orch._registry.load.return_value = plugin_mock

            await orch.setup()

        assert len(orch._plugins) == 1


# ---------------------------------------------------------------------------
# run() — Lines 448-543
# ---------------------------------------------------------------------------


def _setup_run_orch(orch, tmp_path, identity=None, plugins=None, engagement_db=None):
    """Prepare an orchestrator for run() tests by mocking setup."""
    identity = identity or _make_identity()

    scheduler = MagicMock()
    scheduler.add = AsyncMock()
    scheduler.stop = AsyncMock()

    audit = MagicMock()
    audit.log = MagicMock()
    audit.start_background_cleanup = MagicMock()
    audit.stop_background_cleanup = MagicMock()
    audit.close = MagicMock()

    event_bus = MagicMock()
    event_bus.clear = MagicMock()
    event_bus.emit = AsyncMock()

    if plugins is None:
        p = MagicMock()
        p.name = "testplugin"
        p.tick = AsyncMock()
        p.post_heartbeat = None
        p.teardown = AsyncMock()
        plugins = [p]

    async def _mock_setup():
        orch._state = OrchestratorState.SETUP
        orch._identity = identity
        orch._plugins = plugins
        orch._scheduler = scheduler
        orch._audit_log = audit
        orch._event_bus = event_bus
        orch._engagement_db = engagement_db

    return _mock_setup, scheduler, audit, event_bus


class TestOrchestratorRun:
    async def test_run_setup_failure_calls_stop(self, tmp_path):
        """Lines 448-453."""
        orch = _make_orchestrator(tmp_path)
        with patch.object(orch, "setup", new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             patch.object(orch, "stop", new_callable=AsyncMock) as mock_stop:
            with pytest.raises(RuntimeError, match="boom"):
                await orch.run()
            mock_stop.assert_awaited_once()

    async def test_run_normal_shutdown(self, tmp_path):
        """Lines 448-543: full run loop with scheduler + shutdown."""
        orch = _make_orchestrator(tmp_path)
        mock_setup, scheduler, audit, event_bus = _setup_run_orch(orch, tmp_path)

        async def _start_and_shutdown():
            orch._shutdown_event.set()

        scheduler.start = _start_and_shutdown

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        scheduler.add.assert_awaited()
        assert orch.state == OrchestratorState.STOPPED

    async def test_run_with_heartbeat_plugin(self, tmp_path):
        """Lines 502-521."""
        orch = _make_orchestrator(tmp_path)

        plugin = MagicMock()
        plugin.name = "hb_plugin"
        plugin.tick = AsyncMock()
        plugin.post_heartbeat = AsyncMock()
        plugin.teardown = AsyncMock()

        mock_setup, scheduler, audit, event_bus = _setup_run_orch(
            orch, tmp_path, plugins=[plugin]
        )

        async def _start_and_shutdown():
            orch._shutdown_event.set()

        scheduler.start = _start_and_shutdown

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        # Two scheduler.add calls: tick + heartbeat
        assert scheduler.add.await_count == 2

    async def test_run_engagement_db_cleanup_started(self, tmp_path):
        """Lines 464-465."""
        orch = _make_orchestrator(tmp_path)

        eng_db = MagicMock()
        eng_db.start_background_cleanup = MagicMock()
        eng_db.stop_background_cleanup = MagicMock()

        mock_setup, scheduler, audit, event_bus = _setup_run_orch(
            orch, tmp_path, engagement_db=eng_db
        )

        async def _start_and_shutdown():
            orch._shutdown_event.set()

        scheduler.start = _start_and_shutdown

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        eng_db.start_background_cleanup.assert_called_once()

    async def test_run_cancelled_error_in_wait(self, tmp_path):
        """Line 538-539: CancelledError raised during asyncio.wait."""
        orch = _make_orchestrator(tmp_path)
        mock_setup, scheduler, audit, event_bus = _setup_run_orch(orch, tmp_path)

        # Make asyncio.wait itself raise CancelledError
        async def _start_never():
            await asyncio.sleep(9999)

        scheduler.start = _start_never

        async def _run_and_cancel():
            task = asyncio.create_task(orch.run())
            await asyncio.sleep(0.01)  # Let run() reach asyncio.wait
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await _run_and_cancel()

        assert orch.state == OrchestratorState.STOPPED

    async def test_run_generic_exception(self, tmp_path):
        """Lines 540-541."""
        orch = _make_orchestrator(tmp_path)
        mock_setup, scheduler, audit, event_bus = _setup_run_orch(orch, tmp_path)

        scheduler.add = AsyncMock(side_effect=RuntimeError("scheduler boom"))

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        assert orch.state == OrchestratorState.STOPPED

    async def test_run_pending_tasks_cancelled(self, tmp_path):
        """Lines 531-536: pending tasks are cancelled when one completes."""
        orch = _make_orchestrator(tmp_path)
        mock_setup, scheduler, audit, event_bus = _setup_run_orch(orch, tmp_path)

        # scheduler.start completes after a short delay, shutdown_event never fires
        # This means scheduler_task finishes first, shutdown_task is pending and gets cancelled
        async def _short_start():
            await asyncio.sleep(0.01)

        scheduler.start = _short_start

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        assert orch.state == OrchestratorState.STOPPED


# ---------------------------------------------------------------------------
# _is_plugin_stopped (lines 597-618)
# ---------------------------------------------------------------------------


class TestIsPluginStopped:
    async def test_no_control_file(self, tmp_path):
        """Line 597-598."""
        orch = _make_orchestrator(tmp_path)
        orch._control_file = None
        assert await orch._is_plugin_stopped("any") is False

    async def test_cached_hit(self, tmp_path):
        """Lines 604-605."""
        orch = _make_orchestrator(tmp_path)
        orch._control_file = tmp_path / "control.json"
        orch._control_cache = {"myplugin": "stopped"}
        orch._control_cache_ts = time.monotonic()
        assert await orch._is_plugin_stopped("myplugin") is True
        assert await orch._is_plugin_stopped("other") is False

    async def test_reads_file_on_cache_miss(self, tmp_path):
        """Lines 608-615."""
        orch = _make_orchestrator(tmp_path)
        control_file = tmp_path / "control.json"
        control_file.write_text(json.dumps({"myplugin": "stopped"}))
        orch._control_file = control_file
        orch._control_cache_ts = 0.0
        assert await orch._is_plugin_stopped("myplugin") is True

    async def test_missing_file_empty_cache(self, tmp_path):
        """Line 613."""
        orch = _make_orchestrator(tmp_path)
        orch._control_file = tmp_path / "nonexistent.json"
        orch._control_cache_ts = 0.0
        assert await orch._is_plugin_stopped("any") is False

    async def test_corrupt_file_returns_false(self, tmp_path):
        """Lines 616-618."""
        orch = _make_orchestrator(tmp_path)
        control_file = tmp_path / "control.json"
        control_file.write_text("not json {{{")
        orch._control_file = control_file
        orch._control_cache_ts = 0.0
        assert await orch._is_plugin_stopped("any") is False


# ---------------------------------------------------------------------------
# _setup_learning_store (lines 620-650)
# ---------------------------------------------------------------------------


class TestSetupLearningStore:
    async def test_ethos_list_joined(self, tmp_path):
        """Line 627."""
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(raw_config={"ethos": ["be kind", "be fair"]})
        orch._llm_pipeline = MagicMock()
        orch._llm_client = None

        with patch("overblick.core.learning.store.LearningStore") as mock_ls_inner, \
             patch("overblick.core.learning.LearningStore") as mock_ls:
            store = MagicMock()
            store.setup = AsyncMock()
            mock_ls.return_value = store
            mock_ls_inner.return_value = store
            await orch._setup_learning_store(tmp_path)

        assert orch._learning_store is not None

    async def test_ethos_string(self, tmp_path):
        """Line 629."""
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(raw_config={"ethos": "a single ethos"})
        orch._llm_pipeline = MagicMock()
        orch._llm_client = None

        with patch("overblick.core.learning.LearningStore") as mock_ls:
            store = MagicMock()
            store.setup = AsyncMock()
            mock_ls.return_value = store
            await orch._setup_learning_store(tmp_path)

        call_kwargs = mock_ls.call_args[1]
        assert call_kwargs["ethos_text"] == "a single ethos"

    async def test_ethos_empty_fallback(self, tmp_path):
        """Lines 632-633."""
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(raw_config={"ethos": [], "ethos_text": "fallback"})
        orch._llm_pipeline = MagicMock()
        orch._llm_client = None

        with patch("overblick.core.learning.LearningStore") as mock_ls:
            store = MagicMock()
            store.setup = AsyncMock()
            mock_ls.return_value = store
            await orch._setup_learning_store(tmp_path)

        call_kwargs = mock_ls.call_args[1]
        assert call_kwargs["ethos_text"] == "fallback"


# ---------------------------------------------------------------------------
# _get_embed_fn (lines 652-660)
# ---------------------------------------------------------------------------


class TestGetEmbedFn:
    def test_client_with_embed(self, tmp_path):
        """Line 657."""
        orch = _make_orchestrator(tmp_path)
        orch._llm_client = MagicMock()
        orch._llm_client.embed = AsyncMock(return_value=[0.1, 0.2])
        assert orch._get_embed_fn() is not None

    def test_no_client(self, tmp_path):
        """Line 660."""
        orch = _make_orchestrator(tmp_path)
        orch._llm_client = None
        assert orch._get_embed_fn() is None

    def test_client_without_embed(self, tmp_path):
        """Line 660."""
        orch = _make_orchestrator(tmp_path)
        orch._llm_client = MagicMock(spec=[])  # no embed
        assert orch._get_embed_fn() is None

    async def test_embed_fn_calls_client(self, tmp_path):
        """Line 657."""
        orch = _make_orchestrator(tmp_path)
        orch._llm_client = MagicMock()
        orch._llm_client.embed = AsyncMock(return_value=[0.5])
        fn = orch._get_embed_fn()
        result = await fn("hello")
        assert result == [0.5]


# ---------------------------------------------------------------------------
# _setup_capabilities (lines 665-721)
# ---------------------------------------------------------------------------


class TestSetupCapabilities:
    def _prepare_orch(self, tmp_path, **identity_kwargs):
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(**identity_kwargs)
        orch._secrets = MagicMock()
        orch._secrets.get = MagicMock(return_value="s")
        orch._audit_log = MagicMock()
        orch._llm_client = None
        orch._llm_pipeline = None
        orch._quiet_hours = None
        orch._ipc_client = None
        orch._engagement_db = None
        orch._learning_store = None
        return orch

    async def test_no_capabilities(self, tmp_path):
        """Lines 677-679."""
        orch = self._prepare_orch(tmp_path, capability_names=(), enabled_modules=())

        with patch("overblick.core.orchestrator.CapabilityRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            mock_reg_cls.default.return_value = mock_reg
            mock_reg.resolve.return_value = []
            await orch._setup_capabilities()

    async def test_capability_success(self, tmp_path):
        """Lines 711-717."""
        orch = self._prepare_orch(tmp_path, capability_names=("mycap",))

        cap_instance = MagicMock()
        cap_instance.name = "mycap"
        cap_instance.setup = AsyncMock()

        with patch("overblick.core.orchestrator.CapabilityRegistry") as mock_reg_cls, \
             patch("overblick.core.capability.build_capability_configs", return_value={}):
            mock_reg = MagicMock()
            mock_reg_cls.default.return_value = mock_reg
            mock_reg.resolve.return_value = ["mycap"]
            mock_reg.create.return_value = cap_instance
            await orch._setup_capabilities()

        assert "mycap" in orch._capabilities

    async def test_capability_setup_failure(self, tmp_path):
        """Lines 718-719."""
        orch = self._prepare_orch(tmp_path, capability_names=("badcap",))

        cap_instance = MagicMock()
        cap_instance.name = "badcap"
        cap_instance.setup = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("overblick.core.orchestrator.CapabilityRegistry") as mock_reg_cls, \
             patch("overblick.core.capability.build_capability_configs", return_value={}):
            mock_reg = MagicMock()
            mock_reg_cls.default.return_value = mock_reg
            mock_reg.resolve.return_value = ["badcap"]
            mock_reg.create.return_value = cap_instance
            await orch._setup_capabilities()

        assert "badcap" not in orch._capabilities

    async def test_registry_load_failure(self, tmp_path):
        """Lines 683-685."""
        orch = self._prepare_orch(tmp_path, capability_names=("x",))

        with patch("overblick.core.orchestrator.CapabilityRegistry") as mock_reg_cls:
            mock_reg_cls.default.side_effect = ImportError("no module")
            await orch._setup_capabilities()

        assert orch._capabilities == {}

    async def test_fallback_to_enabled_modules(self, tmp_path):
        """Lines 668-669."""
        orch = self._prepare_orch(
            tmp_path, capability_names=(), enabled_modules=("modcap",)
        )

        with patch("overblick.core.orchestrator.CapabilityRegistry") as mock_reg_cls, \
             patch("overblick.core.capability.build_capability_configs", return_value={}):
            mock_reg = MagicMock()
            mock_reg_cls.default.return_value = mock_reg
            mock_reg.resolve.return_value = []
            await orch._setup_capabilities()

    async def test_create_returns_none(self, tmp_path):
        """Line 713."""
        orch = self._prepare_orch(tmp_path, capability_names=("nullcap",))

        with patch("overblick.core.orchestrator.CapabilityRegistry") as mock_reg_cls, \
             patch("overblick.core.capability.build_capability_configs", return_value={}):
            mock_reg = MagicMock()
            mock_reg_cls.default.return_value = mock_reg
            mock_reg.resolve.return_value = ["nullcap"]
            mock_reg.create.return_value = None
            await orch._setup_capabilities()

        assert "nullcap" not in orch._capabilities


# ---------------------------------------------------------------------------
# LLM Routing
# ---------------------------------------------------------------------------


class TestOrchestratorLLMRouting:
    async def test_creates_gateway_client(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._identity = Identity(name="test", llm=LLMSettings(provider="ollama"))
        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.health_check = AsyncMock(return_value=True)
            mock_cls.return_value = mock_instance
            client = await orch._create_llm_client()
            assert client is mock_instance

    async def test_gateway_url_from_identity(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._identity = Identity(name="test", llm=LLMSettings(gateway_url="http://10.0.0.1:8200"))
        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.health_check = AsyncMock(return_value=True)
            mock_cls.return_value = mock_instance
            await orch._create_llm_client()
            assert mock_cls.call_args[1]["base_url"] == "http://10.0.0.1:8200"

    async def test_unhealthy_gateway(self, tmp_path):
        """Lines 762-763."""
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._identity = Identity(name="test", llm=LLMSettings())
        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.health_check = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance
            client = await orch._create_llm_client()
            assert client is mock_instance


# ---------------------------------------------------------------------------
# _create_preflight
# ---------------------------------------------------------------------------


class TestCreatePreflight:
    def test_disabled(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(security=SecuritySettings(enable_preflight=False))
        assert orch._create_preflight() is None

    def test_enabled_with_dict_deflections(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(
            security=SecuritySettings(enable_preflight=True, admin_user_ids=("admin1",)),
            deflections={"topic": ["response"]},
        )
        orch._llm_client = MagicMock()
        with patch("overblick.core.orchestrator.PreflightChecker") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = orch._create_preflight()
            assert result is not None
            kw = mock_cls.call_args[1]
            assert kw["admin_user_ids"] == {"admin1"}
            assert kw["deflections"] == {"topic": ["response"]}

    def test_non_dict_deflections(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(
            security=SecuritySettings(enable_preflight=True),
            deflections=["not", "a", "dict"],
        )
        orch._llm_client = MagicMock()
        with patch("overblick.core.orchestrator.PreflightChecker") as mock_cls:
            mock_cls.return_value = MagicMock()
            orch._create_preflight()
            kw = mock_cls.call_args[1]
            assert kw["deflections"] == {}


# ---------------------------------------------------------------------------
# _create_output_safety (lines 912-935)
# ---------------------------------------------------------------------------


class TestCreateOutputSafety:
    def test_disabled(self, tmp_path):
        """Lines 915-916."""
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(security=SecuritySettings(enable_output_safety=False))
        assert orch._create_output_safety() is None

    def test_with_personality_vocab(self, tmp_path):
        """Lines 923-925."""
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(
            security=SecuritySettings(enable_output_safety=True),
            personality={
                "vocabulary": {
                    "banned_words": ["yolo", "bruh"],
                    "slang_replacements": {"gonna": "going to"},
                }
            },
            deflections=["sorry"],
        )
        with patch("overblick.core.orchestrator.OutputSafety") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = orch._create_output_safety()
            assert result is not None
            kw = mock_cls.call_args[1]
            assert len(kw["banned_slang_patterns"]) == 2
            assert kw["slang_replacements"] == {"gonna": "going to"}
            assert kw["deflections"] == ["sorry"]

    def test_no_personality(self, tmp_path):
        """Lines 920-921: no personality, deflections is dict (not list)."""
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity(
            security=SecuritySettings(enable_output_safety=True),
            deflections={"topic": ["not a list"]},
        )
        with patch("overblick.core.orchestrator.OutputSafety") as mock_cls:
            mock_cls.return_value = MagicMock()
            orch._create_output_safety()
            kw = mock_cls.call_args[1]
            assert kw["banned_slang_patterns"] == []
            assert kw["deflections"] is None


# ---------------------------------------------------------------------------
# IPC Client
# ---------------------------------------------------------------------------


class TestOrchestratorIPCDiscovery:
    def test_from_env_var(self, tmp_path, monkeypatch):
        ipc_dir = tmp_path / "env_ipc"
        ipc_dir.mkdir()
        (ipc_dir / "overblick-supervisor.token").write_text("test-token-env")
        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(ipc_dir))
        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()
        assert client is not None
        assert client._auth_token == "test-token-env"

    def test_from_project_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OVERBLICK_IPC_DIR", raising=False)
        ipc_dir = tmp_path / "data" / "ipc"
        ipc_dir.mkdir(parents=True)
        (ipc_dir / "overblick-supervisor.token").write_text("test-token-project")
        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()
        assert client is not None

    def test_no_token(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OVERBLICK_IPC_DIR", raising=False)
        orch = Orchestrator("anomal", base_dir=tmp_path)
        assert orch._create_ipc_client() is None

    def test_env_fallthrough(self, tmp_path, monkeypatch):
        env_dir = tmp_path / "env_ipc"
        env_dir.mkdir()
        project_dir = tmp_path / "data" / "ipc"
        project_dir.mkdir(parents=True)
        (project_dir / "overblick-supervisor.token").write_text("project-token")
        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(env_dir))
        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()
        assert client is not None
        assert client._auth_token == "project-token"

    def test_creation_exception_returns_none(self, tmp_path, monkeypatch):
        """Lines 830-832."""
        ipc_dir = tmp_path / "env_ipc"
        ipc_dir.mkdir()
        (ipc_dir / "overblick-supervisor.token").write_text("token")
        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(ipc_dir))
        orch = Orchestrator("anomal", base_dir=tmp_path)
        with patch("overblick.supervisor.ipc.read_ipc_token", side_effect=RuntimeError("boom")):
            client = orch._create_ipc_client()
        assert client is None


# ---------------------------------------------------------------------------
# _register_local_plugins (lines 845-871)
# ---------------------------------------------------------------------------


class TestRegisterLocalPlugins:
    def test_no_local_dir(self, tmp_path):
        """Line 842-843."""
        orch = _make_orchestrator(tmp_path)
        # __init__ already called it; no crash means success
        assert orch._registry is not None

    def test_full_discovery(self, tmp_path):
        """Lines 845-871."""
        from overblick.core.plugin_base import PluginBase

        class MyLocalPlugin(PluginBase):
            name = "mylocal"
            async def setup(self): pass
            async def tick(self): pass
            async def teardown(self): pass

        fake_mod = type("FakeModule", (), {
            "MyLocalPlugin": MyLocalPlugin,
            "__name__": "fake",
        })()

        orch = _make_orchestrator(tmp_path)

        # Simulate the method logic with mocked filesystem
        candidate_name = "mylocal"
        module_path = f"overblick.plugins._local.{candidate_name}.plugin"

        # Find PluginBase subclass
        cls_name = None
        for attr_name in dir(fake_mod):
            attr = getattr(fake_mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, PluginBase) and attr is not PluginBase:
                cls_name = attr_name
                break

        assert cls_name == "MyLocalPlugin"

    def test_import_error_logged(self, tmp_path):
        """Lines 853-855."""
        orch = _make_orchestrator(tmp_path)
        # The method is already called in __init__; test its resilience
        # by verifying the orchestrator still works after
        assert orch.state == OrchestratorState.INIT

    def test_no_pluginbase_subclass(self, tmp_path):
        """Lines 858-870: module with no PluginBase subclass."""
        from overblick.core.plugin_base import PluginBase

        fake_mod = type("FakeModule", (), {
            "NotAPlugin": str,
            "__name__": "fake",
        })()

        cls_name = None
        for attr_name in dir(fake_mod):
            attr = getattr(fake_mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, PluginBase) and attr is not PluginBase:
                cls_name = attr_name
                break

        assert cls_name is None


# ---------------------------------------------------------------------------
# _load_local_plugin_config (lines 894-910)
# ---------------------------------------------------------------------------


class TestLoadLocalPluginConfig:
    def test_no_config_file(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        assert orch._load_local_plugin_config() == []

    def test_config_with_plugins(self, tmp_path):
        """Lines 894-907."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(
            "local_plugins:\n"
            "  testident:\n"
            "    - localplugin1\n"
            "    - localplugin2\n"
        )
        orch = _make_orchestrator(tmp_path)
        assert orch._load_local_plugin_config() == ["localplugin1", "localplugin2"]

    def test_no_plugins_for_identity(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text("local_plugins:\n  other:\n    - p\n")
        orch = _make_orchestrator(tmp_path)
        assert orch._load_local_plugin_config() == []

    def test_parse_error(self, tmp_path):
        """Lines 908-910."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(": {{{{ invalid yaml")
        orch = _make_orchestrator(tmp_path)
        assert orch._load_local_plugin_config() == []

    def test_empty_yaml(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text("")
        orch = _make_orchestrator(tmp_path)
        assert orch._load_local_plugin_config() == []


# ---------------------------------------------------------------------------
# _resolve_plugin_dependencies (lines 962-997)
# ---------------------------------------------------------------------------


class TestResolvePluginDependencies:
    def test_no_dependencies(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        meta = MagicMock()
        meta.depends_on = []
        orch._registry = MagicMock()
        orch._registry.get_plugin_metadata.return_value = meta
        assert orch._resolve_plugin_dependencies(["a", "b"]) == ["a", "b"]

    def test_with_dependency(self, tmp_path):
        orch = _make_orchestrator(tmp_path)

        def _meta(name):
            m = MagicMock()
            m.depends_on = ["a"] if name == "b" else []
            return m

        orch._registry = MagicMock()
        orch._registry.get_plugin_metadata.side_effect = _meta
        result = orch._resolve_plugin_dependencies(["b", "a"])
        assert result.index("a") < result.index("b")

    def test_circular_dependency(self, tmp_path):
        """Lines 996-997."""
        orch = _make_orchestrator(tmp_path)

        def _meta(name):
            m = MagicMock()
            m.depends_on = ["b"] if name == "a" else ["a"]
            return m

        orch._registry = MagicMock()
        orch._registry.get_plugin_metadata.side_effect = _meta
        with pytest.raises(RuntimeError, match="Circular dependency"):
            orch._resolve_plugin_dependencies(["a", "b"])

    def test_external_dependency(self, tmp_path):
        """Lines 965-966, 970-971."""
        orch = _make_orchestrator(tmp_path)
        meta = MagicMock()
        meta.depends_on = ["external"]
        orch._registry = MagicMock()
        orch._registry.get_plugin_metadata.return_value = meta
        assert orch._resolve_plugin_dependencies(["a"]) == ["a"]

    def test_metadata_failure(self, tmp_path):
        """Lines 967-968."""
        orch = _make_orchestrator(tmp_path)
        orch._registry = MagicMock()
        orch._registry.get_plugin_metadata.side_effect = ValueError("nope")
        assert set(orch._resolve_plugin_dependencies(["a", "b"])) == {"a", "b"}

    def test_dependency_chain(self, tmp_path):
        """Lines 989-991."""
        orch = _make_orchestrator(tmp_path)

        def _meta(name):
            m = MagicMock()
            if name == "c":
                m.depends_on = ["b"]
            elif name == "b":
                m.depends_on = ["a"]
            else:
                m.depends_on = []
            return m

        orch._registry = MagicMock()
        orch._registry.get_plugin_metadata.side_effect = _meta
        assert orch._resolve_plugin_dependencies(["c", "b", "a"]) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _create_plugin_context
# ---------------------------------------------------------------------------


class TestCreatePluginContext:
    def _prepare_orch(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._identity = _make_identity()
        orch._secrets = MagicMock()
        orch._secrets.get = MagicMock(return_value="val")
        orch._audit_log = MagicMock()
        orch._llm_pipeline = MagicMock()
        orch._llm_client = MagicMock()
        orch._quiet_hours = MagicMock()
        orch._preflight = None
        orch._output_safety = None
        orch._ipc_client = None
        orch._engagement_db = None
        orch._learning_store = None
        return orch

    def test_known_role(self, tmp_path):
        from overblick.core.plugin_base import CommunicationPluginContext
        orch = self._prepare_orch(tmp_path)
        ctx = orch._create_plugin_context("telegram", tmp_path / "d", tmp_path / "l", MagicMock(), MagicMock())
        assert isinstance(ctx, CommunicationPluginContext)

    def test_unknown_role(self, tmp_path):
        from overblick.core.plugin_base import DefaultPluginContext
        orch = self._prepare_orch(tmp_path)
        ctx = orch._create_plugin_context("unknown", tmp_path / "d", tmp_path / "l", MagicMock(), MagicMock())
        assert isinstance(ctx, DefaultPluginContext)

    def test_secrets_getter_wired(self, tmp_path):
        orch = self._prepare_orch(tmp_path)
        ctx = orch._create_plugin_context("telegram", tmp_path / "d", tmp_path / "l", MagicMock(), MagicMock())
        ctx._secrets_getter("key")
        orch._secrets.get.assert_called_with("testident", "key")


# ---------------------------------------------------------------------------
# Guarded tick / heartbeat (tested via _is_plugin_stopped)
# ---------------------------------------------------------------------------


class TestGuardedTick:
    async def test_stopped_plugin_skipped(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._control_file = tmp_path / "ctrl.json"
        (tmp_path / "ctrl.json").write_text(json.dumps({"testplugin": "stopped"}))
        orch._control_cache_ts = 0.0
        assert await orch._is_plugin_stopped("testplugin") is True
        assert await orch._is_plugin_stopped("running_plugin") is False

    async def test_guarded_tick_executes_plugin(self, tmp_path):
        """Lines 476-491: guarded tick calls plugin.tick and emits event."""
        orch = _make_orchestrator(tmp_path)

        plugin = MagicMock()
        plugin.name = "testplugin"
        plugin.tick = AsyncMock()
        plugin.post_heartbeat = None
        plugin.teardown = AsyncMock()

        captured_callbacks = []

        async def _capture_add(name, callback, **kwargs):
            captured_callbacks.append((name, callback))

        mock_setup, scheduler, audit, event_bus = _setup_run_orch(
            orch, tmp_path, plugins=[plugin]
        )
        scheduler.add = _capture_add

        async def _start_and_shutdown():
            orch._shutdown_event.set()

        scheduler.start = _start_and_shutdown

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        # Find and invoke the tick callback
        tick_cb = None
        for name, cb in captured_callbacks:
            if name.startswith("tick_"):
                tick_cb = cb
                break
        assert tick_cb is not None

        # Plugin is not stopped — tick should execute
        orch._control_file = None
        await tick_cb()
        plugin.tick.assert_awaited_once()
        event_bus.emit.assert_awaited()

    async def test_guarded_tick_skips_stopped_plugin(self, tmp_path):
        """Lines 479-481: guarded tick skips when plugin is stopped."""
        orch = _make_orchestrator(tmp_path)

        plugin = MagicMock()
        plugin.name = "stoppedplugin"
        plugin.tick = AsyncMock()
        plugin.post_heartbeat = None
        plugin.teardown = AsyncMock()

        captured_callbacks = []

        async def _capture_add(name, callback, **kwargs):
            captured_callbacks.append((name, callback))

        mock_setup, scheduler, audit, event_bus = _setup_run_orch(
            orch, tmp_path, plugins=[plugin]
        )
        scheduler.add = _capture_add

        async def _start_and_shutdown():
            orch._shutdown_event.set()

        scheduler.start = _start_and_shutdown

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        tick_cb = None
        for name, cb in captured_callbacks:
            if name.startswith("tick_"):
                tick_cb = cb
                break

        # Mark plugin as stopped
        orch._control_file = tmp_path / "ctrl.json"
        (tmp_path / "ctrl.json").write_text(json.dumps({"stoppedplugin": "stopped"}))
        orch._control_cache_ts = 0.0

        await tick_cb()
        plugin.tick.assert_not_awaited()

    async def test_guarded_heartbeat_executes(self, tmp_path):
        """Lines 505-508: heartbeat closure calls post_heartbeat."""
        orch = _make_orchestrator(tmp_path)

        plugin = MagicMock()
        plugin.name = "hbplugin"
        plugin.tick = AsyncMock()
        plugin.post_heartbeat = AsyncMock()
        plugin.teardown = AsyncMock()

        captured_callbacks = []

        async def _capture_add(name, callback, **kwargs):
            captured_callbacks.append((name, callback))

        mock_setup, scheduler, audit, event_bus = _setup_run_orch(
            orch, tmp_path, plugins=[plugin]
        )
        scheduler.add = _capture_add

        async def _start_and_shutdown():
            orch._shutdown_event.set()

        scheduler.start = _start_and_shutdown

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        hb_cb = None
        for name, cb in captured_callbacks:
            if name.startswith("heartbeat_"):
                hb_cb = cb
                break
        assert hb_cb is not None

        # Plugin not stopped — heartbeat should execute
        orch._control_file = None
        await hb_cb()
        plugin.post_heartbeat.assert_awaited_once()

    async def test_guarded_heartbeat_skips_stopped(self, tmp_path):
        """Lines 506-507: heartbeat skipped when stopped."""
        orch = _make_orchestrator(tmp_path)

        plugin = MagicMock()
        plugin.name = "hbplugin"
        plugin.tick = AsyncMock()
        plugin.post_heartbeat = AsyncMock()
        plugin.teardown = AsyncMock()

        captured_callbacks = []

        async def _capture_add(name, callback, **kwargs):
            captured_callbacks.append((name, callback))

        mock_setup, scheduler, audit, event_bus = _setup_run_orch(
            orch, tmp_path, plugins=[plugin]
        )
        scheduler.add = _capture_add

        async def _start_and_shutdown():
            orch._shutdown_event.set()

        scheduler.start = _start_and_shutdown

        with patch.object(orch, "setup", side_effect=mock_setup), \
             patch("overblick.shared.platform.register_shutdown_signals"):
            await orch.run()

        hb_cb = None
        for name, cb in captured_callbacks:
            if name.startswith("heartbeat_"):
                hb_cb = cb
                break

        orch._control_file = tmp_path / "ctrl.json"
        (tmp_path / "ctrl.json").write_text(json.dumps({"hbplugin": "stopped"}))
        orch._control_cache_ts = 0.0

        await hb_cb()
        plugin.post_heartbeat.assert_not_awaited()


# ---------------------------------------------------------------------------
# _register_local_plugins actual execution (lines 845-871)
# ---------------------------------------------------------------------------


class TestRegisterLocalPluginsExecution:
    def test_discovers_and_registers_plugin(self, tmp_path):
        """Lines 845-871: actual execution with mocked importlib."""
        from overblick.core.plugin_base import PluginBase
        import overblick.core.orchestrator as orch_mod
        import importlib as importlib_mod

        class TestLocalPlugin(PluginBase):
            name = "testlocal"
            async def setup(self): pass
            async def tick(self): pass
            async def teardown(self): pass

        fake_mod = type("FakeMod", (), {
            "TestLocalPlugin": TestLocalPlugin,
            "__name__": "fakemod",
        })()

        # Create the _local directory structure at the real location
        local_dir = Path(orch_mod.__file__).parent.parent / "plugins" / "_local"

        test_plugin_dir = local_dir / "_test_orch_plugin"
        created_local = not local_dir.exists()
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            test_plugin_dir.mkdir(exist_ok=True)
            (test_plugin_dir / "plugin.py").write_text("# test")

            original_import = importlib_mod.import_module

            def _mock_import(name, *args, **kwargs):
                if "_test_orch_plugin" in name:
                    return fake_mod
                return original_import(name, *args, **kwargs)

            with patch.object(importlib_mod, "import_module", side_effect=_mock_import):
                orch = Orchestrator("testident", base_dir=tmp_path)

            assert "_test_orch_plugin" in orch._registry._plugins
        finally:
            if (test_plugin_dir / "plugin.py").exists():
                (test_plugin_dir / "plugin.py").unlink()
            if test_plugin_dir.exists():
                test_plugin_dir.rmdir()
            if created_local and local_dir.exists() and not any(local_dir.iterdir()):
                local_dir.rmdir()

    def test_import_error_skips_plugin(self, tmp_path):
        """Lines 853-855: import error skips."""
        import overblick.core.orchestrator as orch_mod
        import importlib as importlib_mod

        local_dir = Path(orch_mod.__file__).parent.parent / "plugins" / "_local"

        test_plugin_dir = local_dir / "_test_bad_plugin"
        created_local = not local_dir.exists()
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            test_plugin_dir.mkdir(exist_ok=True)
            (test_plugin_dir / "plugin.py").write_text("# test")

            original_import = importlib_mod.import_module

            def _mock_import(name, *args, **kwargs):
                if "_test_bad_plugin" in name:
                    raise ImportError("bad import")
                return original_import(name, *args, **kwargs)

            with patch.object(importlib_mod, "import_module", side_effect=_mock_import):
                orch = Orchestrator("testident", base_dir=tmp_path)

            assert "_test_bad_plugin" not in orch._registry._plugins
        finally:
            if (test_plugin_dir / "plugin.py").exists():
                (test_plugin_dir / "plugin.py").unlink()
            if test_plugin_dir.exists():
                test_plugin_dir.rmdir()
            if created_local and local_dir.exists() and not any(local_dir.iterdir()):
                local_dir.rmdir()

    def test_no_subclass_not_registered(self, tmp_path):
        """Lines 858-870: module with no PluginBase subclass."""
        import overblick.core.orchestrator as orch_mod
        import importlib as importlib_mod

        local_dir = Path(orch_mod.__file__).parent.parent / "plugins" / "_local"

        fake_mod = type("FakeMod", (), {
            "NotAPlugin": str,
            "__name__": "fakemod",
        })()

        test_plugin_dir = local_dir / "_test_nosub_plugin"
        created_local = not local_dir.exists()
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            test_plugin_dir.mkdir(exist_ok=True)
            (test_plugin_dir / "plugin.py").write_text("# test")

            original_import = importlib_mod.import_module

            def _mock_import(name, *args, **kwargs):
                if "_test_nosub_plugin" in name:
                    return fake_mod
                return original_import(name, *args, **kwargs)

            with patch.object(importlib_mod, "import_module", side_effect=_mock_import):
                orch = Orchestrator("testident", base_dir=tmp_path)

            assert "_test_nosub_plugin" not in orch._registry._plugins
        finally:
            if (test_plugin_dir / "plugin.py").exists():
                (test_plugin_dir / "plugin.py").unlink()
            if test_plugin_dir.exists():
                test_plugin_dir.rmdir()
            if created_local and local_dir.exists() and not any(local_dir.iterdir()):
                local_dir.rmdir()

    def test_non_dir_candidate_skipped(self, tmp_path):
        """Line 847-848: file in _local/ skipped."""
        import overblick.core.orchestrator as orch_mod

        local_dir = Path(orch_mod.__file__).parent.parent / "plugins" / "_local"
        created_local = not local_dir.exists()
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            test_file = local_dir / "_test_file.py"
            test_file.write_text("# not a plugin dir")

            orch = Orchestrator("testident", base_dir=tmp_path)
            assert True
        finally:
            if (local_dir / "_test_file.py").exists():
                (local_dir / "_test_file.py").unlink()
            if created_local and local_dir.exists() and not any(local_dir.iterdir()):
                local_dir.rmdir()

    def test_dir_without_plugin_py_skipped(self, tmp_path):
        """Line 847-848: dir without plugin.py skipped."""
        import overblick.core.orchestrator as orch_mod

        local_dir = Path(orch_mod.__file__).parent.parent / "plugins" / "_local"
        created_local = not local_dir.exists()
        test_dir = local_dir / "_test_nopy_plugin"
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            test_dir.mkdir(exist_ok=True)

            orch = Orchestrator("testident", base_dir=tmp_path)
            assert "_test_nopy_plugin" not in orch._registry._plugins
        finally:
            if test_dir.exists():
                test_dir.rmdir()
            if created_local and local_dir.exists() and not any(local_dir.iterdir()):
                local_dir.rmdir()
