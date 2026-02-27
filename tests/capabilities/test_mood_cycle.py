"""
Tests for MoodCycleCapability — mood variation for personality authenticity.

Covers:
  - Phase calculation from day number
  - MoodModifiers values per phase
  - Threshold offset calculation
  - to_prompt_context() output safety (never mentions cycle/period/menstrual)
  - Persistence round-trip (save/load cycle start date)
  - Daily randomization stays within bounds
  - Capability lifecycle (setup, tick, get_prompt_context)
"""

import json
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from overblick.capabilities.psychology.mood_cycle import (
    CyclePhase,
    MoodCycleCapability,
    MoodModifiers,
    MoodState,
    _PHASE_PROFILES,
    _THRESHOLD_OFFSETS,
    _randomize_modifiers,
    get_phase_for_day,
)
from overblick.core.capability import CapabilityContext


# -- Fixtures ----------------------------------------------------------------

def _make_ctx(
    identity_name: str = "cherry",
    data_dir: Path = None,
    config: dict = None,
) -> CapabilityContext:
    """Build a minimal CapabilityContext for testing."""
    return CapabilityContext(
        identity_name=identity_name,
        data_dir=data_dir or Path("/tmp/test_mood_cycle"),
        config=config or {},
    )


# -- Phase calculation -------------------------------------------------------

class TestPhaseCalculation:
    """Test get_phase_for_day() returns correct phases."""

    def test_period_days_1_to_5(self):
        for day in range(1, 6):
            assert get_phase_for_day(day) == CyclePhase.PERIOD

    def test_follicular_days_6_to_13(self):
        for day in range(6, 14):
            assert get_phase_for_day(day) == CyclePhase.FOLLICULAR

    def test_ovulation_days_14_to_16(self):
        for day in range(14, 17):
            assert get_phase_for_day(day) == CyclePhase.OVULATION

    def test_luteal_early_days_17_to_21(self):
        for day in range(17, 22):
            assert get_phase_for_day(day) == CyclePhase.LUTEAL_EARLY

    def test_luteal_late_days_22_to_28(self):
        for day in range(22, 29):
            assert get_phase_for_day(day) == CyclePhase.LUTEAL_LATE

    def test_wraps_around_beyond_cycle_length(self):
        # Day 29 of a 28-day cycle = day 1 = PERIOD
        assert get_phase_for_day(29, cycle_length=28) == CyclePhase.PERIOD
        # Day 30 = day 2 = PERIOD
        assert get_phase_for_day(30, cycle_length=28) == CyclePhase.PERIOD
        # Day 42 = day 14 = OVULATION
        assert get_phase_for_day(42, cycle_length=28) == CyclePhase.OVULATION

    def test_day_zero_wraps(self):
        # Day 0 should wrap to day 28 = LUTEAL_LATE
        assert get_phase_for_day(0, cycle_length=28) == CyclePhase.LUTEAL_LATE


# -- Mood modifiers per phase ------------------------------------------------

class TestMoodModifiers:
    """Test that phase profiles have sensible values."""

    def test_all_phases_have_profiles(self):
        for phase in CyclePhase:
            assert phase in _PHASE_PROFILES

    def test_ovulation_is_peak_energy(self):
        ov = _PHASE_PROFILES[CyclePhase.OVULATION]
        for phase in CyclePhase:
            if phase != CyclePhase.OVULATION:
                assert ov.energy >= _PHASE_PROFILES[phase].energy

    def test_luteal_late_is_lowest_sociability(self):
        ll = _PHASE_PROFILES[CyclePhase.LUTEAL_LATE]
        for phase in CyclePhase:
            if phase != CyclePhase.LUTEAL_LATE:
                assert ll.sociability <= _PHASE_PROFILES[phase].sociability

    def test_all_values_in_range(self):
        for phase, m in _PHASE_PROFILES.items():
            for field_name in [
                "energy", "optimism", "confidence", "sociability",
                "irritability", "flirtiness", "comfort_seeking",
                "introspection", "sensitivity", "emotional_intensity",
            ]:
                val = getattr(m, field_name)
                assert 0.0 <= val <= 1.0, f"{phase.value}.{field_name} = {val}"


