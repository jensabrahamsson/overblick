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
        plugin._pairs.append(
            SpegelPair(
                observer_name="a",
                target_name="b",
                profile=Profile(observer_name="a", target_name="b", profile_text="P"),
                reflection=Reflection(target_name="b", observer_name="a", reflection_text="R"),
            )
        )
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


class TestSetupDefaultPairs:
    """Test setup with default identity pair discovery."""

    @pytest.mark.asyncio
    async def test_should_discover_default_pairs_when_none_configured(self, spegel_context):
        """Uses list_identities to build default pairs when not configured."""
        # Remove configured pairs
        spegel_context.identity.raw_config["spegel"]["pairs"] = []
        plugin = SpegelPlugin(spegel_context)
        with patch(
            "overblick.plugins.spegel.plugin.list_identities",
            return_value=["anomal", "supervisor", "cherry"],
        ):
            await plugin.setup()
        # supervisor should be filtered
        assert all(
            "supervisor" not in pair
            for pair in plugin._configured_pairs
        )


class TestTickGeneration:
    """Test tick with full pipeline execution."""

    @pytest.mark.asyncio
    async def test_should_generate_pairs_on_tick(self, spegel_context):
        """Tick generates profiling pairs."""
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        plugin._last_run = 0.0  # Force run

        mock_ident = MagicMock()
        mock_ident.name = "anomal"
        mock_ident.display_name = "Anomal"
        mock_ident.description = "An AI entity"
        mock_ident.voice = {"base_tone": "analytical"}
        mock_ident.traits = {"analytical": 0.9, "warm": 0.2}
        mock_ident.backstory = {"origin": "Born in the cloud. Raised by data."}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            with patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"):
                await plugin.tick()

        assert len(plugin._pairs) > 0
        spegel_context.event_bus.emit.assert_called()
        spegel_context.audit_log.log.assert_any_call(
            action="spegel_round_complete",
            details={"pairs_generated": 2},
        )

    @pytest.mark.asyncio
    async def test_should_trim_pairs_when_exceeding_max(self, spegel_context):
        """Pairs get trimmed to _MAX_PAIRS_STORED."""
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()

        # Pre-fill pairs
        for i in range(100):
            plugin._pairs.append(
                SpegelPair(
                    observer_name="a",
                    target_name="b",
                    profile=Profile(observer_name="a", target_name="b", profile_text="P"),
                    reflection=Reflection(target_name="b", observer_name="a", reflection_text="R"),
                )
            )

        plugin._last_run = 0.0

        mock_ident = MagicMock()
        mock_ident.name = "test"
        mock_ident.display_name = "Test"
        mock_ident.description = "Test"
        mock_ident.voice = {}
        mock_ident.traits = {}
        mock_ident.backstory = {}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            with patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"):
                await plugin.tick()

        assert len(plugin._pairs) <= 100

    @pytest.mark.asyncio
    async def test_should_handle_pipeline_error(self, spegel_context):
        """Tick handles pipeline errors gracefully."""
        spegel_context.llm_pipeline.chat = AsyncMock(side_effect=RuntimeError("boom"))
        plugin = SpegelPlugin(spegel_context)
        await plugin.setup()
        plugin._last_run = 0.0

        mock_ident = MagicMock()
        mock_ident.name = "test"
        mock_ident.display_name = "Test"
        mock_ident.description = ""
        mock_ident.voice = {}
        mock_ident.traits = {}
        mock_ident.backstory = {}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            with patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"):
                # Should not raise
                await plugin.tick()


