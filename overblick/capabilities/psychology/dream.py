"""
DreamCapability — wraps DreamSystem as a composable capability.

Generates morning reflections and provides dream context for prompt injection.
Each identity loads its own dream templates from:
  overblick/identities/{name}/dream_content.yaml

If no file exists, generic fallback templates are used.
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


def _load_dream_content(identity_name: str) -> Optional[dict]:
    """
    Load identity-specific dream content from YAML.

    Returns:
        dict with "templates" and "weights" keys, or None if no file found.
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

    templates: dict[DreamType, list[dict]] = {}
    weights: dict[DreamType, float] = {}

    for type_name, type_data in raw.get("dream_types", {}).items():
        try:
            dream_type = DreamType(type_name)
        except ValueError:
            logger.warning("Unknown dream type '%s' in %s/dream_content.yaml", type_name, identity_name)
            continue

        raw_templates = type_data.get("templates", [])
        for tmpl in raw_templates:
            # Coerce string tone to DreamTone if needed
            if isinstance(tmpl.get("tone"), str):
                try:
                    tmpl["tone"] = DreamTone(tmpl["tone"])
                except ValueError:
                    logger.warning("Unknown tone '%s' in %s templates", tmpl["tone"], type_name)
                    tmpl["tone"] = DreamTone.CONTEMPLATIVE

        templates[dream_type] = raw_templates
        weights[dream_type] = float(type_data.get("weight", 0.20))

    if not templates:
        return None

    logger.debug("Loaded %d dream types for %s", len(templates), identity_name)
    return {"templates": templates, "weights": weights}


class DreamCapability(CapabilityBase):
    """
    Dream generation and reflection capability.

    Wraps the DreamSystem module, exposing it through the standard
    capability lifecycle. Generates a morning dream once per day
    and provides dream context for LLM prompts.
    """

    name = "dream_system"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._dream_system: Optional[DreamSystem] = None
        self._last_dream_date: Optional[date] = None

    async def setup(self) -> None:
        """Initialize, loading identity-specific dream templates if available."""
        content = _load_dream_content(self.ctx.identity_name)
        if content:
            self._dream_system = DreamSystem(
                dream_templates=content["templates"],
                dream_weights=content["weights"],
            )
            logger.info(
                "DreamCapability initialized for %s (identity-specific templates, %d types)",
                self.ctx.identity_name, len(content["templates"]),
            )
        else:
            # Fall back to config-provided templates or generic defaults
            templates = self.ctx.config.get("dream_templates", None)
            self._dream_system = DreamSystem(dream_templates=templates)
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
        dream = self._dream_system.generate_morning_dream()
        type_str = dream.dream_type.value if hasattr(dream.dream_type, "value") else str(dream.dream_type)
        logger.info("Morning dream generated for %s: %s", self.ctx.identity_name, type_str)

    def get_prompt_context(self) -> str:
        """Return dream context for injection into LLM prompts."""
        if not self._dream_system:
            return ""
        return self._dream_system.get_dream_context_for_prompt()

    def generate_morning_dream(
        self,
        recent_topics: Optional[list[str]] = None,
        emotional_state: Optional[Any] = None,
    ):
        """Generate a morning dream. Delegates to DreamSystem."""
        if not self._dream_system:
            return None
        return self._dream_system.generate_morning_dream(recent_topics, emotional_state)

    def get_dream_insights(self, days: int = 7) -> list[str]:
        """Get insights from recent dreams."""
        if not self._dream_system:
            return []
        return self._dream_system.get_dream_insights(days)

    @property
    def inner(self) -> Optional[DreamSystem]:
        """Access the underlying DreamSystem (for tests)."""
        return self._dream_system
