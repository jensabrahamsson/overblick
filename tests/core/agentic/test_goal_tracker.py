"""
Tests for GoalTracker — persistent goal management.
"""

import pytest
from unittest.mock import AsyncMock

from overblick.core.agentic.goal_tracker import GoalTracker
from overblick.core.agentic.models import AgentGoal, GoalStatus


class TestGoalTracker:
    """Test goal lifecycle and management."""

    @pytest.mark.asyncio
    async def test_setup_creates_defaults(self, mock_agentic_db, sample_goals):
        """First setup creates default goals when DB is empty."""
        mock_agentic_db.get_goals = AsyncMock(side_effect=[
            [],  # First call: no goals exist
            sample_goals,  # Second call: after creation
        ])

        tracker = GoalTracker(db=mock_agentic_db)
        await tracker.setup(default_goals=sample_goals)

        assert mock_agentic_db.upsert_goal.call_count == 3
        assert len(tracker.active_goals) == 3

    @pytest.mark.asyncio
    async def test_setup_no_defaults_when_goals_exist(self, mock_agentic_db, sample_goals):
        """Setup loads existing goals without creating defaults."""
        mock_agentic_db.get_goals = AsyncMock(return_value=sample_goals)

        tracker = GoalTracker(db=mock_agentic_db)
        await tracker.setup(default_goals=[AgentGoal(name="unused", description="X")])

        assert mock_agentic_db.upsert_goal.call_count == 0
        assert len(tracker.active_goals) == 3

    @pytest.mark.asyncio
    async def test_setup_no_defaults_provided(self, mock_agentic_db):
        """Setup with no defaults and empty DB results in no goals."""
        tracker = GoalTracker(db=mock_agentic_db)
        await tracker.setup()
        assert len(tracker.active_goals) == 0

    @pytest.mark.asyncio
    async def test_goals_sorted_by_priority(self, mock_agentic_db, sample_goals):
        """active_goals returns goals sorted by priority (descending)."""
        mock_agentic_db.get_goals = AsyncMock(return_value=sample_goals)

        tracker = GoalTracker(db=mock_agentic_db)
        await tracker.setup()

        sorted_goals = tracker.active_goals
        assert sorted_goals[0].name == "goal_high"
        assert sorted_goals[1].name == "goal_mid"
        assert sorted_goals[2].name == "goal_low"

    def test_format_for_planner(self, mock_agentic_db):
        """format_for_planner produces readable text."""
        tracker = GoalTracker(db=mock_agentic_db)
        tracker._goals = [
            AgentGoal(name="test", description="Test goal", priority=80, progress=0.5),
        ]

        text = tracker.format_for_planner()
        assert "[80] test" in text
        assert "50%" in text

    def test_format_empty(self, mock_agentic_db):
        """format_for_planner handles no goals."""
        tracker = GoalTracker(db=mock_agentic_db)
        tracker._goals = []

        text = tracker.format_for_planner()
        assert "No active goals" in text

    @pytest.mark.asyncio
    async def test_update_progress(self, mock_agentic_db):
        """update_progress updates the goal and refreshes cache."""
        goal = AgentGoal(name="g", description="G", priority=50, progress=0.0)
        mock_agentic_db.get_goal_by_name = AsyncMock(return_value=goal)
        mock_agentic_db.get_goals = AsyncMock(return_value=[
            goal.model_copy(update={"progress": 0.7}),
        ])

        tracker = GoalTracker(db=mock_agentic_db)
        tracker._goals = [goal]

        await tracker.update_progress("g", 0.7)

        mock_agentic_db.upsert_goal.assert_called_once()
        call_args = mock_agentic_db.upsert_goal.call_args[0][0]
        assert call_args.progress == 0.7

    @pytest.mark.asyncio
    async def test_update_progress_clamps(self, mock_agentic_db):
        """update_progress clamps values to 0.0-1.0."""
        goal = AgentGoal(name="g", description="G", priority=50)
        mock_agentic_db.get_goal_by_name = AsyncMock(return_value=goal)
        mock_agentic_db.get_goals = AsyncMock(return_value=[goal])

        tracker = GoalTracker(db=mock_agentic_db)
        tracker._goals = [goal]

        await tracker.update_progress("g", 1.5)
        call_args = mock_agentic_db.upsert_goal.call_args[0][0]
        assert call_args.progress == 1.0

    @pytest.mark.asyncio
    async def test_get_goal(self, mock_agentic_db):
        """get_goal delegates to DB."""
        goal = AgentGoal(name="test", description="T")
        mock_agentic_db.get_goal_by_name = AsyncMock(return_value=goal)

        tracker = GoalTracker(db=mock_agentic_db)
        result = await tracker.get_goal("test")
        assert result.name == "test"
