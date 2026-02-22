"""
GoalTracker — persistent goal management for the GitHub agent.

Manages the agent's goals, tracks progress, and provides
goal context for the LLM planner. Goals are stored in the database
and survive restarts.
"""

import logging
from typing import Optional

from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.models import AgentGoal, GoalStatus

logger = logging.getLogger(__name__)

# Default goals created on first run
DEFAULT_GOALS = [
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


class GoalTracker:
    """
    Manages the agent's persistent goals.

    Goals are stored in the database and loaded at startup.
    Default goals are created on first run.
    """

    def __init__(self, db: GitHubDB):
        self._db = db
        self._goals: list[AgentGoal] = []

    async def setup(self) -> None:
        """Load goals from database, creating defaults if empty."""
        self._goals = await self._db.get_goals(status="active")

        if not self._goals:
            logger.info("GitHub agent: no goals found, creating defaults")
            await self._create_defaults()
            self._goals = await self._db.get_goals(status="active")

        logger.info(
            "GitHub agent: %d active goals loaded",
            len(self._goals),
        )

    async def _create_defaults(self) -> None:
        """Insert default goals into the database."""
        for goal in DEFAULT_GOALS:
            existing = await self._db.get_goal_by_name(goal.name)
            if not existing:
                await self._db.upsert_goal(goal)
                logger.info("GitHub agent: created default goal '%s'", goal.name)

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
            # Refresh local cache
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
