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


class IRCEventType(str, Enum):
    """Type of event in an IRC conversation turn."""

    MESSAGE = "message"        # Regular chat message
    JOIN = "join"              # Identity joined the channel
    PART = "part"              # Identity left the channel
    QUIT = "quit"              # Identity disconnected
    NETSPLIT = "netsplit"      # Network split event
    REJOIN = "rejoin"          # Identity rejoined after split/pause
    TOPIC = "topic"            # Channel topic was set


class IRCTurn(BaseModel):
    """A single turn in an IRC conversation."""

    model_config = ConfigDict(frozen=True)

    identity: str             # Name of the speaking identity
    display_name: str = ""    # Display name for UI
    content: str              # The message content
    timestamp: float = Field(default_factory=time.time)
    turn_number: int = 0
    type: IRCEventType = IRCEventType.MESSAGE


class IRCConversation(BaseModel):
    """A complete IRC conversation between identities."""

    id: str                                     # Unique conversation ID
    topic: str                                  # Discussion topic
    topic_description: str = ""                 # Longer description
    channel: str = ""                           # IRC channel name (e.g. #consciousness)
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
        """Total number of turns (including system events)."""
        return len(self.turns)

    @property
    def message_count(self) -> int:
        """Number of actual message turns (excludes system events)."""
        return sum(1 for t in self.turns if t.type == IRCEventType.MESSAGE)

    @property
    def should_end(self) -> bool:
        """Check if conversation has reached max message turns."""
        return self.message_count >= self.max_turns


class TopicState(BaseModel):
    """Tracks topic selection state."""

    available_topics: list[dict[str, Any]] = []     # Pool of topics
    used_topic_ids: list[str] = []                  # Already discussed
    current_topic_id: str | None = None
