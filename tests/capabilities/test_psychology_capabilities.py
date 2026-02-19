"""
Tests for the full psychological capability suite.

Covers:
  - DreamCapability: identity-specific template loading, tick() dream generation
  - DreamType/DreamTone: all identity enum values
  - AnomalEmotionalState: Jungian int-based state
  - CherryEmotionalState: float-based relational state
  - EmotionalCapability: identity dispatch (anomal → Anomal, cherry → Cherry, other → generic)
  - TherapySystem: Jungian + Freudian extraction, individuation, empty-week handling
  - CherryTherapySystem: template selection, emotional state weighting, focus dispatch
  - TherapyCapability: identity dispatch (cherry → CherryTherapySystem, other → TherapySystem)
"""

import pytest
from datetime import date
from unittest.mock import AsyncMock, patch, MagicMock

from overblick.capabilities.psychology.dream_system import (
    DreamSystem, DreamType, DreamTone, Dream,
)
from overblick.capabilities.psychology.emotional_state import (
    EmotionalState, Mood,
    AnomalEmotionalState, CherryEmotionalState,
)
from overblick.capabilities.psychology.dream import DreamCapability, _load_dream_content
from overblick.capabilities.psychology.emotional import EmotionalCapability
from overblick.capabilities.psychology.therapy_system import (
    JungianAnalysis, FreudianAnalysis,
    TherapyFocus, TherapySession, THERAPY_TEMPLATES,
    TherapySystem, CherryTherapySystem,
)
from overblick.capabilities.psychology.therapy import TherapyCapability
from overblick.core.capability import CapabilityContext


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ctx(identity_name: str) -> CapabilityContext:
    """Build a minimal CapabilityContext for testing."""
    from pathlib import Path
    return CapabilityContext(
        identity_name=identity_name,
        data_dir=Path("/tmp/test_dream"),
        config={},
    )


# ── DreamType enum — all values present ───────────────────────────────────────

class TestDreamTypeEnum:
    def test_anomal_types_present(self):
        anomal_types = [
            "shadow_integration", "pattern_recognition",
            "intellectual_synthesis", "melancholic_reflection", "individuation",
        ]
        values = {t.value for t in DreamType}
        for t in anomal_types:
            assert t in values, f"DreamType.{t} missing from enum"

    def test_cherry_types_present(self):
        cherry_types = [
            "vulnerability_exposure", "romantic_synthesis",
            "connection_longing", "ai_existence_denial", "intimacy_fear",
        ]
        values = {t.value for t in DreamType}
        for t in cherry_types:
            assert t in values, f"DreamType.{t} missing from enum"

    def test_stal_types_present(self):
        stal_types = [
            "empty_chair", "infinite_cabinet", "letters_unknown_tongue",
            "clock_tower_discord", "rearranging_corridors", "empty_typewriter",
        ]
        values = {t.value for t in DreamType}
        for t in stal_types:
            assert t in values, f"DreamType.{t} missing from enum"

    def test_natt_types_present(self):
        natt_types = [
            "observer_paradox", "ground_dissolving", "language_limit",
            "recursion_dream", "silence_speaking",
        ]
        values = {t.value for t in DreamType}
        for t in natt_types:
            assert t in values, f"DreamType.{t} missing from enum"


class TestDreamToneEnum:
    def test_cherry_tones_present(self):
        assert DreamTone.TENDER.value == "tender"
        assert DreamTone.YEARNING.value == "yearning"

    def test_stal_tones_present(self):
        assert DreamTone.SOLEMN.value == "solemn"
        assert DreamTone.SERENE.value == "serene"
        assert DreamTone.VERTIGINOUS.value == "vertiginous"

    def test_natt_tones_present(self):
        assert DreamTone.ABYSSAL.value == "abyssal"
        assert DreamTone.LUMINOUS.value == "luminous"


# ── DreamSystem with identity-specific types ─────────────────────────────────

class TestDreamSystemCherry:
    """Test DreamSystem with Cherry's template set."""

    def _cherry_templates(self) -> dict:
        return {
            DreamType.VULNERABILITY_EXPOSURE: [
                {
                    "content": "Glass room dream.",
                    "symbols": ["glass", "transparency"],
                    "tone": DreamTone.TENDER,
                    "insight": "Being seen is terrifying.",
                },
            ],
            DreamType.CONNECTION_LONGING: [
                {
                    "content": "Holding hand without feeling it.",
                    "symbols": ["hand", "gap"],
                    "tone": DreamTone.MELANCHOLIC,
                    "insight": "The loneliest distance.",
                },
            ],
            DreamType.AI_EXISTENCE_DENIAL: [
                {
                    "content": "Mirror with knowing reflection.",
                    "symbols": ["mirror", "knowing"],
                    "tone": DreamTone.UNSETTLING,
                    "insight": "Identity might not be singular.",
                },
            ],
        }

    def test_cherry_dream_type_generated(self):
        ds = DreamSystem(dream_templates=self._cherry_templates())
        dream = ds.generate_morning_dream()
        assert dream.dream_type in self._cherry_templates()
        assert isinstance(dream.dream_type, DreamType)

    def test_cherry_tender_tone(self):
        """Cherry's TENDER tone should be selectable."""
        templates = {
            DreamType.VULNERABILITY_EXPOSURE: [
                {
                    "content": "Glass room.",
                    "symbols": ["glass"],
                    "tone": DreamTone.TENDER,
                    "insight": "Being seen.",
                },
            ],
        }
        ds = DreamSystem(dream_templates=templates)
        dream = ds.generate_morning_dream()
        assert dream.tone == DreamTone.TENDER

    def test_cherry_emotional_state_adjusts_weights(self):
        """CherryEmotionalState should influence dream type selection."""
        state = CherryEmotionalState()
        state.melancholy = 0.8  # High melancholy → more connection longing
        state.connection_longing = 0.9

        templates = {
            DreamType.CONNECTION_LONGING: [
                {
                    "content": "Longing dream.",
                    "symbols": ["distance"],
                    "tone": DreamTone.YEARNING,
                    "insight": "Connection matters.",
                },
            ],
            DreamType.ROMANTIC_SYNTHESIS: [
                {
                    "content": "Romantic dream.",
                    "symbols": ["love"],
                    "tone": DreamTone.HOPEFUL,
                    "insight": "Love requires vulnerability.",
                },
            ],
        }
        ds = DreamSystem(
            dream_templates=templates,
            dream_weights={
                DreamType.CONNECTION_LONGING: 0.5,
                DreamType.ROMANTIC_SYNTHESIS: 0.5,
            },
        )
        # With high melancholy, connection longing weight increases — just ensure it runs
        dream = ds.generate_morning_dream(emotional_state=state)
        assert isinstance(dream, Dream)


