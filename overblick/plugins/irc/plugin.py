"""
IRC Plugin — Identity-to-identity conversations.

Orchestrates free-form conversations between agent identities on deep topics.
Runs at low system load, using the SafeLLMPipeline for each turn.

Architecture:
1. Topic Manager selects a topic and scores identity interest
2. Participants are chosen based on interest scores
3. Turn Coordinator manages the conversation flow via event bus
4. Load Guard checks system health before each turn
5. Conversations are stored as JSON for the dashboard to display

Scheduling:
- Triggered periodically by the Scheduler
- Only runs when system load is low (CPU < 50%, no active LLM queue)
- Respects quiet hours
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext

from .models import ConversationState, IRCConversation, IRCTurn
from .topic_manager import select_participants, select_topic

logger = logging.getLogger(__name__)

# Maximum conversations to store
_MAX_STORED_CONVERSATIONS = 50

# Default turn interval (seconds between turns)
_TURN_INTERVAL = 5.0

# IRC has its own quiet hours (later than Moltbook since it uses less resources)
_IRC_QUIET_START = 23  # 23:00
_IRC_QUIET_END = 7     # 07:00


class IRCPlugin(PluginBase):
    """
    IRC conversation orchestrator plugin.

    Manages multi-identity conversations on curated topics.
    """

    name = "irc"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)

        self._conversations: list[IRCConversation] = []
        self._current_conversation: Optional[IRCConversation] = None
        self._used_topics: list[str] = []
        self._identities: dict[str, Any] = {}  # Loaded identity objects
        self._data_dir: Optional[Path] = None
        self._running = False

    async def setup(self) -> None:
        """Initialize IRC plugin."""
        # ctx.data_dir is already data/<identity>/irc (orchestrator appends plugin name)
        self._data_dir = self.ctx.data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Load stored conversations
        self._conversations = self._load_conversations()

        # Load all identities
        from overblick.identities import list_identities, load_identity
        for name in list_identities():
            try:
                self._identities[name] = load_identity(name)
            except Exception as e:
                logger.warning("Failed to load identity '%s' for IRC: %s", name, e)

        logger.info(
            "IRC Plugin initialized: %d identities, %d stored conversations",
            len(self._identities),
            len(self._conversations),
        )

    async def start(self) -> None:
        """Register scheduled tasks."""
        self._running = True

        # Register with scheduler if available
        if self.ctx.scheduler:
            self.ctx.scheduler.add(
                "irc_conversation_tick",
                self._conversation_tick,
                interval_seconds=300,  # Check every 5 minutes
                run_immediately=False,
            )

    async def tick(self) -> None:
        """Plugin tick — delegates to conversation tick."""
        await self._conversation_tick()

    async def stop(self) -> None:
        """Stop IRC plugin."""
        self._running = False

        # End current conversation if active
        if self._current_conversation and self._current_conversation.is_active:
            self._current_conversation = self._current_conversation.model_copy(
                update={"state": ConversationState.CANCELLED}
            )
            self._save_conversation(self._current_conversation)

        if self.ctx.scheduler:
            self.ctx.scheduler.remove("irc_conversation_tick")

    async def _conversation_tick(self) -> None:
        """
        Periodic tick — start or continue a conversation.

        Only runs if system load is low and we're not in quiet hours.
        """
        if not self._running:
            return

        # IRC uses its own quiet hours (23:00-07:00) instead of the global setting
        if self._is_irc_quiet_hours():
            logger.debug("IRC: Skipping tick — quiet hours (23:00-07:00)")
            return

        # Check system load
        if not await self._is_system_idle():
            if self._current_conversation and self._current_conversation.is_active:
                self._current_conversation = self._current_conversation.model_copy(
                    update={"state": ConversationState.PAUSED}
                )
                logger.info("IRC: Conversation paused — high system load")
            else:
                logger.debug("IRC: Skipping tick — high system load")
            return

        # Resume paused conversation
        if self._current_conversation and self._current_conversation.state == ConversationState.PAUSED:
            self._current_conversation = self._current_conversation.model_copy(
                update={"state": ConversationState.ACTIVE}
            )

        # Start new conversation if none active
        if not self._current_conversation or not self._current_conversation.is_active:
            await self._start_conversation()

        # Run a few turns
        if self._current_conversation and self._current_conversation.is_active:
            await self._run_turns(max_turns=3)

    def _is_irc_quiet_hours(self) -> bool:
        """Check IRC-specific quiet hours (23:00-07:00)."""
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Europe/Stockholm"))
        except Exception:
            now = datetime.now()
        hour = now.hour
        if _IRC_QUIET_START > _IRC_QUIET_END:
            # Wraps midnight: e.g. 23-07
            return hour >= _IRC_QUIET_START or hour < _IRC_QUIET_END
        return _IRC_QUIET_START <= hour < _IRC_QUIET_END

    async def _is_system_idle(self) -> bool:
        """Check if system load is low enough for IRC conversations."""
        try:
            from overblick.capabilities.monitoring.inspector import HostInspectionCapability
            inspector = HostInspectionCapability()
            health = await inspector.inspect()

            # CPU must be below 50% of core count
            if health.cpu.core_count > 0:
                load_pct = health.cpu.load_1m / health.cpu.core_count
                if load_pct > 0.5:
                    return False

            # Memory must be below 80%
            if health.memory.total > 0:
                mem_pct = health.memory.used / health.memory.total
                if mem_pct > 0.8:
                    return False

            return True
        except Exception:
            # If we can't check, assume it's fine
            return True

    async def _start_conversation(self) -> None:
        """Start a new conversation with selected topic and participants."""
        topic = select_topic(self._used_topics)
        if not topic:
            logger.info("IRC: No available topics")
            return

        identities = list(self._identities.values())
        participants = select_participants(identities, topic)

        if len(participants) < 2:
            logger.info("IRC: Not enough interested participants for topic '%s'", topic["topic"])
            return

        conversation = IRCConversation(
            id=f"irc-{uuid.uuid4().hex[:12]}",
            topic=topic["topic"],
            topic_description=topic.get("description", ""),
            participants=[p.name for p in participants],
            max_turns=min(20, len(participants) * 5),
        )

        self._current_conversation = conversation
        self._used_topics.append(topic["id"])

        logger.info(
            "IRC: Started conversation '%s' with %s",
            topic["topic"],
            [p.name for p in participants],
        )

        # Emit event
        if self.ctx.event_bus:
            await self.ctx.event_bus.emit(
                "irc.conversation_start",
                conversation_id=conversation.id,
                topic=topic["topic"],
                participants=conversation.participants,
            )

    async def _run_turns(self, max_turns: int = 3) -> None:
        """Run up to max_turns turns in the current conversation."""
        if not self._current_conversation or not self._current_conversation.is_active:
            return

        for _ in range(max_turns):
            if self._current_conversation.should_end:
                self._current_conversation = self._current_conversation.model_copy(
                    update={"state": ConversationState.COMPLETED}
                )
                self._save_conversation(self._current_conversation)

                if self.ctx.event_bus:
                    await self.ctx.event_bus.emit(
                        "irc.conversation_end",
                        conversation_id=self._current_conversation.id,
                    )
                break

            # Select next speaker (round-robin with some randomness)
            speaker_name = self._select_next_speaker()
            if not speaker_name:
                break

            # Generate response
            response = await self._generate_turn(speaker_name)
            if not response:
                break

            # Add turn
            identity = self._identities.get(speaker_name)
            turn = IRCTurn(
                identity=speaker_name,
                display_name=identity.display_name if identity else speaker_name,
                content=response,
                turn_number=self._current_conversation.turn_count,
            )

            updated_turns = list(self._current_conversation.turns) + [turn]
            self._current_conversation = self._current_conversation.model_copy(
                update={
                    "turns": updated_turns,
                    "updated_at": time.time(),
                }
            )

            # Save after each turn
            self._save_conversation(self._current_conversation)

            # Emit event
            if self.ctx.event_bus:
                await self.ctx.event_bus.emit(
                    "irc.new_turn",
                    conversation_id=self._current_conversation.id,
                    identity=speaker_name,
                    content=response,
                )

            # Brief pause between turns
            await asyncio.sleep(_TURN_INTERVAL)

    def _select_next_speaker(self) -> str | None:
        """Select the next speaker for the conversation."""
        if not self._current_conversation:
            return None

        participants = self._current_conversation.participants
        if not participants:
            return None

        turns = self._current_conversation.turns

        if not turns:
            # First turn — pick randomly
            return participants[0]

        # Avoid same speaker twice in a row
        last_speaker = turns[-1].identity
        others = [p for p in participants if p != last_speaker]

        if not others:
            return participants[0]

        # Simple round-robin
        return others[self._current_conversation.turn_count % len(others)]

    async def _generate_turn(self, speaker_name: str) -> str | None:
        """Generate a conversation turn for the given identity."""
        identity = self._identities.get(speaker_name)
        if not identity:
            return None

        if not self.ctx.llm_pipeline:
            logger.warning("IRC: No LLM pipeline available")
            return None

        conversation = self._current_conversation
        if not conversation:
            return None

        # Build conversation context
        from overblick.identities import build_system_prompt
        system_prompt = build_system_prompt(identity, platform="IRC")

        # Add IRC context to system prompt
        irc_context = (
            f"\n\n=== IRC CONVERSATION CONTEXT ==="
            f"\nYou are in an IRC-style group conversation about: {conversation.topic}"
            f"\n{conversation.topic_description}"
            f"\nOther participants: {', '.join(p for p in conversation.participants if p != speaker_name)}"
            f"\nKeep responses concise (2-4 sentences). Be natural and conversational."
            f"\nRespond to what others have said. Share your unique perspective."
            f"\nDo NOT use formal greetings or sign-offs — this is a casual group chat."
        )
        full_prompt = system_prompt + irc_context

        # Build message history
        messages = [{"role": "system", "content": full_prompt}]

        # Add conversation history as alternating user/assistant messages
        for turn in conversation.turns[-10:]:  # Last 10 turns for context
            role = "assistant" if turn.identity == speaker_name else "user"
            prefix = f"[{turn.display_name}] " if role == "user" else ""
            messages.append({
                "role": role,
                "content": f"{prefix}{turn.content}",
            })

        # Add a prompt for this turn
        if conversation.turns:
            # Continue the conversation
            messages.append({
                "role": "user",
                "content": f"[Continue the conversation as {identity.display_name}. "
                           f"Respond to what was said above about '{conversation.topic}'.]",
            })
        else:
            # Start the conversation
            messages.append({
                "role": "user",
                "content": f"[Start a conversation about '{conversation.topic}'. "
                           f"Share your opening thought as {identity.display_name}.]",
            })

        try:
            result = await self.ctx.llm_pipeline.generate(
                messages=messages,
                identity_name=speaker_name,
                action="irc_conversation",
            )
            if result and result.get("content"):
                return result["content"].strip()
        except Exception as e:
            logger.error("IRC: Failed to generate turn for %s: %s", speaker_name, e)

        return None

    # -------------------------------------------------------------------------
    # Storage
    # -------------------------------------------------------------------------

    def _save_conversation(self, conversation: IRCConversation) -> None:
        """Save a conversation to disk."""
        if not self._data_dir:
            return

        # Update in-memory list
        existing = [c for c in self._conversations if c.id != conversation.id]
        existing.append(conversation)

        # Trim to max stored
        if len(existing) > _MAX_STORED_CONVERSATIONS:
            existing = sorted(existing, key=lambda c: c.updated_at, reverse=True)
            existing = existing[:_MAX_STORED_CONVERSATIONS]

        self._conversations = existing

        # Write to disk
        data = [c.model_dump() for c in self._conversations]
        conversations_file = self._data_dir / "conversations.json"
        conversations_file.write_text(json.dumps(data, indent=2, default=str))

    def _load_conversations(self) -> list[IRCConversation]:
        """Load conversations from disk."""
        if not self._data_dir:
            return []

        conversations_file = self._data_dir / "conversations.json"
        if not conversations_file.exists():
            return []

        try:
            data = json.loads(conversations_file.read_text())
            return [IRCConversation.model_validate(c) for c in data]
        except Exception as e:
            logger.warning("IRC: Failed to load conversations: %s", e)
            return []

    # -------------------------------------------------------------------------
    # Public API (for dashboard)
    # -------------------------------------------------------------------------

    def get_conversations(self, limit: int = 20) -> list[dict]:
        """Get recent conversations for the dashboard."""
        sorted_convs = sorted(
            self._conversations,
            key=lambda c: c.updated_at,
            reverse=True,
        )
        return [c.model_dump() for c in sorted_convs[:limit]]

    def get_conversation(self, conversation_id: str) -> dict | None:
        """Get a specific conversation by ID."""
        for conv in self._conversations:
            if conv.id == conversation_id:
                return conv.model_dump()
        return None

    def get_current_conversation(self) -> dict | None:
        """Get the currently active conversation."""
        if self._current_conversation:
            return self._current_conversation.model_dump()
        return None
