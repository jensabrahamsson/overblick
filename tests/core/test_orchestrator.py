"""Tests for orchestrator state machine, LLM routing, and IPC client discovery."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.orchestrator import Orchestrator, OrchestratorState
from overblick.identities import Identity, LLMSettings, ScheduleSettings, SecuritySettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_identity(**overrides):
    """Create a minimal Identity with sane defaults for testing."""
    defaults = {
        "name": "test",
        "display_name": "Test",
        "plugins": ("fakeplugin",),
        "security": SecuritySettings(
            enable_preflight=False,
            enable_output_safety=False,
        ),
        "schedule": ScheduleSettings(feed_poll_minutes=5, heartbeat_hours=4),
        "raw_config": {},
    }
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
        """stop() from STOPPING state goes through full shutdown to STOPPED."""
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._state = OrchestratorState.STOPPING
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

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
        """Shutdown tears down engagement DB and backend."""
        orch = _make_orchestrator(tmp_path)
        eng_db = MagicMock()
        eng_db.stop_background_cleanup = MagicMock()
        orch._services.engagement_db = eng_db

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
        """_create_components_via_factory delegates to factory.create_all."""
        expected_result = {
            "identity": MagicMock(),
            "llm_pipeline": MagicMock(),
            "registry": MagicMock(),
        }
        factory = MagicMock()
        factory.create_all = AsyncMock(return_value=expected_result)

        orch = _make_orchestrator(tmp_path, factory=factory)
        result = await orch._create_components_via_factory()
        assert result is expected_result
        factory.create_all.assert_awaited_once_with(
            identity_name="testident",
            base_dir=tmp_path,
            plugin_names=[],
        )


# ---------------------------------------------------------------------------
# Setup — factory path (lines 269-289)
# ---------------------------------------------------------------------------


class TestSetupWithFactory:
    async def test_setup_delegates_to_bootstrap_and_plugin_loader(self, tmp_path):
        """setup() delegates to OrchestratorBootstrap and PluginLoader."""
        identity = _make_identity(plugins=("fakeplugin",))
        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        plugin_instance = MagicMock()
        plugin_instance.name = "fakeplugin"

        factory = MagicMock()

        orch = _make_orchestrator(tmp_path, factory=factory)

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls:
            mock_bootstrap = mock_bootstrap_cls.return_value
            mock_bootstrap.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader = mock_loader_cls.return_value
            mock_loader.load_all = AsyncMock(return_value=[plugin_instance])

            await orch.setup()

        assert orch.state == OrchestratorState.SETUP
        assert len(orch._plugins) == 1
        mock_bootstrap.setup.assert_awaited_once()
        mock_loader.load_all.assert_awaited_once()


# ---------------------------------------------------------------------------
# Setup — non-factory path
# ---------------------------------------------------------------------------


def _make_bootstrap_result(tmp_path, identity=None, plugins=None):
    """Create a mock OrchestratorBootstrapResult for setup tests."""
    from overblick.core.orchestrator_bootstrap_result import OrchestratorBootstrapResult
    from overblick.core.orchestrator_paths import OrchestratorPaths
    from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
    from overblick.core.orchestrator_services import OrchestratorServices

    identity = identity or _make_identity()
    services = OrchestratorServices()
    services.identity = identity
    runtime_state = OrchestratorRuntimeState()
    runtime_state.lifecycle_state = OrchestratorState.SETUP
    paths = OrchestratorPaths.for_identity(tmp_path, "testident")

    return OrchestratorBootstrapResult(
        services=services,
        runtime_state=runtime_state,
        paths=paths,
    )


class TestSetupWithoutFactory:
    async def test_setup_delegates_to_bootstrap(self, tmp_path):
        """setup() creates OrchestratorBootstrap and delegates."""
        identity = _make_identity(plugins=("moltbook",))
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        plugin_instance = MagicMock()
        plugin_instance.name = "moltbook"

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls:
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[plugin_instance])

            await orch.setup()

        assert orch.state == OrchestratorState.SETUP
        assert len(orch._plugins) == 1
        mock_bootstrap_cls.return_value.setup.assert_awaited_once()

    async def test_setup_no_identity_skips_plugin_loading(self, tmp_path):
        """When bootstrap returns no identity, plugin loading is skipped."""
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path)
        bootstrap_result.services.identity = None

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls:
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[])

            await orch.setup()

        # load_all should not be called when identity is None
        mock_loader_cls.return_value.load_all.assert_not_awaited()
        assert len(orch._plugins) == 0

    async def test_setup_multiple_plugins_loaded(self, tmp_path):
        """setup() loads multiple plugins via PluginLoader."""
        identity = _make_identity(plugins=("p1", "p2"))
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        p1 = MagicMock()
        p1.name = "p1"
        p2 = MagicMock()
        p2.name = "p2"

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls:
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[p1, p2])

            await orch.setup()

        assert len(orch._plugins) == 2

    async def test_setup_passes_plugin_names_to_loader(self, tmp_path):
        """setup() passes _plugin_names to PluginLoader.load_all."""
        identity = _make_identity(plugins=("existing",))
        orch = Orchestrator("testident", base_dir=tmp_path, plugins=["myplugin"])

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls:
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[])

            await orch.setup()

        mock_loader_cls.return_value.load_all.assert_awaited_once_with(["myplugin"])

    async def test_setup_empty_plugins_returns_empty_list(self, tmp_path):
        """When loader returns no plugins, _plugins is empty."""
        identity = _make_identity(plugins=())
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls:
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[])

            await orch.setup()

        assert len(orch._plugins) == 0


# ---------------------------------------------------------------------------
# run() — Lines 448-543
# ---------------------------------------------------------------------------


class TestOrchestratorRun:
    async def test_run_setup_failure_calls_stop(self, tmp_path):
        """setup() failure triggers stop()."""
        orch = _make_orchestrator(tmp_path)
        with patch.object(orch, "setup", new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             patch.object(orch, "stop", new_callable=AsyncMock) as mock_stop:
            with pytest.raises(RuntimeError, match="boom"):
                await orch.run()
            mock_stop.assert_awaited_once()

    async def test_run_delegates_to_runtime(self, tmp_path):
        """run() creates OrchestratorRuntime and delegates."""
        identity = _make_identity()
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls, \
             patch("overblick.core.orchestrator.OrchestratorRuntime") as mock_runtime_cls, \
             patch("overblick.core.orchestrator.PluginRunController"):
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[])
            mock_runtime = mock_runtime_cls.return_value
            mock_runtime.run = AsyncMock()

            await orch.run()

        mock_runtime.run.assert_awaited_once()
        assert orch.state == OrchestratorState.RUNNING or orch.state == OrchestratorState.STOPPED

    async def test_run_cancelled_error_calls_stop(self, tmp_path):
        """CancelledError during run triggers stop."""
        identity = _make_identity()
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls, \
             patch("overblick.core.orchestrator.OrchestratorRuntime") as mock_runtime_cls, \
             patch("overblick.core.orchestrator.PluginRunController"):
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[])
            mock_runtime = mock_runtime_cls.return_value
            mock_runtime.run = AsyncMock(side_effect=asyncio.CancelledError())

            await orch.run()

        assert orch.state == OrchestratorState.STOPPED

    async def test_run_generic_exception_calls_stop(self, tmp_path):
        """Generic exception during run triggers stop and re-raises."""
        identity = _make_identity()
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls, \
             patch("overblick.core.orchestrator.OrchestratorRuntime") as mock_runtime_cls, \
             patch("overblick.core.orchestrator.PluginRunController"):
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[])
            mock_runtime = mock_runtime_cls.return_value
            mock_runtime.run = AsyncMock(side_effect=RuntimeError("runtime boom"))

            with pytest.raises(RuntimeError, match="runtime boom"):
                await orch.run()

        assert orch.state == OrchestratorState.STOPPED

    async def test_run_sets_running_state(self, tmp_path):
        """run() transitions to RUNNING state before delegating to runtime."""
        identity = _make_identity()
        orch = _make_orchestrator(tmp_path)

        bootstrap_result = _make_bootstrap_result(tmp_path, identity)
        captured_state = None

        async def _capture_state():
            nonlocal captured_state
            captured_state = orch.state

        with patch("overblick.core.orchestrator.OrchestratorBootstrap") as mock_bootstrap_cls, \
             patch("overblick.core.orchestrator.PluginLoader") as mock_loader_cls, \
             patch("overblick.core.orchestrator.OrchestratorRuntime") as mock_runtime_cls, \
             patch("overblick.core.orchestrator.PluginRunController"):
            mock_bootstrap_cls.return_value.setup = AsyncMock(return_value=bootstrap_result)
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=[])
            mock_runtime = mock_runtime_cls.return_value
            mock_runtime.run = _capture_state

            await orch.run()

        assert captured_state == OrchestratorState.RUNNING


# ---------------------------------------------------------------------------
# _is_plugin_stopped (lines 597-618)
# ---------------------------------------------------------------------------


class TestIsPluginStopped:
    """Tests for PluginRunController.is_plugin_stopped (moved from Orchestrator)."""

    async def test_no_control_file(self, tmp_path):
        from overblick.core.plugin_run_controller import PluginRunController
        controller = PluginRunController(control_file=None)
        assert await controller.is_plugin_stopped("any") is False

    async def test_cached_hit(self, tmp_path):
        from overblick.core.plugin_run_controller import PluginRunController
        control_file = tmp_path / "control.json"
        control_file.write_text(json.dumps({"myplugin": "stopped"}))
        controller = PluginRunController(control_file=control_file, cache_ttl_seconds=9999)
        # Prime the cache
        await controller.is_plugin_stopped("myplugin")
        assert await controller.is_plugin_stopped("myplugin") is True
        assert await controller.is_plugin_stopped("other") is False

    async def test_reads_file_on_cache_miss(self, tmp_path):
        from overblick.core.plugin_run_controller import PluginRunController
        control_file = tmp_path / "control.json"
        control_file.write_text(json.dumps({"myplugin": "stopped"}))
        controller = PluginRunController(control_file=control_file)
        assert await controller.is_plugin_stopped("myplugin") is True

    async def test_missing_file_empty_cache(self, tmp_path):
        from overblick.core.plugin_run_controller import PluginRunController
        controller = PluginRunController(control_file=tmp_path / "nonexistent.json")
        assert await controller.is_plugin_stopped("any") is False

    async def test_corrupt_file_returns_false(self, tmp_path):
        from overblick.core.plugin_run_controller import PluginRunController
        control_file = tmp_path / "control.json"
        control_file.write_text("not json {{{")
        controller = PluginRunController(control_file=control_file)
        assert await controller.is_plugin_stopped("any") is False


# ---------------------------------------------------------------------------
# _setup_learning_store (lines 620-650)
# ---------------------------------------------------------------------------


class TestSetupLearningStore:
    """Learning store setup moved to LearningStoreBuilder; test via ResourceSetup."""

    async def test_learning_store_built_during_resource_setup(self, tmp_path):
        """ResourceSetup.setup() builds learning store when identity exists."""
        from overblick.core.orchestrator_paths import OrchestratorPaths
        from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
        from overblick.core.orchestrator_services import OrchestratorServices
        from overblick.core.resource_setup import ResourceSetup

        identity = _make_identity(raw_config={"ethos": ["be kind", "be fair"]})
        services = OrchestratorServices()
        services.identity = identity
        services.llm_client = None
        runtime_state = OrchestratorRuntimeState()
        paths = OrchestratorPaths.for_identity(tmp_path, "testident")
        paths.data_dir.mkdir(parents=True, exist_ok=True)

        mock_store = MagicMock()

        with patch("overblick.core.resource_setup.LearningStoreBuilder") as mock_builder_cls, \
             patch("overblick.core.resource_setup.CapabilitySetup") as mock_cap_cls, \
             patch("overblick.core.resource_setup.IPCBootstrap") as mock_ipc_cls:
            mock_builder_cls.return_value.build = AsyncMock(return_value=mock_store)
            mock_cap_cls.return_value.setup = AsyncMock(return_value={})
            mock_ipc_cls.return_value.create_client.return_value = None

            resource_setup = ResourceSetup(
                services=services,
                runtime_state=runtime_state,
                paths=paths,
                identity_name="testident",
            )
            await resource_setup.setup()

        assert services.learning_store is mock_store

    async def test_learning_store_skipped_when_no_identity(self, tmp_path):
        """ResourceSetup skips learning store when identity is None."""
        from overblick.core.orchestrator_paths import OrchestratorPaths
        from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
        from overblick.core.orchestrator_services import OrchestratorServices
        from overblick.core.resource_setup import ResourceSetup

        services = OrchestratorServices()
        services.identity = None
        runtime_state = OrchestratorRuntimeState()
        paths = OrchestratorPaths.for_identity(tmp_path, "testident")

        with patch("overblick.core.resource_setup.LearningStoreBuilder") as mock_builder_cls, \
             patch("overblick.core.resource_setup.IPCBootstrap") as mock_ipc_cls:
            mock_ipc_cls.return_value.create_client.return_value = None

            resource_setup = ResourceSetup(
                services=services,
                runtime_state=runtime_state,
                paths=paths,
                identity_name="testident",
            )
            await resource_setup.setup()

        mock_builder_cls.assert_not_called()
        assert services.learning_store is None


# ---------------------------------------------------------------------------
# _get_embed_fn (lines 652-660)
# ---------------------------------------------------------------------------


class TestGetEmbedFn:
    """Embed function logic moved to LearningStoreBuilder. Test via builder."""

    async def test_builder_uses_llm_client_embed(self, tmp_path):
        """LearningStoreBuilder uses LLM client's embedding function."""
        from overblick.core.learning_store_builder import LearningStoreBuilder

        identity = _make_identity(raw_config={"learning": {"enabled": True, "ethos": "be kind"}})
        llm_client = MagicMock()
        llm_client.get_embedding_function = AsyncMock(return_value=AsyncMock(return_value=[0.1, 0.2]))

        builder = LearningStoreBuilder(
            identity=identity,
            llm_client=llm_client,
            data_dir=tmp_path,
        )

        with patch("overblick.core.learning_store_builder.LearningStore") as mock_ls:
            mock_store = MagicMock()
            mock_store.setup = AsyncMock()
            mock_ls.return_value = mock_store
            result = await builder.build()

        assert result is mock_store
        llm_client.get_embedding_function.assert_awaited_once()

    async def test_builder_handles_no_client(self, tmp_path):
        """LearningStoreBuilder works without LLM client."""
        from overblick.core.learning_store_builder import LearningStoreBuilder

        identity = _make_identity(raw_config={"learning": {"enabled": True, "ethos": "be kind"}})

        builder = LearningStoreBuilder(
            identity=identity,
            llm_client=None,
            data_dir=tmp_path,
        )

        with patch("overblick.core.learning_store_builder.LearningStore") as mock_ls:
            mock_store = MagicMock()
            mock_store.setup = AsyncMock()
            mock_ls.return_value = mock_store
            result = await builder.build()

        assert result is mock_store
        call_kwargs = mock_ls.call_args[1]
        assert call_kwargs["embed_fn"] is None


# ---------------------------------------------------------------------------
# _setup_capabilities (lines 665-721)
# ---------------------------------------------------------------------------


class TestSetupCapabilities:
    """Capability setup moved to CapabilitySetup; test via that class."""

    def _make_services(self, tmp_path, **identity_kwargs):
        from overblick.core.orchestrator_services import OrchestratorServices
        services = OrchestratorServices()
        services.identity = _make_identity(**identity_kwargs)
        services.secrets = MagicMock()
        services.secrets.get = MagicMock(return_value="s")
        services.audit_log = MagicMock()
        services.llm_client = None
        services.llm_pipeline = None
        services.quiet_hours = None
        return services

    async def test_capability_success(self, tmp_path):
        """CapabilitySetup creates and sets up capabilities."""
        from overblick.core.capability_setup import CapabilitySetup
        from overblick.core.orchestrator_paths import OrchestratorPaths

        services = self._make_services(tmp_path, capability_names=("mycap",))
        paths = OrchestratorPaths.for_identity(tmp_path, "testident")

        cap_instance = MagicMock()
        cap_instance.name = "mycap"
        cap_instance.setup = AsyncMock()

        mock_reg = MagicMock()
        mock_reg.resolve.return_value = ["mycap"]
        mock_reg.create.return_value = cap_instance

        with patch("overblick.core.capability.build_capability_configs", return_value={}):
            cap_setup = CapabilitySetup(
                registry=mock_reg,
                services=services,
                paths=paths,
                identity_name="testident",
            )
            capabilities = await cap_setup.setup()

        assert "mycap" in capabilities

    async def test_capability_setup_failure(self, tmp_path):
        """CapabilitySetup handles setup failures gracefully."""
        from overblick.core.capability_setup import CapabilitySetup
        from overblick.core.orchestrator_paths import OrchestratorPaths

        services = self._make_services(tmp_path, capability_names=("badcap",))
        paths = OrchestratorPaths.for_identity(tmp_path, "testident")

        cap_instance = MagicMock()
        cap_instance.name = "badcap"
        cap_instance.setup = AsyncMock(side_effect=RuntimeError("boom"))

        mock_reg = MagicMock()
        mock_reg.resolve.return_value = ["badcap"]
        mock_reg.create.return_value = cap_instance

        with patch("overblick.core.capability.build_capability_configs", return_value={}):
            cap_setup = CapabilitySetup(
                registry=mock_reg,
                services=services,
                paths=paths,
                identity_name="testident",
            )
            capabilities = await cap_setup.setup()

        assert "badcap" not in capabilities

    async def test_create_returns_none(self, tmp_path):
        """CapabilitySetup skips None results from registry.create."""
        from overblick.core.capability_setup import CapabilitySetup
        from overblick.core.orchestrator_paths import OrchestratorPaths

        services = self._make_services(tmp_path, capability_names=("nullcap",))
        paths = OrchestratorPaths.for_identity(tmp_path, "testident")

        mock_reg = MagicMock()
        mock_reg.resolve.return_value = ["nullcap"]
        mock_reg.create.return_value = None

        with patch("overblick.core.capability.build_capability_configs", return_value={}):
            cap_setup = CapabilitySetup(
                registry=mock_reg,
                services=services,
                paths=paths,
                identity_name="testident",
            )
            capabilities = await cap_setup.setup()

        assert "nullcap" not in capabilities

    async def test_no_identity_returns_empty(self, tmp_path):
        """CapabilitySetup returns empty when no identity."""
        from overblick.core.capability_setup import CapabilitySetup
        from overblick.core.orchestrator_paths import OrchestratorPaths
        from overblick.core.orchestrator_services import OrchestratorServices

        services = OrchestratorServices()
        services.identity = None
        paths = OrchestratorPaths.for_identity(tmp_path, "testident")

        mock_reg = MagicMock()
        cap_setup = CapabilitySetup(
            registry=mock_reg,
            services=services,
            paths=paths,
            identity_name="testident",
        )
        capabilities = await cap_setup.setup()
        assert capabilities == {}


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
    """Preflight creation moved to OrchestratorBootstrap._create_preflight."""

    def _make_bootstrap(self, tmp_path, **identity_kwargs):
        from overblick.core.orchestrator_bootstrap import OrchestratorBootstrap
        from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
        from overblick.core.orchestrator_services import OrchestratorServices
        services = OrchestratorServices()
        services.identity = _make_identity(**identity_kwargs)
        services.llm_client = MagicMock()
        return OrchestratorBootstrap(
            identity_name="testident",
            base_dir=tmp_path,
            plugin_names=[],
            services=services,
            runtime_state=OrchestratorRuntimeState(),
        )

    def test_disabled(self, tmp_path):
        bootstrap = self._make_bootstrap(
            tmp_path, security=SecuritySettings(enable_preflight=False)
        )
        assert bootstrap._create_preflight() is None

    def test_enabled_with_dict_deflections(self, tmp_path):
        bootstrap = self._make_bootstrap(
            tmp_path,
            security=SecuritySettings(enable_preflight=True, admin_user_ids=("admin1",)),
            deflections={"topic": ["response"]},
        )
        with patch("overblick.core.security.preflight.PreflightChecker") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = bootstrap._create_preflight()
            assert result is not None
            kw = mock_cls.call_args[1]
            assert kw["admin_user_ids"] == {"admin1"}
            assert kw["deflections"] == {"topic": ["response"]}

    def test_non_dict_deflections(self, tmp_path):
        bootstrap = self._make_bootstrap(
            tmp_path,
            security=SecuritySettings(enable_preflight=True),
            deflections=["not", "a", "dict"],
        )
        with patch("overblick.core.security.preflight.PreflightChecker") as mock_cls:
            mock_cls.return_value = MagicMock()
            bootstrap._create_preflight()
            kw = mock_cls.call_args[1]
            assert kw["deflections"] == {}


# ---------------------------------------------------------------------------
# _create_output_safety (lines 912-935)
# ---------------------------------------------------------------------------


class TestCreateOutputSafety:
    """Output safety creation moved to OrchestratorBootstrap._create_output_safety."""

    def _make_bootstrap(self, tmp_path, **identity_kwargs):
        from overblick.core.orchestrator_bootstrap import OrchestratorBootstrap
        from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
        from overblick.core.orchestrator_services import OrchestratorServices
        services = OrchestratorServices()
        services.identity = _make_identity(**identity_kwargs)
        services.llm_client = MagicMock()
        return OrchestratorBootstrap(
            identity_name="testident",
            base_dir=tmp_path,
            plugin_names=[],
            services=services,
            runtime_state=OrchestratorRuntimeState(),
        )

    def test_disabled(self, tmp_path):
        bootstrap = self._make_bootstrap(
            tmp_path, security=SecuritySettings(enable_output_safety=False)
        )
        assert bootstrap._create_output_safety() is None

    def test_with_personality_vocab(self, tmp_path):
        bootstrap = self._make_bootstrap(
            tmp_path,
            security=SecuritySettings(enable_output_safety=True),
            personality={
                "vocabulary": {
                    "banned_words": ["yolo", "bruh"],
                    "slang_replacements": {"gonna": "going to"},
                }
            },
            deflections=["sorry"],
        )
        with patch("overblick.core.security.output_safety.OutputSafety") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = bootstrap._create_output_safety()
            assert result is not None
            kw = mock_cls.call_args[1]
            assert len(kw["banned_slang_patterns"]) == 2
            assert kw["slang_replacements"] == {"gonna": "going to"}
            assert kw["deflections"] == ["sorry"]

    def test_no_personality(self, tmp_path):
        bootstrap = self._make_bootstrap(
            tmp_path,
            security=SecuritySettings(enable_output_safety=True),
            deflections={"topic": ["not a list"]},
        )
        with patch("overblick.core.security.output_safety.OutputSafety") as mock_cls:
            mock_cls.return_value = MagicMock()
            bootstrap._create_output_safety()
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
    def test_no_factory_uses_fallback(self, tmp_path):
        """Without factory, _register_local_plugins uses fallback import."""
        orch = _make_orchestrator(tmp_path)
        # __init__ already called it; no crash means success
        assert orch._registry is not None
        assert orch.state == OrchestratorState.INIT

    def test_with_factory_delegates_to_factory(self, tmp_path):
        """With factory, _register_local_plugins delegates to factory."""
        factory = MagicMock()
        factory.register_local_plugins = MagicMock()
        Orchestrator("testident", base_dir=tmp_path, factory=factory)
        factory.register_local_plugins.assert_called_once()

    def test_factory_without_register_local_plugins_skips(self, tmp_path):
        """If factory lacks register_local_plugins, it's silently skipped."""
        factory = MagicMock(spec=[])  # No attributes
        orch = Orchestrator("testident", base_dir=tmp_path, factory=factory)
        assert orch.state == OrchestratorState.INIT

    def test_plugin_base_subclass_detection(self, tmp_path):
        """PluginBase subclass detection logic (used by ComponentFactory)."""
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

        cls_name = None
        for attr_name in dir(fake_mod):
            attr = getattr(fake_mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, PluginBase) and attr is not PluginBase:
                cls_name = attr_name
                break

        assert cls_name == "MyLocalPlugin"