# -- Threshold offsets -------------------------------------------------------

class TestThresholdOffsets:
    """Test engagement threshold offsets per phase."""

    def test_all_phases_have_offsets(self):
        for phase in CyclePhase:
            assert phase in _THRESHOLD_OFFSETS

    def test_ovulation_most_social(self):
        assert _THRESHOLD_OFFSETS[CyclePhase.OVULATION] == -10

    def test_luteal_late_least_social(self):
        assert _THRESHOLD_OFFSETS[CyclePhase.LUTEAL_LATE] == 10

    def test_follicular_slightly_social(self):
        assert _THRESHOLD_OFFSETS[CyclePhase.FOLLICULAR] == -5

    def test_period_less_social(self):
        assert _THRESHOLD_OFFSETS[CyclePhase.PERIOD] == 8

    def test_luteal_early_slightly_less_social(self):
        assert _THRESHOLD_OFFSETS[CyclePhase.LUTEAL_EARLY] == 5


# -- Prompt safety -----------------------------------------------------------

class TestPromptSafety:
    """Test that to_prompt_context() never reveals cycle details."""

    _FORBIDDEN_WORDS = [
        "period", "menstrual", "cycle", "hormone", "ovulation",
        "luteal", "follicular", "pms", "cramp",
    ]

    def _check_no_forbidden(self, text: str) -> None:
        lower = text.lower()
        for word in self._FORBIDDEN_WORDS:
            assert word not in lower, f"Forbidden word '{word}' found in prompt context"

    def test_all_phases_safe(self):
        for phase in CyclePhase:
            modifiers = _PHASE_PROFILES[phase]
            state = MoodState(
                phase=phase, day_in_cycle=1, modifiers=modifiers,
            )
            context = state.to_prompt_context()
            self._check_no_forbidden(context)

    def test_with_subtle_hints_safe(self):
        state = MoodState(
            phase=CyclePhase.PERIOD,
            day_in_cycle=3,
            modifiers=_PHASE_PROFILES[CyclePhase.PERIOD],
            subtle_hints=["not my best day tbh", "low energy vibes today"],
        )
        context = state.to_prompt_context()
        self._check_no_forbidden(context)

    def test_context_is_non_empty_for_all_phases(self):
        for phase in CyclePhase:
            state = MoodState(
                phase=phase, day_in_cycle=14, modifiers=_PHASE_PROFILES[phase],
            )
            context = state.to_prompt_context()
            assert len(context) > 0


# -- Randomization -----------------------------------------------------------

class TestRandomization:
    """Test daily randomization stays within bounds."""

    def test_randomized_values_in_range(self):
        base = _PHASE_PROFILES[CyclePhase.OVULATION]
        for _ in range(100):
            randomized = _randomize_modifiers(base, variance=0.10)
            for field_name in [
                "energy", "optimism", "confidence", "sociability",
                "irritability", "flirtiness", "comfort_seeking",
                "introspection", "sensitivity", "emotional_intensity",
            ]:
                val = getattr(randomized, field_name)
                assert 0.0 <= val <= 1.0, f"{field_name} = {val} out of range"

    def test_randomization_changes_values(self):
        """Over many runs, at least some values should differ from base."""
        base = _PHASE_PROFILES[CyclePhase.FOLLICULAR]
        different_count = 0
        for _ in range(50):
            randomized = _randomize_modifiers(base, variance=0.10)
            if randomized.energy != base.energy:
                different_count += 1
        # With 10% variance, nearly all should differ
        assert different_count > 20


# -- Persistence -------------------------------------------------------------

