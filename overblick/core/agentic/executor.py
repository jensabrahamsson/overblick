"""
ActionExecutor — dispatches planned actions to registered handlers.

Domain-agnostic: looks up handlers by action_type string key.
Provides timing, error handling, and max-actions enforcement.
"""

import logging
import time
from typing import Any

from overblick.core.agentic.models import ActionOutcome, ActionPlan, PlannedAction
from overblick.core.agentic.protocols import ActionHandler

logger = logging.getLogger(__name__)


class ActionExecutor:
    """
    Dispatches planned actions to registered handlers.

    Plugins register handlers by string key. The executor looks up
    the handler for each action_type and delegates execution.
    """

    def __init__(
        self,
        handlers: dict[str, ActionHandler],
        max_actions_per_tick: int = 5,
    ):
        self._handlers = handlers
        self._max_actions = max_actions_per_tick

    async def execute(
        self,
        plan: ActionPlan,
        observation: Any,
    ) -> list[ActionOutcome]:
        """
        Execute a plan against the observed world state.

        Args:
            plan: The action plan from the planner
            observation: The current world state (passed to handlers)

        Returns:
            List of action outcomes
        """
        outcomes: list[ActionOutcome] = []

        for i, action in enumerate(plan.actions):
            if i >= self._max_actions:
                logger.info(
                    "Agent executor: max actions per tick reached (%d)",
                    self._max_actions,
                )
                break

            start_time = time.monotonic()
            outcome = await self._execute_action(action, observation)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            outcome.duration_ms = elapsed_ms

            outcomes.append(outcome)

            if outcome.success:
                logger.info(
                    "Agent executor: %s on %s — %s (%.0fms)",
                    action.action_type, action.target,
                    outcome.result[:100], elapsed_ms,
                )
            else:
                logger.warning(
                    "Agent executor: %s on %s FAILED — %s (%.0fms)",
                    action.action_type, action.target,
                    outcome.error[:100], elapsed_ms,
                )

        return outcomes

    async def _execute_action(
        self,
        action: PlannedAction,
        observation: Any,
    ) -> ActionOutcome:
        """Execute a single planned action by dispatching to its handler."""
        handler = self._handlers.get(action.action_type)
        if not handler:
            return ActionOutcome(
                action=action,
                success=False,
                error=f"No handler registered for action type: {action.action_type}",
            )

        try:
            return await handler.handle(action, observation)
        except Exception as e:
            logger.error(
                "Agent executor: unhandled error in %s: %s",
                action.action_type, e, exc_info=True,
            )
            return ActionOutcome(
                action=action,
                success=False,
                error=f"Unhandled error: {e}",
            )