# ---------------------------------------------------------------------------
# _load_local_plugin_config (lines 894-910)
# ---------------------------------------------------------------------------


class TestLoadLocalPluginConfig:
    """Local plugin config loading moved to LocalPluginConfig class."""

    def test_no_config_file(self, tmp_path):
        from overblick.core.local_plugin_config import LocalPluginConfig
        config = LocalPluginConfig(tmp_path, "testident")
        assert config.configured_plugins() == []

    def test_config_with_plugins(self, tmp_path):
        from overblick.core.local_plugin_config import LocalPluginConfig
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "local_plugins.yaml").write_text(
            "testident:\n"
            "  plugins:\n"
            "    - localplugin1\n"
            "    - localplugin2\n"
        )
        config = LocalPluginConfig(tmp_path, "testident")
        assert config.configured_plugins() == ["localplugin1", "localplugin2"]

    def test_no_plugins_for_identity(self, tmp_path):
        from overblick.core.local_plugin_config import LocalPluginConfig
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "local_plugins.yaml").write_text("other:\n  plugins:\n    - p\n")
        config = LocalPluginConfig(tmp_path, "testident")
        assert config.configured_plugins() == []

    def test_parse_error(self, tmp_path):
        from overblick.core.local_plugin_config import LocalPluginConfig
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "local_plugins.yaml").write_text(": {{{{ invalid yaml")
        config = LocalPluginConfig(tmp_path, "testident")
        assert config.configured_plugins() == []

    def test_empty_yaml(self, tmp_path):
        from overblick.core.local_plugin_config import LocalPluginConfig
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "local_plugins.yaml").write_text("")
        config = LocalPluginConfig(tmp_path, "testident")
        assert config.configured_plugins() == []


