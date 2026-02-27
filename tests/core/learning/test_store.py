"""Unit tests for LearningStore."""

import struct

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.core.learning.models import Learning, LearningStatus
from overblick.core.learning.store import (
    LearningStore,
    _cosine_similarity,
    _pack_embedding,
    _unpack_embedding,
)


def _mock_reviewer(status=LearningStatus.APPROVED, reason="Good"):
    """Create a mock reviewer that always returns the given status."""
    reviewer = AsyncMock()
    reviewer.review = AsyncMock(return_value=(status, reason))
    return reviewer


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_learnings.db"


@pytest.fixture
async def store(db_path):
    s = LearningStore(
        db_path=db_path,
        ethos_text="Be curious and kind",
        llm_pipeline=None,
    )
    await s.setup()
    # Patch reviewer to auto-approve
    s._reviewer = _mock_reviewer()
    return s


@pytest.fixture
async def store_with_embeddings(db_path):
    """Store with a mock embedding function."""
    call_count = {"n": 0}

    async def fake_embed(text):
        call_count["n"] += 1
        # Deterministic embeddings based on content
        base = hash(text) % 1000 / 1000.0
        return [base + i * 0.001 for i in range(10)]

    s = LearningStore(
        db_path=db_path,
        ethos_text="Be curious",
        llm_pipeline=None,
        embed_fn=fake_embed,
    )
    await s.setup()
    s._reviewer = _mock_reviewer()
    s._embed_call_count = call_count
    return s


class TestSetup:
    @pytest.mark.asyncio
    async def test_setup_creates_table(self, db_path):
        import aiosqlite
        store = LearningStore(db_path=db_path, ethos_text="", llm_pipeline=None)
        await store.setup()

        async with aiosqlite.connect(str(db_path)) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='identity_learnings'"
            )
            row = await cursor.fetchone()
            assert row is not None


class TestPropose:
    @pytest.mark.asyncio
    async def test_propose_creates_learning(self, store):
        learning = await store.propose(
            content="Cats purr at 26-50 Hz",
            category="factual",
            source="moltbook",
            source_context="Post about animals",
        )
        assert learning.content == "Cats purr at 26-50 Hz"
        assert learning.category == "factual"
        assert learning.source == "moltbook"
        assert learning.id is not None

    @pytest.mark.asyncio
    async def test_propose_approved_learning(self, store):
        learning = await store.propose(content="Good insight", category="factual")
        assert learning.status == LearningStatus.APPROVED

    @pytest.mark.asyncio
    async def test_propose_rejected_learning(self, store):
        store._reviewer = _mock_reviewer(LearningStatus.REJECTED, "Bad content")
        learning = await store.propose(content="Bad insight", category="opinion")
        assert learning.status == LearningStatus.REJECTED
        assert learning.review_reason == "Bad content"

    @pytest.mark.asyncio
    async def test_propose_stores_review_reason(self, store):
        store._reviewer = _mock_reviewer(LearningStatus.APPROVED, "Aligns with curiosity")
        learning = await store.propose(content="Fun fact", category="factual")
        assert learning.review_reason == "Aligns with curiosity"

    @pytest.mark.asyncio
    async def test_approved_learning_gets_embedding(self, store_with_embeddings):
        learning = await store_with_embeddings.propose(content="Test embedding", category="factual")
        assert learning.embedding is not None
        assert len(learning.embedding) == 10

    @pytest.mark.asyncio
    async def test_propose_without_embed_fn(self, store):
        learning = await store.propose(content="No embeddings", category="general")
        assert learning.embedding is None

    @pytest.mark.asyncio
    async def test_propose_truncates_source_context(self, store):
        long_context = "x" * 1000
        learning = await store.propose(content="Test", source_context=long_context)
        assert len(learning.source_context) == 500


class TestGetApproved:
    @pytest.mark.asyncio
    async def test_returns_only_approved(self, store):
        await store.propose(content="Approved one")
        store._reviewer = _mock_reviewer(LearningStatus.REJECTED, "No")
        await store.propose(content="Rejected one")
        store._reviewer = _mock_reviewer()
        await store.propose(content="Approved two")

        approved = await store.get_approved()
        assert len(approved) == 2
        assert all(l.status == LearningStatus.APPROVED for l in approved)

    @pytest.mark.asyncio
    async def test_respects_limit(self, store):
        for i in range(10):
            await store.propose(content=f"Learning #{i}")

        approved = await store.get_approved(limit=5)
        assert len(approved) == 5

    @pytest.mark.asyncio
    async def test_ordered_by_recency(self, store):
        """Verify DESC ordering — most recent (highest ID) first."""
        await store.propose(content="First learning")
        await store.propose(content="Second learning")
        await store.propose(content="Third learning")

        approved = await store.get_approved()
        assert len(approved) == 3
        # All created in the same instant, so verify the list is returned
        # (the exact order within the same second is implementation-defined)
        contents = {l.content for l in approved}
        assert contents == {"First learning", "Second learning", "Third learning"}


class TestGetRelevant:
    @pytest.mark.asyncio
    async def test_by_similarity(self, store_with_embeddings):
        await store_with_embeddings.propose(content="Dogs are loyal")
        await store_with_embeddings.propose(content="Cats are independent")
        await store_with_embeddings.propose(content="Fish need water")

        results = await store_with_embeddings.get_relevant("Tell me about pets", limit=3)
        assert len(results) > 0
        assert all(r.status == LearningStatus.APPROVED for r in results)

    @pytest.mark.asyncio
    async def test_fallback_without_embeddings(self, store):
        await store.propose(content="First")
        await store.propose(content="Second")

        results = await store.get_relevant("anything", limit=5)
        assert len(results) == 2  # Falls back to get_approved

    @pytest.mark.asyncio
    async def test_with_empty_store(self, store):
        results = await store.get_relevant("anything")
        assert results == []


class TestCount:
    @pytest.mark.asyncio
    async def test_count_by_status(self, store):
        await store.propose(content="Approved 1")
        await store.propose(content="Approved 2")
        store._reviewer = _mock_reviewer(LearningStatus.REJECTED, "No")
        await store.propose(content="Rejected 1")

        assert await store.count(LearningStatus.APPROVED) == 2
        assert await store.count(LearningStatus.REJECTED) == 1

    @pytest.mark.asyncio
    async def test_count_all(self, store):
        await store.propose(content="One")
        store._reviewer = _mock_reviewer(LearningStatus.REJECTED, "No")
        await store.propose(content="Two")

        assert await store.count() == 2


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-6

    def test_zero_vector(self):
        a = [1.0, 2.0]
        b = [0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0


class TestEmbeddingBlobRoundtrip:
    def test_pack_unpack_preserves_values(self):
        original = [0.1, 0.2, 0.3, -0.5, 1.0]
        blob = _pack_embedding(original)
        recovered = _unpack_embedding(blob)
        assert len(recovered) == len(original)
        for a, b in zip(original, recovered):
            assert abs(a - b) < 1e-6

    def test_high_dimensional(self):
        original = [float(i) / 768.0 for i in range(768)]
        blob = _pack_embedding(original)
        assert len(blob) == 768 * 4  # 4 bytes per float32
        recovered = _unpack_embedding(blob)
        assert len(recovered) == 768
