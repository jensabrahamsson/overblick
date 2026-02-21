"""
Tests for IRC TopicManager — topic selection, interest scoring, participant selection.
"""

import random
from unittest.mock import MagicMock

import pytest

from overblick.plugins.irc.topic_manager import (
    TOPIC_POOL,
    topic_to_channel,
    score_identity_interest,
    select_topic,
    select_participants,
)


def _make_identity(
    name: str,
    interest_keywords: list[str] | None = None,
    interests: dict | None = None,
) -> MagicMock:
    """Create a mock Identity with interest data."""
    identity = MagicMock()
    identity.name = name
    identity.interest_keywords = interest_keywords or []
    identity.interests = interests or {}
    return identity


class TestTopicPool:
    """Verify the topic pool is well-formed."""

    def test_pool_not_empty(self):
        assert len(TOPIC_POOL) > 0

    def test_all_topics_have_required_fields(self):
        for topic in TOPIC_POOL:
            assert "id" in topic, f"Topic missing 'id': {topic}"
            assert "topic" in topic, f"Topic missing 'topic': {topic}"
            assert "tags" in topic, f"Topic missing 'tags': {topic}"
            assert len(topic["tags"]) > 0, f"Topic has empty tags: {topic['id']}"

    def test_unique_topic_ids(self):
        ids = [t["id"] for t in TOPIC_POOL]
        assert len(ids) == len(set(ids)), "Duplicate topic IDs found"

    def test_all_topics_have_channels(self):
        for topic in TOPIC_POOL:
            channel = topic_to_channel(topic)
            assert channel.startswith("#"), f"Channel should start with #: {channel}"


class TestTopicToChannel:
    """Tests for topic_to_channel()."""

    def test_uses_configured_channel(self):
        topic = {"id": "test", "channel": "#test-channel"}
        assert topic_to_channel(topic) == "#test-channel"

    def test_derives_from_id_when_no_channel(self):
        topic = {"id": "ai_consciousness"}
        assert topic_to_channel(topic) == "#ai-consciousness"

    def test_fallback_for_missing_id(self):
        topic = {}
        assert topic_to_channel(topic) == "#general"


class TestScoreIdentityInterest:
    """Tests for interest scoring between identities and topics."""

    def test_perfect_match(self):
        identity = _make_identity(
            "test",
            interest_keywords=["AI", "consciousness", "philosophy", "psychology", "technology"],
        )
        topic = {"tags": ["AI", "consciousness", "philosophy", "psychology", "technology"]}
        score = score_identity_interest(identity, topic)
        assert score == 1.0

    def test_no_match(self):
        identity = _make_identity("test", interest_keywords=["cooking", "gardening"])
        topic = {"tags": ["AI", "technology"]}
        score = score_identity_interest(identity, topic)
        assert score == 0.0

    def test_partial_match(self):
        identity = _make_identity(
            "test", interest_keywords=["AI", "philosophy"],
        )
        topic = {"tags": ["AI", "philosophy", "economics", "society"]}
        score = score_identity_interest(identity, topic)
        assert 0.0 < score < 1.0
        assert score == pytest.approx(0.5)

    def test_empty_topic_tags(self):
        identity = _make_identity("test", interest_keywords=["AI"])
        score = score_identity_interest(identity, {"tags": []})
        assert score == 0.0

    def test_empty_identity_keywords(self):
        identity = _make_identity("test", interest_keywords=[])
        score = score_identity_interest(identity, {"tags": ["AI"]})
        assert score == 0.0

    def test_case_insensitive_matching(self):
        identity = _make_identity("test", interest_keywords=["ai", "PHILOSOPHY"])
        topic = {"tags": ["AI", "philosophy"]}
        score = score_identity_interest(identity, topic)
        assert score > 0.0

    def test_interest_areas_contribute(self):
        """Interest areas (dict) should also contribute to matching."""
        identity = _make_identity(
            "test",
            interest_keywords=[],
            interests={
                "technology": {
                    "topics": ["AI development", "machine learning"],
                },
            },
        )
        topic = {"tags": ["AI", "technology", "development"]}
        score = score_identity_interest(identity, topic)
        assert score > 0.0

    def test_multi_word_keywords_split(self):
        """Multi-word keywords should match individual words too."""
        identity = _make_identity(
            "test", interest_keywords=["AI consciousness"],
        )
        topic = {"tags": ["AI", "technology"]}
        score = score_identity_interest(identity, topic)
        assert score > 0.0  # "AI" from split should match

    def test_score_capped_at_one(self):
        identity = _make_identity(
            "test",
            interest_keywords=["AI", "artificial intelligence", "machine learning"],
            interests={"technology": {"topics": ["AI"]}},
        )
        topic = {"tags": ["AI"]}
        score = score_identity_interest(identity, topic)
        assert score <= 1.0


