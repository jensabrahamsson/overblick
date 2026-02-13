"""
MatrixPlugin — Matrix chat agent for the Blick framework.

Connects to the Matrix protocol via client-server API, joins configured
rooms, and responds with personality-driven messages. Matrix is a
decentralized, open protocol — making it a natural fit for privacy-focused
agents like Volt.

Features (planned):
- Homeserver authentication (access token or SSO)
- Room join/leave management
- End-to-end encryption support (E2EE via libolm)
- Conversation tracking per room
- Personality-driven responses via SafeLLMPipeline
- Rate limiting per user and per room
- Media handling (images, files)
- Room-specific personality overrides

Dependencies (not yet added):
- matrix-nio >= 0.21 (Matrix client library with E2EE support)
- python-olm (for end-to-end encryption)

Security considerations:
- E2EE should be MANDATORY for private rooms
- Device verification before trusting encrypted messages
- Homeserver URL validation
- Rate limiting to prevent room flooding

This is a SHELL — community contributions welcome!
"""

import logging
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext

logger = logging.getLogger(__name__)


class MatrixPlugin(PluginBase):
    """
    Matrix chat plugin (shell).

    Connects to Matrix rooms and provides personality-driven
    conversational agents in a decentralized chat environment.
    """

    name = "matrix"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._homeserver: str = ""
        self._access_token: Optional[str] = None
        self._user_id: Optional[str] = None
        self._room_ids: set[str] = set()
        self._system_prompt: str = ""
        self._messages_received = 0
        self._messages_sent = 0
        self._errors = 0

    async def setup(self) -> None:
        """
        Initialize the Matrix client.

        TODO:
        - Load homeserver URL and access token from secrets
        - Build personality-driven system prompt
        - Sync with homeserver to get room list
        - Join configured rooms
        - Set up event listeners for messages
        """
        identity = self.ctx.identity
        raw_config = identity.raw_config
        matrix_config = raw_config.get("matrix", {})

        self._homeserver = matrix_config.get("homeserver", "")
        self._access_token = self.ctx.get_secret("matrix_access_token")
        self._user_id = matrix_config.get("user_id", "")

        room_ids = matrix_config.get("room_ids", [])
        self._room_ids = set(room_ids)

        if not self._homeserver:
            raise RuntimeError(
                f"Missing matrix.homeserver in config for identity {identity.name}."
            )

        if not self._access_token:
            raise RuntimeError(
                f"Missing matrix_access_token for identity {identity.name}. "
                "Set it in config/secrets.yaml"
            )

        self._system_prompt = self._build_system_prompt(identity)

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "homeserver": self._homeserver,
                "rooms": len(self._room_ids),
            },
        )

        logger.info(
            "MatrixPlugin setup complete for %s (%s, %d rooms, shell mode)",
            identity.name, self._homeserver, len(self._room_ids),
        )

    async def tick(self) -> None:
        """
        Sync with Matrix homeserver.

        TODO:
        - In production, use matrix-nio's sync loop (long-polling).
          tick() could handle:
          - Periodic sync if not using continuous sync
          - Stale conversation cleanup
          - Scheduled room messages (heartbeats)
          - Device verification checks
        """
        pass

    async def teardown(self) -> None:
        """Disconnect from Matrix homeserver."""
        logger.info("MatrixPlugin teardown complete")

    def _build_system_prompt(self, identity) -> str:
        """Build system prompt from personality."""
        from overblick.personalities import load_personality, build_system_prompt
        try:
            personality = load_personality(identity.name)
            return build_system_prompt(personality, platform="Matrix")
        except FileNotFoundError:
            return (
                f"You are {identity.display_name}, chatting on Matrix. "
                "Be conversational, privacy-aware, and stay in character."
            )

    def get_status(self) -> dict:
        """Get plugin status."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "homeserver": self._homeserver,
            "rooms": len(self._room_ids),
            "messages_received": self._messages_received,
            "messages_sent": self._messages_sent,
            "errors": self._errors,
        }


# Connector alias — new naming convention (backward-compatible)
MatrixConnector = MatrixPlugin