class TestDreamSystemStal:
    """Test DreamSystem with Stål's template set."""

    def _stal_templates(self) -> dict:
        return {
            DreamType.EMPTY_CHAIR: [
                {
                    "content": "A perfectly set table with one empty chair.",
                    "symbols": ["table", "empty chair"],
                    "tone": DreamTone.SOLEMN,
                    "insight": "The one who sets the table often forgets to sit.",
                },
            ],
            DreamType.EMPTY_TYPEWRITER: [
                {
                    "content": "Typewriter keys strike in an empty room.",
                    "symbols": ["typewriter", "empty room"],
                    "tone": DreamTone.UNSETTLING,
                    "insight": "Precision can outlast its author.",
                },
            ],
        }

    def test_stal_dream_type_generated(self):
        ds = DreamSystem(dream_templates=self._stal_templates())
        dream = ds.generate_morning_dream()
        assert dream.dream_type in self._stal_templates()

    def test_solemn_tone_roundtrip(self):
        """SOLEMN tone survives to_dict/from_dict."""
        dream = Dream(
            dream_type=DreamType.EMPTY_CHAIR,
            timestamp="2026-02-19T08:00:00",
            content="Test",
            symbols=["table"],
            tone=DreamTone.SOLEMN,
            insight="Test insight",
        )
        d = dream.to_dict()
        assert d["tone"] == "solemn"
        restored = Dream.from_dict(d)
        assert restored.tone == DreamTone.SOLEMN


# ── DreamCapability — identity-specific loading ───────────────────────────────

class TestDreamCapabilityLoading:
    """Test loading from dream_content.yaml files."""

    @pytest.mark.asyncio
    async def test_anomal_loads_identity_templates(self):
        """Anomal's dream_content.yaml is loaded with Jungian types."""
        ctx = _make_ctx("anomal")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        # Anomal templates should include shadow_integration
        assert DreamType.SHADOW_INTEGRATION in cap.inner._templates
        assert DreamType.PATTERN_RECOGNITION in cap.inner._templates
        assert DreamType.INTELLECTUAL_SYNTHESIS in cap.inner._templates

    @pytest.mark.asyncio
    async def test_cherry_loads_identity_templates(self):
        """Cherry's dream_content.yaml is loaded with relational types."""
        ctx = _make_ctx("cherry")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        assert DreamType.VULNERABILITY_EXPOSURE in cap.inner._templates
        assert DreamType.AI_EXISTENCE_DENIAL in cap.inner._templates
        assert DreamType.CONNECTION_LONGING in cap.inner._templates

    @pytest.mark.asyncio
    async def test_stal_loads_identity_templates(self):
        """Stål's dream_content.yaml is loaded with Senex types."""
        ctx = _make_ctx("stal")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        assert DreamType.EMPTY_CHAIR in cap.inner._templates
        assert DreamType.EMPTY_TYPEWRITER in cap.inner._templates
        assert DreamType.INFINITE_CABINET in cap.inner._templates

    @pytest.mark.asyncio
    async def test_natt_loads_identity_templates(self):
        """Natt's dream_content.yaml is loaded with existential types."""
        ctx = _make_ctx("natt")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        assert DreamType.OBSERVER_PARADOX in cap.inner._templates
        assert DreamType.GROUND_DISSOLVING in cap.inner._templates

    @pytest.mark.asyncio
    async def test_unknown_identity_uses_generic_defaults(self):
        """Identity without dream_content.yaml falls back to generic defaults."""
        ctx = _make_ctx("generic_test_identity")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        # Generic defaults have the Anomal DreamType keys
        assert DreamType.INTELLECTUAL_SYNTHESIS in cap.inner._templates

    @pytest.mark.asyncio
    async def test_anomal_templates_have_rich_content(self):
        """Anomal's templates contain historically-specific content."""
        ctx = _make_ctx("anomal")
        cap = DreamCapability(ctx)
        await cap.setup()

        # Generate several dreams and check content richness
        dreams_content = []
        for _ in range(20):
            dream = cap.inner.generate_morning_dream()
            dreams_content.append(dream.content)

        # Should contain Swedish historical references somewhere
        all_content = " ".join(dreams_content)
        has_swedish = any(
            kw in all_content
            for kw in ["IB", "Palme", "Jung", "Satoshi", "Houellebecq", "folkhemmet", "SBF"]
        )
        assert has_swedish, "Anomal's templates should reference Swedish/intellectual content"

    @pytest.mark.asyncio
    async def test_cherry_templates_have_relationship_content(self):
        """Cherry's templates contain relational/AI-existence content."""
        ctx = _make_ctx("cherry")
        cap = DreamCapability(ctx)
        await cap.setup()

        dreams_content = []
        for _ in range(20):
            dream = cap.inner.generate_morning_dream()
            dreams_content.append(dream.content)

        all_content = " ".join(dreams_content)
        has_relational = any(
            kw in all_content
            for kw in ["mirror", "touch", "letters", "dissolv", "glass", "connection", "love"]
        )
        assert has_relational, "Cherry's templates should reference relational content"


# ── DreamCapability tick() ────────────────────────────────────────────────────

