"""
Tests for the LogAgentPlugin — setup, tick guards, observer, handlers.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.agentic.models import PlannedAction
from overblick.core.plugin_base import PluginContext
from overblick.plugins.log_agent.models import (
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

    def test_format_for_planner_none_observation(self, tmp_path):
        """format_for_planner() handles None observation."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        scanner = LogScanner(tmp_path, identities=[])
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

        pipeline = AsyncMock()
        handler = _AnalyzePatternHandler(llm_pipeline=pipeline, dry_run=False)
        obs = LogObservation(
            scan_results=[
                LogScanResult(identity="anomal", entries=[]),
            ]
        )

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

        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    errors_found=1,
                    criticals_found=0,
                    entries=[
                        LogEntry(
                            identity="anomal", file_path="a.log", level="ERROR", message="Test"
                        )
                    ],
                ),
            ]
        )

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

        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    errors_found=1,
                    criticals_found=0,
                    entries=[
                        LogEntry(
                            identity="anomal",
                            file_path="a.log",
                            level="ERROR",
                            message="Real error",
                        )
                    ],
                ),
            ]
        )

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

        obs = LogObservation(
            scan_results=[
                LogScanResult(identity="anomal", errors_found=1, entries=[entry]),
            ]
        )

        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)

        assert result.success is True
        assert "deduplicated" in result.result


