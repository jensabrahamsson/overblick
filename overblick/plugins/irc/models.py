"""
IRC Plugin data models.

Defines the conversation, turn, and topic state structures
for identity-to-identity chat sessions.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationState(str, Enum):
    """State of an IRC conversation."""

    ACTIVE = "active"
    PAUSED = "paused"          # Paused due to high load
    COMPLETED = "completed"    # Reached natural end or max turns
    CANCELLED = "cancelled"    # Stopped by supervisor/principal


class IRCTurn(BaseModel):
    """A single turn in an IRC conversation."""

    model_config = ConfigDict(frozen=True)

    identity: str             # Name of the speaking identity
    display_name: str = ""    # Display name for UI
    content: str              # The message content
    timestamp: float = Field(default_factory=time.time)
    turn_number: int = 0


class IRCConversation(BaseModel):
    """A complete IRC conversation between identities."""

    id: str                                     # Unique conversation ID
    topic: str                                  # Discussion topic
    topic_description: str = ""                 # Longer description
    participants: list[str] = []                # Identity names
    turns: list[IRCTurn] = []                   # Conversation history
    state: ConversationState = ConversationState.ACTIVE
    max_turns: int = 20                         # Maximum turns before auto-end
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @property
    def is_active(self) -> bool:
        """Check if conversation is still active."""
        return self.state == ConversationState.ACTIVE

    @property
    def turn_count(self) -> int:
        """Number of turns so far."""
        return len(self.turns)

    @property
    def should_end(self) -> bool:
        """Check if conversation has reached max turns."""
        return self.turn_count >= self.max_turns


class TopicState(BaseModel):
    """Tracks topic selection state."""

    available_topics: list[dict[str, Any]] = []     # Pool of topics
    used_topic_ids: list[str] = []                  # Already discussed
    current_topic_id: str | None = None
