"""
Dev agent plugin — autonomous bug-fixing agent ("Smed").

Inherits AgenticPluginBase to get the OBSERVE/THINK/PLAN/ACT/REFLECT
loop for free. Implements domain-specific methods:
- create_observer() — Bug observation from IPC, logs, and DB
- get_action_handlers() — analyze, fix, test, PR, notify, clean, skip
- get_planning_prompt_config() — Dev-agent-specific prompt configuration

The agent keeps the codebase healthy by:
- Scanning logs for ERROR patterns and tracebacks
- Receiving bug reports from the GitHub agent via IPC
- Using opencode + Devstral 2 to analyze and fix bugs
- Running pytest to validate fixes
- Creating PRs via gh CLI
- Learning from outcomes to improve over time
"""

import logging
import time
from pathlib import Path
from typing import Optional

from overblick.core.agentic.models import AgentGoal
from overblick.core.agentic.plugin_base import AgenticPluginBase
from overblick.core.agentic.protocols import ActionHandler, Observer, PlanningPromptConfig
from overblick.core.plugin_base import PluginContext
from overblick.plugins.dev_agent.action_handlers import build_dev_agent_handlers
from overblick.plugins.dev_agent.database import DevAgentDB
from overblick.plugins.dev_agent.log_watcher import LogWatcher
from overblick.plugins.dev_agent.models import ActionType
from overblick.plugins.dev_agent.observation import BugObserver
from overblick.plugins.dev_agent.opencode_runner import OpencodeRunner
from overblick.plugins.dev_agent.pr_creator import PRCreator
from overblick.plugins.dev_agent.prompts import get_dev_agent_prompt_config
from overblick.plugins.dev_agent.test_runner import TestRunner
from overblick.plugins.dev_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

# Default goals for the dev agent
_DEFAULT_GOALS = [
    AgentGoal(
        name="fix_bugs",
        description=(
            "Autonomously fix bugs found in GitHub issues and log files. "
            "Analyze the root cause, write a fix, run tests, and create a PR. "
            "Never commit directly to main."
        ),
        priority=90,
    ),
    AgentGoal(
        name="fix_log_errors",
        description=(
            "Monitor log files from all identities for ERROR and CRITICAL "
            "patterns. Create bug reports for new errors and attempt to fix them."
        ),
        priority=80,
    ),
    AgentGoal(
        name="maintain_test_health",
        description=(
            "Ensure the test suite passes after every fix. Never commit "
            "code that breaks existing tests. Run the full test suite "
            "regularly to catch regressions."
        ),
        priority=70,
    ),
    AgentGoal(
        name="keep_workspace_clean",
        description=(
            "Keep the workspace tidy. Delete merged branches, sync main "
            "regularly, and clean up stale fix branches."
        ),
        priority=40,
    ),
]


