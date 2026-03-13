"""
LearningStore — per-identity learning persistence with embedding retrieval.

Provides SQLite-backed storage for learnings with:
- Immediate ethos review at propose time
- Embedding-based semantic retrieval
- Graceful fallback when embeddings are unavailable
"""

import logging
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from overblick.core.llm.pipeline import SafeLLMPipeline

from .migrations import run_migrations
from .models import Learning, LearningStatus
from .reviewer import EthosReviewer

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)  # type: ignore[no-any-return]


def _pack_embedding(embedding: list[float]) -> bytes:
    """Pack a float list into a compact binary BLOB."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _unpack_embedding(blob: bytes) -> list[float]:
    """Unpack a binary BLOB back into a float list."""
    count = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{count}f", blob))


class LearningStore:
    """
    Per-identity learning store with ethos gating and embedding retrieval.

    Args:
        db_path: Path to SQLite database file
        ethos_text: Identity's ethos values for review
        llm_pipeline: SafeLLMPipeline for ethos review LLM calls
        embed_fn: Optional async callable(text) -> list[float] for embeddings
    """

    def __init__(
        self,
        db_path: Path,
        ethos_text: str,
        llm_pipeline: SafeLLMPipeline | None = None,
        embed_fn: Callable | None = None,
    ):
        self._db_path = Path(db_path)
        self._reviewer = EthosReviewer(llm_pipeline, ethos_text)  # type: ignore[arg-type]
        self._embed_fn = embed_fn
        self._embedding_cache: dict[str, list[float]] = {}

    async def setup(self) -> None:
        """Run migrations and ensure the database is ready."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        await run_migrations(self._db_path)

    async def _get_embedding(self, text: str) -> list[float] | None:
        """
        Get embedding for text, using cache if available.

        Returns None if embed_fn is unavailable or fails.
        """
        if not self._embed_fn:
            return None
        if text in self._embedding_cache:
            return self._embedding_cache[text]
        try:
            embedding = await self._embed_fn(text)
            # Limit cache size (keep last 100)
            if len(self._embedding_cache) >= 100:
                # Remove oldest entry (FIFO). Use simple popitem(last=False) on ordered dict.
                # Since Python 3.7 dict preserves insertion order.
                next_key = next(iter(self._embedding_cache))
                del self._embedding_cache[next_key]
            self._embedding_cache[text] = embedding
            return embedding
        except Exception as e:
            logger.warning("Embedding failed for text '%s...': %s", text[:50], e)
            return None

    async def propose(
        self,
        content: str,
        category: str = "general",
        source: str = "",
        source_context: str = "",
    ) -> Learning:
        """
        Propose a learning. Immediately reviewed against ethos.

        If approved and embed_fn is available, computes embedding.
        Returns the Learning with final status.
        """
        learning = Learning(
            content=content,
            category=category,
            source=source,
            source_context=source_context[:500],
        )

        # Ethos review (synchronous in tick)
        status, reason = await self._reviewer.review(content, category)
        learning.status = status
        learning.review_reason = reason
        if status != LearningStatus.CANDIDATE:
            learning.reviewed_at = datetime.now(UTC).isoformat()

        # Compute embedding for approved learnings
        if status == LearningStatus.APPROVED:
            embedding = await self._get_embedding(content)
            if embedding is not None:
                learning.embedding = embedding

        # Persist to database
        await self._insert(learning)

        logger.info(
            "Learning proposed [%s]: %s (source=%s)",
            learning.status.value,
            content[:60],
            source,
        )
        return learning

    async def get_relevant(
        self,
        context: str,
        limit: int = 8,
    ) -> list[Learning]:
        """
        Get approved learnings most relevant to context.

        If embeddings are available, uses cosine similarity search.
        Falls back to most recent approved learnings otherwise.
        """
        context_embedding = await self._get_embedding(context)
        if context_embedding is not None:
            try:
                return await self._search_by_similarity(context_embedding, limit)
            except Exception as e:
                logger.debug("Embedding search failed, falling back to recency: %s", e)

        return await self.get_approved(limit=limit)

    async def get_approved(self, limit: int = 10) -> list[Learning]:
        """Get latest approved learnings ordered by recency."""
        async with aiosqlite.connect(str(self._db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM identity_learnings WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (LearningStatus.APPROVED.value, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_learning(r) for r in rows]

    async def count(self, status: LearningStatus | None = None) -> int:
        """Count learnings, optionally filtered by status."""
        async with aiosqlite.connect(str(self._db_path)) as conn:
            if status:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM identity_learnings WHERE status = ?",
                    (status.value,),
                )
            else:
                cursor = await conn.execute("SELECT COUNT(*) FROM identity_learnings")
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def _insert(self, learning: Learning) -> None:
        """Insert a learning into the database."""
        embedding_blob = _pack_embedding(learning.embedding) if learning.embedding else None

        async with aiosqlite.connect(str(self._db_path)) as conn:
            cursor = await conn.execute(
                """INSERT INTO identity_learnings
                   (content, category, source, source_context, status,
                    review_reason, confidence, embedding, reviewed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    learning.content,
                    learning.category,
                    learning.source,
                    learning.source_context,
                    learning.status.value,
                    learning.review_reason,
                    learning.confidence,
                    embedding_blob,
                    learning.reviewed_at,
                ),
            )
            await conn.commit()
            learning.id = cursor.lastrowid

    # Maximum number of embeddings to load for in-memory similarity search.
    # Keeps memory bounded while still returning good results.
    _MAX_SIMILARITY_CANDIDATES = 1000

    async def _search_by_similarity(
        self,
        query_embedding: list[float],
        limit: int,
    ) -> list[Learning]:
        """Search approved learnings by cosine similarity to query embedding.

        Loads at most _MAX_SIMILARITY_CANDIDATES recent embeddings.
        Applies a recency bias to ensure fresh knowledge is prioritized
        when similarity scores are close.
        """
        async with aiosqlite.connect(str(self._db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM identity_learnings "
                "WHERE status = ? AND embedding IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (LearningStatus.APPROVED.value, self._MAX_SIMILARITY_CANDIDATES),
            )
            rows = await cursor.fetchall()

        if not rows:
            return []

        # Score and rank by similarity + recency decay
        scored = []

        for i, row in enumerate(rows):
            embedding = _unpack_embedding(row["embedding"])
            similarity = _cosine_similarity(query_embedding, embedding)

            # Simple recency factor: newer rows (lower index i) get a small boost.
            # Max boost is 0.1 for the very newest, decaying to 0.0 for the 1000th.
            # This ensures that if two learnings have similar content, the newer wins.
            recency_boost = (
                (self._MAX_SIMILARITY_CANDIDATES - i) / self._MAX_SIMILARITY_CANDIDATES * 0.1
            )
            final_score = similarity + recency_boost

            scored.append((final_score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._row_to_learning(row) for _, row in scored[:limit]]

    @staticmethod
    def _row_to_learning(row: aiosqlite.Row) -> Learning:
        """Convert a database row to a Learning model."""
        embedding = None
        if row["embedding"]:
            embedding = _unpack_embedding(row["embedding"])

        return Learning(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            source=row["source"],
            source_context=row["source_context"],
            status=LearningStatus(row["status"]),
            review_reason=row["review_reason"],
            confidence=row["confidence"],
            embedding=embedding,
            created_at=row["created_at"] or "",
            reviewed_at=row["reviewed_at"],
        )