class TestGeneratePair:
    """Test _generate_pair method."""

    @pytest.mark.asyncio
    async def test_should_return_none_when_no_pipeline(self, spegel_context):
        """Returns None when no LLM pipeline is available."""
        spegel_context.llm_pipeline = None
        plugin = SpegelPlugin(spegel_context)
        result = await plugin._generate_pair("anomal", "cherry")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_identity_not_found(self, spegel_context):
        """Returns None when identity raises FileNotFoundError."""
        plugin = SpegelPlugin(spegel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", side_effect=FileNotFoundError("gone")):
            result = await plugin._generate_pair("anomal", "cherry")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_profile_blocked(self, spegel_context):
        """Returns None when profile generation is blocked."""
        from overblick.core.llm.pipeline import PipelineStage

        spegel_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                blocked=True,
                block_reason="safety",
                block_stage=PipelineStage.OUTPUT_SAFETY,
            )
        )
        mock_ident = MagicMock()
        mock_ident.name = "test"
        mock_ident.display_name = "Test"
        mock_ident.description = ""
        mock_ident.voice = {}
        mock_ident.traits = {}
        mock_ident.backstory = {}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7
        plugin = SpegelPlugin(spegel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            with patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"):
                result = await plugin._generate_pair("anomal", "cherry")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_reflection_blocked(self, spegel_context):
        """Returns None when reflection generation is blocked."""
        from overblick.core.llm.pipeline import PipelineStage

        # First call (profile) succeeds, second call (reflection) is blocked
        spegel_context.llm_pipeline.chat = AsyncMock(
            side_effect=[
                PipelineResult(content="A thoughtful profile"),
                PipelineResult(
                    blocked=True,
                    block_reason="safety",
                    block_stage=PipelineStage.OUTPUT_SAFETY,
                ),
            ]
        )
        mock_ident = MagicMock()
        mock_ident.name = "test"
        mock_ident.display_name = "Test"
        mock_ident.description = ""
        mock_ident.voice = {}
        mock_ident.traits = {}
        mock_ident.backstory = {}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7
        plugin = SpegelPlugin(spegel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            with patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"):
                result = await plugin._generate_pair("anomal", "cherry")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_generate_complete_pair(self, spegel_context):
        """Generates a complete profile + reflection pair."""
        mock_ident = MagicMock()
        mock_ident.name = "test"
        mock_ident.display_name = "Test"
        mock_ident.description = "An interesting agent"
        mock_ident.voice = {"base_tone": "warm"}
        mock_ident.traits = {"warm": 0.9, "analytical": 0.3}
        mock_ident.backstory = {"origin": "Created for testing. A simple being."}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        plugin = SpegelPlugin(spegel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            with patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"):
                result = await plugin._generate_pair("anomal", "cherry")

        assert result is not None
        assert result.observer_name == "anomal"
        assert result.target_name == "cherry"
        assert result.profile.profile_text != ""
        assert result.reflection.reflection_text != ""


class TestBuildTargetSummary:
    """Test _build_target_summary method."""

    def test_should_include_description(self, spegel_context):
        """Summary includes identity description."""
        plugin = SpegelPlugin(spegel_context)
        target = MagicMock()
        target.display_name = "Cherry"
        target.description = "A warm attachment-focused agent"
        target.voice = {}
        target.traits = {}
        target.backstory = {}
        result = plugin._build_target_summary(target)
        assert "warm attachment" in result

    def test_should_include_voice_tone(self, spegel_context):
        """Summary includes voice base_tone."""
        plugin = SpegelPlugin(spegel_context)
        target = MagicMock()
        target.display_name = "Test"
        target.description = ""
        target.voice = {"base_tone": "warm and caring"}
        target.traits = {}
        target.backstory = {}
        result = plugin._build_target_summary(target)
        assert "warm and caring" in result

    def test_should_include_high_and_low_traits(self, spegel_context):
        """Summary includes strong and weak traits."""
        plugin = SpegelPlugin(spegel_context)
        target = MagicMock()
        target.display_name = "Test"
        target.description = ""
        target.voice = {}
        target.traits = {"warmth": 0.9, "cynicism": 0.1, "analytical": 0.5}
        target.backstory = {}
        result = plugin._build_target_summary(target)
        assert "warmth" in result
        assert "cynicism" in result

    def test_should_include_backstory_origin(self, spegel_context):
        """Summary includes backstory origin (truncated to 2 sentences)."""
        plugin = SpegelPlugin(spegel_context)
        target = MagicMock()
        target.display_name = "Test"
        target.description = ""
        target.voice = {}
        target.traits = {}
        target.backstory = {"origin": "Born in the cloud. Raised by algorithms. Loves nature."}
        result = plugin._build_target_summary(target)
        assert "Born in the cloud" in result
        assert "Raised by algorithms" in result

    def test_should_return_default_when_no_data(self, spegel_context):
        """Returns default summary when no personality data."""
        plugin = SpegelPlugin(spegel_context)
        target = MagicMock()
        target.display_name = "Unknown"
        target.description = ""
        target.voice = {}
        target.traits = {}
        target.backstory = {}
        result = plugin._build_target_summary(target)
        assert "Unknown is an agent" in result


class TestGetPairsMethods:
    """Test public accessor methods."""

    def test_should_return_pairs_newest_first(self, spegel_context):
        """get_pairs returns in reverse order."""
        plugin = SpegelPlugin(spegel_context)
        for i in range(3):
            plugin._pairs.append(
                SpegelPair(
                    observer_name=f"obs_{i}",
                    target_name=f"tgt_{i}",
                    profile=Profile(observer_name=f"obs_{i}", target_name=f"tgt_{i}", profile_text="P"),
                    reflection=Reflection(target_name=f"tgt_{i}", observer_name=f"obs_{i}", reflection_text="R"),
                )
            )
        result = plugin.get_pairs()
        assert result[0].observer_name == "obs_2"

    def test_should_filter_pairs_by_identity(self, spegel_context):
        """get_pairs_for_identity returns pairs involving the identity."""
        plugin = SpegelPlugin(spegel_context)
        plugin._pairs = [
            SpegelPair(
                observer_name="anomal",
                target_name="cherry",
                profile=Profile(observer_name="anomal", target_name="cherry", profile_text="P"),
                reflection=Reflection(target_name="cherry", observer_name="anomal", reflection_text="R"),
            ),
            SpegelPair(
                observer_name="cherry",
                target_name="bjork",
                profile=Profile(observer_name="cherry", target_name="bjork", profile_text="P"),
                reflection=Reflection(target_name="bjork", observer_name="cherry", reflection_text="R"),
            ),
        ]
        # anomal is observer in first pair
        result = plugin.get_pairs_for_identity("anomal")
        assert len(result) == 1
        # cherry is in both pairs (observer in 2nd, target in 1st)
        result = plugin.get_pairs_for_identity("cherry")
        assert len(result) == 2


class TestSaveStateError:
    """Test _save_state error handling."""

    def test_should_handle_save_state_exception(self, spegel_context):
        """_save_state handles write errors gracefully."""
        plugin = SpegelPlugin(spegel_context)
        plugin._state_file = MagicMock()
        plugin._state_file.parent = MagicMock()
        plugin._state_file.parent.mkdir = MagicMock()
        plugin._state_file.write_text = MagicMock(side_effect=OSError("disk full"))
        # Should not raise
        plugin._save_state()


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