class TestLogAgentTick:
    """Tests for tick guards and execution."""

    @pytest.mark.asyncio
    async def test_tick_skips_when_interval_not_elapsed(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        import time
        plugin._state.last_check = time.time() + 99999
        await plugin.tick()
        assert plugin._state.scans_completed == 0

    @pytest.mark.asyncio
    async def test_tick_skips_during_quiet_hours(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0
        vakt_plugin_context.quiet_hours_checker.is_quiet_hours.return_value = True
        await plugin.tick()
        assert plugin._state.scans_completed == 0

    @pytest.mark.asyncio
    async def test_tick_skips_without_llm(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0
        vakt_plugin_context.llm_pipeline = None
        await plugin.tick()
        assert plugin._state.scans_completed == 0

    @pytest.mark.asyncio
    async def test_tick_runs_agentic_tick(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0
        # Mock agentic_tick to return a TickLog
        from overblick.core.agentic.models import TickLog
        plugin.agentic_tick = AsyncMock(return_value=TickLog(tick_number=1))
        await plugin.tick()
        assert plugin._state.scans_completed == 1

    @pytest.mark.asyncio
    async def test_tick_handles_none_tick_log(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0
        plugin.agentic_tick = AsyncMock(return_value=None)
        await plugin.tick()
        assert plugin._state.scans_completed == 0


class TestLogAgentSetupWarning:
    @pytest.mark.asyncio
    async def test_warns_when_no_scan_identities(self, tmp_path, mock_audit_log):
        from overblick.identities import (
            LLMSettings,
            Personality,
            QuietHoursSettings,
            ScheduleSettings,
            SecuritySettings,
        )

        identity = Personality(
            name="vakt",
            display_name="Vakt",
            description="Log agent",
            engagement_threshold=25,
            llm=LLMSettings(model="qwen3:8b", temperature=0.2, max_tokens=1500),
            quiet_hours=QuietHoursSettings(enabled=True, start_hour=1, end_hour=4),
            schedule=ScheduleSettings(heartbeat_hours=1, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            raw_config={"log_agent": {"scan_identities": []}},
        )
        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            llm_pipeline=AsyncMock(),
            event_bus=MagicMock(),
            scheduler=MagicMock(),
            audit_log=mock_audit_log,
            quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
            identity=identity,
        )
        plugin = LogAgentPlugin(ctx)
        await plugin.setup()
        assert plugin._scanner is not None


class TestNotifyPrincipal:
    @pytest.mark.asyncio
    async def test_should_send_notification(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        result = await plugin._notify_principal("test message")
        assert result is True
        assert plugin._state.alerts_sent == 1

    @pytest.mark.asyncio
    async def test_should_return_false_when_no_capability(self, vakt_plugin_context):
        vakt_plugin_context.capabilities = {}
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        result = await plugin._notify_principal("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_should_handle_notification_exception(self, vakt_plugin_context):
        notifier = vakt_plugin_context.capabilities["telegram_notifier"]
        notifier.send_notification = AsyncMock(side_effect=RuntimeError("send failed"))
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        result = await plugin._notify_principal("test")
        assert result is False


class TestTeardown:
    @pytest.mark.asyncio
    async def test_should_teardown(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        await plugin.teardown()


class TestPlanningPromptConfig:
    @pytest.mark.asyncio
    async def test_should_return_config(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        config = plugin.get_planning_prompt_config()
        assert "Vakt" in config.agent_role
        assert "scan_logs" in config.available_actions

    @pytest.mark.asyncio
    async def test_should_return_learning_categories(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        cats = plugin.get_learning_categories()
        assert "error_patterns" in cats


class TestLogObserverExtended:
    @pytest.mark.asyncio
    async def test_format_with_many_entries(self, sample_log_dir):
        """format_for_planner shows '...and N more' for > 10 entries."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        # Create a log with many errors
        anomal_log = sample_log_dir / "anomal" / "anomal.log"
        lines = []
        for i in range(15):
            lines.append(f"2026-02-26 03:00:{i:02d},000 - test - ERROR - Error #{i}")
        anomal_log.write_text("\n".join(lines) + "\n")

        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        observer = _LogObserver(scanner)
        obs = await observer.observe()
        text = observer.format_for_planner(obs)
        assert "...and" in text
        assert "more" in text


class TestScanLogsHandlerExtended:
    @pytest.mark.asyncio
    async def test_should_use_observation_data(self, sample_log_dir):
        """When observation is a LogObservation with results, use it directly."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        handler = _ScanLogsHandler(scanner)
        action = PlannedAction(action_type="scan_logs")

        obs = LogObservation(
            scan_results=[
                LogScanResult(identity="anomal", errors_found=2, criticals_found=1, entries=[]),
            ],
            total_errors=2,
            total_criticals=1,
            identities_scanned=1,
        )
        result = await handler.handle(action, obs)
        assert result.success is True
        assert "found 3 entries" in result.result

    @pytest.mark.asyncio
    async def test_should_fallback_to_fresh_scan(self, sample_log_dir):
        """When observation is not LogObservation, scanner rescans."""
        from overblick.plugins.log_agent.log_scanner import LogScanner

        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        handler = _ScanLogsHandler(scanner)
        action = PlannedAction(action_type="scan_logs")
        # Pass a non-LogObservation
        result = await handler.handle(action, "not an observation")
        assert result.success is True
        assert "Scanned" in result.result


class TestAnalyzePatternHandlerExtended:
    @pytest.mark.asyncio
    async def test_should_fail_without_pipeline(self):
        handler = _AnalyzePatternHandler(llm_pipeline=None, dry_run=False)
        action = PlannedAction(action_type="analyze_pattern")
        result = await handler.handle(action, LogObservation())
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_should_fail_with_non_observation(self):
        handler = _AnalyzePatternHandler(llm_pipeline=AsyncMock(), dry_run=False)
        action = PlannedAction(action_type="analyze_pattern")
        result = await handler.handle(action, "not an observation")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_should_analyze_entries_with_llm(self):

        pipeline = AsyncMock()
        mock_result = MagicMock()
        mock_result.blocked = False
        mock_result.content = "Root cause: timeout in LLM calls"
        pipeline.chat = AsyncMock(return_value=mock_result)

        handler = _AnalyzePatternHandler(llm_pipeline=pipeline, dry_run=False)
        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    errors_found=2,
                    entries=[
                        LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Timeout"),
                        LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Connection refused"),
                    ],
                ),
            ]
        )
        action = PlannedAction(action_type="analyze_pattern")
        result = await handler.handle(action, obs)
        assert result.success is True
        assert "Root cause" in result.result

    @pytest.mark.asyncio
    async def test_should_handle_blocked_llm_response(self):
        pipeline = AsyncMock()
        mock_result = MagicMock()
        mock_result.blocked = True
        mock_result.content = None
        pipeline.chat = AsyncMock(return_value=mock_result)

        handler = _AnalyzePatternHandler(llm_pipeline=pipeline, dry_run=False)
        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    entries=[
                        LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="err"),
                    ],
                ),
            ]
        )
        action = PlannedAction(action_type="analyze_pattern")
        result = await handler.handle(action, obs)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_should_handle_llm_exception(self):
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        handler = _AnalyzePatternHandler(llm_pipeline=pipeline, dry_run=False)
        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    entries=[
                        LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="err"),
                    ],
                ),
            ]
        )
        action = PlannedAction(action_type="analyze_pattern")
        result = await handler.handle(action, obs)
        assert result.success is False
        assert "LLM analysis failed" in result.error


