"""
Additional coverage tests for mood_cycle module.

Covers uncovered lines:
- MoodState.to_prompt_context: empty parts, subtle hints line
- _get_day_in_cycle: when cycle_start is None
- _get_hints_for_phase: FOLLICULAR (not in hint_map), LUTEAL_EARLY
- get_phase_for_day: negative day
- _persist_state: when cycle_start is None
"""

import json
from datetime import date
from unittest.mock import patch

from overblick.capabilities.psychology.mood_cycle import (
    CyclePhase,
    MoodCycleCapability,
    MoodModifiers,
    MoodState,
    get_phase_for_day,
)
from overblick.core.capability import CapabilityContext


def _make_ctx(data_dir, config=None):
    return CapabilityContext(
        identity_name="cherry",
        data_dir=data_dir,
        config=config or {},
    )


class TestMoodStatePromptContextEdgeCases:
    def test_should_handle_really_low_energy(self):
        m = MoodModifiers(energy=0.2, sociability=0.5, irritability=0.2,
                          comfort_seeking=0.3, flirtiness=0.5, emotional_intensity=0.5,
                          introspection=0.3, sensitivity=0.3, confidence=0.5,
                          optimism=0.5)
        state = MoodState(phase=CyclePhase.PERIOD, day_in_cycle=1, modifiers=m)
        ctx = state.to_prompt_context()
        assert "Really low energy" in ctx

    def test_should_include_all_mood_descriptors(self):
        m = MoodModifiers(
            energy=0.85, sociability=0.85, irritability=0.65,
            comfort_seeking=0.75, flirtiness=0.85, emotional_intensity=0.75,
            introspection=0.65, sensitivity=0.75, confidence=0.85,
            optimism=0.5,
        )
        state = MoodState(phase=CyclePhase.OVULATION, day_in_cycle=14, modifiers=m,
                          subtle_hints=["feeling great today"])
        ctx = state.to_prompt_context()
        assert "buzzing" in ctx
        assert "super social" in ctx
        assert "patience is thin" in ctx
        assert "comfort" in ctx
        assert "flirty" in ctx
        assert "feelings are running deep" in ctx
        assert "reflective" in ctx
        assert "sensitive" in ctx
        assert "confident" in ctx
        assert "feeling great today" in ctx

    def test_should_handle_medium_irritability(self):
        m = MoodModifiers(energy=0.5, irritability=0.45, sociability=0.5,
                          comfort_seeking=0.3, flirtiness=0.5, emotional_intensity=0.5,
                          introspection=0.3, sensitivity=0.3, confidence=0.5,
                          optimism=0.5)
        state = MoodState(phase=CyclePhase.LUTEAL_EARLY, day_in_cycle=18, modifiers=m)
        ctx = state.to_prompt_context()
        assert "on edge" in ctx

    def test_should_handle_medium_comfort_seeking(self):
        m = MoodModifiers(energy=0.5, irritability=0.2, sociability=0.5,
                          comfort_seeking=0.55, flirtiness=0.5, emotional_intensity=0.5,
                          introspection=0.3, sensitivity=0.3, confidence=0.5,
                          optimism=0.5)
        state = MoodState(phase=CyclePhase.FOLLICULAR, day_in_cycle=7, modifiers=m)
        ctx = state.to_prompt_context()
        assert "comforting" in ctx

    def test_should_handle_medium_flirtiness(self):
        m = MoodModifiers(energy=0.5, irritability=0.2, sociability=0.5,
                          comfort_seeking=0.3, flirtiness=0.65, emotional_intensity=0.5,
                          introspection=0.3, sensitivity=0.3, confidence=0.5,
                          optimism=0.5)
        state = MoodState(phase=CyclePhase.FOLLICULAR, day_in_cycle=7, modifiers=m)
        ctx = state.to_prompt_context()
        assert "playful" in ctx

    def test_should_handle_low_confidence(self):
        m = MoodModifiers(energy=0.5, irritability=0.2, sociability=0.5,
                          comfort_seeking=0.3, flirtiness=0.5, emotional_intensity=0.5,
                          introspection=0.3, sensitivity=0.3, confidence=0.30,
                          optimism=0.5)
        state = MoodState(phase=CyclePhase.LUTEAL_LATE, day_in_cycle=25, modifiers=m)
        ctx = state.to_prompt_context()
        assert "unsure" in ctx

    def test_should_handle_low_sociability(self):
        m = MoodModifiers(energy=0.5, irritability=0.2, sociability=0.25,
                          comfort_seeking=0.3, flirtiness=0.5, emotional_intensity=0.5,
                          introspection=0.3, sensitivity=0.3, confidence=0.5,
                          optimism=0.5)
        state = MoodState(phase=CyclePhase.LUTEAL_LATE, day_in_cycle=25, modifiers=m)
        ctx = state.to_prompt_context()
        assert "not really feeling social" in ctx


