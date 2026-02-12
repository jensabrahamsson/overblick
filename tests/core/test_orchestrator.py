"""Tests for orchestrator state machine."""

import pytest
from blick.core.orchestrator import Orchestrator, OrchestratorState


class TestOrchestratorState:
    def test_enum_values(self):
        assert OrchestratorState.INIT.value == "init"
        assert OrchestratorState.RUNNING.value == "running"
        assert OrchestratorState.STOPPED.value == "stopped"


class TestOrchestratorInit:
    def test_initial_state(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        assert orch.state == OrchestratorState.INIT
        assert orch.identity is None

    def test_default_plugins(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        assert orch._plugin_names == ["moltbook"]

    def test_custom_plugins(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path, plugins=["moltbook", "gmail"])
        assert orch._plugin_names == ["moltbook", "gmail"]


class TestOrchestratorStop:
    @pytest.mark.asyncio
    async def test_stop_from_init(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        await orch.stop()
        await orch.stop()  # Should not raise
        assert orch.state == OrchestratorState.STOPPED