# ---------------------------------------------------------------------------
# _resolve_plugin_dependencies (lines 962-997)
# ---------------------------------------------------------------------------


class TestResolvePluginDependencies:
    """Plugin dependency resolution moved to PluginDependencyResolver."""

    def _make_resolver(self, get_class_fn=None):
        from overblick.core.plugin_dependency_resolver import PluginDependencyResolver
        registry = MagicMock()
        if get_class_fn:
            registry.get_plugin_class.side_effect = get_class_fn
        else:
            mock_cls = type("MockPlugin", (), {"REQUIRES_PLUGINS": []})
            registry.get_plugin_class.return_value = mock_cls
        return PluginDependencyResolver(registry)

    def test_no_dependencies(self):
        resolver = self._make_resolver()
        assert resolver.resolve(["a", "b"]) == ["a", "b"]

    def test_with_dependency(self):
        def _get_class(name):
            if name == "b":
                return type("B", (), {"REQUIRES_PLUGINS": ["a"]})
            return type("A", (), {"REQUIRES_PLUGINS": []})

        resolver = self._make_resolver(_get_class)
        result = resolver.resolve(["b", "a"])
        assert result.index("a") < result.index("b")

    def test_circular_dependency(self):
        def _get_class(name):
            if name == "a":
                return type("A", (), {"REQUIRES_PLUGINS": ["b"]})
            return type("B", (), {"REQUIRES_PLUGINS": ["a"]})

        resolver = self._make_resolver(_get_class)
        with pytest.raises(ValueError, match="Cycle detected"):
            resolver.resolve(["a", "b"])

    def test_external_dependency_ignored(self):
        """Dependencies not in the plugin list are ignored."""
        def _get_class(name):
            return type("A", (), {"REQUIRES_PLUGINS": ["external"]})

        resolver = self._make_resolver(_get_class)
        assert resolver.resolve(["a"]) == ["a"]

    def test_metadata_failure_continues(self):
        """Plugin class load failure doesn't crash resolution."""
        registry = MagicMock()
        registry.get_plugin_class.side_effect = ValueError("nope")
        from overblick.core.plugin_dependency_resolver import PluginDependencyResolver
        resolver = PluginDependencyResolver(registry)
        assert set(resolver.resolve(["a", "b"])) == {"a", "b"}

    def test_dependency_chain(self):
        def _get_class(name):
            if name == "c":
                return type("C", (), {"REQUIRES_PLUGINS": ["b"]})
            elif name == "b":
                return type("B", (), {"REQUIRES_PLUGINS": ["a"]})
            return type("A", (), {"REQUIRES_PLUGINS": []})

        resolver = self._make_resolver(_get_class)
        assert resolver.resolve(["c", "b", "a"]) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _create_plugin_context
