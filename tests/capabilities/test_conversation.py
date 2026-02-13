"""
Tests for ConversationCapability — multi-turn conversation tracker.
"""

import time

import pytest
from pathlib import Path

from blick.core.capability import CapabilityContext
from blick.capabilities.conversation.tracker import ConversationCapability, ConversationEntry


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestConversationEntry:
    def test_add_user_message(self):
        entry = ConversationEntry(conversation_id="chat-1")
        entry.add_user_message("Hello!")
        assert len(entry.messages) == 1
        assert entry.messages[0] == {"role": "user", "content": "Hello!"}

    def test_add_assistant_message(self):
        entry = ConversationEntry(conversation_id="chat-1")
        entry.add_assistant_message("Hi there!")
        assert len(entry.messages) == 1
        assert entry.messages[0] == {"role": "assistant", "content": "Hi there!"}

    def test_get_messages_with_system_prompt(self):
        entry = ConversationEntry(conversation_id="chat-1")
        entry.add_user_message("Hello")
        msgs = entry.get_messages(system_prompt="You are a bot.")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_get_messages_without_system_prompt(self):
        entry = ConversationEntry(conversation_id="chat-1")
        entry.add_user_message("Hello")
        msgs = entry.get_messages()
        assert len(msgs) == 1

    def test_history_trimming(self):
        entry = ConversationEntry(conversation_id="chat-1", max_history=3)
        for i in range(20):
            entry.add_user_message(f"msg {i}")
        # max_history * 2 = 6, so only last 6 messages kept
        assert len(entry.messages) == 6

    def test_is_stale(self):
        entry = ConversationEntry(conversation_id="chat-1")
        assert entry.is_stale is False

        # Simulate old conversation
        entry.last_active = time.time() - 7200  # 2 hours ago
        assert entry.is_stale is True


class TestConversationCapability:
    @pytest.mark.asyncio
    async def test_setup(self):
        ctx = make_ctx(config={"max_history": 20, "stale_seconds": 1800})
        cap = ConversationCapability(ctx)
        await cap.setup()
        assert cap._max_history == 20
        assert cap._stale_seconds == 1800

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = ConversationCapability(ctx)
        assert cap.name == "conversation_tracker"

    @pytest.mark.asyncio
    async def test_get_or_create(self):
        ctx = make_ctx()
        cap = ConversationCapability(ctx)
        await cap.setup()

        entry = cap.get_or_create("chat-1")
        assert entry.conversation_id == "chat-1"
        assert cap.active_count == 1

        # Same ID returns same entry
        entry2 = cap.get_or_create("chat-1")
        assert entry is entry2
        assert cap.active_count == 1

    @pytest.mark.asyncio
    async def test_add_and_get_messages(self):
        ctx = make_ctx()
        cap = ConversationCapability(ctx)
        await cap.setup()

        cap.add_user_message("chat-1", "Hello")
        cap.add_assistant_message("chat-1", "Hi!")

        msgs = cap.get_messages("chat-1", system_prompt="Be helpful.")
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "Hello"
        assert msgs[2]["content"] == "Hi!"

    @pytest.mark.asyncio
    async def test_get_messages_nonexistent(self):
        ctx = make_ctx()
        cap = ConversationCapability(ctx)
        await cap.setup()

        msgs = cap.get_messages("nonexistent")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_get_messages_nonexistent_with_system_prompt(self):
        ctx = make_ctx()
        cap = ConversationCapability(ctx)
        await cap.setup()

        msgs = cap.get_messages("nonexistent", system_prompt="Be helpful.")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_reset(self):
        ctx = make_ctx()
        cap = ConversationCapability(ctx)
        await cap.setup()

        cap.add_user_message("chat-1", "Hello")
        assert cap.active_count == 1

        cap.reset("chat-1")
        assert cap.active_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_stale(self):
        ctx = make_ctx(config={"stale_seconds": 1})
        cap = ConversationCapability(ctx)
        await cap.setup()

        cap.add_user_message("chat-1", "Hello")
        # Make it stale
        cap._conversations["chat-1"].last_active = time.time() - 10

        removed = cap.cleanup_stale()
        assert removed == 1
        assert cap.active_count == 0

    @pytest.mark.asyncio
    async def test_tick_calls_cleanup(self):
        ctx = make_ctx(config={"stale_seconds": 1})
        cap = ConversationCapability(ctx)
        await cap.setup()

        cap.add_user_message("chat-1", "Hello")
        cap._conversations["chat-1"].last_active = time.time() - 10

        await cap.tick()
        assert cap.active_count == 0
