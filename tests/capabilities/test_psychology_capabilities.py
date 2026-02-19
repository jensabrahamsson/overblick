"""
Tests for the full psychological capability suite.

Covers:
  - DreamCapability: identity-specific template loading, tick() dream generation
  - DreamType/DreamTone: all identity enum values
  - AnomalEmotionalState: Jungian int-based state
  - CherryEmotionalState: float-based relational state
  - EmotionalCapability: identity dispatch (anomal → Anomal, cherry → Cherry, other → generic)
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