class TestSelectTopic:
    """Tests for topic selection with used-topic exclusion."""

    def test_returns_a_topic(self):
        topic = select_topic()
        assert topic is not None
        assert "id" in topic
        assert "topic" in topic

    def test_avoids_recent_topics(self):
        # Use all but one topic
        all_ids = [t["id"] for t in TOPIC_POOL]
        used = all_ids[:-1]
        topic = select_topic(used_topic_ids=used, window_size=len(used))
        assert topic is not None
        assert topic["id"] == all_ids[-1]

    def test_sliding_window_recycles_old_topics(self):
        """Only the last N topics are excluded (sliding window)."""
        all_ids = [t["id"] for t in TOPIC_POOL]
        # Use all topics, with window_size=5
        topic = select_topic(used_topic_ids=all_ids, window_size=5)
        assert topic is not None
        # Should NOT be one of the last 5 used
        assert topic["id"] not in all_ids[-5:]

    def test_full_pool_used_returns_any(self):
        """When window covers entire pool, fallback to all topics."""
        all_ids = [t["id"] for t in TOPIC_POOL]
        topic = select_topic(
            used_topic_ids=all_ids,
            window_size=len(TOPIC_POOL) + 10,
        )
        assert topic is not None

    def test_none_used_topics(self):
        topic = select_topic(used_topic_ids=None)
        assert topic is not None

    def test_empty_used_topics(self):
        topic = select_topic(used_topic_ids=[])
        assert topic is not None


class TestSelectParticipants:
    """Tests for participant selection."""

    @pytest.fixture
    def identities(self):
        """Create a list of test identities with varied interests."""
        return [
            _make_identity("anomal", interest_keywords=["AI", "philosophy", "consciousness"]),
            _make_identity("cherry", interest_keywords=["dating", "relationships", "pop culture"]),
            _make_identity("blixt", interest_keywords=["privacy", "technology", "hacking"]),
            _make_identity("bjork", interest_keywords=["nature", "philosophy", "stoicism"]),
            _make_identity("natt", interest_keywords=["philosophy", "consciousness", "existentialism"]),
        ]

    @pytest.fixture
    def ai_topic(self):
        return {
            "id": "ai_consciousness",
            "topic": "Can AI have consciousness?",
            "tags": ["AI", "consciousness", "philosophy", "psychology", "technology"],
            "ideal_participants": 3,
        }

    def test_returns_participants(self, identities, ai_topic):
        random.seed(42)
        result = select_participants(identities, ai_topic)
        assert len(result) >= 2
        assert len(result) <= 5

    def test_respects_min_participants(self, identities, ai_topic):
        result = select_participants(identities, ai_topic, min_participants=2)
        assert len(result) >= 2

    def test_respects_max_participants(self, identities, ai_topic):
        result = select_participants(identities, ai_topic, max_participants=2)
        assert len(result) <= 2

    def test_highest_scorer_always_included(self, identities, ai_topic):
        """The identity with highest interest score should always be selected."""
        random.seed(42)
        # Score all identities to find the expected top scorer
        scores = {}
        for identity in identities:
            scores[identity.name] = score_identity_interest(identity, ai_topic)
        top_scorer = max(scores, key=scores.get)

        result = select_participants(identities, ai_topic)
        selected_names = [i.name for i in result]
        assert top_scorer in selected_names

    def test_supervisor_excluded(self):
        """Identity named 'supervisor' should never be a participant."""
        identities = [
            _make_identity("supervisor", interest_keywords=["everything"]),
            _make_identity("anomal", interest_keywords=["AI"]),
            _make_identity("cherry", interest_keywords=["dating"]),
        ]
        topic = {"tags": ["AI"], "ideal_participants": 3}
        result = select_participants(identities, topic)
        names = [i.name for i in result]
        assert "supervisor" not in names

    def test_empty_identities(self):
        result = select_participants([], {"tags": ["AI"], "ideal_participants": 3})
        assert result == []

    def test_diversity_bonus_for_absent_participants(self, identities, ai_topic):
        """Identities not in recent_participants get a diversity bonus."""
        random.seed(42)
        # Run many times and count selections
        recent = ["anomal", "natt"]  # These were recent
        absent_selected = 0
        trials = 100

        for i in range(trials):
            random.seed(i)
            result = select_participants(
                identities, ai_topic, recent_participants=recent
            )
            names = {id.name for id in result}
            # Check if any absent identities were selected
            if names & {"cherry", "blixt", "bjork"}:
                absent_selected += 1

        # With diversity bonus, absent identities should be selected frequently
        assert absent_selected > trials * 0.5
