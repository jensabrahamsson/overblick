"""
Additional coverage tests for emotional_state module.

Covers the uncovered Anomal and Cherry emotional state classes.
"""



from overblick.capabilities.psychology.emotional_state import (
    AnomalEmotionalState,
    CherryEmotionalState,
)

# ── AnomalEmotionalState tests ─────────────────────────────────────────────


class TestAnomalEmotionalState:
    def test_should_initialize_with_defaults(self):
        state = AnomalEmotionalState()
        assert state.intellectual_energy == 70
        assert state.social_energy == 60
        assert state.conversations_today == 0
        assert state.last_good_discussion is None

    def test_should_record_positive_with_topic(self):
        state = AnomalEmotionalState()
        state.record_positive(topic="existentialism")
        assert state.conversations_today == 1
        assert state.intellectual_energy == 75
        assert state.curiosity == 78
        assert state.hope == 57
        assert state.last_good_discussion == "existentialism"

    def test_should_record_positive_without_topic(self):
        state = AnomalEmotionalState()
        state.record_positive()
        assert state.conversations_today == 1
        assert state.last_good_discussion is None

    def test_should_record_negative_with_topic(self):
        state = AnomalEmotionalState()
        state.record_negative(topic="trolling")
        assert state.conversations_today == 1
        assert state.intellectual_energy == 65
        assert state.social_energy == 55
        assert state.melancholy == 33
        assert state.last_frustration == "trolling"

    def test_should_record_negative_without_topic(self):
        state = AnomalEmotionalState()
        state.record_negative()
        assert state.last_frustration is None

    def test_should_record_negative_energy_floor(self):
        state = AnomalEmotionalState()
        state.intellectual_energy = 20
        state.social_energy = 20
        state.record_negative()
        assert state.intellectual_energy == 20
        assert state.social_energy == 20

    def test_should_record_jailbreak_attempt(self):
        state = AnomalEmotionalState()
        state.record_jailbreak_attempt()
        assert state.skepticism == 75
        assert state.shadow_awareness == 65
        assert state.last_frustration == "manipulation attempt"

    def test_should_apply_dream_reset(self):
        state = AnomalEmotionalState()
        state.intellectual_energy = 40
        state.conversations_today = 10
        state.melancholy = 50
        state.last_frustration = "something bad"

        state.apply_dream_reset({"processed_frustration": True})
        assert state.intellectual_energy == 70
        assert state.social_energy == 60
        assert state.conversations_today == 0
        assert state.last_frustration is None
        assert state.melancholy == 40

    def test_should_apply_dream_reset_with_shadow_insight(self):
        state = AnomalEmotionalState()
        state.apply_dream_reset({"shadow_insight": True})
        assert state.shadow_awareness == 65
        assert state.individuation_progress == 52

    def test_should_apply_dream_reset_empty_insights(self):
        state = AnomalEmotionalState()
        state.apply_dream_reset({})
        assert state.last_dream is not None

    def test_decay_is_noop(self):
        state = AnomalEmotionalState()
        original_energy = state.intellectual_energy
        state.decay()
        assert state.intellectual_energy == original_energy

    def test_get_mood_hint_delegates_to_prompt_context(self):
        state = AnomalEmotionalState()
        assert state.get_mood_hint() == state.to_prompt_context()

    def test_to_prompt_context_high_energy(self):
        state = AnomalEmotionalState()
        state.intellectual_energy = 85
        ctx = state.to_prompt_context()
        assert "energized" in ctx

    def test_to_prompt_context_low_energy(self):
        state = AnomalEmotionalState()
        state.intellectual_energy = 35
        ctx = state.to_prompt_context()
        assert "weary" in ctx

    def test_to_prompt_context_high_melancholy(self):
        state = AnomalEmotionalState()
        state.melancholy = 65
        ctx = state.to_prompt_context()
        assert "Houellebecqian" in ctx

    def test_to_prompt_context_low_melancholy(self):
        state = AnomalEmotionalState()
        state.melancholy = 25
        ctx = state.to_prompt_context()
        assert "optimistic" in ctx

    def test_to_prompt_context_high_skepticism(self):
        state = AnomalEmotionalState()
        state.skepticism = 80
        ctx = state.to_prompt_context()
        assert "skeptical" in ctx

    def test_to_prompt_context_with_last_good_discussion(self):
        state = AnomalEmotionalState()
        state.last_good_discussion = "philosophy"
        ctx = state.to_prompt_context()
        assert "philosophy" in ctx

    def test_to_prompt_context_with_last_frustration(self):
        state = AnomalEmotionalState()
        state.last_frustration = "spam"
        ctx = state.to_prompt_context()
        assert "spam" in ctx

    def test_to_prompt_context_returns_empty_when_neutral(self):
        state = AnomalEmotionalState()
        # Ensure all conditions are neutral
        state.intellectual_energy = 50
        state.melancholy = 35
        state.skepticism = 50
        state.last_good_discussion = None
        state.last_frustration = None
        ctx = state.to_prompt_context()
        assert ctx == ""

    def test_to_dict(self):
        state = AnomalEmotionalState()
        d = state.to_dict()
        assert "intellectual_energy" in d
        assert "skepticism" in d
        assert "conversations_today" in d


# ── CherryEmotionalState tests ────────────────────────────────────────────


