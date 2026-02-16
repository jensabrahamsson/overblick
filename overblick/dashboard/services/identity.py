"""
Identity service — read-only access to identity configurations.
"""

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class IdentityService:
    """Read-only access to identity configurations."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def list_identities(self) -> list[str]:
        """List available identity names."""
        from overblick.identities import list_identities
        return list_identities()

    def get_identity(self, name: str) -> Optional[dict[str, Any]]:
        """
        Get identity as a serializable dict.

        Returns None if identity not found.
        """
        try:
            from overblick.identities import load_identity
            identity = load_identity(name)
            return {
                "name": identity.name,
                "display_name": identity.display_name,
                "description": identity.description,
                "version": identity.version,
                "engagement_threshold": identity.engagement_threshold,
                "plugins": list(identity.plugins),
                "capability_names": list(identity.capability_names),
                "traits": dict(identity.traits),
                "llm": {
                    "model": identity.llm.model,
                    "temperature": identity.llm.temperature,
                    "max_tokens": identity.llm.max_tokens,
                    "provider": identity.llm.provider,
                },
                "quiet_hours": {
                    "enabled": identity.quiet_hours.enabled,
                    "timezone": identity.quiet_hours.timezone,
                    "start_hour": identity.quiet_hours.start_hour,
                    "end_hour": identity.quiet_hours.end_hour,
                },
                "schedule": {
                    "heartbeat_hours": identity.schedule.heartbeat_hours,
                    "feed_poll_minutes": identity.schedule.feed_poll_minutes,
                },
                "security": {
                    "enable_preflight": identity.security.enable_preflight,
                    "enable_output_safety": identity.security.enable_output_safety,
                },
                "identity_ref": identity.identity_ref,
            }
        except FileNotFoundError:
            logger.warning("Identity not found: %s", name)
            return None
        except Exception as e:
            logger.error("Error loading identity '%s': %s", name, e, exc_info=True)
            return None

    def get_all_identities(self) -> list[dict[str, Any]]:
        """Get all identities as serializable dicts."""
        results = []
        for name in self.list_identities():
            identity = self.get_identity(name)
            if identity:
                results.append(identity)
        return results
