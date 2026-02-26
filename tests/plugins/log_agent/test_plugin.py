"""
Tests for the LogAgentPlugin — setup, tick guards, observer, handlers.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.agentic.models import ActionOutcome, PlannedAction
from overblick.plugins.log_agent.models import (
    ActionType,
    LogEntry,
    LogObservation,
    LogScanResult,
)
from overblick.plugins.log_agent.plugin import (
    LogAgentPlugin,
    _AnalyzePatternHandler,
    _LogObserver,
    _ScanLogsHandler,
    _SendAlertHandler,
    _SkipHandler,
)


class TestLogAgentSetup:
    """Test plugin initialization."""

    @pytest.mark.asyncio
    async def test_setup_creates_scanner(self, vakt_plugin_context):
        """setup() initializes the log scanner."""
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()

        assert plugin._scanner is not None
        assert "anomal" in plugin._scanner.identities

    @pytest.mark.asyncio
    async def test_setup_requires_identity(self, vakt_plugin_context):
        """setup() raises if no identity is set."""
        vakt_plugin_context.identity = None
        plugin = LogAgentPlugin(vakt_plugin_context)

        with pytest.raises(RuntimeError, match="requires an identity"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_get_status(self, vakt_plugin_context):
        """get_status() returns expected fields."""
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()

        status = plugin.get_status()
        assert status["plugin"] == "log_agent"
        assert status["dry_run"] is True
        assert "scans_completed" in status
        assert "alerts_sent" in status

    @pytest.mark.asyncio
    async def test_get_valid_action_types(self, vakt_plugin_context):
        """get_valid_action_types() returns all action types."""
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()

        types = plugin.get_valid_action_types()
        assert "scan_logs" in types
        assert "analyze_pattern" in types
        assert "send_alert" in types
        assert "skip" in types

    @pytest.mark.asyncio
    async def test_get_default_goals(self, vakt_plugin_context):
        """get_default_goals() returns goals."""
        plugin = LogAgentPlugin(vakt_plugin_context)
        goals = plugin.get_default_goals()
        assert len(goals) == 3
        assert any("error" in g.description.lower() for g in goals)


class TestLogObserver:
    """Tests for the _LogObserver."""

    @pytest.mark.asyncio
    async def test_observe_returns_log_observation(self, sample_log_dir):
        """Observer returns a LogObservation with scan results."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        scanner = LogScanner(sample_log_dir, identities=["anomal", "cherry"])
        observer = _LogObserver(scanner)
        obs = await observer.observe()

        assert isinstance(obs, LogObservation)
        assert obs.identities_scanned == 2
        assert obs.total_errors >= 1

    @pytest.mark.asyncio
    async def test_format_for_planner_with_errors(self, sample_log_dir):
        """format_for_planner() shows errors when present."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        observer = _LogObserver(scanner)
        obs = await observer.observe()
        text = observer.format_for_planner(obs)

        assert "anomal" in text
        assert "ERROR" in text

    @pytest.mark.asyncio
    async def test_format_for_planner_clean(self, sample_log_dir):
        """format_for_planner() shows 'all clear' when no errors."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        scanner = LogScanner(sample_log_dir, identities=["cherry"])
        observer = _LogObserver(scanner)
        obs = await observer.observe()
        text = observer.format_for_planner(obs)

        assert "All clear" in text

    def test_format_for_planner_none_observation(self):
        """format_for_planner() handles None observation."""
        from overblick.plugins.log_agent.log_scanner import LogScanner
        import tempfile
        from pathlib import Path

        scanner = LogScanner(Path(tempfile.mkdtemp()), identities=[])
        observer = _LogObserver(scanner)
        text = observer.format_for_planner(None)

        assert "No log observations" in text


class TestScanLogsHandler:
    """Tests for _ScanLogsHandler."""

    @pytest.mark.asyncio
    async def test_scan_returns_success(self, sample_log_dir):
        """scan_logs handler returns success with counts."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        scanner = LogScanner(sample_log_dir, identities=["anomal", "cherry"])
        handler = _ScanLogsHandler(scanner)

        action = PlannedAction(action_type="scan_logs")
        result = await handler.handle(action, None)

        assert result.success is True
        assert "Scanned 2 identities" in result.result


class TestAnalyzePatternHandler:
    """Tests for _AnalyzePatternHandler."""

    @pytest.mark.asyncio
    async def test_dry_run_skips(self):
        """Dry run mode skips actual analysis."""
        handler = _AnalyzePatternHandler(llm_pipeline=AsyncMock(), dry_run=True)
        action = PlannedAction(action_type="analyze_pattern")
        result = await handler.handle(action, LogObservation())

        assert result.success is True
        assert "DRY RUN" in result.result

    @pytest.mark.asyncio
    async def test_no_entries_returns_success(self):
        """No entries to analyze returns success."""
        from overblick.core.llm.pipeline import PipelineResult

        pipeline = AsyncMock()
        handler = _AnalyzePatternHandler(llm_pipeline=pipeline, dry_run=False)
        obs = LogObservation(scan_results=[
            LogScanResult(identity="anomal", entries=[]),
        ])

        action = PlannedAction(action_type="analyze_pattern")
        result = await handler.handle(action, obs)

        assert result.success is True
        assert "No entries" in result.result


class TestSendAlertHandler:
    """Tests for _SendAlertHandler."""

    @pytest.mark.asyncio
    async def test_dry_run_skips(self):
        """Dry run mode skips sending."""
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        handler = _SendAlertHandler(
            notify_fn=AsyncMock(),
            formatter=AlertFormatter(),
            deduplicator=AlertDeduplicator(),
            dry_run=True,
        )

        obs = LogObservation(scan_results=[
            LogScanResult(
                identity="anomal", errors_found=1, criticals_found=0,
                entries=[LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Test")],
            ),
        ])

        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)

        assert result.success is True
        assert "DRY RUN" in result.result

    @pytest.mark.asyncio
    async def test_sends_alert_live(self):
        """Live mode sends the alert."""
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        notify_fn = AsyncMock(return_value=True)
        handler = _SendAlertHandler(
            notify_fn=notify_fn,
            formatter=AlertFormatter(),
            deduplicator=AlertDeduplicator(),
            dry_run=False,
        )

        obs = LogObservation(scan_results=[
            LogScanResult(
                identity="anomal", errors_found=1, criticals_found=0,
                entries=[LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Real error")],
            ),
        ])

        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)

        assert result.success is True
        notify_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_blocks_repeat(self):
        """Deduplicator blocks repeated alerts."""
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        dedup = AlertDeduplicator(cooldown_seconds=3600)
        entry = LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Same error")

        # Mark as already alerted
        dedup.should_alert(entry)

        handler = _SendAlertHandler(
            notify_fn=AsyncMock(),
            formatter=AlertFormatter(),
            deduplicator=dedup,
            dry_run=False,
        )

        obs = LogObservation(scan_results=[
            LogScanResult(identity="anomal", errors_found=1, entries=[entry]),
        ])

        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)

        assert result.success is True
        assert "deduplicated" in result.result


class TestSkipHandler:
    """Tests for _SkipHandler."""

    @pytest.mark.asyncio
    async def test_skip_returns_success(self):
        """Skip handler always returns success."""
        handler = _SkipHandler()
        action = PlannedAction(action_type="skip", reasoning="Nothing to do")
        result = await handler.handle(action, None)

        assert result.success is True
        assert "Nothing to do" in result.result
