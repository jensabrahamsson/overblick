"""
Log agent plugin — multi-identity log monitoring.

Scans log files and audit databases across all configured identities,
detects error patterns, and alerts the owner via Telegram.

Uses the OBSERVE/THINK/PLAN/ACT/REFLECT loop from AgenticPluginBase.

Actions:
- scan_logs: Scan log files for errors/criticals
- analyze_pattern: Deep LLM analysis of error patterns (complexity="high")
- send_alert: Send Telegram alert to owner
- skip: Do nothing this tick
"""

import logging
import time
from typing import Any, Optional

from overblick.core.agentic.models import AgentGoal
from overblick.core.agentic.plugin_base import AgenticPluginBase
from overblick.core.agentic.protocols import ActionHandler, Observer, PlanningPromptConfig
from overblick.core.plugin_base import PluginContext
from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter
from overblick.plugins.log_agent.log_scanner import LogScanner
from overblick.plugins.log_agent.models import (
    ActionType,
    LogObservation,
    LogScanResult,
    PluginState,
)

logger = logging.getLogger(__name__)

# Default goals for the log agent
_DEFAULT_GOALS = [
    AgentGoal(
        name="detect_errors_fast",
        description=(
            "Scan all configured identity logs every tick. Detect ERROR "
            "and CRITICAL entries quickly. Never miss a critical."
        ),
        priority=90,
    ),
    AgentGoal(
        name="alert_owner",
        description=(
            "Alert the owner via Telegram when significant errors are found. "
            "Prioritize criticals over errors. Avoid alert fatigue — "
            "deduplicate and batch when possible."
        ),
        priority=80,
    ),
    AgentGoal(
        name="analyze_patterns",
        description=(
            "When recurring errors are detected across identities, use "
            "deep analysis to identify root causes and suggest fixes."
        ),
        priority=60,
    ),
]


