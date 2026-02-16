"""
Personality service — read-only access to personality character definitions.

This service provides access to the character/personality aspects of identities
(voice, traits, backstory, etc.) as distinct from the operational config.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PersonalityService:
    """Read-only access to personality (character) configurations."""

    def list_identities(self) -> list[str]:
        """List available identity names."""
        from overblick.identities import list_identities
        return list_identities()

    def get_personality(self, name: str) -> Optional[dict[str, Any]]:
        """
        Get personality character data as a serializable dict.

        Returns None if identity not found.
        """
        try:
            from overblick.identities import load_identity
            p = load_identity(name)
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
            logger.debug("Identity not found: %s", name)
            return None
        except Exception as e:
            logger.error("Error loading identity '%s': %s", name, e, exc_info=True)
            return None

    def get_all_personalities(self) -> list[dict[str, Any]]:
        """Get all personality character data as serializable dicts."""
        results = []
        for name in self.list_identities():
            p = self.get_personality(name)
            if p:
                results.append(p)
        return results
