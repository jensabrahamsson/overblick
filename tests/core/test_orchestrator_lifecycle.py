"""Tests for orchestrator lifecycle — setup, run cycle, shutdown, and error handling."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.orchestrator import Orchestrator, OrchestratorState
from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.identities import Identity, LLMSettings


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


class TestSetupLoadsPlugins:
    @pytest.mark.asyncio
    async def test_setup_loads_plugins(self, tmp_path):
        """Verify setup() discovers and loads configured plugins."""
        orch = _make_orchestrator(tmp_path)
        identity = _mock_identity()

        with (
            patch.object(orch, "_registry") as mock_registry,
            patch("overblick.core.orchestrator.load_identity", return_value=identity),
            patch("overblick.core.orchestrator.SecretsManager"),
            patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls,
            patch("overblick.core.orchestrator.SQLiteBackend") as mock_db_cls,
            patch("overblick.core.orchestrator.EngagementDB") as mock_eng_cls,
            patch.object(orch, "_create_llm_client", new_callable=AsyncMock),
            patch.object(orch, "_setup_capabilities", new_callable=AsyncMock),
            patch.object(orch, "_create_ipc_client", return_value=None),
        ):
            mock_audit = MagicMock()
            mock_audit.log = MagicMock(return_value=1)
            mock_audit_cls.return_value = mock_audit

            mock_db = AsyncMock()
            mock_db_cls.return_value = mock_db

            mock_eng = AsyncMock()
            mock_eng_cls.return_value = mock_eng

            stub = _StubPlugin(
                PluginContext(
                    identity_name="test",
                    data_dir=tmp_path / "data",
                    log_dir=tmp_path / "logs",
                )
            )
            mock_registry.load.return_value = stub

            await orch.setup()

            mock_registry.load.assert_called_once()
            assert stub.setup_called is True
            assert orch.state == OrchestratorState.SETUP


class TestSetupInitializesCapabilities:
    @pytest.mark.asyncio
    async def test_setup_initializes_capabilities(self, tmp_path):
        """Verify setup() calls _setup_capabilities."""
        orch = _make_orchestrator(tmp_path)
        identity = _mock_identity()

        with (
            patch.object(orch, "_registry") as mock_registry,
            patch("overblick.core.orchestrator.load_identity", return_value=identity),
            patch("overblick.core.orchestrator.SecretsManager"),
            patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls,
            patch("overblick.core.orchestrator.SQLiteBackend") as mock_db_cls,
            patch("overblick.core.orchestrator.EngagementDB") as mock_eng_cls,
            patch.object(orch, "_create_llm_client", new_callable=AsyncMock),
            patch.object(
                orch, "_setup_capabilities", new_callable=AsyncMock
            ) as mock_cap,
            patch.object(orch, "_create_ipc_client", return_value=None),
        ):
            mock_audit = MagicMock()
            mock_audit.log = MagicMock(return_value=1)
            mock_audit_cls.return_value = mock_audit

            mock_db = AsyncMock()
            mock_db_cls.return_value = mock_db

            mock_eng = AsyncMock()
            mock_eng_cls.return_value = mock_eng

            stub = _StubPlugin(
                PluginContext(
                    identity_name="test",
                    data_dir=tmp_path / "data",
                    log_dir=tmp_path / "logs",
                )
            )
            mock_registry.load.return_value = stub

            await orch.setup()

            mock_cap.assert_called_once()


class TestShutdownCleansUp:
    @pytest.mark.asyncio
    async def test_shutdown_cleans_up(self, tmp_path):
        """Verify stop() tears down plugins, closes LLM, closes DB, and logs."""
        orch = _make_orchestrator(tmp_path)

        stub = _StubPlugin(
            PluginContext(
                identity_name="test",
                data_dir=tmp_path / "data",
                log_dir=tmp_path / "logs",
            )
        )
        orch._plugins = [stub]
        orch._state = OrchestratorState.RUNNING

        mock_llm = AsyncMock()
        orch._llm_client = mock_llm

        mock_audit = MagicMock()
        orch._audit_log = mock_audit

        mock_db_backend = AsyncMock()
        orch._engagement_db_backend = mock_db_backend

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

        with (
            patch.object(orch, "_registry") as mock_registry,
            patch("overblick.core.orchestrator.load_identity", return_value=identity),
            patch("overblick.core.orchestrator.SecretsManager"),
            patch("overblick.core.orchestrator.AuditLog") as mock_audit_cls,
            patch("overblick.core.orchestrator.SQLiteBackend") as mock_db_cls,
            patch("overblick.core.orchestrator.EngagementDB") as mock_eng_cls,
            patch.object(orch, "_create_llm_client", new_callable=AsyncMock),
            patch.object(orch, "_setup_capabilities", new_callable=AsyncMock),
            patch.object(orch, "_create_ipc_client", return_value=None),
        ):
            mock_audit = MagicMock()
            mock_audit.log = MagicMock(return_value=1)
            mock_audit_cls.return_value = mock_audit

            mock_db = AsyncMock()
            mock_db_cls.return_value = mock_db

            mock_eng = AsyncMock()
            mock_eng_cls.return_value = mock_eng

            # First plugin fails, second succeeds
            failing = _FailingPlugin(
                PluginContext(
                    identity_name="test",
                    data_dir=tmp_path / "data",
                    log_dir=tmp_path / "logs",
                )
            )
            working = _StubPlugin(
                PluginContext(
                    identity_name="test",
                    data_dir=tmp_path / "data",
                    log_dir=tmp_path / "logs",
                )
            )
            mock_registry.load.side_effect = [failing, working]

            with caplog.at_level(logging.ERROR):
                await orch.setup()

            # The failing plugin should have been logged
            assert any("Failed to load plugin" in r.message for r in caplog.records)
            # Audit log should record the failure
            fail_calls = [
                c
                for c in mock_audit.log.call_args_list
                if len(c.args) > 0 and c.args[0] == "plugin_load_failed"
            ]
            assert len(fail_calls) == 1
            # The working plugin should still be loaded
            assert len(orch._plugins) == 1
            assert working.setup_called is True


class TestDoubleStopPrevention:
    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, tmp_path):
        """Calling stop() twice does not raise or double-cleanup."""
        orch = _make_orchestrator(tmp_path)
        orch._state = OrchestratorState.RUNNING

        mock_audit = MagicMock()
        orch._audit_log = mock_audit

        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

        # Second call should be a no-op (state is STOPPED, not STOPPING)
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED
