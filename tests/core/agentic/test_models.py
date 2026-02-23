"""
Tests for core agentic models.
"""

from overblick.core.agentic.models import (
    ActionOutcome,
    ActionPlan,
    AgentGoal,
    AgentLearning,
    GoalStatus,
    PlannedAction,
    TickLog,
)


class TestAgentGoal:
    """Test AgentGoal model."""

    def test_defaults(self):
        goal = AgentGoal(name="test", description="Test goal")
        assert goal.priority == 50
        assert goal.status == GoalStatus.ACTIVE
        assert goal.progress == 0.0
        assert goal.metadata == {}
        assert goal.id is None

    def test_full(self):
        goal = AgentGoal(
            id=1,
            name="test",
            description="Full goal",
            priority=90,
            status=GoalStatus.COMPLETED,
            progress=1.0,
            metadata={"key": "value"},
        )
        assert goal.id == 1
        assert goal.priority == 90
        assert goal.status == GoalStatus.COMPLETED
        assert goal.metadata["key"] == "value"


class TestPlannedAction:
    """Test PlannedAction model — string-based action types."""

    def test_string_action_type(self):
        action = PlannedAction(
            action_type="merge_pr",
            target="PR #42",
            target_number=42,
            repo="owner/repo",
            priority=90,
        )
        assert action.action_type == "merge_pr"
        assert isinstance(action.action_type, str)

    def test_defaults(self):
        action = PlannedAction(action_type="skip")
        assert action.target == ""
        assert action.target_number == 0
        assert action.repo == ""
        assert action.priority == 50
        assert action.params == {}

    def test_custom_action_type(self):
        """Any string works as action_type — no enum restriction."""
        action = PlannedAction(action_type="send_email")
        assert action.action_type == "send_email"


class TestActionPlan:
    """Test ActionPlan model."""

    def test_empty(self):
        plan = ActionPlan()
        assert plan.actions == []
        assert plan.reasoning == ""

    def test_with_actions(self):
        actions = [
            PlannedAction(action_type="a", priority=90),
            PlannedAction(action_type="b", priority=50),
        ]
        plan = ActionPlan(actions=actions, reasoning="test")
        assert len(plan.actions) == 2
        assert plan.reasoning == "test"


class TestActionOutcome:
    """Test ActionOutcome model."""

    def test_success(self):
        action = PlannedAction(action_type="test")
        outcome = ActionOutcome(action=action, success=True, result="Done")
        assert outcome.success is True
        assert outcome.result == "Done"
        assert outcome.error == ""

    def test_failure(self):
        action = PlannedAction(action_type="test")
        outcome = ActionOutcome(action=action, success=False, error="Failed")
        assert outcome.success is False
        assert outcome.error == "Failed"


class TestTickLog:
    """Test TickLog model."""

    def test_defaults(self):
        log = TickLog()
        assert log.tick_number == 0
        assert log.duration_ms == 0.0

    def test_full(self):
        log = TickLog(
            tick_number=10,
            observations_count=15,
            actions_planned=3,
            actions_executed=3,
            actions_succeeded=2,
            duration_ms=1500.0,
        )
        assert log.tick_number == 10
        assert log.actions_succeeded == 2


class TestAgentLearning:
    """Test AgentLearning model — superset with source fields."""

    def test_defaults(self):
        learning = AgentLearning(insight="Test insight")
        assert learning.category == ""
        assert learning.confidence == 0.5
        assert learning.source == "reflection"
        assert learning.source_tick == 0
        assert learning.source_ref is None

    def test_full(self):
        learning = AgentLearning(
            category="email",
            insight="Boss prefers short replies",
            confidence=0.9,
            source="boss_feedback",
            source_tick=5,
            source_ref="alice@example.com",
        )
        assert learning.source == "boss_feedback"
        assert learning.source_ref == "alice@example.com"
        assert learning.confidence == 0.9
