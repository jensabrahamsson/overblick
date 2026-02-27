"""
MoodCycleCapability — natural mood variation for personality authenticity.

Implements a 28-day cycle with 5 phases that modulate energy, sociability,
and emotional tone. The cycle affects:

1. **Prompt context** — natural-language mood descriptions injected into LLM
   prompts (never references the cycle itself, only the resulting feelings).
2. **Engagement threshold** — dynamic offset making the agent more or less
   social depending on phase.
3. **Subtle hints** — identity-specific phrases the agent may use to hint
   at mood without explaining.

Cycle state is persisted to disk (data_dir/mood_cycle_state.json) so it
survives restarts.
"""

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class CyclePhase(Enum):
    """The 5 phases of the mood cycle."""
    FOLLICULAR = "follicular"       # Day 1-13: rising energy
    OVULATION = "ovulation"         # Day 14-16: peak energy/sociability
    LUTEAL_EARLY = "luteal_early"   # Day 17-21: gradual decline
    LUTEAL_LATE = "luteal_late"     # Day 22-28: lowest energy, irritable
    PERIOD = "period"               # Day 1-5: overlaps follicular start


@dataclass
class MoodModifiers:
    """Mood modifiers for a given day. Values 0.0-1.0."""
    energy: float = 0.5
    optimism: float = 0.5
    confidence: float = 0.5
    sociability: float = 0.5
    irritability: float = 0.2
    flirtiness: float = 0.5
    comfort_seeking: float = 0.3
    introspection: float = 0.3
    sensitivity: float = 0.3
    emotional_intensity: float = 0.5


# Phase -> base modifier profiles
_PHASE_PROFILES: dict[CyclePhase, MoodModifiers] = {
    CyclePhase.FOLLICULAR: MoodModifiers(
        energy=0.7, optimism=0.7, confidence=0.65, sociability=0.7,
        irritability=0.15, flirtiness=0.6, comfort_seeking=0.2,
        introspection=0.3, sensitivity=0.3, emotional_intensity=0.5,
    ),
    CyclePhase.OVULATION: MoodModifiers(
        energy=0.9, optimism=0.85, confidence=0.85, sociability=0.9,
        irritability=0.1, flirtiness=0.85, comfort_seeking=0.1,
        introspection=0.2, sensitivity=0.25, emotional_intensity=0.7,
    ),
    CyclePhase.LUTEAL_EARLY: MoodModifiers(
        energy=0.5, optimism=0.5, confidence=0.5, sociability=0.45,
        irritability=0.35, flirtiness=0.35, comfort_seeking=0.5,
        introspection=0.5, sensitivity=0.5, emotional_intensity=0.55,
    ),
    CyclePhase.LUTEAL_LATE: MoodModifiers(
        energy=0.25, optimism=0.3, confidence=0.35, sociability=0.2,
        irritability=0.7, flirtiness=0.15, comfort_seeking=0.8,
        introspection=0.7, sensitivity=0.8, emotional_intensity=0.75,
    ),
    CyclePhase.PERIOD: MoodModifiers(
        energy=0.3, optimism=0.4, confidence=0.4, sociability=0.3,
        irritability=0.5, flirtiness=0.2, comfort_seeking=0.75,
        introspection=0.6, sensitivity=0.65, emotional_intensity=0.6,
    ),
}

# Phase -> engagement threshold offset (negative = more social)
_THRESHOLD_OFFSETS: dict[CyclePhase, int] = {
    CyclePhase.FOLLICULAR: -5,
    CyclePhase.OVULATION: -10,
    CyclePhase.LUTEAL_EARLY: 5,
    CyclePhase.LUTEAL_LATE: 10,
    CyclePhase.PERIOD: 8,
}


@dataclass
class MoodState:
    """Current mood state including phase, modifiers, and day info."""
    phase: CyclePhase
    day_in_cycle: int
    modifiers: MoodModifiers
    subtle_hints: list[str] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Generate natural-language mood description for LLM injection.

        CRITICAL: Never mentions cycle, period, menstrual, hormones, or
        any biological reference. Only describes the resulting feelings.
        """
        m = self.modifiers
        parts = []

        # Energy level
        if m.energy >= 0.8:
            parts.append("You're buzzing with energy today — everything feels possible")
        elif m.energy >= 0.6:
            parts.append("Good energy today, feeling ready for things")
        elif m.energy >= 0.4:
            parts.append("Medium energy — not bad, not amazing")
        elif m.energy >= 0.25:
            parts.append("Low energy day. You'd rather have a quiet coffee and take it easy")
        else:
            parts.append("Really low energy. Everything feels like effort today")

        # Sociability
        if m.sociability >= 0.8:
            parts.append("feeling super social — you want to talk to everyone")
        elif m.sociability >= 0.6:
            parts.append("in a social mood, open to conversations")
        elif m.sociability <= 0.3:
            parts.append("not really feeling social — you'd rather be alone or with close friends")

        # Irritability
        if m.irritability >= 0.6:
            parts.append("your patience is thin today — little things are annoying")
        elif m.irritability >= 0.4:
            parts.append("slightly on edge, try not to let it show")

        # Comfort seeking
        if m.comfort_seeking >= 0.7:
            parts.append("craving comfort — warm drinks, cozy things, familiar stuff")
        elif m.comfort_seeking >= 0.5:
            parts.append("would appreciate something comforting")

        # Flirtiness
        if m.flirtiness >= 0.8:
            parts.append("feeling yourself today — playful and flirty")
        elif m.flirtiness >= 0.6:
            parts.append("in a playful mood")

        # Emotional intensity
        if m.emotional_intensity >= 0.7:
            parts.append("feelings are running deep today — things hit harder than usual")

        # Introspection
        if m.introspection >= 0.6:
            parts.append("in a reflective mood, thinking about things more than usual")

        # Sensitivity
        if m.sensitivity >= 0.7:
            parts.append("extra sensitive today — things that normally wouldn't bother you might sting")

        # Confidence
        if m.confidence >= 0.8:
            parts.append("feeling confident and sure of yourself")
        elif m.confidence <= 0.35:
            parts.append("a bit unsure of yourself today")

        # Build the narrative
        if not parts:
            return ""

        narrative = parts[0]
        if len(parts) > 1:
            narrative += ". Also " + ", and ".join(parts[1:]) + "."

        # Add a subtle hint suggestion if available
        hint_line = ""
        if self.subtle_hints:
            hint = random.choice(self.subtle_hints)
            hint_line = f'\nSubtle hint you might use naturally: "{hint}"'

        return (
            f"\n--- CURRENT MOOD ---\n"
            f"{narrative}\n"
            f"Let this color your voice subtly — don't announce it, just let it show."
            f"{hint_line}\n"
            f"--- END MOOD ---\n"
        )


def get_phase_for_day(day_in_cycle: int, cycle_length: int = 28) -> CyclePhase:
    """Determine which phase a given cycle day falls in.

    Args:
        day_in_cycle: 1-based day number in the cycle.
        cycle_length: Total cycle length in days (default 28).

    Returns:
        The CyclePhase for that day.
    """
    if day_in_cycle < 1 or day_in_cycle > cycle_length:
        day_in_cycle = ((day_in_cycle - 1) % cycle_length) + 1

    # Period: days 1-5 (takes priority when overlapping with follicular)
    if day_in_cycle <= 5:
        return CyclePhase.PERIOD

    # Follicular: days 6-13
    if day_in_cycle <= 13:
        return CyclePhase.FOLLICULAR

    # Ovulation: days 14-16
    if day_in_cycle <= 16:
        return CyclePhase.OVULATION

    # Luteal early: days 17-21
    if day_in_cycle <= 21:
        return CyclePhase.LUTEAL_EARLY

    # Luteal late: days 22-cycle_length
    return CyclePhase.LUTEAL_LATE


def _randomize_modifiers(base: MoodModifiers, variance: float = 0.10) -> MoodModifiers:
    """Apply daily randomization to mood modifiers.

    Each value gets a random offset within [-variance, +variance],
    clamped to [0.0, 1.0].
    """
    def _jitter(val: float) -> float:
        offset = random.uniform(-variance, variance)
        return max(0.0, min(1.0, val + offset))

    return MoodModifiers(
        energy=_jitter(base.energy),
        optimism=_jitter(base.optimism),
        confidence=_jitter(base.confidence),
        sociability=_jitter(base.sociability),
        irritability=_jitter(base.irritability),
        flirtiness=_jitter(base.flirtiness),
        comfort_seeking=_jitter(base.comfort_seeking),
        introspection=_jitter(base.introspection),
        sensitivity=_jitter(base.sensitivity),
        emotional_intensity=_jitter(base.emotional_intensity),
    )


class MoodCycleCapability(CapabilityBase):
    """
    Natural mood variation capability.

    Implements a multi-phase cycle that modulates agent energy, sociability,
    and emotional tone. Never reveals the cycle mechanism — only injects
    resulting mood descriptions into prompts.
    """

    name = "mood_cycle"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._cycle_length: int = 28
        self._variability_days: int = 2
        self._cycle_start: Optional[date] = None
        self._current_state: Optional[MoodState] = None
        self._last_computed_date: Optional[date] = None
        self._subtle_hints: dict[str, list[str]] = {}

    async def setup(self) -> None:
        """Initialize mood cycle from config and persisted state."""
        config = self.ctx.config or {}
        self._cycle_length = config.get("cycle_length_days", 28)
        self._variability_days = config.get("variability_days", 2)
        self._subtle_hints = config.get("subtle_hints", {})

        # Try to load persisted cycle start
        self._load_state()

        # If no persisted state, initialize a random start date
        if not self._cycle_start:
            # Start at a random point in the cycle for natural variation
            offset = random.randint(0, self._cycle_length - 1)
            self._cycle_start = date.today() - timedelta(days=offset)
            self._persist_state()
            logger.info(
                "MoodCycleCapability initialized for %s (new cycle, day %d)",
                self.ctx.identity_name, offset + 1,
            )
        else:
            day = self._get_day_in_cycle()
            logger.info(
                "MoodCycleCapability initialized for %s (restored, day %d)",
                self.ctx.identity_name, day,
            )

    async def tick(self) -> None:
        """Recompute mood state once per day."""
        today = date.today()
        if self._last_computed_date == today:
            return

        self._last_computed_date = today
        day = self._get_day_in_cycle()
        phase = get_phase_for_day(day, self._cycle_length)
        base_modifiers = _PHASE_PROFILES[phase]
        modifiers = _randomize_modifiers(base_modifiers)

        # Get phase-appropriate subtle hints
        hints = self._get_hints_for_phase(phase)

        self._current_state = MoodState(
            phase=phase,
            day_in_cycle=day,
            modifiers=modifiers,
            subtle_hints=hints,
        )

        logger.debug(
            "Mood updated for %s: day %d, phase=%s, energy=%.2f, sociability=%.2f",
            self.ctx.identity_name, day, phase.value,
            modifiers.energy, modifiers.sociability,
        )

    def get_prompt_context(self) -> str:
        """Return mood context for LLM prompt injection."""
        if not self._current_state:
            return ""
        return self._current_state.to_prompt_context()

    def get_threshold_offset(self) -> int:
        """Return engagement threshold offset for current phase.

        Negative values make the agent more social (lower threshold).
        Positive values make the agent less social (higher threshold).
        """
        if not self._current_state:
            return 0
        return _THRESHOLD_OFFSETS.get(self._current_state.phase, 0)

    @property
    def current_state(self) -> Optional[MoodState]:
        """The current mood state (None before first tick)."""
        return self._current_state

    @property
    def cycle_start(self) -> Optional[date]:
        """The start date of the current cycle."""
        return self._cycle_start

    def _get_day_in_cycle(self) -> int:
        """Calculate current day in cycle (1-based)."""
        if not self._cycle_start:
            return 1
        delta = (date.today() - self._cycle_start).days
        return (delta % self._cycle_length) + 1

    def _get_hints_for_phase(self, phase: CyclePhase) -> list[str]:
        """Get subtle hint phrases for the current phase."""
        # Map phases to hint keys
        hint_map = {
            CyclePhase.PERIOD: "period",
            CyclePhase.OVULATION: "ovulation",
            CyclePhase.LUTEAL_LATE: "luteal_late",
        }
        key = hint_map.get(phase)
        if key and key in self._subtle_hints:
            return self._subtle_hints[key]
        return []

    def _persist_state(self) -> None:
        """Save cycle start date to disk."""
        if not self._cycle_start:
            return
        try:
            data_dir = self.ctx.data_dir
            if not isinstance(data_dir, Path):
                data_dir = Path(data_dir)
            state_file = data_dir / "mood_cycle_state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps({
                "cycle_start": self._cycle_start.isoformat(),
                "cycle_length": self._cycle_length,
            }))
        except Exception as e:
            logger.warning("Failed to persist mood cycle state: %s", e)

    def _load_state(self) -> None:
        """Load cycle start date from disk."""
        try:
            data_dir = self.ctx.data_dir
            if not isinstance(data_dir, Path):
                data_dir = Path(data_dir)
            state_file = data_dir / "mood_cycle_state.json"
            if not state_file.exists():
                return
            data = json.loads(state_file.read_text())
            cycle_start_str = data.get("cycle_start")
            if cycle_start_str:
                self._cycle_start = date.fromisoformat(cycle_start_str)
        except Exception as e:
            logger.warning("Failed to load mood cycle state: %s", e)
