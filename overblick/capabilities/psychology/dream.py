"""
DreamCapability — wraps DreamSystem as a composable capability.

Generates morning reflections via LLM using thematic guidance, and
provides dream context for prompt injection. Dreams are persisted to
EngagementDB when available.

Each identity loads its own dream guidance from:
  overblick/identities/{name}/dream_content.yaml

If no file exists, generic fallback guidance is used.
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.capabilities.psychology.dream_system import DreamSystem, DreamType, DreamTone

logger = logging.getLogger(__name__)

_IDENTITIES_DIR = Path(__file__).parent.parent.parent / "identities"


def _load_dream_guidance(identity_name: str) -> Optional[dict]:
    """
    Load identity-specific dream guidance from YAML.

    Returns:
        dict with "guidance", "weights", and "identity_voice" keys,
        or None if no file found.
    """
    dream_content_path = _IDENTITIES_DIR / identity_name / "dream_content.yaml"
    if not dream_content_path.exists():
        return None

    try:
        with dream_content_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        logger.warning("Failed to load dream_content.yaml for %s: %s", identity_name, e)
        return None

    guidance: dict[DreamType, dict] = {}
    weights: dict[DreamType, float] = {}

    for type_name, type_data in raw.get("dream_types", {}).items():
        try:
            dream_type = DreamType(type_name)
        except ValueError:
            logger.warning("Unknown dream type '%s' in %s/dream_content.yaml", type_name, identity_name)
            continue

        guidance[dream_type] = {
            "themes": type_data.get("themes", []),
            "symbols": type_data.get("symbols", []),
            "tones": type_data.get("tones", []),
            "psychological_core": type_data.get("psychological_core", ""),
        }
        weights[dream_type] = float(type_data.get("weight", 0.20))

    if not guidance:
        return None

    identity_voice = raw.get("identity_voice", {})
    logger.debug("Loaded %d dream types for %s", len(guidance), identity_name)
    return {"guidance": guidance, "weights": weights, "identity_voice": identity_voice}


class DreamCapability(CapabilityBase):
    """
    Dream generation and reflection capability.

    Wraps the DreamSystem module, exposing it through the standard
    capability lifecycle. Generates a morning dream once per day
    via LLM and persists to EngagementDB.
    """

    name = "dream_system"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._dream_system: Optional[DreamSystem] = None
        self._last_dream_date: Optional[date] = None

    async def setup(self) -> None:
        """Initialize, loading identity-specific dream guidance if available."""
        content = _load_dream_guidance(self.ctx.identity_name)
        if content:
            self._dream_system = DreamSystem(
                dream_guidance=content["guidance"],
                dream_weights=content["weights"],
                identity_voice=content.get("identity_voice", {}),
            )
            logger.info(
                "DreamCapability initialized for %s (identity-specific guidance, %d types)",
                self.ctx.identity_name, len(content["guidance"]),
            )
        else:
            self._dream_system = DreamSystem()
            logger.info(
                "DreamCapability initialized for %s (generic defaults)",
                self.ctx.identity_name,
            )

    async def tick(self) -> None:
        """Generate morning dream once per day (after 06:00 local time)."""
        if not self._dream_system:
            return

        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Europe/Stockholm"))
        except Exception:
            now = datetime.now()

        today = now.date()

        # Generate at most once per day, not before 06:00
        if now.hour < 6:
            return
        if self._last_dream_date == today:
            return

        self._last_dream_date = today

        # Load recent dreams from DB to avoid repetition
        recent_db_dreams: list[dict] = []
        if self.ctx.engagement_db:
            try:
                recent_db_dreams = await self.ctx.engagement_db.get_recent_dreams(days=3)
            except Exception as e:
                logger.warning("Failed to load recent dreams from DB: %s", e)

        dream = await self._dream_system.generate_morning_dream(
            llm_pipeline=self.ctx.llm_pipeline,
            identity_name=self.ctx.identity_name,
            recent_dreams=recent_db_dreams,
        )

        # Persist to database
        if self.ctx.engagement_db and dream:
            try:
                await self.ctx.engagement_db.save_dream(dream.to_dict())
                logger.info("Dream persisted to DB for %s", self.ctx.identity_name)
            except Exception as e:
                logger.warning("Failed to persist dream to DB: %s", e)

        type_str = dream.dream_type.value if hasattr(dream.dream_type, "value") else str(dream.dream_type)
        logger.info("Morning dream generated for %s: %s", self.ctx.identity_name, type_str)

    def get_prompt_context(self) -> str:
        """Return dream context for injection into LLM prompts."""
        if not self._dream_system:
            return ""
        return self._dream_system.get_dream_context_for_prompt()

    async def generate_dream(
        self,
        recent_topics: Optional[list[str]] = None,
        emotional_state: Optional[Any] = None,
    ) -> Optional["Dream"]:
        """Generate a dream on demand. Delegates to DreamSystem."""
        if not self._dream_system:
            return None

        recent_db_dreams: list[dict] = []
        if self.ctx.engagement_db:
            try:
                recent_db_dreams = await self.ctx.engagement_db.get_recent_dreams(days=3)
            except Exception:
                pass

        return await self._dream_system.generate_morning_dream(
            llm_pipeline=self.ctx.llm_pipeline,
            identity_name=self.ctx.identity_name,
            recent_topics=recent_topics,
            emotional_state=emotional_state,
            recent_dreams=recent_db_dreams,
        )

    def get_dream_insights(self, days: int = 7) -> list[str]:
        """Get insights from recent dreams."""
        if not self._dream_system:
            return []
        return self._dream_system.get_dream_insights(days)

    @property
    def inner(self) -> Optional[DreamSystem]:
        """Access the underlying DreamSystem (for tests)."""
        return self._dream_system
