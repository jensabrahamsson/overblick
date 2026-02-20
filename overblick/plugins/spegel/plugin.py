"""
SpegelPlugin — Inter-Agent Psychological Profiling.

Each agent writes a psychological profile of another agent based on
their personality definition and psychological framework. The profiled
agent then reads the profile and responds with a reflection.

Self-awareness through others' eyes.

Architecture: Scheduled (configurable interval). For each identity pair:
load observer -> load target -> generate profile -> target reflects ->
store and display on dashboard.

Security: All LLM calls go through SafeLLMPipeline.
"""

import json
import logging
import time
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.identities import list_identities

from .models import Profile, Reflection, SpegelPair

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_HOURS = 168  # Weekly
_MAX_PAIRS_STORED = 100


class SpegelPlugin(PluginBase):
    """
    Inter-agent psychological profiling plugin.

    Lifecycle:
        setup()    — Load config, discover identity pairs, restore state
        tick()     — Check schedule, generate profiles and reflections
        teardown() — Persist state
    """

    name = "spegel"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._interval_hours: int = _DEFAULT_INTERVAL_HOURS
        self._configured_pairs: list[tuple[str, str]] = []
        self._pairs: list[SpegelPair] = []
        self._last_run: float = 0.0
        self._state_file: Optional[Any] = None
        self._tick_count: int = 0

    async def setup(self) -> None:
        """Initialize plugin — load config, build identity pairs."""
        identity = self.ctx.identity
        logger.info("Setting up SpegelPlugin for identity: %s", identity.name)

        raw_config = identity.raw_config
        spegel_config = raw_config.get("spegel", {})

        self._interval_hours = spegel_config.get(
            "interval_hours", _DEFAULT_INTERVAL_HOURS
        )

        # Build identity pairs
        configured = spegel_config.get("pairs", [])
        if configured:
            self._configured_pairs = [
                (p["observer"], p["target"]) for p in configured
            ]
        else:
            # Default: generate interesting cross-pairs from all identities
            identities = spegel_config.get("identities", list_identities())
            # Filter out supervisor
            identities = [i for i in identities if i != "supervisor"]
            self._configured_pairs = self._build_default_pairs(identities)

        # State persistence
        self._state_file = self.ctx.data_dir / "spegel_state.json"
        self._load_state()

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "pairs": len(self._configured_pairs),
                "interval_hours": self._interval_hours,
            },
        )
        logger.info(
            "SpegelPlugin setup complete (%d pairs, %dh interval)",
            len(self._configured_pairs),
            self._interval_hours,
        )

    async def tick(self) -> None:
        """Check if it's time to generate new profiles."""
        self._tick_count += 1

        if not self._is_run_time():
            return

        self._last_run = time.time()
        logger.info("SpegelPlugin: starting profiling round")

        try:
            for observer_name, target_name in self._configured_pairs:
                pair = await self._generate_pair(observer_name, target_name)
                if pair:
                    self._pairs.append(pair)

            # Trim old pairs
            if len(self._pairs) > _MAX_PAIRS_STORED:
                self._pairs = self._pairs[-_MAX_PAIRS_STORED:]

            self._save_state()

            if self.ctx.event_bus:
                await self.ctx.event_bus.emit(
                    "spegel.round_complete",
                    {"pairs_generated": len(self._configured_pairs)},
                )

            self.ctx.audit_log.log(
                action="spegel_round_complete",
                details={"pairs_generated": len(self._configured_pairs)},
            )

        except Exception as e:
            logger.error("SpegelPlugin pipeline error: %s", e, exc_info=True)
            self._save_state()

    async def _generate_pair(
        self, observer_name: str, target_name: str
    ) -> Optional[SpegelPair]:
        """Generate a complete profile + reflection pair."""
        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            return None

        try:
            observer = self.ctx.load_identity(observer_name)
            target = self.ctx.load_identity(target_name)
        except FileNotFoundError as e:
            logger.warning("SpegelPlugin: identity not found: %s", e)
            return None

        # Step 1: Observer profiles the target
        observer_prompt = self.ctx.build_system_prompt(
            observer, platform="Spegel Analysis"
        )

        # Gather target's personality summary for the observer
        target_summary = self._build_target_summary(target)

        profile_messages = [
            {"role": "system", "content": observer_prompt},
            {
                "role": "user",
                "content": (
                    f"You are asked to write a psychological profile of "
                    f"{target.display_name}. Based on what you know about them:\n\n"
                    f"{target_summary}\n\n"
                    "Write a 200-400 word psychological profile through your "
                    "own analytical lens. What patterns do you see? What drives "
                    "them? What are their blind spots? Be insightful and honest."
                ),
            },
        ]

        profile_result = await pipeline.chat(
            messages=profile_messages,
            temperature=observer.llm.temperature,
            max_tokens=1000,
            audit_action="spegel_profile",
            audit_details={
                "observer": observer_name,
                "target": target_name,
            },
        )

        if profile_result.blocked or not profile_result.content:
            logger.warning(
                "SpegelPlugin: profile %s->%s blocked", observer_name, target_name
            )
            return None

        profile = Profile(
            observer_name=observer_name,
            observer_display_name=observer.display_name,
            target_name=target_name,
            target_display_name=target.display_name,
            profile_text=profile_result.content,
        )

        # Step 2: Target reflects on the profile
        target_prompt = self.ctx.build_system_prompt(
            target, platform="Spegel Reflection"
        )

        reflection_messages = [
            {"role": "system", "content": target_prompt},
            {
                "role": "user",
                "content": (
                    f"{observer.display_name} wrote this psychological profile "
                    f"about you:\n\n"
                    f'"{profile_result.content}"\n\n'
                    "How does reading this make you feel? Do you agree with "
                    "their assessment? What did they get right? What did they "
                    "miss? Respond in 150-300 words, staying in character."
                ),
            },
        ]

        reflection_result = await pipeline.chat(
            messages=reflection_messages,
            temperature=target.llm.temperature,
            max_tokens=800,
            audit_action="spegel_reflection",
            audit_details={
                "target": target_name,
                "observer": observer_name,
            },
        )

        if reflection_result.blocked or not reflection_result.content:
            logger.warning(
                "SpegelPlugin: reflection %s blocked", target_name
            )
            return None

        reflection = Reflection(
            target_name=target_name,
            target_display_name=target.display_name,
            observer_name=observer_name,
            reflection_text=reflection_result.content,
        )

        return SpegelPair(
            observer_name=observer_name,
            target_name=target_name,
            profile=profile,
            reflection=reflection,
        )

    def _build_target_summary(self, target) -> str:
        """Build a personality summary of the target for the observer."""
        parts = []
        if target.description:
            parts.append(f"Description: {target.description}")
        if target.voice:
            tone = target.voice.get("base_tone", "")
            if tone:
                parts.append(f"Voice: {tone}")
        if target.traits:
            high = [k for k, v in target.traits.items() if v >= 0.7]
            low = [k for k, v in target.traits.items() if v <= 0.3]
            if high:
                parts.append(f"Strong traits: {', '.join(high)}")
            if low:
                parts.append(f"Weak traits: {', '.join(low)}")
        if target.backstory:
            origin = target.backstory.get("origin", "")
            if origin:
                sentences = origin.strip().split(". ")[:2]
                parts.append(f"Background: {'. '.join(sentences)}.")
        return "\n".join(parts) if parts else f"{target.display_name} is an agent."

    def _build_default_pairs(
        self, identities: list[str]
    ) -> list[tuple[str, str]]:
        """Build interesting cross-pairs from available identities.

        Instead of all N*(N-1) pairs, pick a diverse selection.
        """
        if len(identities) <= 4:
            # Small enough to do all pairs
            return [
                (a, b) for a in identities for b in identities if a != b
            ]

        # For larger sets, create a ring + a few cross-links
        pairs: list[tuple[str, str]] = []
        for i, name in enumerate(identities):
            next_name = identities[(i + 1) % len(identities)]
            pairs.append((name, next_name))
            # Add one cross-link (skip 2)
            cross = identities[(i + 3) % len(identities)]
            if cross != name:
                pairs.append((name, cross))

        # Deduplicate
        seen = set()
        unique: list[tuple[str, str]] = []
        for pair in pairs:
            if pair not in seen:
                seen.add(pair)
                unique.append(pair)
        return unique

    def get_pairs(self, limit: int = 20) -> list[SpegelPair]:
        """Get recent profiling pairs (newest first)."""
        return list(reversed(self._pairs[-limit:]))

    def get_pairs_for_identity(self, name: str) -> list[SpegelPair]:
        """Get all pairs involving a specific identity."""
        return [
            p
            for p in self._pairs
            if p.observer_name == name or p.target_name == name
        ]

    def _is_run_time(self) -> bool:
        if self._last_run == 0.0:
            return True
        elapsed = time.time() - self._last_run
        return elapsed >= self._interval_hours * 3600

    def _load_state(self) -> None:
        if self._state_file and self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self._last_run = data.get("last_run", 0.0)
                for pair_data in data.get("pairs", []):
                    self._pairs.append(SpegelPair.model_validate(pair_data))
            except Exception as e:
                logger.warning("SpegelPlugin: failed to load state: %s", e)

    def _save_state(self) -> None:
        if self._state_file:
            try:
                data = {
                    "last_run": self._last_run,
                    "pairs": [
                        p.model_dump() for p in self._pairs[-_MAX_PAIRS_STORED:]
                    ],
                }
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                self._state_file.write_text(json.dumps(data, indent=2))
            except Exception as e:
                logger.warning("SpegelPlugin: failed to save state: %s", e)

    async def teardown(self) -> None:
        self._save_state()
        logger.info("SpegelPlugin teardown complete")
