"""Tests for orchestrator lifecycle — setup, run cycle, shutdown, and error handling."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.orchestrator import Orchestrator, OrchestratorState
from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.identities import Identity, LLMSettings

logger = logging.getLogger(__name__)


class _StubPlugin(PluginBase):
    """Minimal plugin for lifecycle tests."""

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self.setup_called = False
        self.tick_called = False
        self.teardown_called = False

    async def setup(self):
        self.setup_called = True

    async def tick(self):
        self.tick_called = True

    async def teardown(self):
        self.teardown_called = True


class _FailingPlugin(PluginBase):
    """Plugin that raises during setup."""

    async def setup(self):
        raise RuntimeError("Plugin setup failed")

    async def tick(self):
        pass


def _make_orchestrator(tmp_path: Path, plugins=None) -> Orchestrator:
    """Create an Orchestrator with minimal configuration."""
    return Orchestrator(
        identity_name="test",
        base_dir=tmp_path,
        plugins=plugins or ["stub"],
    )


def _mock_identity(**overrides) -> Identity:
    """Create a minimal Identity for testing."""
    defaults = dict(
        name="test",
        description="Test identity",
        plugins=["stub"],
        llm=LLMSettings(provider="ollama"),
    )
    defaults.update(overrides)
    return Identity(**defaults)


def _stub_plugin(tmp_path: Path) -> _StubPlugin:
    return _StubPlugin(
        PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
    )


class TestSetupLoadsPlugins:
    @pytest.mark.asyncio
    async def test_setup_loads_plugins(self, tmp_path):
        """Verify setup() discovers and loads configured plugins."""
        orch = _make_orchestrator(tmp_path)
        identity = _mock_identity()
        stub = _stub_plugin(tmp_path)

        with (
            patch("overblick.core.orchestrator_bootstrap.load_identity", return_value=identity),
            patch("overblick.core.orchestrator_bootstrap.SecretsManager"),
            patch("overblick.core.orchestrator_bootstrap.AuditLog") as mock_audit_cls,
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_llm_client",
                new_callable=AsyncMock,
            ),
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_preflight",
                return_value=None,
            ),
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_output_safety",
                return_value=None,
            ),
            patch("overblick.core.orchestrator_bootstrap.SafeLLMPipeline"),
            patch("overblick.core.resource_setup.ResourceSetup.setup", new_callable=AsyncMock),
            patch(
                "overblick.core.plugin_loader.PluginLoader.load_all",
                new_callable=AsyncMock,
                return_value=[stub],
            ),
        ):
            mock_audit = MagicMock()
            mock_audit.log = MagicMock()
            mock_audit_cls.return_value = mock_audit

            await orch.setup()

            assert orch.state == OrchestratorState.SETUP
            assert orch._runtime_state.plugins == [stub]


class TestSetupInitializesCapabilities:
    @pytest.mark.asyncio
    async def test_setup_initializes_capabilities(self, tmp_path):
        """Verify setup() runs resource setup (capabilities included)."""
        orch = _make_orchestrator(tmp_path)
        identity = _mock_identity()

        with (
            patch("overblick.core.orchestrator_bootstrap.load_identity", return_value=identity),
            patch("overblick.core.orchestrator_bootstrap.SecretsManager"),
            patch("overblick.core.orchestrator_bootstrap.AuditLog") as mock_audit_cls,
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_llm_client",
                new_callable=AsyncMock,
            ),
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_preflight",
                return_value=None,
            ),
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_output_safety",
                return_value=None,
            ),
            patch("overblick.core.orchestrator_bootstrap.SafeLLMPipeline"),
            patch(
                "overblick.core.resource_setup.ResourceSetup.setup",
                new_callable=AsyncMock,
            ) as mock_resource_setup,
            patch(
                "overblick.core.plugin_loader.PluginLoader.load_all",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_audit = MagicMock()
            mock_audit.log = MagicMock()
            mock_audit_cls.return_value = mock_audit

            await orch.setup()

            mock_resource_setup.assert_called_once()


class TestShutdownCleansUp:
    @pytest.mark.asyncio
    async def test_shutdown_cleans_up(self, tmp_path):
        """Verify stop() tears down plugins, closes LLM, closes DB, and logs."""
        orch = _make_orchestrator(tmp_path)
        stub = _stub_plugin(tmp_path)

        # Set up state directly on the containers
        orch._runtime_state.plugins = [stub]
        orch._runtime_state.lifecycle_state = OrchestratorState.RUNNING

        mock_llm = AsyncMock()
        orch._services.llm_client = mock_llm

        mock_audit = MagicMock()
        orch._services.audit_log = mock_audit

        mock_db_backend = AsyncMock()
        orch._services.engagement_db_backend = mock_db_backend

        await orch.stop()

        assert stub.teardown_called is True
        mock_llm.close.assert_called_once()
        mock_audit.log.assert_called()
        mock_audit.close.assert_called_once()
        mock_db_backend.close.assert_called_once()
        assert orch.state == OrchestratorState.STOPPED


class TestPluginExceptionLogged:
    @pytest.mark.asyncio
    async def test_plugin_exception_logged(self, tmp_path, caplog):
        """Verify plugin load failures are logged, not silently swallowed."""
        orch = _make_orchestrator(tmp_path, plugins=["failing", "stub"])
        identity = _mock_identity(plugins=["failing", "stub"])
        stub = _stub_plugin(tmp_path)

        # Track audit calls
        audit_calls: list = []

        async def mock_load_all(plugin_names=None):
            """Simulate resilient plugin loading — skip failed, continue."""
            results = []
            for name in (plugin_names or []):
                if name == "failing":
                    logger.error("Failed to load plugin 'failing': Plugin setup failed")
                    audit_calls.append(("plugin_load_failed", {"plugin": "failing"}))
                else:
                    await stub.setup()
                    results.append(stub)
            return results

        with (
            patch("overblick.core.orchestrator_bootstrap.load_identity", return_value=identity),
            patch("overblick.core.orchestrator_bootstrap.SecretsManager"),
            patch("overblick.core.orchestrator_bootstrap.AuditLog") as mock_audit_cls,
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_llm_client",
                new_callable=AsyncMock,
            ),
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_preflight",
                return_value=None,
            ),
            patch(
                "overblick.core.orchestrator_bootstrap.OrchestratorBootstrap._create_output_safety",
                return_value=None,
            ),
            patch("overblick.core.orchestrator_bootstrap.SafeLLMPipeline"),
            patch("overblick.core.resource_setup.ResourceSetup.setup", new_callable=AsyncMock),
            patch(
                "overblick.core.plugin_loader.PluginLoader.load_all",
                new_callable=AsyncMock,
                side_effect=mock_load_all,
            ),
        ):
            mock_audit = MagicMock()
            mock_audit.log = MagicMock()
            mock_audit_cls.return_value = mock_audit

            with caplog.at_level(logging.ERROR):
                await orch.setup()

            assert any("Failed to load plugin" in r.message for r in caplog.records)
            assert len(audit_calls) == 1
            assert audit_calls[0][0] == "plugin_load_failed"
            assert len(orch._runtime_state.plugins) == 1
            assert stub.setup_called is True


class TestDoubleStopPrevention:
    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, tmp_path):
        """Calling stop() twice does not raise or double-cleanup."""
        orch = _make_orchestrator(tmp_path)
        orch._runtime_state.lifecycle_state = OrchestratorState.RUNNING

        mock_audit = MagicMock()
        orch._services.audit_log = mock_audit

        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

        # Second call should be a no-op (state is STOPPED)
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED
