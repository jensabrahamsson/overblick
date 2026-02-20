"""Tests for SpegelPlugin — inter-agent psychological profiling."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.plugins.spegel.models import Profile, Reflection, SpegelPair
from overblick.plugins.spegel.plugin import SpegelPlugin


class TestSetup:
    """Test plugin initialization and setup."""

    @pytest.mark.asyncio
    async def test_setup_success(self, spegel_context):
        """Plugin sets up correctly with configured pairs."""
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        assert len(plugin._configured_pairs) == 2
        assert ("anomal", "cherry") in plugin._configured_pairs
        assert ("cherry", "anomal") in plugin._configured_pairs

    @pytest.mark.asyncio
    async def test_setup_audits(self, spegel_context):
        """Plugin logs setup to audit log."""
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        spegel_context.audit_log.log.assert_any_call(
            action="plugin_setup",
            details={
                "plugin": "spegel",
                "identity": "test",
                "pairs": 2,
                "interval_hours": 168,
            },
        )


class TestTick:
    """Test the main work cycle."""

    @pytest.mark.asyncio
    async def test_tick_skips_if_not_run_time(self, spegel_context):
        """Plugin skips when interval hasn't elapsed."""
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        plugin._last_run = time.time()
        await plugin.tick()
        spegel_context.llm_pipeline.chat.assert_not_called()


class TestRunTime:
    """Test the scheduling logic."""

    def test_is_run_time_first_run(self, spegel_context):
        plugin = SpegelPlugin(spegel_context)
        plugin._last_run = 0.0
        assert plugin._is_run_time() is True

    def test_is_run_time_after_interval(self, spegel_context):
        plugin = SpegelPlugin(spegel_context)
        plugin._interval_hours = 168
        plugin._last_run = time.time() - 169 * 3600
        assert plugin._is_run_time() is True

    def test_is_not_run_time_before_interval(self, spegel_context):
        plugin = SpegelPlugin(spegel_context)
        plugin._interval_hours = 168
        plugin._last_run = time.time() - 1 * 3600
        assert plugin._is_run_time() is False


class TestDefaultPairs:
    """Test default pair generation."""

    def test_small_set_generates_all_pairs(self, spegel_context):
        plugin = SpegelPlugin(spegel_context)
        pairs = plugin._build_default_pairs(["a", "b", "c"])
        assert ("a", "b") in pairs
        assert ("b", "a") in pairs
        assert ("a", "c") in pairs
        assert len(pairs) == 6  # 3 * 2

    def test_large_set_generates_subset(self, spegel_context):
        plugin = SpegelPlugin(spegel_context)
        names = [f"id_{i}" for i in range(8)]
        pairs = plugin._build_default_pairs(names)
        # Should have ring + cross-links, less than 8*7=56
        assert len(pairs) < 56
        assert len(pairs) >= 8  # At least one per identity


class TestModels:
    """Test Spegel data models."""

    def test_profile_model(self):
        profile = Profile(
            observer_name="anomal",
            target_name="cherry",
            profile_text="A thoughtful analysis.",
        )
        assert profile.observer_name == "anomal"
        assert profile.target_name == "cherry"

    def test_spegel_pair_model(self):
        pair = SpegelPair(
            observer_name="anomal",
            target_name="cherry",
            profile=Profile(
                observer_name="anomal",
                target_name="cherry",
                profile_text="Profile text",
            ),
            reflection=Reflection(
                target_name="cherry",
                observer_name="anomal",
                reflection_text="Reflection text",
            ),
        )
        assert pair.observer_name == "anomal"
        assert pair.target_name == "cherry"


class TestStateManagement:
    """Test state persistence."""

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, spegel_context):
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        plugin._last_run = 12345.0
        plugin._pairs.append(SpegelPair(
            observer_name="a",
            target_name="b",
            profile=Profile(observer_name="a", target_name="b", profile_text="P"),
            reflection=Reflection(target_name="b", observer_name="a", reflection_text="R"),
        ))
        plugin._save_state()

        plugin2 = SpegelPlugin(spegel_context)
        await plugin2.setup()
        assert plugin2._last_run == 12345.0
        assert len(plugin2._pairs) == 1

    @pytest.mark.asyncio
    async def test_handles_corrupt_state(self, spegel_context):
        state_file = spegel_context.data_dir / "spegel_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("broken json {{{")

        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        assert plugin._last_run == 0.0


class TestTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_saves_state(self, spegel_context):
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        plugin._last_run = 99999.0
        await plugin.teardown()

        state_file = spegel_context.data_dir / "spegel_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["last_run"] == 99999.0
