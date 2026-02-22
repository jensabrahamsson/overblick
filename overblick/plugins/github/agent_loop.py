"""
AgentLoop — OBSERVE / THINK / PLAN / ACT / REFLECT orchestration.

Wires together ObservationCollector, GoalTracker, ActionPlanner,
ActionExecutor to form the complete agentic cycle.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from overblick.plugins.github.action_executor import ActionExecutor
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.goal_system import GoalTracker
from overblick.plugins.github.models import (
    ActionOutcome,
    AgentLearning,
    RepoObservation,
    TickLog,
)
from overblick.plugins.github.observation import ObservationCollector
from overblick.plugins.github.planner import ActionPlanner
from overblick.plugins.github.prompts import reflection_prompt

logger = logging.getLogger(__name__)


class AgentLoop:
    """
    The agentic control loop for the GitHub plugin.

    Each tick runs the full cycle:
    1. OBSERVE — gather world state from GitHub API
    2. THINK  — format observations + goals for the LLM
    3. PLAN   — LLM produces prioritized action list
    4. ACT    — execute top-priority actions
    5. REFLECT — record outcomes, extract learnings
    """

    def __init__(
        self,
        observer: ObservationCollector,
        goal_tracker: GoalTracker,
        planner: ActionPlanner,
        executor: ActionExecutor,
        db: GitHubDB,
        llm_pipeline=None,
        system_prompt: str = "",
        repos: Optional[list[str]] = None,
        max_actions_per_tick: int = 5,
    ):
        self._observer = observer
        self._goals = goal_tracker
        self._planner = planner
        self._executor = executor
        self._db = db
        self._llm_pipeline = llm_pipeline
        self._system_prompt = system_prompt
        self._repos = repos or []
        self._max_actions = max_actions_per_tick
        self._tick_count = 0

    async def setup(self) -> None:
        """Initialize the agent loop (load goals, get tick count)."""
        await self._goals.setup()
        self._tick_count = await self._db.get_tick_count()
        logger.info(
            "GitHub agent loop initialized (tick #%d, %d repos, %d goals)",
            self._tick_count, len(self._repos), len(self._goals.active_goals),
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

        logger.info("GitHub agent tick #%d starting", tick_number)

        # ── 1. OBSERVE ──────────────────────────────────────────────────
        observations: dict[str, RepoObservation] = {}
        for repo in self._repos:
            try:
                obs = await self._observer.observe(repo)
                observations[repo] = obs
            except Exception as e:
                logger.error("Observation failed for %s: %s", repo, e, exc_info=True)

        if not observations:
            logger.info("GitHub agent tick #%d: no observations collected", tick_number)
            return None

        total_obs = sum(
            len(o.open_prs) + len(o.open_issues) for o in observations.values()
        )

        # ── 2. THINK — format state for planner ────────────────────────
        observations_text = "\n\n".join(
            self._observer.format_for_planner(obs)
            for obs in observations.values()
        )
        goals_text = self._goals.format_for_planner()

        # Get recent actions for context
        recent_actions_rows = await self._db.get_recent_actions(limit=10)
        recent_actions_text = self._format_recent_actions(recent_actions_rows)

        # Get learnings
        learnings = await self._db.get_learnings(limit=10)
        learnings_text = "\n".join(
            f"- [{l.category}] {l.insight}" for l in learnings
        ) if learnings else ""

        # ── 3. PLAN ─────────────────────────────────────────────────────
        plan = await self._planner.plan(
            observations=observations_text,
            goals=goals_text,
            recent_actions=recent_actions_text,
            learnings=learnings_text,
            max_actions=self._max_actions,
        )

        if not plan.actions:
            logger.info("GitHub agent tick #%d: planner produced no actions", tick_number)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            tick_log = TickLog(
                tick_number=tick_number,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                observations_count=total_obs,
                actions_planned=0,
                actions_executed=0,
                actions_succeeded=0,
                reasoning_summary=plan.reasoning[:500] if plan.reasoning else "No actions needed",
                duration_ms=elapsed_ms,
            )
            await self._db.log_tick(tick_log)
            return tick_log

        logger.info(
            "GitHub agent tick #%d: plan has %d actions (reasoning: %s)",
            tick_number, len(plan.actions),
            plan.reasoning[:100] if plan.reasoning else "none",
        )

        # ── 4. ACT ──────────────────────────────────────────────────────
        outcomes = await self._executor.execute(plan, observations)

        # Log outcomes to DB
        for outcome in outcomes:
            await self._db.log_action(tick_number, outcome)

        succeeded = sum(1 for o in outcomes if o.success)

        # ── 5. REFLECT ──────────────────────────────────────────────────
        await self._reflect(tick_number, plan.reasoning, outcomes)

        # ── Record tick ──────────────────────────────────────────────────
        elapsed_ms = (time.monotonic() - start_time) * 1000
        tick_log = TickLog(
            tick_number=tick_number,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            observations_count=total_obs,
            actions_planned=len(plan.actions),
            actions_executed=len(outcomes),
            actions_succeeded=succeeded,
            reasoning_summary=plan.reasoning[:500] if plan.reasoning else "",
            duration_ms=elapsed_ms,
        )
        await self._db.log_tick(tick_log)

        logger.info(
            "GitHub agent tick #%d complete: %d/%d actions succeeded (%.0fms)",
            tick_number, succeeded, len(outcomes), elapsed_ms,
        )

        return tick_log

    async def _reflect(
        self,
        tick_number: int,
        planning_reasoning: str,
        outcomes: list[ActionOutcome],
    ) -> None:
        """
        Extract learnings from the tick's outcomes via LLM.

        Skips reflection if no LLM is available or no actions were taken.
        """
        if not outcomes or not self._llm_pipeline:
            return

        # Format outcomes for reflection
        outcomes_text = "\n".join(
            f"- {o.action.action_type.value} on {o.action.target}: "
            f"{'SUCCESS' if o.success else 'FAILED'} — "
            f"{o.result if o.success else o.error}"
            for o in outcomes
        )

        tick_summary = (
            f"Tick #{tick_number}\n"
            f"Planning reasoning: {planning_reasoning}\n"
            f"Actions executed: {len(outcomes)}\n"
            f"Succeeded: {sum(1 for o in outcomes if o.success)}"
        )

        messages = reflection_prompt(
            system_prompt=self._system_prompt,
            tick_summary=tick_summary,
            action_outcomes=outcomes_text,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action="github_agent_reflection",
                skip_preflight=True,
                complexity="low",
                priority="low",
            )

            if result and result.content and not result.blocked:
                await self._store_learnings(tick_number, result.content.strip())

        except Exception as e:
            logger.debug("Reflection failed (non-critical): %s", e)

    async def _store_learnings(self, tick_number: int, raw: str) -> None:
        """Parse and store learnings from reflection LLM response."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end])
                except json.JSONDecodeError:
                    return
            else:
                return

        learnings = data.get("learnings", [])
        for learning_data in learnings:
            if not isinstance(learning_data, dict):
                continue
            learning = AgentLearning(
                category=learning_data.get("category", "general"),
                insight=learning_data.get("insight", ""),
                confidence=float(learning_data.get("confidence", 0.5)),
                source_tick=tick_number,
            )
            if learning.insight:
                await self._db.add_learning(learning)

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
