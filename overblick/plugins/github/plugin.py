"""
GitHub agent plugin — agentic repository caretaker.

Replaces the old reactive bot pattern with an agentic loop:
OBSERVE world state -> THINK about goals -> PLAN actions -> ACT -> REFLECT

The agent keeps repos healthy by:
- Auto-merging safe Dependabot PRs (patch/minor with passing CI)
- Responding to issues with identity-voiced, code-aware answers
- Notifying the owner about failing CI, stale PRs, and major bumps
- Learning from outcomes to improve over time
"""

import logging
import time
from typing import Optional

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.plugins.github.action_executor import ActionExecutor
from overblick.plugins.github.agent_loop import AgentLoop
from overblick.plugins.github.client import GitHubAPIClient
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.dependabot_handler import DependabotHandler
from overblick.plugins.github.goal_system import GoalTracker
from overblick.plugins.github.issue_responder import IssueResponder
from overblick.plugins.github.models import PluginState
from overblick.plugins.github.observation import ObservationCollector
from overblick.plugins.github.planner import ActionPlanner
from overblick.plugins.github.response_gen import ResponseGenerator

logger = logging.getLogger(__name__)


class GitHubAgentPlugin(PluginBase):
    """
    Agentic GitHub plugin — keeps repositories healthy.

    Uses the OBSERVE/THINK/PLAN/ACT/REFLECT loop to autonomously
    manage PRs, issues, and CI status across configured repos.
    """

    name = "github"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._db: Optional[GitHubDB] = None
        self._client: Optional[GitHubAPIClient] = None
        self._agent_loop: Optional[AgentLoop] = None
        self._state = PluginState()
        self._check_interval: int = 600  # 10 minutes default
        self._repos: list[str] = []
        self._dry_run: bool = True

    async def setup(self) -> None:
        """Initialize all components and wire the agentic loop."""
        identity = self.ctx.identity
        if not identity:
            raise RuntimeError("GitHubAgentPlugin requires an identity")

        # Load config from identity YAML
        raw_config = identity.raw_config
        gh_config = raw_config.get("github", {})

        self._repos = gh_config.get("repos", [])
        if not self._repos:
            raise RuntimeError("GitHubAgentPlugin: no repos configured")

        self._dry_run = gh_config.get("dry_run", True)
        bot_username = gh_config.get("bot_username", "")
        default_branch = gh_config.get("default_branch", "main")
        max_actions_per_tick = gh_config.get("max_actions_per_tick", 5)
        tick_interval_minutes = gh_config.get("tick_interval_minutes", 10)
        self._check_interval = tick_interval_minutes * 60

        # Dependabot config
        dep_config = gh_config.get("dependabot", {})
        auto_merge_patch = dep_config.get("auto_merge_patch", True)
        auto_merge_minor = dep_config.get("auto_merge_minor", True)
        auto_merge_major = dep_config.get("auto_merge_major", False)
        require_ci_pass = dep_config.get("require_ci_pass", True)

        # Issue config
        issue_config = gh_config.get("issues", {})
        respond_to_labels = issue_config.get(
            "respond_to_labels", ["question", "help wanted", "bug"],
        )
        max_response_age_hours = issue_config.get("max_response_age_hours", 168)

        # Code context config
        cc_config = gh_config.get("code_context", {})

        # Load GitHub token
        token = self.ctx.get_secret("github_token") or ""
        if not token:
            logger.warning("GitHubAgentPlugin: no github_token secret — read-only mode")

        # ── Initialize infrastructure ────────────────────────────────────

        # Database
        db_path = self.ctx.data_dir / "github.db"
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        self._db = GitHubDB(backend)
        await self._db.setup()

        # API client
        self._client = GitHubAPIClient(token=token)

        # System prompt
        system_prompt = self._build_system_prompt()

        # Code context builder
        code_context = CodeContextBuilder(
            client=self._client,
            db=self._db,
            llm_pipeline=self.ctx.llm_pipeline,
            max_files=cc_config.get("max_files_per_question", 12),
            max_file_size=cc_config.get("max_file_size_bytes", 100000),
            max_context_chars=cc_config.get("max_context_chars", 100000),
            tree_refresh_minutes=cc_config.get("tree_refresh_minutes", 30),
        )

        # Response generator (reused from existing code)
        response_gen = ResponseGenerator(
            llm_pipeline=self.ctx.llm_pipeline,
            code_context_builder=code_context,
            system_prompt=system_prompt,
        )

        # ── Initialize agentic components ────────────────────────────────

        # Observer
        observer = ObservationCollector(
            client=self._client,
            db=self._db,
            bot_username=bot_username,
        )

        # Goal tracker
        goal_tracker = GoalTracker(db=self._db)

        # Planner
        planner = ActionPlanner(
            llm_pipeline=self.ctx.llm_pipeline,
            system_prompt=system_prompt,
        )

        # Dependabot handler
        dependabot = DependabotHandler(
            client=self._client,
            db=self._db,
            llm_pipeline=self.ctx.llm_pipeline,
            system_prompt=system_prompt,
            auto_merge_patch=auto_merge_patch,
            auto_merge_minor=auto_merge_minor,
            auto_merge_major=auto_merge_major,
            require_ci_pass=require_ci_pass,
            dry_run=self._dry_run,
        )

        # Issue responder
        issue_responder = IssueResponder(
            client=self._client,
            db=self._db,
            response_gen=response_gen,
            llm_pipeline=self.ctx.llm_pipeline,
            dry_run=self._dry_run,
            respond_to_labels=respond_to_labels,
            max_response_age_hours=max_response_age_hours,
        )

        # Executor
        executor = ActionExecutor(
            client=self._client,
            db=self._db,
            dependabot_handler=dependabot,
            issue_responder=issue_responder,
            notify_fn=self._notify_principal,
            max_actions_per_tick=max_actions_per_tick,
            dry_run=self._dry_run,
            default_branch=default_branch,
        )

        # ── Wire the agent loop ──────────────────────────────────────────

        self._agent_loop = AgentLoop(
            observer=observer,
            goal_tracker=goal_tracker,
            planner=planner,
            executor=executor,
            db=self._db,
            llm_pipeline=self.ctx.llm_pipeline,
            system_prompt=system_prompt,
            repos=self._repos,
            max_actions_per_tick=max_actions_per_tick,
        )
        await self._agent_loop.setup()

        # Load stats
        stats = await self._db.get_stats()
        self._state.events_processed = stats.get("events_processed", 0)
        self._state.comments_posted = stats.get("comments_posted", 0)
        self._state.repos_monitored = len(self._repos)

        mode = "DRY RUN" if self._dry_run else "LIVE"
        logger.info(
            "GitHubAgentPlugin [%s] setup for '%s' (repos: %s, %d goals)",
            mode, self.ctx.identity_name,
            ", ".join(self._repos),
            len(goal_tracker.active_goals),
        )

    async def tick(self) -> None:
        """
        Run the agentic loop.

        Guards: interval check, quiet hours, LLM pipeline availability.
        """
        now = time.time()

        # Interval guard
        if self._state.last_check and (now - self._state.last_check < self._check_interval):
            return

        # Quiet hours guard
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        # LLM pipeline guard
        if not self.ctx.llm_pipeline:
            logger.debug("GitHub agent: no LLM pipeline available")
            return

        self._state.last_check = now

        # Run the agentic loop
        if self._agent_loop:
            tick_log = await self._agent_loop.tick()
            if tick_log:
                self._state.events_processed += tick_log.observations_count
                self._state.comments_posted += tick_log.actions_succeeded

        # Update rate limit info
        if self._client:
            self._state.rate_limit_remaining = self._client.rate_limit_remaining

    async def _notify_principal(self, message: str) -> bool:
        """Send a notification via TelegramNotifier capability."""
        notifier = self.ctx.get_capability("telegram_notifier")
        if not notifier:
            logger.debug("GitHub agent: telegram_notifier capability not available")
            return False

        try:
            await notifier.send_notification(message)
            self._state.notifications_sent += 1
            return True
        except Exception as e:
            logger.warning("GitHub agent: notification failed: %s", e)
            return False

    def _build_system_prompt(self) -> str:
        """Build system prompt from identity personality."""
        try:
            identity = self.ctx.load_identity(self.ctx.identity_name)
            return self.ctx.build_system_prompt(identity, platform="GitHub")
        except FileNotFoundError:
            return (
                "You are a helpful GitHub repository caretaker. "
                "Keep the repo healthy by reviewing PRs, responding to issues, "
                "and notifying the owner of important events."
            )

    def get_status(self) -> dict:
        """Expose status for dashboard."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "events_processed": self._state.events_processed,
            "comments_posted": self._state.comments_posted,
            "notifications_sent": self._state.notifications_sent,
            "repos_monitored": self._state.repos_monitored,
            "rate_limit_remaining": self._state.rate_limit_remaining,
            "dry_run": self._dry_run,
            "health": self._state.current_health,
        }

    async def teardown(self) -> None:
        """Cleanup database and HTTP session."""
        if self._client:
            await self._client.close()
        if self._db:
            await self._db.close()
        logger.info("GitHubAgentPlugin teardown complete")
