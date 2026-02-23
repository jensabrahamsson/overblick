"""
Tests for GoalTracker — persistent goal management.

Now uses core agentic GoalTracker with GitHub-specific default goals.
"""

import pytest
from unittest.mock import AsyncMock

from overblick.core.agentic.goal_tracker import GoalTracker
from overblick.core.agentic.models import AgentGoal, GoalStatus
from overblick.plugins.github.plugin import _DEFAULT_GOALS as DEFAULT_GOALS


class TestGoalTracker:
    """Test goal lifecycle and management."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.get_goals = AsyncMock(return_value=[])
        db.get_goal_by_name = AsyncMock(return_value=None)
        db.upsert_goal = AsyncMock(return_value=1)
        return db

    @pytest.mark.asyncio
    async def test_setup_creates_defaults(self, mock_db):
        """First setup creates default goals."""
        # First call returns empty (no goals), second returns defaults after creation
        mock_db.get_goals = AsyncMock(side_effect=[
            [],  # First call: no goals exist
            [AgentGoal(name=g.name, description=g.description, priority=g.priority)
             for g in DEFAULT_GOALS],  # Second call: after creation
        ])

        tracker = GoalTracker(db=mock_db)
        await tracker.setup(default_goals=DEFAULT_GOALS)

        assert mock_db.upsert_goal.call_count == len(DEFAULT_GOALS)
        assert len(tracker.active_goals) == len(DEFAULT_GOALS)

    @pytest.mark.asyncio
    async def test_setup_loads_existing_goals(self, mock_db):
        """Setup loads existing goals without creating defaults."""
        existing = [
            AgentGoal(name="test_goal", description="Test", priority=50),
        ]
        mock_db.get_goals = AsyncMock(return_value=existing)

        tracker = GoalTracker(db=mock_db)
        await tracker.setup(default_goals=DEFAULT_GOALS)

        assert len(tracker.active_goals) == 1
        assert mock_db.upsert_goal.call_count == 0

    @pytest.mark.asyncio
    async def test_goals_sorted_by_priority(self, mock_db):
        """active_goals returns goals sorted by priority (descending)."""
        goals = [
            AgentGoal(name="low", description="Low priority", priority=20),
            AgentGoal(name="high", description="High priority", priority=90),
            AgentGoal(name="mid", description="Mid priority", priority=50),
        ]
        mock_db.get_goals = AsyncMock(return_value=goals)

        tracker = GoalTracker(db=mock_db)
        await tracker.setup()

        sorted_goals = tracker.active_goals
        assert sorted_goals[0].name == "high"
        assert sorted_goals[1].name == "mid"
        assert sorted_goals[2].name == "low"

    def test_format_for_planner(self, mock_db):
        """format_for_planner produces readable text."""
        tracker = GoalTracker(db=mock_db)
        tracker._goals = [
            AgentGoal(name="test", description="Test goal", priority=80, progress=0.5),
        ]

        text = tracker.format_for_planner()
        assert "[80] test" in text
        assert "50%" in text

    def test_format_empty(self, mock_db):
        """format_for_planner handles no goals."""
        tracker = GoalTracker(db=mock_db)
        tracker._goals = []

        text = tracker.format_for_planner()
        assert "No active goals" in text


class TestDefaultGoals:
    """Verify default goal definitions."""

    def test_default_goals_exist(self):
        assert len(DEFAULT_GOALS) >= 5

    def test_default_goals_have_names(self):
        for goal in DEFAULT_GOALS:
            assert goal.name
            assert goal.description
            assert goal.priority > 0

    def test_default_goals_unique_names(self):
        names = [g.name for g in DEFAULT_GOALS]
        assert len(names) == len(set(names))

    def test_communicate_with_owner_highest_priority(self):
        priorities = {g.name: g.priority for g in DEFAULT_GOALS}
        assert priorities["communicate_with_owner"] == max(priorities.values())
