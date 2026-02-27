"""Tests for LearningStore integration with the agentic loop and reflection."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.core.agentic.models import ActionOutcome, AgentLearning, PlannedAction
from overblick.core.agentic.reflection import ReflectionPipeline
from overblick.core.agentic.loop import AgentLoop
from overblick.core.learning.models import LearningStatus
from overblick.core.learning.store import LearningStore


def _make_llm_result(content: str):
    result = MagicMock()
    result.content = content
    result.blocked = False
    return result


class TestReflectionWithLearningStore:
    @pytest.mark.asyncio
    async def test_routes_learning_through_store(self, mock_agentic_db, tmp_path):
        """ReflectionPipeline uses LearningStore.propose() instead of DB insert."""
        # Create real learning store
        store = LearningStore(
            db_path=tmp_path / "learnings.db",
            ethos_text="Be curious",
            llm_pipeline=None,
        )
        await store.setup()
        # Patch reviewer to auto-approve
        store._reviewer = AsyncMock()
        store._reviewer.review = AsyncMock(return_value=(LearningStatus.APPROVED, "Good"))

        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=_make_llm_result(
            '{"learnings": [{"category": "pattern", "insight": "Tests should be fast", "confidence": 0.8}]}'
        ))

        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=pipeline,
            learning_store=store,
        )

        outcome = ActionOutcome(
            action=PlannedAction(action_type="test", target="t1", priority=50, reasoning="r"),
            success=True,
            result="Test passed",
        )
        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=[outcome])

        # Should have proposed to learning store (not direct DB)
        assert await store.count(LearningStatus.APPROVED) == 1
        mock_agentic_db.add_learning.assert_not_called()

    @pytest.mark.asyncio
    async def test_backward_compat_without_store(self, mock_agentic_db):
        """Without learning_store, falls back to AgenticDB."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=_make_llm_result(
            '{"learnings": [{"category": "general", "insight": "Legacy path works", "confidence": 0.5}]}'
        ))

        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=pipeline,
            learning_store=None,  # No store
        )

        outcome = ActionOutcome(
            action=PlannedAction(action_type="test", target="t1", priority=50, reasoning="r"),
            success=True,
            result="Done",
        )
        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=[outcome])

        # Should use old DB path
        mock_agentic_db.add_learning.assert_called_once()


class TestAgentLoopWithLearningStore:
    @pytest.mark.asyncio
    async def test_reads_from_learning_store(self, mock_agentic_db, tmp_path):
        """Agent loop reads learnings from LearningStore when available."""
        store = LearningStore(
            db_path=tmp_path / "learnings.db",
            ethos_text="Be curious",
            llm_pipeline=None,
        )
        await store.setup()
        store._reviewer = AsyncMock()
        store._reviewer.review = AsyncMock(return_value=(LearningStatus.APPROVED, "Good"))
        await store.propose(content="Learned fact 1", category="factual")
        await store.propose(content="Learned fact 2", category="pattern")

        # Mock components
        observer = AsyncMock()
        observer.observe = AsyncMock(return_value={"items": ["a", "b"]})
        observer.format_for_planner = MagicMock(return_value="Observations: items found")

        goal_tracker = MagicMock()
        goal_tracker.active_goals = []
        goal_tracker.format_for_planner = MagicMock(return_value="No goals")

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=MagicMock(actions=[], reasoning="No work"))

        executor = AsyncMock()
        reflection = AsyncMock()

        loop = AgentLoop(
            observer=observer,
            goal_tracker=goal_tracker,
            planner=planner,
            executor=executor,
            reflection=reflection,
            db=mock_agentic_db,
            learning_store=store,
        )
        await loop.setup()
        await loop.tick()

        # Verify planner was called with learnings text from the store
        call_kwargs = planner.plan.call_args[1]
        assert "Learned fact" in call_kwargs["learnings"]

    @pytest.mark.asyncio
    async def test_backward_compat_without_store(self, mock_agentic_db):
        """Without learning_store, reads from AgenticDB."""
        mock_agentic_db.get_learnings = AsyncMock(return_value=[
            AgentLearning(category="test", insight="DB learning", confidence=0.5),
        ])

        observer = AsyncMock()
        observer.observe = AsyncMock(return_value={"items": ["a"]})
        observer.format_for_planner = MagicMock(return_value="Obs")

        goal_tracker = MagicMock()
        goal_tracker.active_goals = []
        goal_tracker.format_for_planner = MagicMock(return_value="")

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=MagicMock(actions=[], reasoning=""))

        executor = AsyncMock()
        reflection = AsyncMock()

        loop = AgentLoop(
            observer=observer,
            goal_tracker=goal_tracker,
            planner=planner,
            executor=executor,
            reflection=reflection,
            db=mock_agentic_db,
            learning_store=None,
        )
        await loop.setup()
        await loop.tick()

        mock_agentic_db.get_learnings.assert_called_once_with(limit=10)
