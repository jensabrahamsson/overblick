"""
GitHub agent plugin — agentic repository caretaker.

Inherits AgenticPluginBase to get the OBSERVE/THINK/PLAN/ACT/REFLECT
loop for free. Implements domain-specific methods:
- create_observer() — GitHub API observation
- get_action_handlers() — PR merge, approve, review, issue response, notify
- get_planning_prompt_config() — GitHub-specific prompt configuration

The agent keeps repos healthy by:
- Auto-merging safe Dependabot PRs (patch/minor with passing CI)
- Responding to issues with identity-voiced, code-aware answers
- Notifying the owner about failing CI, stale PRs, and major bumps
- Learning from outcomes to improve over time
"""

import logging
import time
from typing import Any, Optional

from overblick.core.agentic.models import AgentGoal
from overblick.core.agentic.plugin_base import AgenticPluginBase
from overblick.core.agentic.protocols import ActionHandler, Observer, PlanningPromptConfig
from overblick.core.plugin_base import PluginContext
from overblick.plugins.github.action_executor import build_github_handlers
from overblick.plugins.github.client import GitHubAPIClient
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.dependabot_handler import DependabotHandler
from overblick.plugins.github.issue_responder import IssueResponder
from overblick.plugins.github.models import ActionType, PluginState
from overblick.plugins.github.observation import ObservationCollector
from overblick.plugins.github.owner_commands import OwnerCommandQueue
from overblick.plugins.github.response_gen import ResponseGenerator

logger = logging.getLogger(__name__)

# Default goals for the GitHub agent
_DEFAULT_GOALS = [
    AgentGoal(
        name="communicate_with_owner",
        description=(
            "Keep the repository owner informed of significant events "
            "via Telegram. Notify about failing CI, stale PRs, and "
            "important issues. Never spam — only meaningful updates."
        ),
        priority=90,
    ),
    AgentGoal(
        name="merge_safe_dependabot",
        description=(
            "Auto-merge Dependabot PRs that are patch or minor version "
            "bumps with all CI checks passing and mergeable status. "
            "Major bumps require owner approval."
        ),
        priority=80,
    ),
    AgentGoal(
        name="respond_issues_24h",
        description=(
            "Respond to issues labeled 'question', 'help wanted', or "
            "'bug' within 24 hours. Provide technically accurate, "
            "identity-voiced responses with code context where relevant."
        ),
        priority=70,
    ),
    AgentGoal(
        name="no_stale_prs",
        description=(
            "No open PRs should go unreviewed for more than 48 hours. "
            "If a PR is stale, notify the owner."
        ),
        priority=60,
    ),
    AgentGoal(
        name="maintain_codebase_understanding",
        description=(
            "Keep the file tree cache fresh and maintain an up-to-date "
            "understanding of the repository structure. Refresh the "
            "tree periodically and generate repo summaries."
        ),
        priority=40,
    ),
]


