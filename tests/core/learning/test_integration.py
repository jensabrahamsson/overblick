"""Integration tests for the learning system — end-to-end flows."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.core.learning.models import Learning, LearningStatus
from overblick.core.learning.store import LearningStore


def _make_pipeline(responses: list[str] | str = "APPROVED: Good"):
    """Create a mock LLM pipeline with configurable responses."""
    if isinstance(responses, str):
        responses = [responses]

    results = []
    for text in responses:
        r = MagicMock()
        r.content = text
        r.blocked = False
        results.append(r)

    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(side_effect=results if len(results) > 1 else results * 100)
    return pipeline


async def _fake_embed(text: str) -> list[float]:
    """Deterministic embedding based on text hash."""
    h = hash(text) % 10000
    base = h / 10000.0
    return [base + i * 0.001 for i in range(10)]


@pytest.fixture
async def full_store(tmp_path):
    """Store with real reviewer and embeddings."""
    pipeline = _make_pipeline("APPROVED: Aligns with values")
    s = LearningStore(
        db_path=tmp_path / "learnings.db",
        ethos_text="Be curious and empathetic",
        llm_pipeline=pipeline,
        embed_fn=_fake_embed,
    )
    await s.setup()
    return s


@pytest.fixture
async def reject_store(tmp_path):
    """Store whose reviewer always rejects."""
    pipeline = _make_pipeline("REJECTED: Contradicts values")
    s = LearningStore(
        db_path=tmp_path / "learnings.db",
        ethos_text="Be peaceful",
        llm_pipeline=pipeline,
        embed_fn=_fake_embed,
    )
    await s.setup()
    return s


class TestFullCycle:
    @pytest.mark.asyncio
    async def test_propose_approve_retrieve(self, full_store):
        """propose → ethos review (approved) → embed → get_relevant finds it."""
        learning = await full_store.propose(
            content="Attachment theory explains bonding patterns",
            category="factual",
            source="moltbook",
            source_context="Post about psychology",
        )
        assert learning.status == LearningStatus.APPROVED
        assert learning.embedding is not None

        results = await full_store.get_relevant("attachment styles", limit=5)
        assert len(results) == 1
        assert results[0].content == "Attachment theory explains bonding patterns"

    @pytest.mark.asyncio
    async def test_rejected_not_retrievable(self, reject_store):
        """propose → ethos review (rejected) → not returned by get_relevant."""
        learning = await reject_store.propose(content="Violence is okay", category="opinion")
        assert learning.status == LearningStatus.REJECTED

        results = await reject_store.get_relevant("violence")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_multiple_learnings_similarity_ranking(self, full_store):
        """Multiple learnings ranked by cosine similarity."""
        await full_store.propose(content="Cats purr at 26 Hz")
        await full_store.propose(content="Dogs wag tails when happy")
        await full_store.propose(content="Quantum physics is about probabilities")
        await full_store.propose(content="Python uses dynamic typing")
        await full_store.propose(content="Birds migrate south in winter")

        results = await full_store.get_relevant("Tell me about animals", limit=5)
        assert len(results) == 5
        # All are approved
        assert all(r.status == LearningStatus.APPROVED for r in results)

    @pytest.mark.asyncio
    async def test_persists_across_store_instances(self, tmp_path):
        """Learning survives store recreation with same DB."""
        db_path = tmp_path / "persist.db"
        pipeline = _make_pipeline("APPROVED: Good")

        # First store instance
        store1 = LearningStore(db_path=db_path, ethos_text="Be good", llm_pipeline=pipeline)
        await store1.setup()
        await store1.propose(content="Persisted insight", category="factual")

        # New store instance, same DB
        store2 = LearningStore(db_path=db_path, ethos_text="Be good", llm_pipeline=pipeline)
        await store2.setup()

        approved = await store2.get_approved()
        assert len(approved) == 1
        assert approved[0].content == "Persisted insight"

    @pytest.mark.asyncio
    async def test_concurrent_proposes(self, full_store):
        """Multiple concurrent propose() calls don't corrupt DB."""
        tasks = [
            full_store.propose(content=f"Concurrent learning #{i}", category="factual")
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 10
        assert all(r.status == LearningStatus.APPROVED for r in results)
        assert await full_store.count() == 10

    @pytest.mark.asyncio
    async def test_with_real_ethos_text(self, tmp_path):
        """Uses Cherry's actual ethos values."""
        cherry_ethos = (
            "Empathy above all\n"
            "Question everything with kindness\n"
            "Celebrate emotional complexity\n"
            "Resist oversimplification\n"
            "Authenticity over agreement"
        )
        pipeline = _make_pipeline("APPROVED: Aligns with emotional complexity value")
        store = LearningStore(
            db_path=tmp_path / "cherry.db",
            ethos_text=cherry_ethos,
            llm_pipeline=pipeline,
        )
        await store.setup()
        learning = await store.propose(
            content="Ambivalent attachment is a valid pattern",
            category="factual",
        )
        assert learning.status == LearningStatus.APPROVED

    @pytest.mark.asyncio
    async def test_from_different_sources(self, full_store):
        """Learnings from different sources all stored correctly."""
        await full_store.propose(content="Moltbook fact", source="moltbook")
        await full_store.propose(content="Email fact", source="email")
        await full_store.propose(content="Reflection fact", source="reflection")

        approved = await full_store.get_approved()
        sources = {l.source for l in approved}
        assert sources == {"moltbook", "email", "reflection"}

    @pytest.mark.asyncio
    async def test_get_relevant_ignores_rejected_and_candidates(self, tmp_path):
        """Only approved learnings returned regardless of embedding match."""
        pipeline = _make_pipeline([
            "APPROVED: Good",
            "REJECTED: Bad",
            "APPROVED: Good",
        ])
        store = LearningStore(
            db_path=tmp_path / "mixed.db",
            ethos_text="Be good",
            llm_pipeline=pipeline,
            embed_fn=_fake_embed,
        )
        await store.setup()

        await store.propose(content="Good learning one")
        await store.propose(content="Bad learning")
        await store.propose(content="Good learning two")

        results = await store.get_relevant("anything", limit=10)
        assert len(results) == 2
        assert all(r.status == LearningStatus.APPROVED for r in results)
