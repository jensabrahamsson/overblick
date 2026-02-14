"""
Personality service — read-only access to personality definitions.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PersonalityService:
    """Read-only access to personality configurations."""

    def list_personalities(self) -> list[str]:
        """List available personality names."""
        from overblick.personalities import list_personalities
        return list_personalities()

    def get_personality(self, name: str) -> Optional[dict[str, Any]]:
        """
        Get personality as a serializable dict.

        Returns None if personality not found.
        """
        try:
            from overblick.personalities import load_personality
            p = load_personality(name)
            return {
                "name": p.name,
                "display_name": p.display_name,
                "version": p.version,
                "identity_info": dict(p.identity_info),
                "backstory": dict(p.backstory),
                "voice": dict(p.voice),
                "traits": dict(p.traits),
                "interests": dict(p.interests),
                "vocabulary": dict(p.vocabulary),
                "signature_phrases": {k: list(v) for k, v in p.signature_phrases.items()},
                "ethos": p.ethos if isinstance(p.ethos, list) else dict(p.ethos) if p.ethos else {},
                "moltbook_bio": p.moltbook_bio,
                "raw": dict(p.raw) if p.raw else {},
            }
        except FileNotFoundError:
            logger.debug("Personality not found: %s", name)
            return None
        except Exception as e:
            logger.error("Error loading personality '%s': %s", name, e)
            return None

    def get_all_personalities(self) -> list[dict[str, Any]]:
        """Get all personalities as serializable dicts."""
        results = []
        for name in self.list_personalities():
            p = self.get_personality(name)
            if p:
                results.append(p)
        return results
