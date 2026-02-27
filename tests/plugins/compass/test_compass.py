"""Tests for CompassPlugin — identity drift detector."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.compass.models import (
    BaselineProfile,
    DriftAlert,
    DriftMetrics,
    StyleMetrics,
)
from overblick.plugins.compass.plugin import CompassPlugin


class TestSetup:
    """Test plugin initialization and setup."""

    @pytest.mark.asyncio
    async def test_setup_success(self, compass_context):
        """Plugin sets up correctly with valid config."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        assert plugin._window_size == 10
        assert plugin._baseline_samples == 5
        assert plugin._drift_threshold == 2.0

    @pytest.mark.asyncio
    async def test_setup_subscribes_to_events(self, compass_context):
        """Plugin subscribes to LLM output events."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        compass_context.event_bus.subscribe.assert_called_once_with(
            "llm.output", plugin._on_llm_output
        )

    @pytest.mark.asyncio
    async def test_setup_audits(self, compass_context):
        """Plugin logs setup to audit log."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        compass_context.audit_log.log.assert_any_call(
            action="plugin_setup",
            details={
                "plugin": "compass",
                "identity": "test",
                "window_size": 10,
                "drift_threshold": 2.0,
            },
        )


class TestRecordOutput:
    """Test output recording."""

    @pytest.mark.asyncio
    async def test_record_output_buffers(self, compass_context):
        """Outputs are buffered until tick processes them."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        plugin.record_output("anomal", "Some test output text here.")
        assert len(plugin._output_buffer) == 1

    @pytest.mark.asyncio
    async def test_tick_processes_buffer(self, compass_context):
        """Tick processes buffered outputs."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        plugin.record_output("anomal", "This is a reasonably long test output with enough words to be analyzed properly by the stylometric engine.")
        await plugin.tick()
        assert len(plugin._output_buffer) == 0
        assert "anomal" in plugin._windows

    @pytest.mark.asyncio
    async def test_short_outputs_filtered(self, compass_context):
        """Very short outputs are filtered out."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        plugin.record_output("anomal", "Short.")
        await plugin.tick()
        assert len(plugin._windows.get("anomal", [])) == 0


class TestBaselineEstablishment:
    """Test baseline establishment."""

    @pytest.mark.asyncio
    async def test_baseline_established_after_enough_samples(self, compass_context):
        """Baseline is established after baseline_samples outputs."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()

        for i in range(6):
            plugin.record_output(
                "anomal",
                f"This is test output number {i} with enough words to pass the minimum threshold for analysis.",
            )
            await plugin.tick()

        assert "anomal" in plugin._baselines
        assert plugin._baselines["anomal"].sample_count == 5

    @pytest.mark.asyncio
    async def test_baseline_not_established_with_few_samples(self, compass_context):
        """Baseline is not established with too few samples."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()

        for i in range(3):
            plugin.record_output(
                "anomal",
                f"Test output {i} with enough words to be analyzed by the engine.",
            )
            await plugin.tick()

        assert "anomal" not in plugin._baselines


class TestDriftDetection:
    """Test drift detection logic."""

    @pytest.mark.asyncio
    async def test_no_drift_with_consistent_outputs(self, compass_context):
        """No drift alert when outputs are consistent."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()

        # Generate consistent baseline and window
        text = "This is a consistent test output with the same style and tone. Always writing with similar sentence length and vocabulary richness."
        for i in range(15):
            plugin.record_output("anomal", text)
            await plugin.tick()

        # Should have baseline but no alerts
        assert "anomal" in plugin._baselines
        alerts = plugin.get_alerts()
        # If there are alerts, they should be below threshold
        for alert in alerts:
            assert alert.drift_score <= plugin._drift_threshold or alert.identity_name != "anomal"

    @pytest.mark.asyncio
    async def test_drift_alert_with_radical_change(self, compass_context):
        """Drift alert when style changes radically."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()

        # Establish baseline with formal, long-sentence text
        formal_text = (
            "Furthermore, the comprehensive analysis unequivocally demonstrates "
            "significant correlations between the observed phenomena and the "
            "established theoretical predictions found throughout the literature. "
            "Additionally, the methodology employed herein provides substantial "
            "evidence supporting the fundamental hypothesis proposed earlier."
        )
        for i in range(6):
            plugin.record_output("anomal", formal_text)
            await plugin.tick()

        assert "anomal" in plugin._baselines

        # Now force drift by directly manipulating the baseline to make
        # differences more detectable
        baseline = plugin._baselines["anomal"]
        # Make std_devs tight so any deviation is significant
        tight_stds = {k: 0.01 for k in baseline.std_devs}
        plugin._baselines["anomal"] = BaselineProfile(
            identity_name="anomal",
            metrics=baseline.metrics,
            sample_count=baseline.sample_count,
            std_devs=tight_stds,
        )

        # Switch to very different style — short, informal, lots of exclamations
        informal_text = "lol yeah?! gonna do it!! haha wow!! cool stuff right?! yes!! no!!"
        for i in range(5):
            plugin.record_output("anomal", informal_text)
            await plugin.tick()

        # Should have drift alerts
        alerts = [a for a in plugin.get_alerts() if a.identity_name == "anomal"]
        assert len(alerts) > 0


class TestModels:
    """Test Compass data models."""

    def test_style_metrics_defaults(self):
        metrics = StyleMetrics()
        assert metrics.avg_sentence_length == 0.0
        assert metrics.word_count == 0

    def test_baseline_profile(self):
        baseline = BaselineProfile(
            identity_name="anomal",
            metrics=StyleMetrics(avg_sentence_length=15.5, word_count=200),
            sample_count=10,
        )
        assert baseline.identity_name == "anomal"
        assert baseline.sample_count == 10

    def test_drift_alert(self):
        alert = DriftAlert(
            identity_name="anomal",
            drift_score=3.5,
            threshold=2.0,
            drifted_dimensions=["formality_score", "avg_sentence_length"],
        )
        assert alert.drift_score > alert.threshold


class TestStateManagement:
    """Test state persistence."""

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, compass_context):
        plugin = CompassPlugin(compass_context)
        await plugin.setup()

        plugin._baselines["anomal"] = BaselineProfile(
            identity_name="anomal",
            metrics=StyleMetrics(avg_sentence_length=15.0),
            sample_count=10,
        )
        plugin._alerts.append(DriftAlert(
            identity_name="anomal",
            drift_score=2.5,
            threshold=2.0,
        ))
        plugin._save_state()

        plugin2 = CompassPlugin(compass_context)
        await plugin2.setup()
        assert "anomal" in plugin2._baselines
        assert len(plugin2._alerts) == 1

    @pytest.mark.asyncio
    async def test_handles_corrupt_state(self, compass_context):
        state_file = compass_context.data_dir / "compass_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("broken json {{{")

        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        assert len(plugin._baselines) == 0


class TestEventHandler:
    """Test the _on_llm_output event handler."""

    @pytest.mark.asyncio
    async def test_on_llm_output_buffers_output(self, compass_context):
        """Handler receives kwargs from EventBus and buffers output."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        await plugin._on_llm_output(identity="anomal", content="Test text")
        assert len(plugin._output_buffer) == 1
        assert plugin._output_buffer[0] == ("anomal", "Test text")

    @pytest.mark.asyncio
    async def test_on_llm_output_ignores_empty_identity(self, compass_context):
        """Handler ignores events with empty identity."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        await plugin._on_llm_output(identity="", content="Some text")
        assert len(plugin._output_buffer) == 0

    @pytest.mark.asyncio
    async def test_on_llm_output_ignores_empty_content(self, compass_context):
        """Handler ignores events with empty content."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        await plugin._on_llm_output(identity="anomal", content="")
        assert len(plugin._output_buffer) == 0

    @pytest.mark.asyncio
    async def test_on_llm_output_ignores_missing_kwargs(self, compass_context):
        """Handler gracefully handles missing kwargs."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        await plugin._on_llm_output()
        assert len(plugin._output_buffer) == 0


class TestPublicAccessors:
    """Test public accessor methods."""

    @pytest.mark.asyncio
    async def test_get_baseline_exists(self, compass_context):
        """get_baseline() returns baseline when it exists."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        plugin._baselines["anomal"] = BaselineProfile(
            identity_name="anomal",
            metrics=StyleMetrics(avg_sentence_length=15.0),
            sample_count=10,
        )
        baseline = plugin.get_baseline("anomal")
        assert baseline is not None
        assert baseline.sample_count == 10

    @pytest.mark.asyncio
    async def test_get_baseline_missing(self, compass_context):
        """get_baseline() returns None when identity has no baseline."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        assert plugin.get_baseline("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_drift_history_filtered(self, compass_context):
        """get_drift_history() filters by identity_name."""
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        plugin._drift_history.append(DriftMetrics(
            identity_name="anomal",
            current_metrics=StyleMetrics(),
            drift_score=1.0,
        ))
        plugin._drift_history.append(DriftMetrics(
            identity_name="cherry",
            current_metrics=StyleMetrics(),
            drift_score=2.0,
        ))
        anomal_only = plugin.get_drift_history(identity_name="anomal")
        assert len(anomal_only) == 1
        assert anomal_only[0].identity_name == "anomal"


class TestTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_saves_state(self, compass_context):
        plugin = CompassPlugin(compass_context)
        await plugin.setup()
        plugin._baselines["test"] = BaselineProfile(
            identity_name="test",
            metrics=StyleMetrics(),
            sample_count=5,
        )
        await plugin.teardown()

        state_file = compass_context.data_dir / "compass_state.json"
        assert state_file.exists()