class TestPersistence:
    """Test cycle start date persistence round-trip."""

    def test_persist_and_load(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = date(2026, 1, 15)
        cap._persist_state()

        # Create new instance and load
        cap2 = MoodCycleCapability(ctx)
        cap2._load_state()
        assert cap2._cycle_start == date(2026, 1, 15)

    def test_load_missing_file(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path)
        cap = MoodCycleCapability(ctx)
        cap._load_state()
        assert cap._cycle_start is None

    def test_load_corrupted_file(self, tmp_path):
        state_file = tmp_path / "mood_cycle_state.json"
        state_file.write_text("not json{{{")
        ctx = _make_ctx(data_dir=tmp_path)
        cap = MoodCycleCapability(ctx)
        cap._load_state()
        assert cap._cycle_start is None

    def test_persist_creates_directory(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        ctx = _make_ctx(data_dir=nested)
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = date(2026, 2, 1)
        cap._persist_state()
        assert (nested / "mood_cycle_state.json").exists()


# -- Capability lifecycle ----------------------------------------------------

class TestCapabilityLifecycle:
    """Test MoodCycleCapability setup and tick."""

    @pytest.mark.asyncio
    async def test_setup_creates_cycle_start(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        assert cap.cycle_start is not None
        assert (tmp_path / "mood_cycle_state.json").exists()

    @pytest.mark.asyncio
    async def test_setup_restores_persisted_state(self, tmp_path):
        state_file = tmp_path / "mood_cycle_state.json"
        state_file.write_text(json.dumps({
            "cycle_start": "2026-01-10",
            "cycle_length": 28,
        }))
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        assert cap.cycle_start == date(2026, 1, 10)

    @pytest.mark.asyncio
    async def test_tick_computes_mood_state(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        await cap.tick()
        assert cap.current_state is not None
        assert cap.current_state.phase in CyclePhase

    @pytest.mark.asyncio
    async def test_tick_only_once_per_day(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        await cap.tick()
        first_state = cap.current_state

        # Second tick same day should not recompute
        await cap.tick()
        assert cap.current_state is first_state  # Same object reference

    @pytest.mark.asyncio
    async def test_get_prompt_context_before_tick_is_empty(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path)
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_get_prompt_context_after_tick_is_nonempty(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        await cap.tick()
        context = cap.get_prompt_context()
        assert len(context) > 0
        assert "CURRENT MOOD" in context

    @pytest.mark.asyncio
    async def test_get_threshold_offset_returns_int(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        await cap.tick()
        offset = cap.get_threshold_offset()
        assert isinstance(offset, int)

    @pytest.mark.asyncio
    async def test_get_threshold_offset_before_tick_is_zero(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path)
        cap = MoodCycleCapability(ctx)
        await cap.setup()
        assert cap.get_threshold_offset() == 0

    @pytest.mark.asyncio
    async def test_subtle_hints_from_config(self, tmp_path):
        config = {
            "cycle_length_days": 28,
            "subtle_hints": {
                "period": ["not my best day"],
                "ovulation": ["feeling great"],
            },
        }
        ctx = _make_ctx(data_dir=tmp_path, config=config)
        cap = MoodCycleCapability(ctx)
        # Force cycle to period phase (day 1)
        cap._cycle_start = date.today()
        await cap.setup()
        await cap.tick()
        assert cap.current_state.phase == CyclePhase.PERIOD
        assert cap.current_state.subtle_hints == ["not my best day"]


# -- Day in cycle calculation ------------------------------------------------

class TestDayInCycle:
    """Test internal day calculation."""

    @pytest.mark.asyncio
    async def test_day_calculation(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = date.today() - timedelta(days=13)
        # Day should be 14 (0-indexed delta + 1)
        assert cap._get_day_in_cycle() == 14

    @pytest.mark.asyncio
    async def test_day_wraps_around(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = date.today() - timedelta(days=28)
        # 28 days later wraps to day 1
        assert cap._get_day_in_cycle() == 1

    @pytest.mark.asyncio
    async def test_day_wraps_multiple_cycles(self, tmp_path):
        ctx = _make_ctx(data_dir=tmp_path, config={"cycle_length_days": 28})
        cap = MoodCycleCapability(ctx)
        cap._cycle_start = date.today() - timedelta(days=56)  # 2 full cycles
        assert cap._get_day_in_cycle() == 1