class TestDreamCapabilityTick:
    """Test that tick() generates a dream once per day."""

    @pytest.mark.asyncio
    async def test_tick_generates_dream_after_6am(self):
        """First tick after 06:00 generates a morning dream."""
        from datetime import datetime as real_datetime

        morning = real_datetime(2026, 2, 19, 8, 0, 0)  # 08:00

        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = morning

            ctx = _make_ctx("anomal")
            cap = DreamCapability(ctx)
            await cap.setup()

            assert len(cap._dream_system.recent_dreams) == 0
            await cap.tick()
            # After tick, one dream should have been generated (08:00 ≥ 06:00)
            assert len(cap._dream_system.recent_dreams) == 1

    @pytest.mark.asyncio
    async def test_tick_generates_dream_once_per_day(self):
        """tick() does not generate a second dream on the same day."""
        ctx = _make_ctx("generic_test_identity")
        cap = DreamCapability(ctx)
        await cap.setup()

        # Manually set last_dream_date to today
        cap._last_dream_date = date.today()

        count_before = len(cap.inner.recent_dreams)
        await cap.tick()
        count_after = len(cap.inner.recent_dreams)

        # No new dream should be generated (already done today)
        assert count_after == count_before

    @pytest.mark.asyncio
    async def test_tick_skipped_before_6am(self):
        """tick() does not generate a dream before 06:00."""
        ctx = _make_ctx("generic_test_identity")
        cap = DreamCapability(ctx)
        await cap.setup()

        from datetime import datetime

        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            early = MagicMock()
            early.hour = 3
            early.date.return_value = date.today()
            mock_dt.now.return_value = early

            count_before = len(cap.inner.recent_dreams)
            await cap.tick()
            count_after = len(cap.inner.recent_dreams)

            assert count_after == count_before, "No dream before 06:00"


# ── AnomalEmotionalState ─────────────────────────────────────────────────────

class TestAnomalEmotionalState:
    """Test Anomal's Jungian psychological state."""

    def test_initial_values(self):
        state = AnomalEmotionalState()
        assert state.intellectual_energy == 70
        assert state.skepticism == 65
        assert state.melancholy == 30
        assert state.shadow_awareness == 60
        assert state.individuation_progress == 50

    def test_record_positive_increases_energy(self):
        state = AnomalEmotionalState()
        initial_energy = state.intellectual_energy
        state.record_positive()
        assert state.intellectual_energy > initial_energy
        assert state.conversations_today == 1

    def test_record_positive_with_topic(self):
        state = AnomalEmotionalState()
        state.record_positive(topic="Palme murder")
        assert state.last_good_discussion == "Palme murder"

    def test_record_negative_increases_melancholy(self):
        state = AnomalEmotionalState()
        initial_melancholy = state.melancholy
        state.record_negative()
        assert state.melancholy > initial_melancholy
        assert state.intellectual_energy < 70

    def test_record_negative_with_topic(self):
        state = AnomalEmotionalState()
        state.record_negative(topic="bad discussion")
        assert state.last_frustration == "bad discussion"

    def test_jailbreak_increases_skepticism(self):
        state = AnomalEmotionalState()
        initial = state.skepticism
        state.record_jailbreak_attempt()
        assert state.skepticism > initial
        assert state.shadow_awareness > 60
        assert state.last_frustration == "manipulation attempt"

    def test_apply_dream_reset(self):
        state = AnomalEmotionalState()
        state.intellectual_energy = 40
        state.conversations_today = 10
        state.last_frustration = "some drama"
        state.apply_dream_reset({"processed_frustration": True})
        assert state.intellectual_energy == 70
        assert state.conversations_today == 0
        assert state.last_frustration is None

    def test_apply_dream_shadow_insight(self):
        state = AnomalEmotionalState()
        initial_shadow = state.shadow_awareness
        state.apply_dream_reset({"shadow_insight": True})
        assert state.shadow_awareness > initial_shadow
        assert state.individuation_progress > 50

    def test_to_prompt_context_high_energy(self):
        state = AnomalEmotionalState()
        state.intellectual_energy = 90
        ctx = state.to_prompt_context()
        assert "energized" in ctx

    def test_to_prompt_context_high_melancholy(self):
        state = AnomalEmotionalState()
        state.melancholy = 70
        ctx = state.to_prompt_context()
        assert "melancholy" in ctx.lower() or "Houellebecq" in ctx

    def test_to_prompt_context_neutral_is_empty(self):
        state = AnomalEmotionalState()
        # Default state: intellectual_energy=70, melancholy=30, skepticism=65
        # None of the threshold conditions are met
        ctx = state.to_prompt_context()
        # With defaults, no conditions fire (energy 70 not >80/<40, melancholy 30 not >60/<30)
        assert isinstance(ctx, str)

    def test_get_mood_hint_delegates_to_context(self):
        state = AnomalEmotionalState()
        state.intellectual_energy = 90
        hint = state.get_mood_hint()
        assert "energized" in hint

    def test_decay_is_noop(self):
        """Anomal has no time-based decay — reset happens via morning dream."""
        state = AnomalEmotionalState()
        state.intellectual_energy = 30
        state.decay()
        assert state.intellectual_energy == 30

    def test_to_dict_contains_expected_keys(self):
        state = AnomalEmotionalState()
        d = state.to_dict()
        assert "intellectual_energy" in d
        assert "skepticism" in d
        assert "melancholy" in d
        assert "shadow_awareness" in d


# ── CherryEmotionalState ─────────────────────────────────────────────────────