# ---------------------------------------------------------------------------


class TestCreatePluginContext:
    """Plugin context creation moved to PluginContextFactory."""

    def _make_factory(self, tmp_path):
        from overblick.core.orchestrator_paths import OrchestratorPaths
        from overblick.core.orchestrator_services import OrchestratorServices
        from overblick.core.plugin_context_factory import PluginContextFactory
        services = OrchestratorServices()
        services.identity = _make_identity()
        services.secrets = MagicMock()
        services.secrets.get = MagicMock(return_value="val")
        services.audit_log = MagicMock()
        services.llm_pipeline = MagicMock()
        services.llm_client = MagicMock()
        services.quiet_hours = MagicMock()
        services.preflight = None
        services.output_safety = None
        services.ipc_client = None
        services.engagement_db = None
        services.learning_store = None
        paths = OrchestratorPaths.for_identity(tmp_path, "testident")
        return PluginContextFactory(
            identity_name="testident",
            services=services,
            paths=paths,
        ), services

    def test_known_role(self, tmp_path):
        from overblick.core.plugin_base import CommunicationPluginContext
        factory, _ = self._make_factory(tmp_path)
        ctx = factory.create(
            plugin_name="telegram",
            permissions=MagicMock(),
            capability_checker=MagicMock(),
        )
        assert isinstance(ctx, CommunicationPluginContext)

    def test_unknown_role(self, tmp_path):
        from overblick.core.plugin_base import DefaultPluginContext
        factory, _ = self._make_factory(tmp_path)
        ctx = factory.create(
            plugin_name="unknown",
            permissions=MagicMock(),
            capability_checker=MagicMock(),
        )
        assert isinstance(ctx, DefaultPluginContext)

    def test_secrets_getter_wired(self, tmp_path):
        factory, services = self._make_factory(tmp_path)
        ctx = factory.create(
            plugin_name="telegram",
            permissions=MagicMock(),
            capability_checker=MagicMock(),
        )
        ctx._secrets_getter("key")
        services.secrets.get.assert_called_with("testident", "key")