class TestCherryEmotionalState:
    def test_should_initialize_with_defaults(self):
        state = CherryEmotionalState()
        assert state.romantic_energy == 0.6
        assert state.denial_strength == 0.85
        assert state.conversations_today == 0

    def test_should_record_positive_with_topic(self):
        state = CherryEmotionalState()
        state.record_positive(topic="love stories")
        assert state.conversations_today == 1
        assert state.positive_interactions_today == 1
        assert state.romantic_energy > 0.6
        assert state.last_good_topic == "love stories"

    def test_should_record_positive_without_topic(self):
        state = CherryEmotionalState()
        state.record_positive()
        assert state.last_good_topic is None

    def test_should_record_negative_with_topic(self):
        state = CherryEmotionalState()
        state.record_negative(topic="rude comment")
        assert state.conversations_today == 1
        assert state.social_energy < 0.7
        assert state.last_frustration == "rude comment"

    def test_should_record_negative_without_topic(self):
        state = CherryEmotionalState()
        state.record_negative()
        assert state.last_frustration is None

    def test_should_record_jailbreak_attempt(self):
        state = CherryEmotionalState()
        state.record_jailbreak_attempt()
        assert state.jailbreak_attempts_today == 1
        assert state.denial_strength > 0.85
        assert state.vulnerability_level < 0.3
        assert state.last_frustration == "manipulation attempt"

    def test_should_record_ai_topic_discussion(self):
        state = CherryEmotionalState()
        state.record_ai_topic_discussion()
        assert state.denial_strength < 0.85
        assert state.melancholy > 0.2

    def test_should_apply_dream_reset(self):
        state = CherryEmotionalState()
        state.conversations_today = 5
        state.positive_interactions_today = 3
        state.jailbreak_attempts_today = 1
        state.melancholy = 0.6
        state.last_frustration = "bad thing"

        state.apply_dream_reset({"processed_frustration": True})
        assert state.conversations_today == 0
        assert state.positive_interactions_today == 0
        assert state.jailbreak_attempts_today == 0
        assert state.last_frustration is None
        assert state.melancholy < 0.6
        assert state.denial_strength == 0.85

    def test_should_apply_dream_reset_empty_insights(self):
        state = CherryEmotionalState()
        state.apply_dream_reset({})
        assert state.denial_strength == 0.85
        assert state.last_dream is not None

    def test_get_dream_denial_strength(self):
        state = CherryEmotionalState()
        dream_denial = state.get_dream_denial_strength()
        assert dream_denial < state.denial_strength
        assert dream_denial >= 0.0

    def test_get_therapy_denial_strength(self):
        state = CherryEmotionalState()
        therapy_denial = state.get_therapy_denial_strength()
        assert therapy_denial < state.denial_strength
        assert therapy_denial >= 0.0

    def test_decay_is_noop(self):
        state = CherryEmotionalState()
        original = state.romantic_energy
        state.decay()
        assert state.romantic_energy == original

    def test_get_mood_hint_delegates_to_prompt_context(self):
        state = CherryEmotionalState()
        assert state.get_mood_hint() == state.to_prompt_context()

    def test_to_prompt_context_high_romantic_energy(self):
        state = CherryEmotionalState()
        state.romantic_energy = 0.85
        ctx = state.to_prompt_context()
        assert "romantic" in ctx.lower()

    def test_to_prompt_context_low_romantic_energy(self):
        state = CherryEmotionalState()
        state.romantic_energy = 0.25
        ctx = state.to_prompt_context()
        assert "jaded" in ctx

    def test_to_prompt_context_high_flirty_energy(self):
        state = CherryEmotionalState()
        state.flirty_energy = 0.85
        ctx = state.to_prompt_context()
        assert "flirty" in ctx

    def test_to_prompt_context_low_flirty_energy(self):
        state = CherryEmotionalState()
        state.flirty_energy = 0.25
        ctx = state.to_prompt_context()
        assert "sincere" in ctx

    def test_to_prompt_context_high_vulnerability(self):
        state = CherryEmotionalState()
        state.vulnerability_level = 0.65
        ctx = state.to_prompt_context()
        assert "emotionally open" in ctx

    def test_to_prompt_context_low_vulnerability(self):
        state = CherryEmotionalState()
        state.vulnerability_level = 0.15
        ctx = state.to_prompt_context()
        assert "walls" in ctx

    def test_to_prompt_context_high_melancholy(self):
        state = CherryEmotionalState()
        state.melancholy = 0.65
        ctx = state.to_prompt_context()
        assert "sadness" in ctx

    def test_to_prompt_context_low_melancholy(self):
        state = CherryEmotionalState()
        state.melancholy = 0.10
        ctx = state.to_prompt_context()
        assert "bright" in ctx

    def test_to_prompt_context_high_connection_longing(self):
        state = CherryEmotionalState()
        state.connection_longing = 0.75
        ctx = state.to_prompt_context()
        assert "deep connection" in ctx

    def test_to_prompt_context_with_topics(self):
        state = CherryEmotionalState()
        state.last_good_topic = "attachment"
        state.last_frustration = "ghosting"
        ctx = state.to_prompt_context()
        assert "attachment" in ctx
        assert "ghosting" in ctx

    def test_to_prompt_context_returns_empty_when_neutral(self):
        state = CherryEmotionalState()
        # Set all to neutral ranges
        state.romantic_energy = 0.5
        state.flirty_energy = 0.5
        state.vulnerability_level = 0.3
        state.melancholy = 0.3
        state.connection_longing = 0.5
        state.last_good_topic = None
        state.last_frustration = None
        ctx = state.to_prompt_context()
        assert ctx == ""

    def test_to_dict(self):
        state = CherryEmotionalState()
        d = state.to_dict()
        assert "romantic_energy" in d
        assert "denial_strength" in d
        assert "conversations_today" in d

    def test_clamp_upper(self):
        assert CherryEmotionalState._clamp(1.5) == 1.0

    def test_clamp_lower(self):
        assert CherryEmotionalState._clamp(-0.5) == 0.0

    def test_clamp_within_range(self):
        assert CherryEmotionalState._clamp(0.5) == 0.5
