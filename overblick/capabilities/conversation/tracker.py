"""
ConversationCapability — reusable multi-turn conversation tracker.

Manages per-conversation message history with stale cleanup,
extracted from TelegramPlugin's inline ConversationContext pattern
into a reusable capability.
"""

import logging
import time
from typing import Optional

from pydantic import BaseModel, Field

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class ConversationEntry(BaseModel):
    """Tracks conversation history for a single conversation."""
    conversation_id: str
    messages: list[dict[str, str]] = []
    last_active: float = Field(default_factory=time.time)
    max_history: int = 10

    def add_user_message(self, text: str) -> None:
        """Add a user message to the conversation history."""
        self.messages.append({"role": "user", "content": text})
        if len(self.messages) > self.max_history * 2:
            self.messages = self.messages[-self.max_history * 2:]
        self.last_active = time.time()

    def add_assistant_message(self, text: str) -> None:
        """Add the assistant's response to the conversation history."""
        self.messages.append({"role": "assistant", "content": text})
        self.last_active = time.time()

    def get_messages(self, system_prompt: str = "") -> list[dict[str, str]]:
        """Get full message list including optional system prompt."""
        if system_prompt:
            return [{"role": "system", "content": system_prompt}] + self.messages
        return list(self.messages)

    @property
    def is_stale(self) -> bool:
        """Conversation is stale if inactive for > 1 hour."""
        return (time.time() - self.last_active) > 3600


class ConversationCapability(CapabilityBase):
    """
    Multi-turn conversation tracking capability.

    Manages per-conversation history with automatic stale cleanup.
    Reusable across any connector that needs conversation context
    (Telegram, Discord, Matrix, etc.).
    """

    name = "conversation_tracker"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._conversations: dict[str, ConversationEntry] = {}
        self._max_history: int = 10
        self._stale_seconds: int = 3600

    async def setup(self) -> None:
        self._max_history = self.ctx.config.get("max_history", 10)
        self._stale_seconds = self.ctx.config.get("stale_seconds", 3600)
        logger.info("ConversationCapability initialized for %s", self.ctx.identity_name)

    def get_or_create(self, conversation_id: str) -> ConversationEntry:
        """Get or create a conversation entry."""
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = ConversationEntry(
                conversation_id=conversation_id,
                max_history=self._max_history,
            )
        return self._conversations[conversation_id]

    def add_user_message(self, conversation_id: str, text: str) -> None:
        """Add a user message to a conversation."""
        entry = self.get_or_create(conversation_id)
        entry.add_user_message(text)

    def add_assistant_message(self, conversation_id: str, text: str) -> None:
        """Add an assistant message to a conversation."""
        entry = self.get_or_create(conversation_id)
        entry.add_assistant_message(text)

    def get_messages(self, conversation_id: str, system_prompt: str = "") -> list[dict[str, str]]:
        """Get message history for a conversation."""
        entry = self._conversations.get(conversation_id)
        if not entry:
            if system_prompt:
                return [{"role": "system", "content": system_prompt}]
            return []
        return entry.get_messages(system_prompt)

    def reset(self, conversation_id: str) -> None:
        """Reset a conversation's history."""
        self._conversations.pop(conversation_id, None)

    def cleanup_stale(self) -> int:
        """Remove stale conversations. Returns count of removed."""
        stale = [
            cid for cid, entry in self._conversations.items()
            if (time.time() - entry.last_active) > self._stale_seconds
        ]
        for cid in stale:
            del self._conversations[cid]
        if stale:
            logger.debug("Cleaned up %d stale conversations", len(stale))
        return len(stale)

    async def tick(self) -> None:
        """Periodic cleanup of stale conversations."""
        self.cleanup_stale()

    @property
    def active_count(self) -> int:
        """Number of active conversations."""
        return len(self._conversations)
