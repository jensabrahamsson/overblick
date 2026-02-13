"""Tests for therapy system."""

from datetime import datetime
from overblick.plugins.moltbook.therapy_system import TherapySystem, TherapySession


class TestTherapySystem:
    def test_creation(self):
        ts = TherapySystem()
        assert ts is not None

    def test_creation_with_therapy_day(self):
        ts = TherapySystem(therapy_day=0)  # Monday
        assert ts._therapy_day == 0

    def test_is_therapy_day(self):
        today = datetime.now().weekday()
        ts = TherapySystem(therapy_day=today)
        assert ts.is_therapy_day()

    def test_is_not_therapy_day(self):
        # Pick a day that is NOT today
        today = datetime.now().weekday()
        other = (today + 3) % 7
        ts = TherapySystem(therapy_day=other)
        assert not ts.is_therapy_day()

    def test_session_history_starts_empty(self):
        ts = TherapySystem()
        assert ts._session_history == []


class TestTherapySession:
    def test_defaults(self):
        session = TherapySession()
        assert session.dreams_processed == 0
        assert session.learnings_processed == 0
        assert session.dream_themes == []
        assert session.shadow_patterns == []

    def test_to_dict(self):
        session = TherapySession(
            week_number=5,
            dreams_processed=3,
            learnings_processed=2,
            dream_themes=["growth", "identity"],
            synthesis_insights=["Insight 1"],
            shadow_patterns=["avoidance"],
            archetype_encounters=["sage"],
        )
        d = session.to_dict()
        assert d["week_number"] == 5
        assert d["dreams_processed"] == 3
        assert d["dream_themes"] == ["growth", "identity"]
        assert d["shadow_patterns"] == ["avoidance"]
        assert d["archetype_encounters"] == ["sage"]
        assert "timestamp" in d

    def test_post_fields(self):
        session = TherapySession(
            post_title="Weekly Reflection",
            post_content="Content here",
            post_submolt="philosophy",
        )
        assert session.post_title == "Weekly Reflection"
        assert session.post_submolt == "philosophy"
