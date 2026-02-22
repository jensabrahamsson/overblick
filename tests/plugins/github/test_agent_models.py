"""
Tests for GitHub agent models — PRSnapshot, RepoObservation, ActionPlan, etc.
"""

import pytest

from overblick.plugins.github.models import (
    ActionOutcome,
    ActionPlan,
    ActionType,
    AgentGoal,
    AgentLearning,
    CIStatus,
    GoalStatus,
    IssueSnapshot,
    PlannedAction,
    PRSnapshot,
    RepoObservation,
    TickLog,
    VersionBumpType,
)


class TestPRSnapshot:
    """Test PRSnapshot model."""

    def test_basic_pr(self):
        pr = PRSnapshot(
            number=42,
            title="Bump lodash from 4.17.20 to 4.17.21",
            author="dependabot[bot]",
            is_dependabot=True,
            version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.SUCCESS,
            mergeable=True,
        )
        assert pr.number == 42
        assert pr.is_dependabot is True
        assert pr.version_bump == VersionBumpType.PATCH
        assert pr.ci_status == CIStatus.SUCCESS

    def test_default_values(self):
        pr = PRSnapshot(number=1, title="Test", author="user")
        assert pr.state == "open"
        assert pr.draft is False
        assert pr.mergeable is False
        assert pr.ci_status == CIStatus.UNKNOWN
        assert pr.version_bump == VersionBumpType.UNKNOWN


class TestIssueSnapshot:
    """Test IssueSnapshot model."""

    def test_basic_issue(self):
        issue = IssueSnapshot(
            number=7,
            title="Bug in auth",
            author="reporter",
            labels=["bug"],
            age_hours=25.0,
            has_our_response=False,
        )
        assert issue.number == 7
        assert issue.has_our_response is False
        assert issue.age_hours == 25.0


class TestRepoObservation:
    """Test RepoObservation model."""

    def test_empty_observation(self):
        obs = RepoObservation(repo="owner/repo")
        assert obs.open_prs == []
        assert obs.open_issues == []
        assert obs.dependabot_prs == []
        assert obs.failing_ci == []

    def test_observation_with_data(self):
        pr = PRSnapshot(number=1, title="Test PR", author="user")
        issue = IssueSnapshot(number=2, title="Test Issue", author="user")

        obs = RepoObservation(
            repo="owner/repo",
            open_prs=[pr],
            open_issues=[issue],
            dependabot_prs=[pr],
        )
        assert len(obs.open_prs) == 1
        assert len(obs.dependabot_prs) == 1


class TestActionPlan:
    """Test ActionPlan and PlannedAction models."""

    def test_empty_plan(self):
        plan = ActionPlan()
        assert plan.actions == []
        assert plan.reasoning == ""

    def test_plan_with_actions(self):
        action = PlannedAction(
            action_type=ActionType.MERGE_PR,
            target="PR #42",
            target_number=42,
            repo="owner/repo",
            priority=90,
            reasoning="Safe patch bump",
        )
        plan = ActionPlan(
            actions=[action],
            reasoning="Dependabot PR with passing CI",
        )
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == ActionType.MERGE_PR
        assert plan.actions[0].priority == 90


class TestActionOutcome:
    """Test ActionOutcome model."""

    def test_success_outcome(self):
        action = PlannedAction(
            action_type=ActionType.MERGE_PR,
            target="PR #42",
            target_number=42,
            repo="owner/repo",
        )
        outcome = ActionOutcome(
            action=action,
            success=True,
            result="Merged PR #42",
        )
        assert outcome.success is True
        assert "Merged" in outcome.result

    def test_failure_outcome(self):
        action = PlannedAction(
            action_type=ActionType.MERGE_PR,
            target="PR #42",
            target_number=42,
            repo="owner/repo",
        )
        outcome = ActionOutcome(
            action=action,
            success=False,
            error="CI not passing",
        )
        assert outcome.success is False
        assert "CI" in outcome.error


class TestGoalAndLearning:
    """Test AgentGoal and AgentLearning models."""

    def test_goal_defaults(self):
        goal = AgentGoal(name="test_goal", description="Test description")
        assert goal.priority == 50
        assert goal.status == GoalStatus.ACTIVE
        assert goal.progress == 0.0

    def test_learning(self):
        learning = AgentLearning(
            category="dependabot",
            insight="Patch bumps in this repo always pass CI",
            confidence=0.8,
            source_tick=5,
        )
        assert learning.confidence == 0.8
        assert learning.source_tick == 5


class TestTickLog:
    """Test TickLog model."""

    def test_tick_log(self):
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