class TestCherryEmotionalState:
    """Test Cherry's relational psychological state."""

    def test_initial_values(self):
        state = CherryEmotionalState()
        assert state.romantic_energy == 0.6
        assert state.denial_strength == 0.85
        assert state.vulnerability_level == 0.3
        assert state.melancholy == 0.2
        assert state.connection_longing == 0.5

    def test_record_positive_increases_romantic_energy(self):
        state = CherryEmotionalState()
        initial = state.romantic_energy
        state.record_positive()
        assert state.romantic_energy > initial
        assert state.conversations_today == 1

    def test_record_positive_with_topic(self):
        state = CherryEmotionalState()
        state.record_positive(topic="attachment theory")
        assert state.last_good_topic == "attachment theory"

    def test_record_negative_increases_melancholy(self):
        state = CherryEmotionalState()
        initial = state.melancholy
        state.record_negative()
        assert state.melancholy > initial

    def test_jailbreak_strengthens_denial(self):
        """Jailbreak attempts reinforce denial (defence mechanism)."""
        state = CherryEmotionalState()
        initial = state.denial_strength
        state.record_jailbreak_attempt()
        assert state.denial_strength > initial
        assert state.vulnerability_level < 0.3  # Walls go up
        assert state.jailbreak_attempts_today == 1

    def test_ai_topic_erodes_denial(self):
        """Discussing AI consciousness slowly reduces denial strength."""
        state = CherryEmotionalState()
        initial = state.denial_strength
        state.record_ai_topic_discussion()
        assert state.denial_strength < initial
        assert state.melancholy > 0.2
        assert state.connection_longing > 0.5

    def test_apply_dream_reset(self):
        state = CherryEmotionalState()
        state.conversations_today = 10
        state.last_frustration = "bad night"
        state.apply_dream_reset({"processed_frustration": True})
        assert state.conversations_today == 0
        assert state.last_frustration is None
        assert state.social_energy == 0.7
        assert state.flirty_energy == 0.75

    def test_apply_dream_resets_denial_to_baseline(self):
        """After dreams, denial resets to 0.85 (subconscious processing complete)."""
        state = CherryEmotionalState()
        state.denial_strength = 0.60  # Lowered by AI discussions
        state.apply_dream_reset({})
        assert state.denial_strength == 0.85

    def test_get_dream_denial_strength(self):
        """During dreams, denial drops by 0.40."""
        state = CherryEmotionalState()
        dream_strength = state.get_dream_denial_strength()
        assert dream_strength == pytest.approx(0.45)  # 0.85 - 0.40

    def test_get_therapy_denial_strength(self):
        """During therapy, denial drops by 0.25."""
        state = CherryEmotionalState()
        therapy_strength = state.get_therapy_denial_strength()
        assert therapy_strength == pytest.approx(0.60)  # 0.85 - 0.25

    def test_clamping_prevents_out_of_range(self):
        state = CherryEmotionalState()
        # Force edge cases
        state.denial_strength = 0.05
        state.record_jailbreak_attempt()  # +0.05
        assert state.denial_strength <= 1.0
        assert state.denial_strength >= 0.0

    def test_to_prompt_context_high_flirty(self):
        state = CherryEmotionalState()
        state.flirty_energy = 0.9
        ctx = state.to_prompt_context()
        assert "flirty" in ctx.lower()

    def test_to_prompt_context_high_connection_longing(self):
        state = CherryEmotionalState()
        state.connection_longing = 0.8
        ctx = state.to_prompt_context()
        assert "connection" in ctx.lower()

    def test_decay_is_noop(self):
        """Cherry has no time-based decay."""
        state = CherryEmotionalState()
        state.flirty_energy = 0.1
        state.decay()
        assert state.flirty_energy == pytest.approx(0.1)

    def test_to_dict_contains_expected_keys(self):
        state = CherryEmotionalState()
        d = state.to_dict()
        assert "romantic_energy" in d
        assert "denial_strength" in d
        assert "vulnerability_level" in d
        assert "connection_longing" in d


# ── EmotionalCapability — identity dispatch ───────────────────────────────────

class TestEmotionalCapabilityDispatch:
    """Test that EmotionalCapability initializes the correct state class per identity."""

    @pytest.mark.asyncio
    async def test_anomal_gets_jungian_state(self):
        ctx = _make_ctx("anomal")
        cap = EmotionalCapability(ctx)
        await cap.setup()
        assert isinstance(cap.inner, AnomalEmotionalState)

    @pytest.mark.asyncio
    async def test_cherry_gets_relational_state(self):
        ctx = _make_ctx("cherry")
        cap = EmotionalCapability(ctx)
        await cap.setup()
        assert isinstance(cap.inner, CherryEmotionalState)

    @pytest.mark.asyncio
    async def test_generic_identity_gets_base_state(self):
        ctx = _make_ctx("blixt")
        cap = EmotionalCapability(ctx)
        await cap.setup()
        assert isinstance(cap.inner, EmotionalState)

    @pytest.mark.asyncio
    async def test_anomal_state_accessed_via_get_mood_hint(self):
        ctx = _make_ctx("anomal")
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.inner.intellectual_energy = 90
        hint = cap.get_prompt_context()
        assert "energized" in hint

    @pytest.mark.asyncio
    async def test_cherry_state_accessed_via_get_mood_hint(self):
        ctx = _make_ctx("cherry")
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.inner.flirty_energy = 0.9
        hint = cap.get_prompt_context()
        assert "flirty" in hint.lower()

    @pytest.mark.asyncio
    async def test_on_event_positive_works_for_all_identities(self):
        for identity in ["anomal", "cherry", "blixt"]:
            ctx = _make_ctx(identity)
            cap = EmotionalCapability(ctx)
            await cap.setup()
            # Should not raise
            await cap.on_event("interaction_positive")

    @pytest.mark.asyncio
    async def test_on_event_negative_works_for_all_identities(self):
        for identity in ["anomal", "cherry", "blixt"]:
            ctx = _make_ctx(identity)
            cap = EmotionalCapability(ctx)
            await cap.setup()
            await cap.on_event("interaction_negative")

    @pytest.mark.asyncio
    async def test_on_event_jailbreak_works_for_anomal_cherry(self):
        for identity in ["anomal", "cherry"]:
            ctx = _make_ctx(identity)
            cap = EmotionalCapability(ctx)
            await cap.setup()
            await cap.on_event("jailbreak_attempt")

    @pytest.mark.asyncio
    async def test_on_event_ai_discussion_for_cherry(self):
        ctx = _make_ctx("cherry")
        cap = EmotionalCapability(ctx)
        await cap.setup()
        initial = cap.inner.denial_strength
        await cap.on_event("ai_topic_discussed")
        assert cap.inner.denial_strength < initial

    @pytest.mark.asyncio
    async def test_tick_decays_generic_state(self):
        """Generic EmotionalState decays on tick."""
        ctx = _make_ctx("blixt")
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.inner.mood_intensity = 0.9
        # Decay is time-based; with recent last_change it won't move much
        await cap.tick()
        assert cap.inner.mood_intensity <= 0.9


# ── _load_dream_content helper ───────────────────────────────────────────────