# ---------------------------------------------------------------------------
# Guarded tick / heartbeat (tested via _is_plugin_stopped)
# ---------------------------------------------------------------------------


class TestGuardedTick:
    """Guarded tick/heartbeat logic moved to OrchestratorRuntime."""

    async def test_stopped_plugin_skipped_via_controller(self, tmp_path):
        from overblick.core.plugin_run_controller import PluginRunController
        control_file = tmp_path / "ctrl.json"
        control_file.write_text(json.dumps({"testplugin": "stopped"}))
        controller = PluginRunController(control_file=control_file)
        assert await controller.is_plugin_stopped("testplugin") is True
        assert await controller.is_plugin_stopped("running_plugin") is False

    async def test_runtime_registers_tick_callbacks(self, tmp_path):
        """OrchestratorRuntime registers tick callbacks for plugins."""
        from overblick.core.orchestrator_runtime import OrchestratorRuntime
        from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
        from overblick.core.orchestrator_services import OrchestratorServices

        identity = _make_identity()
        services = OrchestratorServices()
        services.identity = identity
        services.audit_log = MagicMock()
        services.audit_log.log = MagicMock()
        services.audit_log.start_background_cleanup = MagicMock()
        services.event_bus = MagicMock()
        services.event_bus.emit = AsyncMock()

        plugin = MagicMock()
        plugin.name = "testplugin"
        plugin.tick = AsyncMock()
        plugin.post_heartbeat = None

        runtime_state = OrchestratorRuntimeState()
        runtime_state.plugins = [plugin]

        scheduler = MagicMock()
        captured_callbacks = []

        async def _capture_add(name, callback, **kwargs):
            captured_callbacks.append((name, callback))

        scheduler.add = _capture_add

        async def _start_and_shutdown():
            runtime_state.shutdown_event.set()

        scheduler.start = _start_and_shutdown
        services.scheduler = scheduler

        runtime = OrchestratorRuntime(
            identity_name="testident",
            services=services,
            runtime_state=runtime_state,
            on_stop_requested=AsyncMock(),
        )

        with patch("overblick.shared.platform.register_shutdown_signals"):
            await runtime.run()

        # Should have registered tick callback
        tick_names = [name for name, _ in captured_callbacks if name.startswith("tick_")]
        assert len(tick_names) == 1

    async def test_runtime_registers_heartbeat_for_supporting_plugins(self, tmp_path):
        """OrchestratorRuntime registers heartbeat when plugin supports it."""
        from overblick.core.orchestrator_runtime import OrchestratorRuntime
        from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
        from overblick.core.orchestrator_services import OrchestratorServices

        identity = _make_identity()
        services = OrchestratorServices()
        services.identity = identity
        services.audit_log = MagicMock()
        services.audit_log.log = MagicMock()
        services.audit_log.start_background_cleanup = MagicMock()
        services.event_bus = MagicMock()

        plugin = MagicMock()
        plugin.name = "hb_plugin"
        plugin.tick = AsyncMock()
        plugin.post_heartbeat = AsyncMock()

        runtime_state = OrchestratorRuntimeState()
        runtime_state.plugins = [plugin]

        scheduler = MagicMock()
        captured_callbacks = []

        async def _capture_add(name, callback, **kwargs):
            captured_callbacks.append((name, callback))

        scheduler.add = _capture_add

        async def _start_and_shutdown():
            runtime_state.shutdown_event.set()

        scheduler.start = _start_and_shutdown
        services.scheduler = scheduler

        runtime = OrchestratorRuntime(
            identity_name="testident",
            services=services,
            runtime_state=runtime_state,
            on_stop_requested=AsyncMock(),
        )

        with patch("overblick.shared.platform.register_shutdown_signals"):
            await runtime.run()

        # Should have both tick and heartbeat
        names = [name for name, _ in captured_callbacks]
        assert any(n.startswith("tick_") for n in names)
        assert any(n.startswith("heartbeat_") for n in names)