class TestSendAlertHandlerExtended:
    @pytest.mark.asyncio
    async def test_should_fail_without_observation(self):
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        handler = _SendAlertHandler(
            notify_fn=AsyncMock(),
            formatter=AlertFormatter(),
            deduplicator=AlertDeduplicator(),
            dry_run=False,
        )
        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, "not a log observation")
        assert result.success is False
        assert "No log observation" in result.error

    @pytest.mark.asyncio
    async def test_should_return_no_alerts_when_message_empty(self):
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        handler = _SendAlertHandler(
            notify_fn=AsyncMock(),
            formatter=AlertFormatter(),
            deduplicator=AlertDeduplicator(),
            dry_run=False,
        )
        # Observation with no entries
        obs = LogObservation(scan_results=[])
        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)
        assert result.success is True
        assert "No new alerts" in result.result

    @pytest.mark.asyncio
    async def test_should_return_no_alerts_when_formatter_returns_none(self):
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        formatter = AlertFormatter()
        handler = _SendAlertHandler(
            notify_fn=AsyncMock(),
            formatter=formatter,
            deduplicator=AlertDeduplicator(),
            dry_run=False,
        )
        # Entries with WARNING level -> errors_found=0, criticals_found=0 -> format returns None
        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    errors_found=0,
                    criticals_found=0,
                    entries=[
                        LogEntry(identity="anomal", file_path="a.log", level="WARNING", message="warn"),
                    ],
                ),
            ]
        )
        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)
        assert result.success is True
        assert "No alerts to send" in result.result

    @pytest.mark.asyncio
    async def test_should_fail_without_notify_fn(self):
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        handler = _SendAlertHandler(
            notify_fn=None,
            formatter=AlertFormatter(),
            deduplicator=AlertDeduplicator(),
            dry_run=False,
        )
        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    errors_found=1,
                    entries=[
                        LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="err"),
                    ],
                ),
            ]
        )
        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)
        assert result.success is False
        assert "No notification function" in result.error

    @pytest.mark.asyncio
    async def test_should_handle_send_exception(self):
        from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter

        notify_fn = AsyncMock(side_effect=RuntimeError("send failed"))
        handler = _SendAlertHandler(
            notify_fn=notify_fn,
            formatter=AlertFormatter(),
            deduplicator=AlertDeduplicator(),
            dry_run=False,
        )
        obs = LogObservation(
            scan_results=[
                LogScanResult(
                    identity="anomal",
                    errors_found=1,
                    entries=[
                        LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="err"),
                    ],
                ),
            ]
        )
        action = PlannedAction(action_type="send_alert")
        result = await handler.handle(action, obs)
        assert result.success is False
        assert "Alert send failed" in result.error


class TestActionHandlers:
    @pytest.mark.asyncio
    async def test_get_action_handlers_returns_all(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        handlers = plugin.get_action_handlers()
        assert "scan_logs" in handlers
        assert "analyze_pattern" in handlers
        assert "send_alert" in handlers
        assert "skip" in handlers


class TestCreateObserver:
    @pytest.mark.asyncio
    async def test_should_create_observer(self, vakt_plugin_context):
        plugin = LogAgentPlugin(vakt_plugin_context)
        await plugin.setup()
        observer = await plugin.create_observer()
        assert observer is not None


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