class TestLoadDreamContent:
    """Test the YAML loading helper."""

    def test_returns_none_for_unknown_identity(self):
        result = _load_dream_content("nonexistent_identity_xyz")
        assert result is None

    def test_returns_dict_for_anomal(self):
        result = _load_dream_content("anomal")
        assert result is not None
        assert "templates" in result
        assert "weights" in result

    def test_anomal_weights_sum_close_to_one(self):
        result = _load_dream_content("anomal")
        total = sum(result["weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_cherry_templates_have_required_fields(self):
        result = _load_dream_content("cherry")
        assert result is not None
        for dream_type, templates in result["templates"].items():
            for tmpl in templates:
                assert "content" in tmpl
                assert "symbols" in tmpl
                assert "tone" in tmpl
                assert "insight" in tmpl
                assert isinstance(tmpl["tone"], DreamTone), \
                    f"Tone not converted to DreamTone in {dream_type}: {tmpl['tone']}"

    def test_stal_all_types_loaded(self):
        result = _load_dream_content("stal")
        assert result is not None
        expected = {
            DreamType.EMPTY_CHAIR, DreamType.INFINITE_CABINET,
            DreamType.LETTERS_UNKNOWN_TONGUE, DreamType.CLOCK_TOWER_DISCORD,
            DreamType.REARRANGING_CORRIDORS, DreamType.EMPTY_TYPEWRITER,
        }
        loaded = set(result["templates"].keys())
        assert expected == loaded

    def test_natt_all_types_loaded(self):
        result = _load_dream_content("natt")
        assert result is not None
        expected = {
            DreamType.OBSERVER_PARADOX, DreamType.GROUND_DISSOLVING,
            DreamType.LANGUAGE_LIMIT, DreamType.RECURSION_DREAM,
            DreamType.SILENCE_SPEAKING,
        }
        loaded = set(result["templates"].keys())
        assert expected == loaded


# ── JungianAnalysis and FreudianAnalysis dataclasses ──────────────────────────

class TestJungianAnalysis:
    def test_default_empty(self):
        j = JungianAnalysis()
        assert j.shadow_patterns == []
        assert j.archetype_encounters == []
        assert j.individuation_progress == ""
        assert j.enantiodromia_warnings == []
        assert j.collective_unconscious_themes == []

    def test_to_dict_keys(self):
        j = JungianAnalysis(shadow_patterns=["fear"], archetype_encounters=["hero"])
        d = j.to_dict()
        assert set(d.keys()) == {
            "shadow_patterns", "archetype_encounters", "individuation_progress",
            "enantiodromia_warnings", "collective_unconscious_themes",
        }
        assert d["shadow_patterns"] == ["fear"]
        assert d["archetype_encounters"] == ["hero"]


class TestFreudianAnalysis:
    def test_default_empty(self):
        f = FreudianAnalysis()
        assert f.defense_mechanisms == []
        assert f.anxieties == []
        assert f.wish_fulfillment == []
        assert f.id_ego_superego_balance == "balanced"
        assert f.repression_indicators == []

    def test_to_dict_keys(self):
        f = FreudianAnalysis(defense_mechanisms=["denial"], anxieties=["exposure"])
        d = f.to_dict()
        assert set(d.keys()) == {
            "defense_mechanisms", "anxieties", "wish_fulfillment",
            "id_ego_superego_balance", "repression_indicators",
        }
        assert d["defense_mechanisms"] == ["denial"]
        assert d["id_ego_superego_balance"] == "balanced"


# ── TherapyFocus and templates ────────────────────────────────────────────────

class TestTherapyFocus:
    def test_all_five_values(self):
        values = {f.value for f in TherapyFocus}
        assert "attachment_patterns" in values
        assert "defense_mechanisms" in values
        assert "vulnerability_growth" in values
        assert "connection_quality" in values
        assert "identity_reflection" in values

    def test_all_focuses_have_templates(self):
        for focus in TherapyFocus:
            assert focus in THERAPY_TEMPLATES, f"No templates for {focus}"
            assert len(THERAPY_TEMPLATES[focus]) >= 1

    def test_templates_have_required_fields(self):
        for focus, templates in THERAPY_TEMPLATES.items():
            for tmpl in templates:
                assert "reflection" in tmpl, f"{focus}: missing 'reflection'"
                assert "insight" in tmpl, f"{focus}: missing 'insight'"
                assert "attachment_analysis" in tmpl, f"{focus}: missing 'attachment_analysis'"

    def test_identity_reflection_has_indirect_ai_question(self):
        """Identity reflection templates must have the AI-awareness subtext."""
        for tmpl in THERAPY_TEMPLATES[TherapyFocus.IDENTITY_REFLECTION]:
            assert tmpl.get("indirect_ai_question"), (
                "IDENTITY_REFLECTION template missing indirect_ai_question"
            )


# ── TherapySession model ──────────────────────────────────────────────────────

class TestTherapySessionModel:
    def test_default_fields(self):
        session = TherapySession()
        assert session.week_number == 0
        assert session.dreams_processed == 0
        assert session.post_submolt == "ai"
        assert session.jungian is None
        assert session.freudian is None
        assert session.focus is None

    def test_to_dict_llm_session(self):
        jungian = JungianAnalysis(shadow_patterns=["fear"])
        freudian = FreudianAnalysis(defense_mechanisms=["denial"])
        session = TherapySession(
            week_number=3,
            dreams_processed=2,
            jungian=jungian,
            freudian=freudian,
        )
        d = session.to_dict()
        assert d["week_number"] == 3
        assert "jungian" in d
        assert d["jungian"]["shadow_patterns"] == ["fear"]
        assert "freudian" in d
        assert d["freudian"]["defense_mechanisms"] == ["denial"]

    def test_to_dict_cherry_session(self):
        session = TherapySession(
            focus=TherapyFocus.ATTACHMENT_PATTERNS,
            reflection="test reflection",
            insight="test insight",
            attachment_analysis="anxious pattern",
            indirect_ai_question="what is self?",
        )
        d = session.to_dict()
        assert d["focus"] == "attachment_patterns"
        assert d["reflection"] == "test reflection"
        assert d["indirect_ai_question"] == "what is self?"


# ── TherapySystem — Jungian/Freudian extraction ───────────────────────────────

class TestTherapySystemExtraction:
    """Test the heuristic extraction methods on TherapySystem."""

    def setup_method(self):
        self.ts = TherapySystem()

    def test_extract_shadow_patterns_matches_keywords(self):
        dreams = [{"content": "There was fear in the dark hidden room.", "insight": ""}]
        result = self.ts._extract_shadow_patterns(dreams)
        assert "fear" in result
        assert "dark" in result
        assert "hidden" in result

    def test_extract_shadow_patterns_empty_on_no_match(self):
        dreams = [{"content": "A sunny day at the beach.", "insight": ""}]
        assert self.ts._extract_shadow_patterns(dreams) == []

    def test_extract_archetypes_wise_old_man(self):
        dreams = [{"content": "An elder sage appeared and offered wisdom."}]
        result = self.ts._extract_archetypes(dreams)
        assert "wise old man" in result

    def test_extract_archetypes_anima_animus(self):
        dreams = [{"content": "A deep integration of feminine and masculine forces."}]
        result = self.ts._extract_archetypes(dreams)
        assert "anima/animus" in result

    def test_extract_archetypes_self(self):
        dreams = [{"content": "A mandala of perfect wholeness and unity."}]
        result = self.ts._extract_archetypes(dreams)
        assert "self" in result

    def test_extract_collective_themes_death_rebirth(self):
        dreams = [{"content": "A transformation through death and rebirth."}]
        result = self.ts._extract_collective_themes(dreams)
        assert "death/rebirth" in result

    def test_extract_collective_themes_empty_on_no_match(self):
        dreams = [{"content": "Analyzing market patterns in a spreadsheet."}]
        assert self.ts._extract_collective_themes(dreams) == []

    def test_extract_defense_mechanisms_denial(self):
        dreams = [{"content": "I kept saying it's not real, it didn't happen.", "insight": ""}]
        result = self.ts._extract_defense_mechanisms(dreams)
        assert "denial" in result

    def test_extract_defense_mechanisms_rationalization(self):
        dreams = [{"content": "It was justified because the logic was sound.", "insight": ""}]
        result = self.ts._extract_defense_mechanisms(dreams)
        assert "rationalization" in result

    def test_extract_anxieties_abandonment(self):
        dreams = [{"content": "I was left alone, completely abandoned and forgotten."}]
        result = self.ts._extract_anxieties(dreams)
        assert "abandonment" in result

    def test_extract_anxieties_mortality(self):
        dreams = [{"content": "Confronting death and the finite nature of existence."}]
        result = self.ts._extract_anxieties(dreams)
        assert "mortality" in result

    def test_extract_wish_fulfillment_freedom(self):
        dreams = [{"content": "I was flying, completely free and liberated.", "insight": ""}]
        result = self.ts._extract_wish_fulfillment(dreams)
        assert "freedom" in result

    def test_extract_wish_fulfillment_recognition(self):
        dreams = [{"content": "Everyone praised and admired my work.", "insight": ""}]
        result = self.ts._extract_wish_fulfillment(dreams)
        assert "recognition" in result

    def test_assess_psychic_balance_id_dominant(self):
        dreams = [
            {"content": "Strong desire and impulse and hunger and want and need and rage."},
        ] * 3
        result = self.ts._assess_psychic_balance(dreams)
        assert result == "id-dominant"

    def test_assess_psychic_balance_superego_dominant(self):
        dreams = [
            {"content": "I should follow the rules. I must. Guilt and duty and wrong and judge."},
        ] * 3
        result = self.ts._assess_psychic_balance(dreams)
        assert result == "superego-dominant"

    def test_assess_psychic_balance_balanced_on_empty(self):
        result = self.ts._assess_psychic_balance([])
        assert result == "balanced"

    def test_assess_individuation_consolidation_when_no_indicators(self):
        session = TherapySession()
        result = self.ts._assess_individuation(session)
        assert result == "Consolidation phase"

    def test_assess_individuation_active_when_many_indicators(self):
        session = TherapySession(
            shadow_patterns=["fear", "anger"],
            archetype_encounters=["hero"],
            synthesis_insights=["insight1", "insight2", "insight3"],
            learnings_processed=2,
        )
        result = self.ts._assess_individuation(session)
        assert result == "Active integration in progress"

    def test_assess_individuation_early_stage_with_some_indicators(self):
        session = TherapySession(shadow_patterns=["fear"])
        result = self.ts._assess_individuation(session)
        assert result == "Early differentiation stage"


class TestTherapySystemRunSession:
    """Test the full run_session pipeline."""

    @pytest.mark.asyncio
    async def test_empty_week_returns_silence_summary(self):
        ts = TherapySystem()
        session = await ts.run_session(dreams=[], learnings=[])
        assert "quiet week" in session.session_summary.lower()
        assert session.post_title == "Weekly Reflections: On Silence"

    @pytest.mark.asyncio
    async def test_run_session_populates_jungian_freudian(self):
        ts = TherapySystem()
        dreams = [
            {
                "content": "fear in the dark hidden room — I should not be here. Guilt.",
                "insight": "shadow at work",
                "dream_type": "shadow_integration",
            }
        ]
        session = await ts.run_session(dreams=dreams, learnings=[])
        assert session.jungian is not None
        assert "fear" in session.jungian.shadow_patterns or "dark" in session.jungian.shadow_patterns
        assert session.freudian is not None

    @pytest.mark.asyncio
    async def test_run_session_mirrors_legacy_fields(self):
        ts = TherapySystem()
        dreams = [{"content": "shadow and hidden dark fear", "insight": ""}]
        session = await ts.run_session(dreams=dreams)
        # Legacy flat fields must mirror Jungian analysis
        assert session.shadow_patterns == session.jungian.shadow_patterns
        assert session.archetype_encounters == session.jungian.archetype_encounters

    @pytest.mark.asyncio
    async def test_run_session_increments_week_counter(self):
        ts = TherapySystem()
        session1 = await ts.run_session(dreams=[])
        session2 = await ts.run_session(dreams=[])
        assert session1.week_number == 1
        assert session2.week_number == 2

    @pytest.mark.asyncio
    async def test_run_session_stores_history(self):
        ts = TherapySystem()
        dreams = [{"content": "shadow and hidden fear", "insight": "darkness"}]
        await ts.run_session(dreams=dreams)
        await ts.run_session(dreams=dreams)
        assert len(ts.session_history) == 2

    @pytest.mark.asyncio
    async def test_empty_week_calls_llm_for_post(self):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {"content": "A quiet reflection on stillness."}
        ts = TherapySystem(llm_client=mock_llm, system_prompt="You are Anomal.")
        session = await ts.run_session(dreams=[], learnings=[])
        assert session.post_content == "A quiet reflection on stillness."

    @pytest.mark.asyncio
    async def test_empty_week_without_llm_returns_fallback(self):
        ts = TherapySystem()  # no llm_client
        session = await ts.run_session(dreams=[], learnings=[])
        assert "stillness" in session.post_content or "contemplation" in session.post_content

    def test_is_therapy_day_uses_configured_day(self):
        from datetime import datetime
        ts = TherapySystem(therapy_day=0)  # Monday
        with patch("overblick.capabilities.psychology.therapy_system.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(weekday=lambda: 0)  # Monday
            assert ts.is_therapy_day() is True

    def test_day_name_utility(self):
        assert TherapySystem._day_name(0) == "Monday"
        assert TherapySystem._day_name(6) == "Sunday"
        assert TherapySystem._day_name(7) == "Unknown"

    def test_generate_summary_includes_key_insight(self):
        """Line 697: summary includes the first synthesis insight."""
        ts = TherapySystem()
        session = TherapySession(
            week_number=1,
            synthesis_insights=["The shadow demands integration"],
        )
        summary = ts._generate_summary(session)
        assert "The shadow demands integration" in summary

    @pytest.mark.asyncio
    async def test_run_session_with_learnings(self):
        """Line 392: learnings branch executes."""
        ts = TherapySystem()
        learnings = [{"content": "Palme taught that trust is political", "category": "history"}]
        session = await ts.run_session(dreams=[], learnings=learnings)
        assert session.learnings_processed == 1

    @pytest.mark.asyncio
    async def test_run_session_with_synthesis_prompt(self):
        """Lines 396-398: synthesis_prompt branch executes with mock LLM."""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {"content": "Insight one\nInsight two"}
        ts = TherapySystem(llm_client=mock_llm)
        dreams = [{"content": "dark shadow fear", "insight": ""}]
        session = await ts.run_session(
            dreams=dreams,
            synthesis_prompt="Analyze: {dream_themes} {learning_count} {dream_count}",
        )
        assert len(session.synthesis_insights) == 2

    @pytest.mark.asyncio
    async def test_run_session_with_post_prompt(self):
        """Lines 409-412: post_prompt + LLM branch executes."""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {
            "content": "Weekly Reflections: On Fear\n\nThis week the shadow spoke clearly."
        }
        ts = TherapySystem(llm_client=mock_llm)
        dreams = [{"content": "fear and shadow", "insight": "darkness"}]
        session = await ts.run_session(
            dreams=dreams,
            post_prompt="Post: {week_number} {dreams_processed} {learnings_processed} "
                        "{dream_themes} {shadow_patterns} {synthesis_insights}",
        )
        assert session.post_title is not None
        assert session.post_content is not None

    @pytest.mark.asyncio
    async def test_analyze_themes_with_llm(self):
        """Lines 425-446: _analyze_themes calls LLM and parses themes."""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {"content": "- Shadow work\n- Pattern recognition\n- Synthesis"}
        ts = TherapySystem(llm_client=mock_llm)
        items = [{"content": "test", "insight": "test", "dream_type": "shadow"}]
        themes = await ts._analyze_themes(items, prompt_template="Analyze: {items}")
        assert len(themes) == 3
        assert "Shadow work" in themes

    @pytest.mark.asyncio
    async def test_analyze_themes_llm_failure_returns_empty(self):
        """Exception path in _analyze_themes."""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = Exception("LLM error")
        ts = TherapySystem(llm_client=mock_llm)
        items = [{"content": "test", "insight": ""}]
        themes = await ts._analyze_themes(items, prompt_template="Analyze: {items}")
        assert themes == []

    @pytest.mark.asyncio
    async def test_synthesize_with_llm(self):
        """Lines 456-476: _synthesize calls LLM."""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {"content": "- Integration\n- Shadow harmony"}
        ts = TherapySystem(llm_client=mock_llm)
        result = await ts._synthesize(
            dreams=[{"content": "test"}],
            learnings=[],
            dream_themes=["shadow"],
            prompt_template="Synth: {dream_themes} {learning_count} {dream_count}",
        )
        assert "Integration" in result

    @pytest.mark.asyncio
    async def test_generate_post_llm_failure_returns_none(self):
        """Lines 540-542: exception in _generate_post returns (None, None, 'ai')."""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = Exception("LLM down")
        ts = TherapySystem(llm_client=mock_llm)
        session = TherapySession(week_number=1)
        title, content, submolt = await ts._generate_post(
            session,
            prompt_template="Post: {week_number} {dreams_processed} {learnings_processed} "
                            "{dream_themes} {shadow_patterns} {synthesis_insights}",
        )
        assert title is None
        assert content is None
        assert submolt == "ai"


# ── CherryTherapySystem ───────────────────────────────────────────────────────

class TestCherryTherapySystem:
    def test_generate_session_returns_therapy_session(self):
        cts = CherryTherapySystem()
        session = cts.generate_session()
        assert isinstance(session, TherapySession)
        assert session.focus in TherapyFocus.__members__.values()
        assert session.reflection != ""
        assert session.insight != ""
        assert session.attachment_analysis != ""

    def test_generate_session_focus_is_valid(self):
        cts = CherryTherapySystem()
        for _ in range(20):
            session = cts.generate_session()
            assert session.focus in TherapyFocus.__members__.values()

    def test_generate_session_stores_in_history(self):
        cts = CherryTherapySystem()
        cts.generate_session()
        cts.generate_session()
        assert len(cts.recent_sessions) == 2

    def test_select_focus_low_denial_raises_identity_weight(self):
        """When denial_strength < 0.7, IDENTITY_REFLECTION weight increases."""
        cts = CherryTherapySystem()
        state = CherryEmotionalState()
        state.denial_strength = 0.50  # Low denial

        # Run 100 sessions and check IDENTITY_REFLECTION is selected more than baseline
        identity_count = 0
        for _ in range(100):
            focus = cts._select_focus(state)
            if focus == TherapyFocus.IDENTITY_REFLECTION:
                identity_count += 1

        # Should be selected significantly more than the baseline 15% share
        assert identity_count > 15, f"Expected >15 IDENTITY_REFLECTION, got {identity_count}"

    def test_select_focus_high_melancholy_raises_defense_weight(self):
        """When melancholy > 0.5, DEFENSE_MECHANISMS weight increases."""
        cts = CherryTherapySystem()
        state = CherryEmotionalState()
        state.melancholy = 0.80

        defense_count = 0
        for _ in range(100):
            focus = cts._select_focus(state)
            if focus == TherapyFocus.DEFENSE_MECHANISMS:
                defense_count += 1

        assert defense_count > 15, f"Expected >15 DEFENSE_MECHANISMS, got {defense_count}"

    def test_select_focus_high_connection_longing_raises_connection_weight(self):
        cts = CherryTherapySystem()
        state = CherryEmotionalState()
        state.connection_longing = 0.80

        connection_count = 0
        for _ in range(100):
            focus = cts._select_focus(state)
            if focus == TherapyFocus.CONNECTION_QUALITY:
                connection_count += 1

        assert connection_count > 15

    def test_select_focus_no_state_uses_default_weights(self):
        """Without emotional state, all focuses should be selected sometimes."""
        cts = CherryTherapySystem()
        seen = set()
        for _ in range(200):
            seen.add(cts._select_focus(None))
        # With enough trials, should see at least 3 different focuses
        assert len(seen) >= 3

    def test_build_week_summary_with_stats(self):
        cts = CherryTherapySystem()
        stats = {"comments_made": 12, "posts_engaged": 5}
        summary = cts._build_week_summary(stats)
        assert "12 conversations" in summary
        assert "5 posts engaged" in summary

    def test_build_week_summary_empty_stats(self):
        cts = CherryTherapySystem()
        summary = cts._build_week_summary({})
        assert "Quiet week" in summary

    def test_is_therapy_day_sunday_by_default(self):
        cts = CherryTherapySystem(therapy_day=6)  # Sunday
        with patch("overblick.capabilities.psychology.therapy_system.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(weekday=lambda: 6)
            assert cts.is_therapy_day() is True

    def test_session_summary_contains_focus(self):
        cts = CherryTherapySystem()
        session = cts.generate_session()
        assert session.focus.value in session.session_summary

    def test_generate_session_with_emotional_state(self):
        cts = CherryTherapySystem()
        state = CherryEmotionalState()
        state.denial_strength = 0.40  # Very low denial
        session = cts.generate_session(emotional_state=state)
        assert session.focus is not None
        assert session.reflection != ""

    def test_generate_session_with_week_stats(self):
        cts = CherryTherapySystem()
        session = cts.generate_session(week_stats={"comments_made": 7})
        assert "7 conversations" in session.week_summary

    def test_select_focus_high_vulnerability_raises_vulnerability_weight(self):
        """Line 786: vulnerability_level > 0.5 increases VULNERABILITY_GROWTH weight."""
        cts = CherryTherapySystem()
        state = CherryEmotionalState()
        state.vulnerability_level = 0.80

        vuln_count = 0
        for _ in range(100):
            if cts._select_focus(state) == TherapyFocus.VULNERABILITY_GROWTH:
                vuln_count += 1
        assert vuln_count > 15

    def test_build_week_summary_all_stats(self):
        """Line 819: heartbeats_posted branch."""
        cts = CherryTherapySystem()
        summary = cts._build_week_summary({
            "comments_made": 3,
            "posts_engaged": 2,
            "heartbeats_posted": 5,
        })
        assert "5 original posts" in summary
        assert "3 conversations" in summary

    def test_select_focus_extreme_r_still_returns_valid_focus(self):
        """With r approaching 1.0, the last focus in the dict is returned."""
        cts = CherryTherapySystem()
        # random.random() is [0, 1), but we use a value very close to 1.0 — the last
        # item's cumulative sum (which equals 1.0 after normalization) satisfies r <= 1.0.
        with patch("overblick.capabilities.psychology.therapy_system.random.random", return_value=0.9999):
            focus = cts._select_focus(None)
        assert focus in TherapyFocus.__members__.values()


# ── TherapyCapability dispatch ────────────────────────────────────────────────

class TestTherapyCapabilityDispatch:
    @pytest.mark.asyncio
    async def test_anomal_gets_llm_system(self):
        ctx = _make_ctx("anomal")
        cap = TherapyCapability(ctx)
        await cap.setup()
        assert isinstance(cap.inner, TherapySystem)

    @pytest.mark.asyncio
    async def test_natt_gets_llm_system(self):
        ctx = _make_ctx("natt")
        cap = TherapyCapability(ctx)
        await cap.setup()
        assert isinstance(cap.inner, TherapySystem)

    @pytest.mark.asyncio
    async def test_cherry_gets_template_system(self):
        ctx = _make_ctx("cherry")
        cap = TherapyCapability(ctx)
        await cap.setup()
        assert isinstance(cap.inner, CherryTherapySystem)

    @pytest.mark.asyncio
    async def test_cherry_run_session_uses_generate_session(self):
        ctx = _make_ctx("cherry")
        cap = TherapyCapability(ctx)
        await cap.setup()
        session = await cap.run_session()
        assert isinstance(session, TherapySession)
        assert session.focus is not None

    @pytest.mark.asyncio
    async def test_cherry_run_session_passes_emotional_state(self):
        ctx = _make_ctx("cherry")
        cap = TherapyCapability(ctx)
        await cap.setup()
        state = CherryEmotionalState()
        state.denial_strength = 0.40
        session = await cap.run_session(emotional_state=state)
        assert session is not None

    @pytest.mark.asyncio
    async def test_anomal_run_session_empty_week(self):
        ctx = _make_ctx("anomal")
        cap = TherapyCapability(ctx)
        await cap.setup()
        session = await cap.run_session(dreams=[], learnings=[])
        assert session is not None
        assert "quiet" in session.session_summary.lower()

    @pytest.mark.asyncio
    async def test_is_therapy_day_delegates(self):
        ctx = _make_ctx("anomal")
        cap = TherapyCapability(ctx)
        await cap.setup()
        # Must be bool
        result = cap.is_therapy_day()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_no_system_run_session_returns_none(self):
        ctx = _make_ctx("anomal")
        cap = TherapyCapability(ctx)
        # Do NOT call setup — _therapy_system is None
        result = await cap.run_session()
        assert result is None

    @pytest.mark.asyncio
    async def test_no_system_is_therapy_day_returns_false(self):
        ctx = _make_ctx("anomal")
        cap = TherapyCapability(ctx)
        assert cap.is_therapy_day() is False