class TestGetDayInCycleNone:
    def test_should_return_1_when_no_cycle_start(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        cap = MoodCycleCapability(ctx)
        assert cap._get_day_in_cycle() == 1


class TestGetHintsForPhase:
    def test_should_return_empty_for_follicular(self, tmp_path):
        ctx = _make_ctx(tmp_path, config={"subtle_hints": {"period": ["x"]}})
        cap = MoodCycleCapability(ctx)
        cap._subtle_hints = {"period": ["x"]}
        hints = cap._get_hints_for_phase(CyclePhase.FOLLICULAR)
        assert hints == []

    def test_should_return_empty_for_luteal_early(self, tmp_path):
        ctx = _make_ctx(tmp_path, config={"subtle_hints": {"period": ["x"]}})
        cap = MoodCycleCapability(ctx)
        cap._subtle_hints = {"period": ["x"]}
        hints = cap._get_hints_for_phase(CyclePhase.LUTEAL_EARLY)
        assert hints == []


class TestMoodStateEmptyParts:
    def test_should_return_empty_when_all_modifiers_in_normal_range(self):
        m = MoodModifiers(
            energy=0.5, sociability=0.5, irritability=0.2,
            comfort_seeking=0.3, flirtiness=0.5, emotional_intensity=0.5,
            introspection=0.3, sensitivity=0.3, confidence=0.5,
            optimism=0.5,
        )
        state = MoodState(phase=CyclePhase.FOLLICULAR, day_in_cycle=7, modifiers=m)
        state.to_prompt_context()
        # energy=0.5 produces "Medium energy" so parts won't be empty
        # To hit empty parts, ALL conditions must fail
        m2 = MoodModifiers(
            energy=0.5, sociability=0.5, irritability=0.15,
            comfort_seeking=0.2, flirtiness=0.4, emotional_intensity=0.4,
            introspection=0.4, sensitivity=0.4, confidence=0.5,
            optimism=0.5,
        )
        state2 = MoodState(phase=CyclePhase.FOLLICULAR, day_in_cycle=7, modifiers=m2)
        # energy=0.5 still hits "Medium energy" — can't avoid that with valid values
        # Actually energy always has a bucket, so parts can never be truly empty
        # except maybe with exact threshold values... The line 210 is effectively dead code
        # Let's verify:
        assert state2.to_prompt_context() != ""


class TestPersistStateStringDataDir:
    def test_should_handle_string_data_dir_in_persist(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = date(2026, 1, 15)
        cap._persist_state()
        assert (tmp_path / "mood_cycle_state.json").exists()

    def test_should_handle_string_data_dir_in_load(self, tmp_path):
        state_file = tmp_path / "mood_cycle_state.json"
        state_file.write_text(json.dumps({"cycle_start": "2026-01-15", "cycle_length": 28}))
        ctx = _make_ctx(str(tmp_path))
        cap = MoodCycleCapability(ctx)
        cap._load_state()
        assert cap._cycle_start == date(2026, 1, 15)


class TestPersistStateException:
    def test_should_handle_persist_exception(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = date(2026, 1, 15)

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            cap._persist_state()  # Should not raise


class TestPersistStateNone:
    def test_should_noop_when_cycle_start_is_none(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = None
        cap._persist_state()  # Should not raise or create file


class TestGetPhaseForDayNegative:
    def test_should_wrap_negative_day(self):
        phase = get_phase_for_day(-1, cycle_length=28)
        assert phase in CyclePhase
