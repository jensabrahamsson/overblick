"""
Fixtures for core agentic tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.agentic.database import AgenticDB
from overblick.core.agentic.models import (
    ActionOutcome,
    ActionPlan,
    AgentGoal,
    AgentLearning,
    GoalStatus,
    PlannedAction,
    TickLog,
)
from overblick.core.agentic.protocols import PlanningPromptConfig
from overblick.core.database.base import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend


@pytest.fixture
async def sqlite_backend(tmp_path):
    """Real SQLite backend for integration tests."""
    config = DatabaseConfig(sqlite_path=str(tmp_path / "test.db"))
    backend = SQLiteBackend(config)
    await backend.connect()
    yield backend
    await backend.close()


@pytest.fixture
def mock_agentic_db():
    """Mock AgenticDB for unit tests."""
    db = AsyncMock(spec=AgenticDB)
    db.get_goals = AsyncMock(return_value=[])
    db.get_goal_by_name = AsyncMock(return_value=None)
    db.upsert_goal = AsyncMock(return_value=1)
    db.get_recent_actions = AsyncMock(return_value=[])
    db.get_learnings = AsyncMock(return_value=[])
    db.log_action = AsyncMock(return_value=1)
    db.log_tick = AsyncMock(return_value=1)
    db.add_learning = AsyncMock(return_value=1)
    db.get_tick_count = AsyncMock(return_value=0)
    return db


@pytest.fixture
def sample_goals():
    """Sample goals for testing."""
    return [
        AgentGoal(name="goal_high", description="High priority goal", priority=90),
        AgentGoal(name="goal_mid", description="Mid priority goal", priority=50),
        AgentGoal(name="goal_low", description="Low priority goal", priority=20),
    ]


@pytest.fixture
def sample_plan():
    """Sample action plan for testing."""
    return ActionPlan(
        actions=[
            PlannedAction(
                action_type="test_action",
                target="target_1",
                target_number=1,
                priority=90,
                reasoning="Test reasoning",
            ),
            PlannedAction(
                action_type="skip",
                target="target_2",
                target_number=2,
                priority=50,
                reasoning="Nothing to do",
            ),
        ],
        reasoning="Test plan reasoning",
    )


@pytest.fixture
def sample_prompt_config():
    """Sample PlanningPromptConfig for testing."""
    return PlanningPromptConfig(
        agent_role="You are a test agent.",
        available_actions="- test_action: Do a test\n- skip: Do nothing",
        safety_rules="Never do anything dangerous.",
        learning_categories="testing|general",
    )