class GitHubAgentPlugin(AgenticPluginBase):
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
        self._observer: Optional[ObservationCollector] = None
        self._handlers: dict[str, ActionHandler] = {}
        self._state = PluginState()
        self._check_interval: int = 600  # 10 minutes default
        self._repos: list[str] = []
        self._dry_run: bool = True
        self._command_queue = OwnerCommandQueue()

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

        # Store agentic DB reference for the base class
        self._agentic_db = self._db.agentic

        # API client
        self._client = GitHubAPIClient(token=token)

        # System prompt
        system_prompt = self.get_system_prompt()

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

        # Response generator
        response_gen = ResponseGenerator(
            llm_pipeline=self.ctx.llm_pipeline,
            code_context_builder=code_context,
            system_prompt=system_prompt,
        )

        # ── Initialize domain-specific components ────────────────────────

        # Observer
        self._observer = ObservationCollector(
            client=self._client,
            db=self._db,
            bot_username=bot_username,
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

        # Build handlers for core executor
        self._handlers = build_github_handlers(
            client=self._client,
            dependabot=dependabot,
            issue_responder=issue_responder,
            notify_fn=self._notify_principal,
            dry_run=self._dry_run,
            default_branch=default_branch,
        )

        # ── Wire the agentic loop (provided by AgenticPluginBase) ────────
        await self.setup_agentic_loop(
            max_actions_per_tick=max_actions_per_tick,
            audit_action_prefix="github_agent",
        )

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
            len(self.goal_tracker.active_goals) if self.goal_tracker else 0,
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

        # Fetch owner commands from Telegram before the planning phase
        notifier = self.ctx.get_capability("telegram_notifier")
        if notifier:
            await self._command_queue.fetch_commands(notifier)

        # Run the agentic loop (provided by AgenticPluginBase)
        tick_log = await self.agentic_tick()
        if tick_log:
            self._state.events_processed += tick_log.observations_count
            self._state.comments_posted += tick_log.actions_succeeded

        # Clear processed commands after the tick
        self._command_queue.pop_commands()

        # Update rate limit info
        if self._client:
            self._state.rate_limit_remaining = self._client.rate_limit_remaining

    # ── AgenticPluginBase abstract methods ────────────────────────────────

    async def create_observer(self) -> Observer:
        """Create the GitHub-specific multi-repo observer wrapper."""
        return _MultiRepoObserver(self._observer, self._repos)

    def get_action_handlers(self) -> dict[str, ActionHandler]:
        """Return GitHub action handlers."""
        return self._handlers

    def get_planning_prompt_config(self) -> PlanningPromptConfig:
        """Return GitHub-specific planning prompt configuration."""
        action_types = "|".join(a.value for a in ActionType)
        return PlanningPromptConfig(
            agent_role=(
                "You are a GitHub repository caretaker. Your job is to keep the repo healthy.\n"
                "You observe the current state of the repository, consider your goals, and plan actions."
            ),
            available_actions=(
                "- merge_pr: Merge a pull request (only safe Dependabot PRs with passing CI)\n"
                "- approve_pr: Approve a pull request\n"
                "- review_pr: Leave a review comment on a PR\n"
                "- respond_issue: Respond to a GitHub issue\n"
                "- notify_owner: Send a notification to the repo owner\n"
                "- comment_pr: Leave a comment on a PR\n"
                "- refresh_context: Refresh repository understanding\n"
                "- skip: Do nothing (explain why in reasoning)"
            ),
            safety_rules=(
                "- ONLY merge Dependabot PRs that are patch/minor bumps with ALL CI checks passing\n"
                "- NEVER merge major version bumps — notify the owner instead\n"
                "- NEVER merge non-Dependabot PRs without explicit owner command\n"
                "- When unsure, NOTIFY the owner rather than acting\n"
                "- Owner commands (from Telegram) always take highest priority"
            ),
            output_format_hint=f'Valid action_type values: {action_types}',
            learning_categories="dependabot|issues|ci|general",
        )

    def get_default_goals(self) -> list[AgentGoal]:
        """Return default goals for the GitHub agent."""
        return _DEFAULT_GOALS

    def get_learning_categories(self) -> str:
        """Return GitHub-specific learning categories."""
        return "dependabot|issues|ci|general"

    def get_valid_action_types(self) -> set[str]:
        """Return set of valid GitHub action type strings."""
        return {a.value for a in ActionType}

    def get_extra_planning_context(self) -> str:
        """Inject pending owner commands into the planner's context."""
        return self._command_queue.format_for_planner()

    # ── Plugin-specific methods ──────────────────────────────────────────

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


class _MultiRepoObserver:
    """
    Adapter that wraps ObservationCollector to implement the Observer protocol.

    The core loop expects a single observe() call that returns the complete
    world state. This adapter iterates over configured repos and returns
    a dict[str, RepoObservation].
    """

    def __init__(self, observer: ObservationCollector, repos: list[str]):
        self._observer = observer
        self._repos = repos

    async def observe(self) -> Any:
        """Collect observations for all configured repos."""
        observations = {}
        for repo in self._repos:
            try:
                obs = await self._observer.observe(repo)
                observations[repo] = obs
            except Exception as e:
                logger.error("Observation failed for %s: %s", repo, e, exc_info=True)

        return observations if observations else None

    def format_for_planner(self, observation: Any) -> str:
        """Format all repo observations as text for the LLM planner."""
        if not observation or not isinstance(observation, dict):
            return "No observations available."

        return "\n\n".join(
            self._observer.format_for_planner(obs)
            for obs in observation.values()
        )
