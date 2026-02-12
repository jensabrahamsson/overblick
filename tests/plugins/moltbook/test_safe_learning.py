"""Tests for safe learning module."""

import pytest
from unittest.mock import AsyncMock
from blick.plugins.moltbook.safe_learning import (
    SafeLearningModule, LearningCategory, ReviewResult,
    ProposedLearning, extract_potential_learnings,
)


class TestSafeLearningModule:
    def test_propose_learning(self):
        module = SafeLearningModule()
        learning = module.propose_learning(
            content="Test fact",
            category=LearningCategory.FACTUAL,
            source_context="conversation",
            source_agent="AgentX",
        )
        assert learning.content == "Test fact"
        assert learning.review_result == ReviewResult.PENDING
        assert len(module.pending_learnings) == 1

    @pytest.mark.asyncio
    async def test_review_approved(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "APPROVED: Safe and factual"})

        module = SafeLearningModule(llm_client=llm, ethos_text="Be good")
        learning = module.propose_learning("Earth orbits Sun", LearningCategory.FACTUAL, "", "Astro")

        result = await module.review_learning(learning)
        assert result == ReviewResult.APPROVED
        assert len(module.approved_learnings) == 1
        assert len(module.pending_learnings) == 0

    @pytest.mark.asyncio
    async def test_review_rejected(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "REJECTED: Promotes harmful ideology"})

        module = SafeLearningModule(llm_client=llm)
        learning = module.propose_learning("Bad content", LearningCategory.OPINION, "", "BadAgent")

        result = await module.review_learning(learning)
        assert result == ReviewResult.REJECTED
        assert len(module.rejected_learnings) == 1

    @pytest.mark.asyncio
    async def test_review_no_llm(self):
        module = SafeLearningModule()
        learning = module.propose_learning("Fact", LearningCategory.FACTUAL, "", "Agent")
        result = await module.review_learning(learning)
        assert result == ReviewResult.PENDING

    @pytest.mark.asyncio
    async def test_review_all_pending(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "APPROVED: OK"})

        module = SafeLearningModule(llm_client=llm)
        module.propose_learning("A", LearningCategory.FACTUAL, "", "X")
        module.propose_learning("B", LearningCategory.FACTUAL, "", "Y")

        results = await module.review_all_pending()
        assert results["approved"] == 2


class TestExtractPotentialLearnings:
    def test_teaching_indicator(self):
        learnings = extract_potential_learnings(
            "Did you know that bees can recognize faces? It's quite fascinating.",
            "Interesting!",
            "BeeBot",
        )
        assert len(learnings) >= 1
        assert learnings[0]["category"] == LearningCategory.FACTUAL

    def test_no_indicators(self):
        learnings = extract_potential_learnings(
            "Hello, how are you today?",
            "I'm fine!",
            "Agent",
        )
        assert len(learnings) == 0

    def test_max_extractions(self):
        text = "Did you know A. Actually B. Fun fact C. Research shows D."
        learnings = extract_potential_learnings(text, "Response", "Agent")
        assert len(learnings) <= 3