class LogAgentPlugin(AgenticPluginBase):
    """
    Agentic log monitoring plugin.

    Scans logs for all configured identities, detects error patterns,
    and sends alerts via Telegram.
    """

    name = "log_agent"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._scanner: Optional[LogScanner] = None
        self._formatter = AlertFormatter()
        self._deduplicator = AlertDeduplicator()
        self._state = PluginState()
        self._check_interval: int = 300  # 5 minutes default
        self._dry_run: bool = True
        self._last_observation: Optional[LogObservation] = None

    async def setup(self) -> None:
        """Initialize the log agent."""
        identity = self.ctx.identity
        if not identity:
            raise RuntimeError("LogAgentPlugin requires an identity")

        raw_config = identity.raw_config
        la_config = raw_config.get("log_agent", {})

        # Scan configuration
        scan_identities = la_config.get("scan_identities", [])
        if not scan_identities:
            logger.warning("LogAgentPlugin: no scan_identities configured")

        tick_interval_minutes = la_config.get("tick_interval_minutes", 5)
        self._check_interval = tick_interval_minutes * 60
        self._dry_run = la_config.get("dry_run", True)

        # Alert config
        alert_config = la_config.get("alerting", {})
        cooldown = alert_config.get("cooldown_seconds", 3600)
        self._deduplicator = AlertDeduplicator(cooldown_seconds=cooldown)

        # Determine base log directory (parent of identity log dirs)
        base_log_dir = self.ctx.log_dir.parent

        # Initialize scanner
        self._scanner = LogScanner(
            base_log_dir=base_log_dir,
            identities=scan_identities,
        )

        # Database — agentic loop needs goal/learning/tick storage
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = self.ctx.data_dir / "log_agent.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        await self.setup_agentic_db(backend)

        # Wire the agentic loop
        await self.setup_agentic_loop(
            max_actions_per_tick=3,
            audit_action_prefix="log_agent",
        )

        mode = "DRY RUN" if self._dry_run else "LIVE"
        logger.info(
            "LogAgentPlugin [%s] setup for '%s' (scanning: %s)",
            mode, self.ctx.identity_name,
            ", ".join(scan_identities),
        )

    async def tick(self) -> None:
        """Run the agentic loop with interval and quiet hours guards."""
        now = time.time()

        if self._state.last_check and (now - self._state.last_check < self._check_interval):
            return

        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        if not self.ctx.llm_pipeline:
            logger.debug("Log agent: no LLM pipeline available")
            return

        self._state.last_check = now

        tick_log = await self.agentic_tick()
        if tick_log:
            self._state.scans_completed += 1

    # ── AgenticPluginBase abstract methods ────────────────────────────────

    async def create_observer(self) -> Observer:
        """Create the log scanning observer."""
        return _LogObserver(self._scanner)

    def get_action_handlers(self) -> dict[str, ActionHandler]:
        """Return log agent action handlers."""
        return {
            ActionType.SCAN_LOGS.value: _ScanLogsHandler(self._scanner),
            ActionType.ANALYZE_PATTERN.value: _AnalyzePatternHandler(
                self.ctx.llm_pipeline, self._dry_run,
            ),
            ActionType.SEND_ALERT.value: _SendAlertHandler(
                notify_fn=self._notify_principal,
                formatter=self._formatter,
                deduplicator=self._deduplicator,
                dry_run=self._dry_run,
            ),
            ActionType.SKIP.value: _SkipHandler(),
        }

    def get_planning_prompt_config(self) -> PlanningPromptConfig:
        """Return log agent planning prompt config."""
        action_types = "|".join(a.value for a in ActionType)
        return PlanningPromptConfig(
            agent_role=(
                "You are Vakt, a log monitoring agent. You watch logs across all "
                "identities in the Överblick system. Your job is to detect errors, "
                "analyze patterns, and alert the owner about significant issues."
            ),
            available_actions=(
                "- scan_logs: Scan log files for new ERROR/CRITICAL entries\n"
                "- analyze_pattern: Deep analysis of recurring error patterns (uses LLM)\n"
                "- send_alert: Send Telegram alert about findings\n"
                "- skip: Do nothing this tick (explain why)"
            ),
            safety_rules=(
                "- Always scan before alerting — never alert on stale data\n"
                "- Deduplicate alerts — do not spam the owner with repeated errors\n"
                "- Prioritize CRITICAL entries over ERROR entries\n"
                "- If no new errors found, skip or analyze patterns\n"
                "- send_alert only when there are actual findings to report"
            ),
            output_format_hint=f"Valid action_type values: {action_types}",
            learning_categories="error_patterns|false_positives|alerting|general",
        )

    def get_default_goals(self) -> list[AgentGoal]:
        return _DEFAULT_GOALS

    def get_learning_categories(self) -> str:
        return "error_patterns|false_positives|alerting|general"

    def get_valid_action_types(self) -> set[str]:
        return {a.value for a in ActionType}

    # ── Plugin-specific methods ──────────────────────────────────────────

    async def _notify_principal(self, message: str) -> bool:
        """Send a notification via TelegramNotifier capability."""
        notifier = self.ctx.get_capability("telegram_notifier")
        if not notifier:
            logger.debug("Log agent: telegram_notifier capability not available")
            return False

        try:
            await notifier.send_notification(message)
            self._state.alerts_sent += 1
            return True
        except Exception as e:
            logger.warning("Log agent: notification failed: %s", e)
            return False

    def get_status(self) -> dict:
        """Expose status for dashboard."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "scans_completed": self._state.scans_completed,
            "alerts_sent": self._state.alerts_sent,
            "patterns_analyzed": self._state.patterns_analyzed,
            "dry_run": self._dry_run,
            "health": self._state.current_health,
        }

    async def teardown(self) -> None:
        """Cleanup."""
        logger.info("LogAgentPlugin teardown complete")


# ── Observer ────────────────────────────────────────────────────────────

class _LogObserver:
    """Observer that scans logs across all configured identities."""

    def __init__(self, scanner: LogScanner):
        self._scanner = scanner

    async def observe(self) -> Any:
        """Scan all identity logs and return observation."""
        results = self._scanner.scan_all()

        return LogObservation(
            scan_results=results,
            total_errors=sum(r.errors_found for r in results),
            total_criticals=sum(r.criticals_found for r in results),
            identities_scanned=len(results),
        )

    def format_for_planner(self, observation: Any) -> str:
        """Format log observation as text for the LLM planner."""
        if not observation or not isinstance(observation, LogObservation):
            return "No log observations available."

        obs = observation
        lines = [
            f"Log scan: {obs.identities_scanned} identities scanned",
            f"Total: {obs.total_errors} errors, {obs.total_criticals} criticals",
        ]

        for result in obs.scan_results:
            if result.entries:
                lines.append(f"\n{result.identity}:")
                for entry in result.entries[:10]:
                    msg = entry.message[:150]
                    lines.append(f"  [{entry.level}] {msg}")
                if len(result.entries) > 10:
                    lines.append(f"  ...and {len(result.entries) - 10} more")

        if obs.total_errors == 0 and obs.total_criticals == 0:
            lines.append("\nAll clear — no new errors detected.")

        return "\n".join(lines)


# ── Action Handlers ─────────────────────────────────────────────────────

class _ScanLogsHandler:
    """Handler for scan_logs action.

    Uses observation data from the observer instead of re-scanning
    (scanning advances byte offsets, so a second scan would find nothing).
    """

    def __init__(self, scanner: LogScanner):
        self._scanner = scanner

    async def handle(self, action: Any, observation: Any) -> Any:
        from overblick.core.agentic.models import ActionOutcome

        # Use observation data if available (observer already scanned)
        if isinstance(observation, LogObservation) and observation.scan_results:
            results = observation.scan_results
            total = observation.total_errors + observation.total_criticals
        else:
            # Fallback: fresh scan if no observation available
            results = self._scanner.scan_all()
            total = sum(r.errors_found + r.criticals_found for r in results)

        return ActionOutcome(
            action=action,
            success=True,
            result=f"Scanned {len(results)} identities, found {total} entries",
        )


class _AnalyzePatternHandler:
    """Handler for analyze_pattern action — uses LLM for deep analysis."""

    def __init__(self, llm_pipeline: Any, dry_run: bool = True):
        self._llm_pipeline = llm_pipeline
        self._dry_run = dry_run

    async def handle(self, action: Any, observation: Any) -> Any:
        from overblick.core.agentic.models import ActionOutcome

        if self._dry_run:
            return ActionOutcome(
                action=action, success=True,
                result="DRY RUN: would analyze error patterns",
            )

        if not self._llm_pipeline or not isinstance(observation, LogObservation):
            return ActionOutcome(
                action=action, success=False,
                error="LLM pipeline or observation not available",
            )

        # Collect all entries for analysis
        all_entries = []
        for result in observation.scan_results:
            all_entries.extend(result.entries)

        if not all_entries:
            return ActionOutcome(
                action=action, success=True,
                result="No entries to analyze",
            )

        # Format entries for LLM
        entry_text = "\n".join(
            f"[{e.identity}] [{e.level}] {e.message[:200]}"
            for e in all_entries[:20]
        )

        messages = [
            {"role": "system", "content": (
                "You are a log analysis expert. Analyze these log entries "
                "and identify patterns, root causes, and recommended actions. "
                "Be concise (3-5 sentences)."
            )},
            {"role": "user", "content": f"Log entries:\n{entry_text}"},
        ]

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action="log_agent_analyze",
                complexity="high",
            )
            if result and not result.blocked and result.content:
                return ActionOutcome(
                    action=action, success=True,
                    result=f"Analysis: {result.content.strip()[:500]}",
                )
        except Exception as e:
            logger.error("Log agent: analysis failed: %s", e, exc_info=True)

        return ActionOutcome(
            action=action, success=False,
            error="LLM analysis failed",
        )


class _SendAlertHandler:
    """Handler for send_alert action."""

    def __init__(
        self, notify_fn, formatter: AlertFormatter,
        deduplicator: AlertDeduplicator, dry_run: bool = True,
    ):
        self._notify_fn = notify_fn
        self._formatter = formatter
        self._deduplicator = deduplicator
        self._dry_run = dry_run

    async def handle(self, action: Any, observation: Any) -> Any:
        from overblick.core.agentic.models import ActionOutcome

        if not isinstance(observation, LogObservation):
            return ActionOutcome(
                action=action, success=False,
                error="No log observation available",
            )

        # Filter entries through deduplicator (pure check — don't record yet)
        alertable_results: list[LogScanResult] = []
        all_alertable_entries = []
        for result in observation.scan_results:
            alertable_entries = [
                e for e in result.entries
                if self._deduplicator.would_alert(e)
            ]
            if alertable_entries:
                all_alertable_entries.extend(alertable_entries)
                alertable_results.append(LogScanResult(
                    identity=result.identity,
                    errors_found=sum(1 for e in alertable_entries if e.level == "ERROR"),
                    criticals_found=sum(1 for e in alertable_entries if e.level == "CRITICAL"),
                    entries=alertable_entries,
                ))

        if not alertable_results:
            return ActionOutcome(
                action=action, success=True,
                result="No new alerts to send (all deduplicated)",
            )

        message = self._formatter.format_scan_summary(alertable_results)
        if not message:
            return ActionOutcome(
                action=action, success=True,
                result="No alerts to send",
            )

        if self._dry_run:
            # Record as sent even in dry run (prevents repeated dry-run alerts)
            for entry in all_alertable_entries:
                self._deduplicator.record_sent(entry)
            return ActionOutcome(
                action=action, success=True,
                result=f"DRY RUN: would send alert ({len(alertable_results)} identities)",
            )

        if self._notify_fn:
            try:
                await self._notify_fn(message)
                # Only record as sent after successful delivery
                for entry in all_alertable_entries:
                    self._deduplicator.record_sent(entry)
                return ActionOutcome(
                    action=action, success=True,
                    result=f"Alert sent ({len(alertable_results)} identities)",
                )
            except Exception as e:
                return ActionOutcome(
                    action=action, success=False,
                    error=f"Alert send failed: {e}",
                )

        return ActionOutcome(
            action=action, success=False,
            error="No notification function available",
        )


class _SkipHandler:
    """Handler for skip action."""

    async def handle(self, action: Any, observation: Any) -> Any:
        from overblick.core.agentic.models import ActionOutcome
        return ActionOutcome(
            action=action, success=True,
            result=f"Skipped: {getattr(action, 'reasoning', 'no reason given')}",
        )
