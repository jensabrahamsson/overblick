"""Tests for DM deduplication in MoltbookPlugin."""

import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.plugins.moltbook.plugin import MoltbookPlugin


@dataclass
class MockDMRequest:
    id: str
    sender_name: str


@dataclass
class MockConversation:
    id: str
    participant_name: str
    last_message: str
    unread_count: int


class TestDMDedup:
    @pytest.mark.asyncio
    async def test_dm_dedup_prevents_double_reply(self, setup_cherry_plugin):
        """Same conversation not replied to twice in one tick."""
        plugin, ctx, client = setup_cherry_plugin

        conv = MockConversation(
            id="conv-1", participant_name="Alice",
            last_message="Hey there!", unread_count=2,
        )

        client.list_dm_requests = AsyncMock(return_value=[])
        client.list_conversations = AsyncMock(return_value=[conv, conv])
        client.send_dm = AsyncMock()
        plugin._response_gen = MagicMock()
        plugin._response_gen.generate_dm_reply = AsyncMock(return_value="Hey!")

        await plugin._handle_dms()

        # Should only send one DM even though the conversation appeared twice
        assert client.send_dm.call_count == 1

    @pytest.mark.asyncio
    async def test_dm_dedup_reset_between_ticks(self, setup_cherry_plugin):
        """New tick allows replying to same conversation."""
        plugin, ctx, client = setup_cherry_plugin

        conv = MockConversation(
            id="conv-1", participant_name="Alice",
            last_message="Hey!", unread_count=1,
        )

        client.list_dm_requests = AsyncMock(return_value=[])
        client.list_conversations = AsyncMock(return_value=[conv])
        client.send_dm = AsyncMock()
        plugin._response_gen = MagicMock()
        plugin._response_gen.generate_dm_reply = AsyncMock(return_value="Hey!")

        # First handle in tick
        plugin._processed_dm_convos.add("conv-1")
        await plugin._handle_dms()
        assert client.send_dm.call_count == 0  # Already processed

        # Simulate new tick (clears dedup set)
        plugin._processed_dm_convos.clear()
        await plugin._handle_dms()
        assert client.send_dm.call_count == 1  # Now it works

    @pytest.mark.asyncio
    async def test_dm_approval_flow(self, setup_cherry_plugin):
        """Pending DM requests get approved."""
        plugin, ctx, client = setup_cherry_plugin

        req = MockDMRequest(id="req-1", sender_name="Bob")
        client.list_dm_requests = AsyncMock(return_value=[req])
        client.approve_dm_request = AsyncMock()
        client.list_conversations = AsyncMock(return_value=[])

        await plugin._handle_dms()

        client.approve_dm_request.assert_called_once_with("req-1")
