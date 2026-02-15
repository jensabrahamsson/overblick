"""
Tests for EmotionalState — personality-driven mood tracking.
"""

import pytest
import time

from overblick.capabilities.psychology.emotional_state import EmotionalState, Mood


class TestEmotionalState:
    def test_initialization_defaults(self):
        state = EmotionalState()
        assert state.current_mood == Mood.NEUTRAL
        assert state.mood_intensity == 0.5
        assert state.positive_interactions == 0
        assert state.negative_interactions == 0
        assert state.last_change > 0

    def test_record_positive_interaction(self):
        state = EmotionalState()
        initial_time = state.last_change
        
        state.record_positive()
        
        assert state.positive_interactions == 1
        assert state.current_mood in [
            Mood.CURIOUS,
            Mood.ENTHUSIASTIC,
            Mood.AMUSED,
            Mood.INSPIRED,
        ]
        assert state.mood_intensity > 0.5
        assert state.last_change > initial_time

    def test_record_negative_interaction(self):
        state = EmotionalState()
        initial_time = state.last_change
        
        state.record_negative()
        
        assert state.negative_interactions == 1
        assert state.current_mood in [Mood.CONTEMPLATIVE, Mood.FRUSTRATED]
        assert state.mood_intensity > 0.5
        assert state.last_change > initial_time

    def test_multiple_positive_interactions(self):
        state = EmotionalState()
        
        for _ in range(5):
            state.record_positive()
        
        assert state.positive_interactions == 5
        # Intensity should be capped at 1.0
        assert state.mood_intensity <= 1.0

    def test_multiple_negative_interactions(self):
        state = EmotionalState()
        
        for _ in range(10):
            state.record_negative()
        
        assert state.negative_interactions == 10
        # Intensity should be capped at 1.0
        assert state.mood_intensity <= 1.0

    def test_decay_reduces_intensity(self):
        state = EmotionalState()
        state.record_positive()
        initial_intensity = state.mood_intensity
        
        # Simulate time passing (set last_change to past)
        state.last_change = time.time() - 3600  # 1 hour ago
        
        state.decay()
        
        assert state.mood_intensity < initial_intensity

    def test_decay_returns_to_neutral(self):
        state = EmotionalState()
        state.record_positive()
        
        # Simulate long time passing
        state.last_change = time.time() - 36000  # 10 hours ago
        
        state.decay()
        
        # Should return to neutral
        assert state.current_mood == Mood.NEUTRAL
        assert state.mood_intensity == 0.5

    def test_decay_no_change_if_recent(self):
        state = EmotionalState()
        state.record_positive()
        initial_intensity = state.mood_intensity
        initial_mood = state.current_mood
        
        # Just changed, decay should have minimal effect
        state.decay()
        
        # Intensity should be very close to initial
        assert abs(state.mood_intensity - initial_intensity) < 0.01

    def test_get_mood_hint_neutral(self):
        state = EmotionalState()
        hint = state.get_mood_hint()
        assert hint == ""

    def test_get_mood_hint_with_mood(self):
        state = EmotionalState()
        state.record_positive()
        
        hint = state.get_mood_hint()
        
        assert hint != ""
        assert "mood:" in hint.lower()
        assert state.current_mood.value in hint

    def test_to_dict(self):
        state = EmotionalState()
        state.record_positive()
        state.record_positive()
        state.record_negative()
        
        data = state.to_dict()
        
        assert data["mood"] == state.current_mood.value
        assert data["intensity"] == state.mood_intensity
        assert data["positive"] == 2
        assert data["negative"] == 1

    def test_mood_enum_values(self):
        assert Mood.NEUTRAL.value == "neutral"
        assert Mood.CURIOUS.value == "curious"
        assert Mood.ENTHUSIASTIC.value == "enthusiastic"
        assert Mood.CONTEMPLATIVE.value == "contemplative"
        assert Mood.AMUSED.value == "amused"
        assert Mood.FRUSTRATED.value == "frustrated"
        assert Mood.INSPIRED.value == "inspired"

    def test_intensity_increase_positive(self):
        state = EmotionalState()
        initial = state.mood_intensity
        
        state.record_positive()
        
        # Should increase by 0.1
        assert state.mood_intensity >= initial + 0.1

    def test_intensity_increase_negative(self):
        state = EmotionalState()
        initial = state.mood_intensity
        
        state.record_negative()
        
        # Should increase by 0.05
        assert state.mood_intensity >= initial + 0.05

    def test_intensity_cap_at_one(self):
        state = EmotionalState()
        state.mood_intensity = 0.98
        
        state.record_positive()
        
        assert state.mood_intensity == 1.0

    def test_decay_floor_at_zero(self):
        state = EmotionalState()
        state.mood_intensity = 0.05
        state.last_change = time.time() - 3600
        
        state.decay()
        
        # Should not go below 0
        assert state.mood_intensity >= 0.0

    def test_mixed_interactions(self):
        state = EmotionalState()
        
        state.record_positive()
        state.record_positive()
        state.record_negative()
        state.record_positive()
        
        assert state.positive_interactions == 3
        assert state.negative_interactions == 1
        # Mood should reflect latest interaction trend
        assert state.mood_intensity > 0.5