class DevAgentPlugin(AgenticPluginBase):
    """
    Agentic dev agent plugin — autonomous bug fixer.

    Uses the OBSERVE/THINK/PLAN/ACT/REFLECT loop to autonomously
    find, analyze, fix, test, and PR bugs.
    """

    name = "dev_agent"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._db: Optional[DevAgentDB] = None
        self._workspace: Optional[WorkspaceManager] = None
        self._opencode: Optional[OpencodeRunner] = None
        self._test_runner: Optional[TestRunner] = None
        self._pr_creator: Optional[PRCreator] = None
        self._log_watcher: Optional[LogWatcher] = None
        self._observer: Optional[BugObserver] = None
        self._handlers: dict[str, ActionHandler] = {}
        self._check_interval: int = 1800  # 30 minutes default
        self._last_check: Optional[float] = None
        self._dry_run: bool = True

    async def setup(self) -> None:
        """Initialize all components and wire the agentic loop."""
        identity = self.ctx.identity
        if not identity:
            raise RuntimeError("DevAgentPlugin requires an identity")

        # Load config from identity YAML
        raw_config = identity.raw_config
        da_config = raw_config.get("dev_agent", {})

        repo_url = da_config.get("repo_url", "")
        if not repo_url:
            raise RuntimeError("DevAgentPlugin: no repo_url configured")

        workspace_dir = da_config.get("workspace_dir", "workspace/overblick")
        default_branch = da_config.get("default_branch", "main")
        self._dry_run = da_config.get("dry_run", True)
        max_fix_attempts = da_config.get("max_fix_attempts", 3)
        max_actions_per_tick = da_config.get("max_actions_per_tick", 3)
        tick_interval_minutes = da_config.get("tick_interval_minutes", 30)
        self._check_interval = tick_interval_minutes * 60

        # Opencode config
        oc_config = da_config.get("opencode", {})
        oc_model = oc_config.get("model", "lmstudio/devstral-2-123b-iq5")
        oc_timeout = oc_config.get("timeout_seconds", 600)

        # Log watcher config
        lw_config = da_config.get("log_watcher", {})
        lw_enabled = lw_config.get("enabled", True)
        lw_identities = lw_config.get("scan_identities", [])

        # ── Initialize infrastructure ────────────────────────────────────

        # Database
        db_path = self.ctx.data_dir / "dev_agent.db"
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        self._db = DevAgentDB(backend)
        await self._db.setup()

        # Store agentic DB reference for the base class
        self._agentic_db = self._db.agentic

        # Workspace
        workspace_path = self.ctx.data_dir / workspace_dir
        self._workspace = WorkspaceManager(
            workspace_path=workspace_path,
            repo_url=repo_url,
            default_branch=default_branch,
            dry_run=self._dry_run,
        )

        # Opencode runner
        self._opencode = OpencodeRunner(
            workspace_path=workspace_path,
            model=oc_model,
            timeout=oc_timeout,
            dry_run=self._dry_run,
        )

        # Test runner
        self._test_runner = TestRunner(
            workspace_path=workspace_path,
            dry_run=self._dry_run,
        )

        # PR creator
        self._pr_creator = PRCreator(
            workspace_path=workspace_path,
            default_branch=default_branch,
            dry_run=self._dry_run,
        )

        # Log watcher
        base_log_dir = Path("data")  # data/<identity>/logs/
        self._log_watcher = LogWatcher(
            base_log_dir=base_log_dir,
            scan_identities=lw_identities,
            enabled=lw_enabled,
        )

        # Observer
        self._observer = BugObserver(
            db=self._db,
            log_watcher=self._log_watcher,
            workspace_state_fn=self._workspace.get_state,
        )

        # Build action handlers
        self._handlers = build_dev_agent_handlers(
            db=self._db,
            workspace=self._workspace,
            opencode=self._opencode,
            test_runner=self._test_runner,
            pr_creator=self._pr_creator,
            notify_fn=self._notify_principal,
            dry_run=self._dry_run,
        )

        # ── Wire the agentic loop (provided by AgenticPluginBase) ────────
        await self.setup_agentic_loop(
            max_actions_per_tick=max_actions_per_tick,
            audit_action_prefix="dev_agent",
        )

        # Register IPC handlers
        self._register_ipc_handlers()

        mode = "DRY RUN" if self._dry_run else "LIVE"
        logger.info(
            "DevAgentPlugin [%s] setup for '%s' (repo: %s, workspace: %s, %d goals)",
            mode, self.ctx.identity_name,
            repo_url, workspace_path,
            len(self.goal_tracker.active_goals) if self.goal_tracker else 0,
        )

    async def tick(self) -> None:
        """
        Run the agentic loop.

        Guards: interval check, quiet hours, LLM pipeline availability.
        """
        now = time.time()

        # Interval guard
        if self._last_check and (now - self._last_check < self._check_interval):
            return

        # Quiet hours guard
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        # LLM pipeline guard
        if not self.ctx.llm_pipeline:
            logger.debug("Dev agent: no LLM pipeline available")
            return

        self._last_check = now

        # Run the agentic loop (provided by AgenticPluginBase)
        await self.agentic_tick()

    # ── AgenticPluginBase abstract methods ────────────────────────────────

    async def create_observer(self) -> Observer:
        """Create the dev-agent-specific observer."""
        return self._observer

    def get_action_handlers(self) -> dict[str, ActionHandler]:
        """Return dev agent action handlers."""
        return self._handlers

    def get_planning_prompt_config(self) -> PlanningPromptConfig:
        """Return dev-agent-specific planning prompt configuration."""
        return get_dev_agent_prompt_config()

    def get_default_goals(self) -> list[AgentGoal]:
        """Return default goals for the dev agent."""
        return _DEFAULT_GOALS

    def get_learning_categories(self) -> str:
        """Return dev-agent-specific learning categories."""
        return "bug_analysis|code_fixes|test_patterns|pr_creation|general"

    def get_valid_action_types(self) -> set[str]:
        """Return set of valid dev agent action type strings."""
        return {a.value for a in ActionType}

    # ── Plugin-specific methods ──────────────────────────────────────────

    def _register_ipc_handlers(self) -> None:
        """Register IPC message handlers for bug reports and log alerts."""
        if hasattr(self.ctx, "ipc_server") and self.ctx.ipc_server:
            self.ctx.ipc_server.on("bug_report", self._handle_ipc_bug_report)
            self.ctx.ipc_server.on("log_alert", self._handle_ipc_log_alert)

    async def _handle_ipc_bug_report(self, msg) -> None:
        """Handle incoming bug_report IPC message."""
        if self._observer:
            self._observer.enqueue_ipc_message("bug_report", msg.payload)
        logger.info("Received bug_report IPC: %s", msg.payload.get("title", ""))

    async def _handle_ipc_log_alert(self, msg) -> None:
        """Handle incoming log_alert IPC message."""
        if self._observer:
            self._observer.enqueue_ipc_message("log_alert", msg.payload)
        logger.info("Received log_alert IPC: %s", msg.payload.get("message", ""))

    async def _notify_principal(self, message: str) -> bool:
        """Send a notification via TelegramNotifier capability."""
        notifier = self.ctx.get_capability("telegram_notifier")
        if not notifier:
            logger.debug("Dev agent: telegram_notifier capability not available")
            return False

        try:
            await notifier.send_notification(message)
            return True
        except Exception as e:
            logger.warning("Dev agent: notification failed: %s", e)
            return False

    def get_status(self) -> dict:
        """Expose status for dashboard."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "dry_run": self._dry_run,
        }

    async def teardown(self) -> None:
        """Cleanup database."""
        if self._db:
            await self._db.close()
        logger.info("DevAgentPlugin teardown complete")
