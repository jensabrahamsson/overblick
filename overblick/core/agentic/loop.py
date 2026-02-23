"""
AgentLoop — OBSERVE / THINK / PLAN / ACT / REFLECT orchestration.

Wires together Observer, GoalTracker, ActionPlanner, ActionExecutor,
and ReflectionPipeline to form the complete agentic cycle.
Domain-agnostic: all domain-specific behavior is injected via
protocols and handlers.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from overblick.core.agentic.database import AgenticDB
from overblick.core.agentic.executor import ActionExecutor
from overblick.core.agentic.goal_tracker import GoalTracker
from overblick.core.agentic.models import TickLog
from overblick.core.agentic.planner import ActionPlanner
from overblick.core.agentic.protocols import Observer
from overblick.core.agentic.reflection import ReflectionPipeline

logger = logging.getLogger(__name__)


class AgentLoop:
    """
    The agentic control loop.

    Each tick runs the full cycle:
    1. OBSERVE — gather world state via Observer
    2. THINK  — format observations + goals for the LLM
    3. PLAN   — LLM produces prioritized action list
    4. ACT    — execute top-priority actions
    5. REFLECT — record outcomes, extract learnings
    """

    def __init__(
        self,
        observer: Observer,
        goal_tracker: GoalTracker,
        planner: ActionPlanner,
        executor: ActionExecutor,
        reflection: ReflectionPipeline,
        db: AgenticDB,
        max_actions_per_tick: int = 5,
        get_extra_context: Optional[Any] = None,
    ):
        self._observer = observer
        self._goals = goal_tracker
        self._planner = planner
        self._executor = executor
        self._reflection = reflection
        self._db = db
        self._max_actions = max_actions_per_tick
        self._get_extra_context = get_extra_context
        self._tick_count = 0

    async def setup(self) -> None:
        """Initialize the agent loop (get tick count)."""
        self._tick_count = await self._db.get_tick_count()
        logger.info(
            "Agent loop initialized (tick #%d, %d goals)",
            self._tick_count, len(self._goals.active_goals),
        )

    async def tick(self) -> Optional[TickLog]:
        """
        Run one complete agentic cycle.

        Returns:
            TickLog with cycle details, or None if no work was done
        """
        self._tick_count += 1
        tick_number = self._tick_count
        start_time = time.monotonic()
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info("Agent tick #%d starting", tick_number)

        # -- 1. OBSERVE ------------------------------------------------
        observation = await self._observe()
        if observation is None:
            logger.info("Agent tick #%d: no observations collected", tick_number)
            return None

        observations_text = self._observer.format_for_planner(observation)
        obs_count = self._count_observations(observation)

        # -- 2. THINK -- format state for planner ----------------------
        goals_text = self._goals.format_for_planner()

        recent_actions_rows = await self._db.get_recent_actions(limit=10)
        recent_actions_text = self._format_recent_actions(recent_actions_rows)

        learnings = await self._db.get_learnings(limit=10)
        learnings_text = "\n".join(
            f"- [{l.category}] {l.insight}" for l in learnings
        ) if learnings else ""

        extra_context = ""
        if self._get_extra_context:
            extra_context = self._get_extra_context()

        # -- 3. PLAN ---------------------------------------------------
        plan = await self._planner.plan(
            observations=observations_text,
            goals=goals_text,
            recent_actions=recent_actions_text,
            learnings=learnings_text,
            extra_context=extra_context,
            max_actions=self._max_actions,
        )

        if not plan.actions:
            logger.info("Agent tick #%d: planner produced no actions", tick_number)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            tick_log = TickLog(
                tick_number=tick_number,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                observations_count=obs_count,
                actions_planned=0,
                actions_executed=0,
                actions_succeeded=0,
                reasoning_summary=plan.reasoning[:500] if plan.reasoning else "No actions needed",
                duration_ms=elapsed_ms,
            )
            await self._db.log_tick(tick_log)
            return tick_log

        logger.info(
            "Agent tick #%d: plan has %d actions (reasoning: %s)",
            tick_number, len(plan.actions),
            plan.reasoning[:100] if plan.reasoning else "none",
        )

        # -- 4. ACT ----------------------------------------------------
        outcomes = await self._executor.execute(plan, observation)

        # Log outcomes to DB
        for outcome in outcomes:
            await self._db.log_action(tick_number, outcome)

        succeeded = sum(1 for o in outcomes if o.success)

        # -- 5. REFLECT ------------------------------------------------
        await self._reflection.reflect(tick_number, plan.reasoning, outcomes)

        # -- Record tick -----------------------------------------------
        elapsed_ms = (time.monotonic() - start_time) * 1000
        tick_log = TickLog(
            tick_number=tick_number,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            observations_count=obs_count,
            actions_planned=len(plan.actions),
            actions_executed=len(outcomes),
            actions_succeeded=succeeded,
            reasoning_summary=plan.reasoning[:500] if plan.reasoning else "",
            duration_ms=elapsed_ms,
        )
        await self._db.log_tick(tick_log)

        logger.info(
            "Agent tick #%d complete: %d/%d actions succeeded (%.0fms)",
            tick_number, succeeded, len(outcomes), elapsed_ms,
        )

        return tick_log

    async def _observe(self) -> Any:
        """Run the observer and handle errors."""
        try:
            return await self._observer.observe()
        except Exception as e:
            logger.error("Observation failed: %s", e, exc_info=True)
            return None

    @staticmethod
    def _count_observations(observation: Any) -> int:
        """Estimate observation count. Supports dicts and objects with len()."""
        if isinstance(observation, dict):
            return sum(
                len(v) if isinstance(v, (list, dict)) else 1
                for v in observation.values()
            )
        if hasattr(observation, "__len__"):
            return len(observation)
        return 1

    @staticmethod
    def _format_recent_actions(rows: list[dict]) -> str:
        """Format recent action log entries for the planner."""
        if not rows:
            return ""
        parts = []
        for r in rows[:10]:
            status = "OK" if r.get("success") else "FAIL"
            parts.append(
                f"- [{status}] {r.get('action_type', '?')} on {r.get('target', '?')} "
                f"({r.get('created_at', '?')})"
            )
        return "\n".join(parts)
