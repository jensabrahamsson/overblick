"""Unit tests for learning data models."""

from overblick.core.learning.models import Learning, LearningStatus


class TestLearningStatus:
    def test_candidate_value(self):
        assert LearningStatus.CANDIDATE == "candidate"

    def test_approved_value(self):
        assert LearningStatus.APPROVED == "approved"

    def test_rejected_value(self):
        assert LearningStatus.REJECTED == "rejected"

    def test_is_str_enum(self):
        assert isinstance(LearningStatus.CANDIDATE, str)


class TestLearning:
    def test_default_values(self):
        l = Learning(content="test insight")
        assert l.id is None
        assert l.content == "test insight"
        assert l.category == "general"
        assert l.source == ""
        assert l.source_context == ""
        assert l.status == LearningStatus.CANDIDATE
        assert l.review_reason == ""
        assert l.confidence == 0.5
        assert l.embedding is None
        assert l.created_at == ""
        assert l.reviewed_at is None

    def test_with_all_fields(self):
        l = Learning(
            id=42,
            content="Attachment theory is about bonding",
            category="factual",
            source="moltbook",
            source_context="Post about psychology",
            status=LearningStatus.APPROVED,
            review_reason="Aligns with values",
            confidence=0.9,
            embedding=[0.1, 0.2, 0.3],
            created_at="2026-02-27T10:00:00",
            reviewed_at="2026-02-27T10:00:01",
        )
        assert l.id == 42
        assert l.embedding == [0.1, 0.2, 0.3]
        assert l.status == LearningStatus.APPROVED

    def test_embedding_none(self):
        l = Learning(content="no vectors")
        assert l.embedding is None

    def test_embedding_with_values(self):
        emb = [0.5] * 768
        l = Learning(content="high dim", embedding=emb)
        assert len(l.embedding) == 768
        assert l.embedding[0] == 0.5
