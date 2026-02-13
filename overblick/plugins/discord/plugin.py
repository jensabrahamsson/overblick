"""
DiscordPlugin — Discord bot agent for the Blick framework.

Connects to Discord as a bot, listens for messages in configured channels,
and responds with personality-driven messages via SafeLLMPipeline.

Features (planned):
- Guild + channel whitelisting
- Slash command registration (/ask, /status, /persona)
- Conversation threading via Discord threads
- Personality-driven responses
- Rate limiting per user and per channel
- Reaction-based engagement (upvote = positive feedback)
- Voice channel presence (future)

Dependencies (not yet added):
- discord.py >= 2.0 (or hikari as alternative)

This is a SHELL — community contributions welcome!
"""

import logging
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext

logger = logging.getLogger(__name__)


class DiscordPlugin(PluginBase):
    """
    Discord bot plugin (shell).

    Implements the PluginBase interface for Discord integration.
    Requires discord.py or hikari to be installed.
    """

    name = "discord"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._bot_token: Optional[str] = None
        self._guild_ids: set[int] = set()
        self._channel_ids: set[int] = set()
        self._system_prompt: str = ""
        self._messages_received = 0
        self._messages_sent = 0
        self._errors = 0

    async def setup(self) -> None:
        """
        Initialize the Discord bot.

        TODO:
        - Load bot token from secrets (ctx.get_secret("discord_bot_token"))
        - Build personality-driven system prompt
        - Configure guild/channel whitelists from identity config
        - Register slash commands
        - Connect to Discord gateway
        """
        identity = self.ctx.identity
        self._bot_token = self.ctx.get_secret("discord_bot_token")

        if not self._bot_token:
            raise RuntimeError(
                f"Missing discord_bot_token for identity {identity.name}. "
                "Set it in config/secrets.yaml"
            )

        # Load guild/channel whitelists from config
        raw_config = identity.raw_config
        discord_config = raw_config.get("discord", {})
        self._guild_ids = set(discord_config.get("guild_ids", []))
        self._channel_ids = set(discord_config.get("channel_ids", []))

        self._system_prompt = self._build_system_prompt(identity)

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={"plugin": self.name, "identity": identity.name},
        )

        logger.info("DiscordPlugin setup complete for %s (shell mode)", identity.name)

    async def tick(self) -> None:
        """
        Process Discord events.

        TODO:
        - In production, Discord uses websocket events (not polling).
          The tick() method could be used for:
          - Checking for stale conversations to clean up
          - Sending scheduled messages (heartbeats)
          - Updating bot presence/status
        """
        pass

    async def teardown(self) -> None:
        """Disconnect from Discord gracefully."""
        logger.info("DiscordPlugin teardown complete")

    def _build_system_prompt(self, identity) -> str:
        """Build system prompt from personality."""
        from overblick.personalities import load_personality, build_system_prompt
        try:
            personality = load_personality(identity.name)
            return build_system_prompt(personality, platform="Discord")
        except FileNotFoundError:
            return (
                f"You are {identity.display_name}, chatting on Discord. "
                "Be conversational, helpful, and stay in character."
            )

    def get_status(self) -> dict:
        """Get plugin status."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "messages_received": self._messages_received,
            "messages_sent": self._messages_sent,
            "guilds": len(self._guild_ids),
            "errors": self._errors,
        }


# Connector alias — new naming convention (backward-compatible)
DiscordConnector = DiscordPlugin
