"""
AgenticPluginBase — extends PluginBase with agentic scaffolding.

Provides the agentic loop infrastructure so plugins only need to
implement domain-specific methods:
- create_observer() -> Observer
- get_action_handlers() -> dict[str, ActionHandler]
- get_planning_prompt_config() -> PlanningPromptConfig

Optional overrides:
- get_default_goals() -> list[AgentGoal]
- get_extra_planning_context() -> str
"""

import logging
from abc import abstractmethod
from typing import Optional

from overblick.core.agentic.database import AGENTIC_MIGRATIONS, AgenticDB
from overblick.core.agentic.executor import ActionExecutor
from overblick.core.agentic.goal_tracker import GoalTracker
from overblick.core.agentic.loop import AgentLoop
from overblick.core.agentic.models import AgentGoal, TickLog
from overblick.core.agentic.planner import ActionPlanner
from overblick.core.agentic.protocols import ActionHandler, Observer, PlanningPromptConfig
from overblick.core.agentic.reflection import ReflectionPipeline
from overblick.core.database.base import DatabaseBackend, MigrationManager, Migration
from overblick.core.plugin_base import PluginBase, PluginContext

logger = logging.getLogger(__name__)


class AgenticPluginBase(PluginBase):
    """
    Base class for agentic plugins.

    Extends PluginBase with the OBSERVE/THINK/PLAN/ACT/REFLECT loop.
    Subclasses implement domain-specific methods; the agentic infrastructure
    is wired automatically.

    Usage in subclass setup():
        1. Create your DatabaseBackend
        2. Call setup_agentic_db(backend, extra_migrations)
        3. Create domain-specific components
        4. Call setup_agentic_loop()

    Usage in subclass tick():
        1. Guard checks (interval, quiet hours, LLM availability)
        2. Call agentic_tick()
    """

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._agentic_db: Optional[AgenticDB] = None
        self._goal_tracker: Optional[GoalTracker] = None
        self._agent_loop: Optional[AgentLoop] = None
        self._max_actions_per_tick: int = 5

    # ── Abstract methods (plugin MUST implement) ─────────────────────────

    @abstractmethod
    async def create_observer(self) -> Observer:
        """Create and return the domain-specific observer."""

    @abstractmethod
    def get_action_handlers(self) -> dict[str, ActionHandler]:
        """Return a dict mapping action_type strings to handlers."""

    @abstractmethod
    def get_planning_prompt_config(self) -> PlanningPromptConfig:
        """Return domain-specific planning prompt configuration."""

    # ── Optional overrides ───────────────────────────────────────────────

    def get_default_goals(self) -> list[AgentGoal]:
        """Return default goals for first-run initialization. Override in subclass."""
        return []

    def get_extra_planning_context(self) -> str:
        """Return extra context for the planner (e.g. owner commands). Override in subclass."""
        return ""

    def get_learning_categories(self) -> str:
        """Return domain-specific learning categories for reflection. Override in subclass."""
        return "general"

    def get_valid_action_types(self) -> Optional[set[str]]:
        """Return set of valid action type strings. None = accept all."""
        return set(self.get_action_handlers().keys())

    def get_system_prompt(self) -> str:
        """Build the system prompt. Override for custom behavior."""
        try:
            identity = self.ctx.load_identity(self.ctx.identity_name)
            return self.ctx.build_system_prompt(identity, platform=self.name)
        except FileNotFoundError:
            return f"You are an autonomous agent ({self.name})."

    # ── Agentic setup helpers ────────────────────────────────────────────

    async def setup_agentic_db(
        self,
        backend: DatabaseBackend,
        extra_migrations: Optional[list[Migration]] = None,
    ) -> AgenticDB:
        """
        Set up the agentic database layer.

        Applies both the plugin's extra_migrations and AGENTIC_MIGRATIONS.
        Returns the AgenticDB instance for goal/learning/tick queries.
        """
        await backend.connect()

        # Combine plugin-specific and agentic migrations
        all_migrations = list(extra_migrations or []) + list(AGENTIC_MIGRATIONS)
        migration_mgr = MigrationManager(backend)
        await migration_mgr.apply(all_migrations)

        self._agentic_db = AgenticDB(backend)
        return self._agentic_db

    async def setup_agentic_loop(
        self,
        max_actions_per_tick: int = 5,
        audit_action_prefix: str = "agent",
        complexity: str = "high",
    ) -> AgentLoop:
        """
        Wire and return the complete agentic loop.

        Must be called after setup_agentic_db() and after the plugin
        has created its domain-specific components.
        """
        if not self._agentic_db:
            raise RuntimeError("Call setup_agentic_db() before setup_agentic_loop()")

        self._max_actions_per_tick = max_actions_per_tick
        system_prompt = self.get_system_prompt()

        # Goal tracker
        self._goal_tracker = GoalTracker(db=self._agentic_db)
        await self._goal_tracker.setup(default_goals=self.get_default_goals())

        # Observer
        observer = await self.create_observer()

        # Planner
        planner = ActionPlanner(
            llm_pipeline=self.ctx.llm_pipeline,
            system_prompt=system_prompt,
            prompt_config=self.get_planning_prompt_config(),
            valid_actions=self.get_valid_action_types(),
            audit_action=f"{audit_action_prefix}_planning",
            complexity=complexity,
        )

        # Executor
        executor = ActionExecutor(
            handlers=self.get_action_handlers(),
            max_actions_per_tick=max_actions_per_tick,
        )

        # Reflection
        reflection = ReflectionPipeline(
            db=self._agentic_db,
            llm_pipeline=self.ctx.llm_pipeline,
            system_prompt=system_prompt,
            learning_categories=self.get_learning_categories(),
            audit_action=f"{audit_action_prefix}_reflection",
        )

        # Wire the loop
        self._agent_loop = AgentLoop(
            observer=observer,
            goal_tracker=self._goal_tracker,
            planner=planner,
            executor=executor,
            reflection=reflection,
            db=self._agentic_db,
            max_actions_per_tick=max_actions_per_tick,
            get_extra_context=self.get_extra_planning_context,
        )
        await self._agent_loop.setup()

        return self._agent_loop

    async def agentic_tick(self) -> Optional[TickLog]:
        """
        Run one agentic tick cycle.

        Returns the TickLog, or None if no work was done.
        Call this from your plugin's tick() method after guard checks.
        """
        if not self._agent_loop:
            logger.warning("Agent loop not initialized — call setup_agentic_loop() first")
            return None

        return await self._agent_loop.tick()

    @property
    def goal_tracker(self) -> Optional[GoalTracker]:
        """Access the goal tracker (available after setup_agentic_loop)."""
        return self._goal_tracker

    @property
    def agentic_db(self) -> Optional[AgenticDB]:
        """Access the agentic DB (available after setup_agentic_db)."""
        return self._agentic_db
