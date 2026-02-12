"""Tests for emotional state engine."""

import time
from unittest.mock import patch
from blick.core.emotional_state import EmotionalState, Mood


class TestEmotionalState:
    def test_initial_mood(self):
        es = EmotionalState()
        assert es.current_mood == Mood.NEUTRAL
        assert es.mood_intensity == 0.5

    def test_record_positive(self):
        es = EmotionalState()
        es.record_positive()
        assert es.positive_interactions == 1
        assert es.current_mood in [Mood.CURIOUS, Mood.ENTHUSIASTIC, Mood.AMUSED, Mood.INSPIRED]
        assert es.mood_intensity > 0.5

    def test_record_negative(self):
        es = EmotionalState()
        es.record_negative()
        assert es.negative_interactions == 1
        assert es.current_mood in [Mood.CONTEMPLATIVE, Mood.FRUSTRATED]

    def test_multiple_positives_increase_intensity(self):
        es = EmotionalState()
        for _ in range(5):
            es.record_positive()
        assert es.mood_intensity > 0.5
        assert es.positive_interactions == 5

    def test_decay_toward_neutral(self):
        es = EmotionalState()
        es.record_positive()
        # Simulate 10 hours elapsed
        es.last_change = time.time() - 36000
        es.mood_intensity = 0.05
        es.decay()
        assert es.current_mood == Mood.NEUTRAL

    def test_get_mood_hint_neutral(self):
        es = EmotionalState()
        assert es.get_mood_hint() == ""

    def test_get_mood_hint_non_neutral(self):
        es = EmotionalState()
        es.record_positive()
        hint = es.get_mood_hint()
        assert "Current mood:" in hint
        assert "intensity:" in hint

    def test_to_dict(self):
        es = EmotionalState()
        es.record_positive()
        es.record_negative()
        d = es.to_dict()
        assert d["positive"] == 1
        assert d["negative"] == 1
        assert "mood" in d
        assert "intensity" in d

    def test_intensity_capped_at_one(self):
        es = EmotionalState()
        for _ in range(100):
            es.record_positive()
        assert es.mood_intensity <= 1.0


class TestMood:
    def test_enum_values(self):
        assert Mood.NEUTRAL.value == "neutral"
        assert Mood.CURIOUS.value == "curious"
        assert Mood.FRUSTRATED.value == "frustrated"
