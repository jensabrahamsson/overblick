"""
Tests for ReflectionPipeline — learning extraction.
"""

import json

import pytest
from unittest.mock import AsyncMock

from overblick.core.agentic.models import ActionOutcome, PlannedAction
from overblick.core.agentic.reflection import ReflectionPipeline
from overblick.core.llm.pipeline import PipelineResult


class TestReflectionPipeline:
    """Test learning extraction from tick outcomes."""

    @pytest.mark.asyncio
    async def test_reflect_stores_learnings(self, mock_agentic_db):
        """Reflection extracts and stores learnings from LLM response."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content=json.dumps({
                "learnings": [
                    {"category": "testing", "insight": "Tests are important", "confidence": 0.9},
                    {"category": "general", "insight": "Logging helps debug", "confidence": 0.7},
                ],
                "tick_summary": "Two actions executed successfully",
            }),
        ))

        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=mock_pipeline,
            system_prompt="test",
        )

        outcomes = [
            ActionOutcome(
                action=PlannedAction(action_type="test", target="t1"),
                success=True, result="OK",
            ),
        ]

        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=outcomes)

        assert mock_agentic_db.add_learning.call_count == 2
        # Check first learning
        first_call = mock_agentic_db.add_learning.call_args_list[0][0][0]
        assert first_call.category == "testing"
        assert first_call.insight == "Tests are important"
        assert first_call.confidence == 0.9

    @pytest.mark.asyncio
    async def test_reflect_skips_no_outcomes(self, mock_agentic_db):
        """Reflection is skipped when no outcomes exist."""
        mock_pipeline = AsyncMock()

        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=mock_pipeline,
        )

        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=[])
        mock_pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_reflect_skips_no_llm(self, mock_agentic_db):
        """Reflection is skipped when no LLM is available."""
        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=None,
        )

        outcomes = [
            ActionOutcome(
                action=PlannedAction(action_type="test"),
                success=True, result="OK",
            ),
        ]

        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=outcomes)
        mock_agentic_db.add_learning.assert_not_called()

    @pytest.mark.asyncio
    async def test_reflect_handles_llm_error(self, mock_agentic_db):
        """Reflection gracefully handles LLM errors."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=mock_pipeline,
        )

        outcomes = [
            ActionOutcome(
                action=PlannedAction(action_type="test"),
                success=True, result="OK",
            ),
        ]

        # Should not raise
        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=outcomes)
        mock_agentic_db.add_learning.assert_not_called()

    @pytest.mark.asyncio
    async def test_reflect_skips_empty_insight(self, mock_agentic_db):
        """Learnings with empty insight are not stored."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content=json.dumps({
                "learnings": [
                    {"category": "test", "insight": "", "confidence": 0.5},
                    {"category": "test", "insight": "Real insight", "confidence": 0.8},
                ],
            }),
        ))

        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=mock_pipeline,
        )

        outcomes = [
            ActionOutcome(
                action=PlannedAction(action_type="test"),
                success=True, result="OK",
            ),
        ]

        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=outcomes)
        assert mock_agentic_db.add_learning.call_count == 1

    @pytest.mark.asyncio
    async def test_reflect_handles_invalid_json(self, mock_agentic_db):
        """Reflection handles unparseable LLM responses."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="This is not JSON at all",
        ))

        reflection = ReflectionPipeline(
            db=mock_agentic_db,
            llm_pipeline=mock_pipeline,
        )

        outcomes = [
            ActionOutcome(
                action=PlannedAction(action_type="test"),
                success=True, result="OK",
            ),
        ]

        # Should not raise
        await reflection.reflect(tick_number=1, planning_reasoning="test", outcomes=outcomes)
        mock_agentic_db.add_learning.assert_not_called()

    def test_extract_json_with_prefix(self):
        """Extract JSON from text with surrounding content."""
        data = ReflectionPipeline._extract_json(
            'Some text before {"learnings": []} and after'
        )
        assert data == {"learnings": []}

    def test_extract_json_returns_none_for_garbage(self):
        """Returns None for completely unparseable text."""
        assert ReflectionPipeline._extract_json("no json here") is None
