"""
GoalTracker — persistent goal management for agentic plugins.

Manages the agent's goals, tracks progress, and provides
goal context for the LLM planner. Goals are stored in the database
and survive restarts.
"""

import logging
from typing import Optional

from overblick.core.agentic.database import AgenticDB
from overblick.core.agentic.models import AgentGoal, GoalStatus

logger = logging.getLogger(__name__)


class GoalTracker:
    """
    Manages the agent's persistent goals.

    Goals are stored in the database and loaded at startup.
    If no goals exist, plugins can provide defaults via
    AgenticPluginBase.get_default_goals().
    """

    def __init__(self, db: AgenticDB):
        self._db = db
        self._goals: list[AgentGoal] = []

    async def setup(self, default_goals: Optional[list[AgentGoal]] = None) -> None:
        """Load goals from database, creating defaults if empty."""
        self._goals = await self._db.get_goals(status="active")

        if not self._goals and default_goals:
            logger.info("Agent: no goals found, creating %d defaults", len(default_goals))
            await self._create_defaults(default_goals)
            self._goals = await self._db.get_goals(status="active")

        logger.info("Agent: %d active goals loaded", len(self._goals))

    async def _create_defaults(self, defaults: list[AgentGoal]) -> None:
        """Insert default goals into the database."""
        for goal in defaults:
            existing = await self._db.get_goal_by_name(goal.name)
            if not existing:
                await self._db.upsert_goal(goal)
                logger.info("Agent: created default goal '%s'", goal.name)

    @property
    def active_goals(self) -> list[AgentGoal]:
        """Get all active goals sorted by priority (descending)."""
        return sorted(self._goals, key=lambda g: g.priority, reverse=True)

    async def update_progress(self, goal_name: str, progress: float) -> None:
        """Update goal progress (0.0 to 1.0)."""
        goal = await self._db.get_goal_by_name(goal_name)
        if goal:
            updated = goal.model_copy(update={"progress": min(max(progress, 0.0), 1.0)})
            await self._db.upsert_goal(updated)
            self._goals = await self._db.get_goals(status="active")

    async def get_goal(self, name: str) -> Optional[AgentGoal]:
        """Get a specific goal by name."""
        return await self._db.get_goal_by_name(name)

    def format_for_planner(self) -> str:
        """Format goals as text for the LLM planner."""
        if not self._goals:
            return "No active goals."

        parts = []
        for goal in self.active_goals:
            progress_pct = goal.progress * 100
            parts.append(
                f"- [{goal.priority}] {goal.name}: {goal.description} "
                f"(progress: {progress_pct:.0f}%)"
            )

        return "\n".join(parts)
