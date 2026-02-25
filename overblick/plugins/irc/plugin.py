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
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext

from .models import ConversationState, IRCConversation, IRCEventType, IRCTurn
from .topic_manager import select_participants, select_topic, topic_to_channel

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
        self._host_inspector = None
        self._recent_participants: list[str] = []  # Track recent participants for diversity
        # Protects multi-step mutations of _current_conversation across await points
        self._conversation_lock = asyncio.Lock()

    async def setup(self) -> None:
        """Initialize IRC plugin."""
        # ctx.data_dir is already data/<identity>/irc (orchestrator appends plugin name)
        self._data_dir = self.ctx.data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Load stored conversations
        self._conversations = self._load_conversations()

        # Load topic state (prevents repeating the same topic after restart)
        self._load_topic_state()

        # Load all identities
        from overblick.identities import list_identities, load_identity
        for name in list_identities():
            try:
                self._identities[name] = load_identity(name)
            except Exception as e:
                logger.warning("Failed to load identity '%s' for IRC: %s", name, e)

        # Mark as running — orchestrator calls setup() then tick() via scheduler,
        # so we activate here (start() is not part of the PluginBase lifecycle)
        self._running = True

        logger.info(
            "IRC Plugin initialized: %d identities, %d stored conversations, %d used topics",
            len(self._identities),
            len(self._conversations),
            len(self._used_topics),
        )

    async def tick(self) -> None:
        """Plugin tick — delegates to conversation tick."""
        await self._conversation_tick()

    async def teardown(self) -> None:
        """Gracefully shut down IRC plugin."""
        self._running = False

        # End current conversation if active — emit QUIT events
        async with self._conversation_lock:
            if self._current_conversation and self._current_conversation.is_active:
                quit_turns = self._make_system_events(
                    IRCEventType.QUIT,
                    self._current_conversation.participants,
                    self._current_conversation.channel,
                    "Connection closed (shutdown)",
                )
                updated_turns = list(self._current_conversation.turns) + quit_turns
                self._current_conversation = self._current_conversation.model_copy(
                    update={
                        "state": ConversationState.CANCELLED,
                        "turns": updated_turns,
                        "updated_at": time.time(),
                    }
                )
                self._save_conversation(self._current_conversation)
                logger.info("IRC: Cancelled active conversation on teardown")

    async def _conversation_tick(self) -> None:
        """
        Periodic tick — start or continue a conversation.

        Only runs if system load is low and we're not in quiet hours.
        Uses _conversation_lock to protect state mutations against concurrent teardown.
        """
        if not self._running:
            return

        # IRC uses its own quiet hours (23:00-07:00) instead of the global setting
        if self._is_irc_quiet_hours():
            logger.debug("IRC: Skipping tick — quiet hours (23:00-07:00)")
            return

        # Check system load
        if not await self._is_system_idle():
            async with self._conversation_lock:
                if self._current_conversation and self._current_conversation.is_active:
                    # Emit NETSPLIT event before pausing
                    netsplit_turn = IRCTurn(
                        identity="server",
                        display_name="",
                        content="Netsplit: *.overblick.net <-> *.llm.local",
                        turn_number=self._current_conversation.turn_count,
                        type=IRCEventType.NETSPLIT,
                    )
                    updated_turns = list(self._current_conversation.turns) + [netsplit_turn]
                    self._current_conversation = self._current_conversation.model_copy(
                        update={
                            "state": ConversationState.PAUSED,
                            "turns": updated_turns,
                            "updated_at": time.time(),
                        }
                    )
                    self._save_conversation(self._current_conversation)
                    logger.info("IRC: Conversation paused — high system load (netsplit)")
                else:
                    logger.debug("IRC: Skipping tick — high system load")
            return

        # Lock protects state mutations; released before _run_turns (which has its own locking)
        async with self._conversation_lock:
            # Resume paused conversation — emit REJOIN events
            if self._current_conversation and self._current_conversation.state == ConversationState.PAUSED:
                rejoin_turns = self._make_system_events(
                    IRCEventType.REJOIN,
                    self._current_conversation.participants,
                    self._current_conversation.channel,
                )
                updated_turns = list(self._current_conversation.turns) + rejoin_turns
                self._current_conversation = self._current_conversation.model_copy(
                    update={
                        "state": ConversationState.ACTIVE,
                        "turns": updated_turns,
                        "updated_at": time.time(),
                    }
                )
                self._save_conversation(self._current_conversation)

            # Start new conversation if none active
            if not self._current_conversation or not self._current_conversation.is_active:
                await self._start_conversation()

        # Run a few turns (uses its own internal locking)
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
            if self._host_inspector is None:
                from overblick.capabilities.monitoring.inspector import HostInspectionCapability
                self._host_inspector = HostInspectionCapability()
            health = await self._host_inspector.inspect()

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
        participants = select_participants(
            identities, topic, recent_participants=self._recent_participants,
        )

        if len(participants) < 2:
            logger.info("IRC: Not enough interested participants for topic '%s'", topic["topic"])
            return

        channel = topic_to_channel(topic)
        participant_names = [p.name for p in participants]

        # Build initial system events: JOIN for each participant, then TOPIC
        initial_turns: list[IRCTurn] = []
        for p in participants:
            initial_turns.append(IRCTurn(
                identity=p.name,
                display_name=getattr(p, "display_name", p.name),
                content=channel,
                turn_number=len(initial_turns),
                type=IRCEventType.JOIN,
            ))
        initial_turns.append(IRCTurn(
            identity=participant_names[0],
            display_name=getattr(participants[0], "display_name", participant_names[0]),
            content=f"{topic['topic']} — {topic.get('description', '')}".strip(" —"),
            turn_number=len(initial_turns),
            type=IRCEventType.TOPIC,
        ))

        conversation = IRCConversation(
            id=f"irc-{uuid.uuid4().hex[:12]}",
            topic=topic["topic"],
            topic_description=topic.get("description", ""),
            channel=channel,
            participants=participant_names,
            turns=initial_turns,
            max_turns=min(20, len(participants) * 5),
        )

        self._current_conversation = conversation
        self._used_topics.append(topic["id"])
        self._save_topic_state()

        logger.info(
            "IRC: Started conversation '%s' in %s with %s",
            topic["topic"],
            channel,
            participant_names,
        )

        # Emit event
        if self.ctx.event_bus:
            await self.ctx.event_bus.emit(
                "irc.conversation_start",
                conversation_id=conversation.id,
                topic=topic["topic"],
                channel=channel,
                participants=conversation.participants,
            )

    async def _run_turns(self, max_turns: int = 3) -> None:
        """Run up to max_turns turns in the current conversation."""
        if not self._current_conversation or not self._current_conversation.is_active:
            return

        for _ in range(max_turns):
            if self._current_conversation.should_end:
                # Emit PART events for all participants
                async with self._conversation_lock:
                    part_turns = self._make_system_events(
                        IRCEventType.PART,
                        self._current_conversation.participants,
                        self._current_conversation.channel,
                    )
                    updated_turns = list(self._current_conversation.turns) + part_turns
                    self._current_conversation = self._current_conversation.model_copy(
                        update={
                            "state": ConversationState.COMPLETED,
                            "turns": updated_turns,
                            "updated_at": time.time(),
                        }
                    )
                    self._save_conversation(self._current_conversation)

                    # Track participants for diversity rotation
                    self._recent_participants.extend(
                        self._current_conversation.participants
                    )
                    # Keep only the last ~15 names
                    self._recent_participants = self._recent_participants[-15:]

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

            # Add turn — lock protects the read-modify-write across the preceding await
            async with self._conversation_lock:
                if not self._current_conversation or not self._current_conversation.is_active:
                    break  # teardown may have cancelled the conversation during _generate_turn
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

        # Only consider actual messages — ignore JOIN, TOPIC, PART, NETSPLIT, etc.
        # Without this filter, the TOPIC system event (owned by participant[0]) would
        # prevent the initiator from ever being selected first.
        message_turns = [
            t for t in self._current_conversation.turns
            if t.type == IRCEventType.MESSAGE
        ]

        if not message_turns:
            # No messages yet — let the initiator (participant[0]) speak first
            return participants[0]

        # Avoid same speaker twice in a row
        last_speaker = message_turns[-1].identity
        others = [p for p in participants if p != last_speaker]

        if not others:
            return participants[0]

        # Random selection from others (excluding last speaker)
        return random.choice(others)

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
            f"\nDo NOT use formal greetings or sign-offs — this is a casual group chat."
            f"\n\n=== CONVERSATION RULES ==="
            f"\n- Do NOT repeat points already made in this conversation."
            f"\n- If you agree with something, BUILD on it — don't restate."
            f"\n- Bring up a NEW angle, example, or counterpoint each time."
            f"\n- React to what was JUST said, not to the topic in general."
            f"\n- It's OK to disagree, shift focus, or ask a provocative question."
        )
        full_prompt = system_prompt + irc_context

        # Build message history
        messages = [{"role": "system", "content": full_prompt}]

        # Add conversation history as alternating user/assistant messages
        for turn in conversation.turns[-20:]:  # Last 20 turns for context
            role = "assistant" if turn.identity == speaker_name else "user"
            prefix = f"[{turn.display_name}] " if role == "user" else ""
            messages.append({
                "role": role,
                "content": f"{prefix}{turn.content}",
            })

        # Add a prompt for this turn
        if conversation.turns:
            # Count how many times this speaker has already spoken
            speaker_msg_count = sum(
                1 for t in conversation.turns if t.identity == speaker_name
            )
            # Summarize recent points to avoid repetition
            recent_points = []
            for t in conversation.turns[-10:]:
                if t.content and t.identity != "server":
                    recent_points.append(f"- {t.display_name}: {t.content[:80]}")
            points_summary = "\n".join(recent_points) if recent_points else "None yet."

            # Continue the conversation
            messages.append({
                "role": "user",
                "content": (
                    f"[Continue the conversation as {identity.display_name}. "
                    f"This is your message #{speaker_msg_count + 1}.\n"
                    f"Points already made (DO NOT repeat these):\n{points_summary}\n"
                    f"Add something NEW — a fresh angle, a specific example, or a challenge to what was just said.]"
                ),
            })
        else:
            # Start the conversation
            messages.append({
                "role": "user",
                "content": f"[Start a conversation about '{conversation.topic}'. "
                           f"Share your opening thought as {identity.display_name}.]",
            })

        try:
            result = await self.ctx.llm_pipeline.chat(
                messages=messages,
                user_id=speaker_name,
                audit_action="irc_conversation",
                skip_preflight=True,  # Internal prompt, not external content
            )
            if result.blocked:
                logger.warning("IRC: Turn blocked for %s: %s", speaker_name, result.block_reason)
                return None
            if result.content:
                return result.content.strip()
        except Exception as e:
            logger.error("IRC: Failed to generate turn for %s: %s", speaker_name, e)

        return None

    # -------------------------------------------------------------------------
    # System Events
    # -------------------------------------------------------------------------

    def _make_system_events(
        self,
        event_type: IRCEventType,
        participants: list[str],
        channel: str,
        reason: str = "",
    ) -> list[IRCTurn]:
        """Create system event turns (JOIN, PART, QUIT, REJOIN) for participants."""
        base_num = self._current_conversation.turn_count if self._current_conversation else 0
        turns = []
        for i, name in enumerate(participants):
            identity = self._identities.get(name)
            display = getattr(identity, "display_name", name) if identity else name
            content = channel
            if reason:
                content = f"{channel} ({reason})"
            turns.append(IRCTurn(
                identity=name,
                display_name=display,
                content=content,
                turn_number=base_num + i,
                type=event_type,
            ))
        return turns

    # -------------------------------------------------------------------------
    # Topic Persistence
    # -------------------------------------------------------------------------

    def _save_topic_state(self) -> None:
        """Save used topic IDs to disk to survive restarts."""
        if not self._data_dir:
            return
        state = {"used_topic_ids": self._used_topics}
        topic_file = self._data_dir / "topic_state.json"
        topic_file.write_text(json.dumps(state, indent=2))

    def _load_topic_state(self) -> None:
        """Load used topic IDs from disk."""
        if not self._data_dir:
            return
        topic_file = self._data_dir / "topic_state.json"
        if not topic_file.exists():
            return
        try:
            state = json.loads(topic_file.read_text())
            self._used_topics = state.get("used_topic_ids", [])
        except Exception as e:
            logger.warning("IRC: Failed to load topic state: %s", e)

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
