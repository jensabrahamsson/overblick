"""
Tests for AgentLoop — full cycle with mock observer/handlers.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.core.agentic.executor import ActionExecutor
from overblick.core.agentic.goal_tracker import GoalTracker
from overblick.core.agentic.loop import AgentLoop
from overblick.core.agentic.models import (
    ActionOutcome,
    ActionPlan,
    AgentGoal,
    PlannedAction,
)
from overblick.core.agentic.planner import ActionPlanner
from overblick.core.agentic.reflection import ReflectionPipeline


class MockObserver:
    """Mock observer that returns a fixed observation."""

    def __init__(self, observation=None):
        self._observation = observation or {"items": ["a", "b", "c"]}

    async def observe(self):
        return self._observation

    def format_for_planner(self, observation):
        return f"Observed {len(observation.get('items', []))} items"


class MockHandler:
    """Mock handler that succeeds."""

    async def handle(self, action, observation):
        return ActionOutcome(action=action, success=True, result="Done")


class TestAgentLoop:
    """Test full agentic cycle."""

    @pytest.fixture
    def mock_components(self, mock_agentic_db):
        """Create all mock components for the loop."""
        # Goal tracker
        goal_tracker = GoalTracker(db=mock_agentic_db)
        goal_tracker._goals = [
            AgentGoal(name="test_goal", description="Test", priority=80),
        ]

        # Planner that returns a plan
        planner = MagicMock(spec=ActionPlanner)
        planner.plan = AsyncMock(return_value=ActionPlan(
            actions=[
                PlannedAction(action_type="test_action", target="t1", priority=90),
            ],
            reasoning="Test plan",
        ))

        # Executor
        executor = ActionExecutor(
            handlers={"test_action": MockHandler()},
            max_actions_per_tick=5,
        )

        # Reflection
        reflection = MagicMock(spec=ReflectionPipeline)
        reflection.reflect = AsyncMock()

        return {
            "observer": MockObserver(),
            "goal_tracker": goal_tracker,
            "planner": planner,
            "executor": executor,
            "reflection": reflection,
            "db": mock_agentic_db,
        }

    @pytest.mark.asyncio
    async def test_full_cycle(self, mock_components):
        """A complete tick runs OBSERVE -> THINK -> PLAN -> ACT -> REFLECT."""
        loop = AgentLoop(**mock_components)
        await loop.setup()

        tick_log = await loop.tick()

        assert tick_log is not None
        assert tick_log.tick_number == 1
        assert tick_log.actions_planned == 1
        assert tick_log.actions_executed == 1
        assert tick_log.actions_succeeded == 1
        assert tick_log.duration_ms > 0

        # Verify reflection was called
        mock_components["reflection"].reflect.assert_called_once()

        # Verify tick was logged
        mock_components["db"].log_tick.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_observations(self, mock_components):
        """Returns None when observer produces nothing."""
        mock_components["observer"] = MockObserver(observation=None)

        # Need a custom observer that returns None
        class NoneObserver:
            async def observe(self):
                return None
            def format_for_planner(self, obs):
                return ""

        mock_components["observer"] = NoneObserver()

        loop = AgentLoop(**mock_components)
        await loop.setup()

        tick_log = await loop.tick()
        assert tick_log is None

    @pytest.mark.asyncio
    async def test_no_actions_planned(self, mock_components):
        """Returns tick log with zero actions when planner says skip."""
        mock_components["planner"].plan = AsyncMock(
            return_value=ActionPlan(reasoning="Nothing to do"),
        )

        loop = AgentLoop(**mock_components)
        await loop.setup()

        tick_log = await loop.tick()

        assert tick_log is not None
        assert tick_log.actions_planned == 0
        assert tick_log.actions_executed == 0

    @pytest.mark.asyncio
    async def test_tick_count_increments(self, mock_components):
        """Tick count increments with each cycle."""
        loop = AgentLoop(**mock_components)
        await loop.setup()

        tick1 = await loop.tick()
        tick2 = await loop.tick()

        assert tick1.tick_number == 1
        assert tick2.tick_number == 2

    @pytest.mark.asyncio
    async def test_extra_context_callback(self, mock_components):
        """Extra context callback is called and passed to planner."""
        extra_called = []

        def get_extra():
            extra_called.append(True)
            return "Owner says: merge PR #42"

        mock_components["get_extra_context"] = get_extra

        loop = AgentLoop(**mock_components)
        await loop.setup()

        await loop.tick()

        assert len(extra_called) == 1
        # Verify planner received extra_context
        call_kwargs = mock_components["planner"].plan.call_args
        assert "Owner says" in call_kwargs.kwargs.get("extra_context", "")

    @pytest.mark.asyncio
    async def test_observer_error_returns_none(self, mock_components):
        """Observer errors result in None tick log."""
        class ErrorObserver:
            async def observe(self):
                raise RuntimeError("API down")
            def format_for_planner(self, obs):
                return ""

        mock_components["observer"] = ErrorObserver()

        loop = AgentLoop(**mock_components)
        await loop.setup()

        tick_log = await loop.tick()
        assert tick_log is None

    @pytest.mark.asyncio
    async def test_action_outcomes_logged(self, mock_components):
        """Each action outcome is logged to the DB."""
        loop = AgentLoop(**mock_components)
        await loop.setup()

        await loop.tick()

        mock_components["db"].log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_observations_dict(self):
        """_count_observations handles dict observations."""
        assert AgentLoop._count_observations({"prs": [1, 2], "issues": [3]}) == 3

    @pytest.mark.asyncio
    async def test_count_observations_list(self):
        """_count_observations handles list observations."""
        assert AgentLoop._count_observations([1, 2, 3]) == 3

    @pytest.mark.asyncio
    async def test_count_observations_scalar(self):
        """_count_observations handles scalar observations."""
        assert AgentLoop._count_observations("some text") == 9

    def test_format_recent_actions_empty(self):
        """Format empty recent actions."""
        assert AgentLoop._format_recent_actions([]) == ""

    def test_format_recent_actions(self):
        """Format recent actions into readable text."""
        rows = [
            {"action_type": "merge_pr", "target": "PR #42", "success": 1, "created_at": "2026-02-23"},
            {"action_type": "notify", "target": "CI fail", "success": 0, "created_at": "2026-02-23"},
        ]
        text = AgentLoop._format_recent_actions(rows)
        assert "[OK] merge_pr" in text
        assert "[FAIL] notify" in text