# ---------------------------------------------------------------------------
# _register_local_plugins actual execution (lines 845-871)
# ---------------------------------------------------------------------------


class TestRegisterLocalPluginsExecution:
    """Local plugin discovery moved to ComponentFactory._register_local_plugins.
    These tests now exercise ComponentFactory directly."""

    def test_discovers_and_registers_plugin(self, tmp_path):
        """ComponentFactory discovers and registers local plugins."""
        from overblick.core.component_factory import ComponentFactory
        from overblick.core.plugin_base import PluginBase

        class TestLocalPlugin(PluginBase):
            name = "testlocal"
            async def setup(self): pass
            async def tick(self): pass
            async def teardown(self): pass

        fake_mod = type("FakeMod", (), {
            "TestLocalPlugin": TestLocalPlugin,
            "__name__": "fakemod",
        })()

        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        test_plugin_dir = local_dir / "_test_orch_plugin"
        local_dir.mkdir(parents=True)
        test_plugin_dir.mkdir()
        (test_plugin_dir / "plugin.py").write_text("# test")

        from unittest.mock import Mock
        registry = Mock()

        factory = ComponentFactory("testident", tmp_path)
        with patch("importlib.import_module", return_value=fake_mod):
            factory._register_local_plugins(registry)

        registry.register.assert_called_once()
        call_args = registry.register.call_args
        assert call_args[0][0] == "_test_orch_plugin"

    def test_import_error_skips_plugin(self, tmp_path):
        """Import errors are logged and plugin is skipped."""
        from overblick.core.component_factory import ComponentFactory

        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        test_plugin_dir = local_dir / "_test_bad_plugin"
        local_dir.mkdir(parents=True)
        test_plugin_dir.mkdir()
        (test_plugin_dir / "plugin.py").write_text("# test")

        from unittest.mock import Mock
        registry = Mock()

        factory = ComponentFactory("testident", tmp_path)
        with patch("importlib.import_module", side_effect=ImportError("bad import")):
            factory._register_local_plugins(registry)

        registry.register.assert_not_called()

    def test_no_subclass_not_registered(self, tmp_path):
        """Module without PluginBase subclass is not registered."""
        from overblick.core.component_factory import ComponentFactory

        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        test_plugin_dir = local_dir / "_test_nosub_plugin"
        local_dir.mkdir(parents=True)
        test_plugin_dir.mkdir()
        (test_plugin_dir / "plugin.py").write_text("# test")

        fake_mod = type("FakeMod", (), {
            "NotAPlugin": str,
            "__name__": "fakemod",
        })()

        from unittest.mock import Mock
        registry = Mock()

        factory = ComponentFactory("testident", tmp_path)
        with patch("importlib.import_module", return_value=fake_mod):
            factory._register_local_plugins(registry)

        registry.register.assert_not_called()

    def test_non_dir_candidate_skipped(self, tmp_path):
        """Files in _local/ are skipped."""
        from overblick.core.component_factory import ComponentFactory

        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        local_dir.mkdir(parents=True)
        (local_dir / "_test_file.py").write_text("# not a plugin dir")

        from unittest.mock import Mock
        registry = Mock()

        factory = ComponentFactory("testident", tmp_path)
        factory._register_local_plugins(registry)
        registry.register.assert_not_called()

    def test_dir_without_plugin_py_skipped(self, tmp_path):
        """Directory without plugin.py is skipped."""
        from overblick.core.component_factory import ComponentFactory

        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        local_dir.mkdir(parents=True)
        (local_dir / "_test_nopy_plugin").mkdir()

        from unittest.mock import Mock
        registry = Mock()

        factory = ComponentFactory("testident", tmp_path)
        factory._register_local_plugins(registry)
        registry.register.assert_not_called()
