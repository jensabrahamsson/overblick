"""
Tests for ActionExecutor — dispatch, timing, max-actions.
"""

import pytest
from unittest.mock import AsyncMock

from overblick.core.agentic.executor import ActionExecutor
from overblick.core.agentic.models import ActionOutcome, ActionPlan, PlannedAction


class MockHandler:
    """Mock action handler for testing."""

    def __init__(self, success: bool = True, result: str = "Done"):
        self._success = success
        self._result = result

    async def handle(self, action, observation):
        return ActionOutcome(
            action=action,
            success=self._success,
            result=self._result if self._success else "",
            error="" if self._success else self._result,
        )


class FailingHandler:
    """Handler that raises an exception."""

    async def handle(self, action, observation):
        raise RuntimeError("Handler crashed")


class TestActionExecutor:
    """Test ActionExecutor dispatch and error handling."""

    @pytest.mark.asyncio
    async def test_dispatch_to_handler(self):
        """Executor dispatches actions to the correct handler."""
        handlers = {
            "action_a": MockHandler(success=True, result="A done"),
            "action_b": MockHandler(success=True, result="B done"),
        }
        executor = ActionExecutor(handlers=handlers)

        plan = ActionPlan(actions=[
            PlannedAction(action_type="action_a", target="t1", priority=90),
            PlannedAction(action_type="action_b", target="t2", priority=50),
        ])

        outcomes = await executor.execute(plan, observation=None)

        assert len(outcomes) == 2
        assert outcomes[0].success is True
        assert outcomes[0].result == "A done"
        assert outcomes[1].result == "B done"

    @pytest.mark.asyncio
    async def test_unknown_action_type(self):
        """Unknown action types produce failure outcomes."""
        executor = ActionExecutor(handlers={"known": MockHandler()})

        plan = ActionPlan(actions=[
            PlannedAction(action_type="unknown", target="t1"),
        ])

        outcomes = await executor.execute(plan, observation=None)

        assert len(outcomes) == 1
        assert outcomes[0].success is False
        assert "No handler registered" in outcomes[0].error

    @pytest.mark.asyncio
    async def test_max_actions_per_tick(self):
        """Executor respects max_actions_per_tick limit."""
        handlers = {"action": MockHandler()}
        executor = ActionExecutor(handlers=handlers, max_actions_per_tick=2)

        plan = ActionPlan(actions=[
            PlannedAction(action_type="action", target=f"t{i}")
            for i in range(5)
        ])

        outcomes = await executor.execute(plan, observation=None)
        assert len(outcomes) == 2

    @pytest.mark.asyncio
    async def test_handler_exception_caught(self):
        """Handler exceptions are caught and produce failure outcomes."""
        handlers = {"crash": FailingHandler()}
        executor = ActionExecutor(handlers=handlers)

        plan = ActionPlan(actions=[
            PlannedAction(action_type="crash", target="t1"),
        ])

        outcomes = await executor.execute(plan, observation=None)

        assert len(outcomes) == 1
        assert outcomes[0].success is False
        assert "Unhandled error" in outcomes[0].error

    @pytest.mark.asyncio
    async def test_timing_recorded(self):
        """Executor records duration_ms for each action."""
        handlers = {"action": MockHandler()}
        executor = ActionExecutor(handlers=handlers)

        plan = ActionPlan(actions=[
            PlannedAction(action_type="action", target="t1"),
        ])

        outcomes = await executor.execute(plan, observation=None)

        assert outcomes[0].duration_ms >= 0

    @pytest.mark.asyncio
    async def test_mixed_outcomes(self):
        """Executor handles a mix of success and failure."""
        handlers = {
            "good": MockHandler(success=True, result="OK"),
            "bad": MockHandler(success=False, result="Error"),
        }
        executor = ActionExecutor(handlers=handlers)

        plan = ActionPlan(actions=[
            PlannedAction(action_type="good", target="t1", priority=90),
            PlannedAction(action_type="bad", target="t2", priority=50),
        ])

        outcomes = await executor.execute(plan, observation=None)

        assert outcomes[0].success is True
        assert outcomes[1].success is False

    @pytest.mark.asyncio
    async def test_empty_plan(self):
        """Empty plan produces no outcomes."""
        executor = ActionExecutor(handlers={})
        outcomes = await executor.execute(ActionPlan(), observation=None)
        assert outcomes == []

    @pytest.mark.asyncio
    async def test_observation_passed_to_handler(self):
        """Observation is passed through to handlers."""
        received_obs = []

        class CapturingHandler:
            async def handle(self, action, observation):
                received_obs.append(observation)
                return ActionOutcome(action=action, success=True, result="ok")

        handlers = {"capture": CapturingHandler()}
        executor = ActionExecutor(handlers=handlers)

        plan = ActionPlan(actions=[
            PlannedAction(action_type="capture", target="t1"),
        ])

        my_obs = {"key": "value"}
        await executor.execute(plan, observation=my_obs)

        assert len(received_obs) == 1
        assert received_obs[0] == {"key": "value"}
