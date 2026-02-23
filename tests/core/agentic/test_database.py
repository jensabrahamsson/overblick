"""
Tests for AgenticDB — migrations and query methods.
"""

import pytest

from overblick.core.agentic.database import AGENTIC_MIGRATIONS, AgenticDB
from overblick.core.agentic.models import (
    ActionOutcome,
    AgentGoal,
    AgentLearning,
    GoalStatus,
    PlannedAction,
    TickLog,
)
from overblick.core.database.base import MigrationManager


class TestAgenticMigrations:
    """Test AGENTIC_MIGRATIONS apply cleanly."""

    @pytest.mark.asyncio
    async def test_migrations_apply(self, sqlite_backend):
        """All agentic migrations apply without error."""
        mgr = MigrationManager(sqlite_backend)
        applied = await mgr.apply(AGENTIC_MIGRATIONS)
        assert applied == len(AGENTIC_MIGRATIONS)

    @pytest.mark.asyncio
    async def test_migrations_idempotent(self, sqlite_backend):
        """Applying migrations twice is safe (IF NOT EXISTS)."""
        mgr = MigrationManager(sqlite_backend)
        await mgr.apply(AGENTIC_MIGRATIONS)
        # Second apply should be no-op
        applied = await mgr.apply(AGENTIC_MIGRATIONS)
        assert applied == 0

    @pytest.mark.asyncio
    async def test_migration_versions_900_plus(self):
        """All agentic migrations have version >= 900."""
        for m in AGENTIC_MIGRATIONS:
            assert m.version >= 900, f"Migration {m.name} has version {m.version}"

    def test_migration_names_unique(self):
        """All migration names are unique."""
        names = [m.name for m in AGENTIC_MIGRATIONS]
        assert len(names) == len(set(names))


class TestAgenticDB:
    """Test AgenticDB query methods with real SQLite."""

    @pytest.fixture
    async def agentic_db(self, sqlite_backend):
        """AgenticDB with applied migrations."""
        mgr = MigrationManager(sqlite_backend)
        await mgr.apply(AGENTIC_MIGRATIONS)
        return AgenticDB(sqlite_backend)

    # ── Goals ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_upsert_and_get_goal(self, agentic_db):
        goal = AgentGoal(name="test_goal", description="Test", priority=80)
        await agentic_db.upsert_goal(goal)

        result = await agentic_db.get_goal_by_name("test_goal")
        assert result is not None
        assert result.name == "test_goal"
        assert result.priority == 80
        assert result.status == GoalStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_get_goals_by_status(self, agentic_db):
        await agentic_db.upsert_goal(AgentGoal(name="active1", description="A", priority=50))
        await agentic_db.upsert_goal(AgentGoal(
            name="paused1", description="B", priority=30, status=GoalStatus.PAUSED,
        ))

        active = await agentic_db.get_goals(status="active")
        assert len(active) == 1
        assert active[0].name == "active1"

        paused = await agentic_db.get_goals(status="paused")
        assert len(paused) == 1

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, agentic_db):
        await agentic_db.upsert_goal(AgentGoal(name="g", description="V1", priority=50))
        await agentic_db.upsert_goal(AgentGoal(name="g", description="V2", priority=90))

        result = await agentic_db.get_goal_by_name("g")
        assert result.description == "V2"
        assert result.priority == 90

    @pytest.mark.asyncio
    async def test_goals_sorted_by_priority(self, agentic_db):
        await agentic_db.upsert_goal(AgentGoal(name="low", description="L", priority=10))
        await agentic_db.upsert_goal(AgentGoal(name="high", description="H", priority=99))

        goals = await agentic_db.get_goals()
        assert goals[0].name == "high"
        assert goals[1].name == "low"

    @pytest.mark.asyncio
    async def test_goal_metadata(self, agentic_db):
        await agentic_db.upsert_goal(AgentGoal(
            name="meta_goal", description="With metadata",
            metadata={"key": "value", "count": 42},
        ))

        result = await agentic_db.get_goal_by_name("meta_goal")
        assert result.metadata == {"key": "value", "count": 42}

    # ── Action log ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_log_and_get_actions(self, agentic_db):
        action = PlannedAction(
            action_type="test_action", target="target_1",
            target_number=1, priority=90,
        )
        outcome = ActionOutcome(action=action, success=True, result="Done", duration_ms=100.0)

        row_id = await agentic_db.log_action(tick_number=1, outcome=outcome)
        assert row_id > 0

        recent = await agentic_db.get_recent_actions(limit=5)
        assert len(recent) == 1
        assert recent[0]["action_type"] == "test_action"
        assert recent[0]["success"] == 1

    # ── Learnings ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_add_and_get_learnings(self, agentic_db):
        learning = AgentLearning(
            category="testing",
            insight="Tests should be fast",
            confidence=0.9,
            source="reflection",
            source_tick=1,
        )
        await agentic_db.add_learning(learning)

        results = await agentic_db.get_learnings(limit=5)
        assert len(results) == 1
        assert results[0].category == "testing"
        assert results[0].insight == "Tests should be fast"
        assert results[0].source == "reflection"

    @pytest.mark.asyncio
    async def test_learning_with_source_ref(self, agentic_db):
        learning = AgentLearning(
            category="email",
            insight="Short replies preferred",
            source="boss_feedback",
            source_ref="alice@example.com",
            source_tick=3,
        )
        await agentic_db.add_learning(learning)

        results = await agentic_db.get_learnings()
        assert results[0].source_ref == "alice@example.com"

    # ── Tick log ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_log_tick(self, agentic_db):
        tick = TickLog(
            tick_number=1,
            started_at="2026-02-23T10:00:00Z",
            completed_at="2026-02-23T10:00:02Z",
            observations_count=5,
            actions_planned=2,
            actions_executed=2,
            actions_succeeded=1,
            duration_ms=2000.0,
        )
        row_id = await agentic_db.log_tick(tick)
        assert row_id > 0

    @pytest.mark.asyncio
    async def test_tick_count(self, agentic_db):
        assert await agentic_db.get_tick_count() == 0

        await agentic_db.log_tick(TickLog(tick_number=1))
        await agentic_db.log_tick(TickLog(tick_number=2))

        assert await agentic_db.get_tick_count() == 2
