"""
Tests for the BossRequestCapability.

Verifies:
- Initialization with and without IPC client
- Research request sending via IPC
- Timeout and error handling
- Graceful degradation when IPC unavailable
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.capabilities.communication.boss_request import BossRequestCapability
from overblick.supervisor.ipc import IPCMessage


def _make_ctx(ipc_client=None):
    """Create a mock capability context."""
    ctx = MagicMock()
    ctx.identity_name = "stal"
    ctx.ipc_client = ipc_client
    return ctx


class TestBossRequestSetup:
    """Test initialization."""

    @pytest.mark.asyncio
    async def test_setup_with_ipc(self):
        """setup() stores IPC client when available."""
        mock_ipc = AsyncMock()
        ctx = _make_ctx(ipc_client=mock_ipc)

        cap = BossRequestCapability(ctx)
        await cap.setup()

        assert cap.configured is True
        assert cap._ipc_client is mock_ipc

    @pytest.mark.asyncio
    async def test_setup_without_ipc(self):
        """setup() marks as not configured when IPC unavailable."""
        ctx = _make_ctx(ipc_client=None)

        cap = BossRequestCapability(ctx)
        await cap.setup()

        assert cap.configured is False

    def test_name(self):
        """Capability name is set correctly."""
        ctx = _make_ctx()
        cap = BossRequestCapability(ctx)
        assert cap.name == "boss_request"


class TestBossRequestResearch:
    """Test research request functionality."""

    @pytest.mark.asyncio
    async def test_request_research_success(self):
        """request_research() returns summary on successful IPC."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=IPCMessage(
            msg_type="research_response",
            payload={
                "summary": "The EUR/SEK rate is currently 11.45.",
                "source": "duckduckgo_summarized",
            },
            sender="supervisor",
        ))

        ctx = _make_ctx(ipc_client=mock_ipc)
        cap = BossRequestCapability(ctx)
        await cap.setup()

        result = await cap.request_research(
            "What is the current EUR/SEK rate?",
            context="Need for a financial email reply",
        )

        assert result == "The EUR/SEK rate is currently 11.45."
        mock_ipc.send.assert_called_once()

        # Verify the IPC message payload
        sent_msg = mock_ipc.send.call_args.args[0]
        assert sent_msg.msg_type == "research_request"
        assert sent_msg.payload["query"] == "What is the current EUR/SEK rate?"
        assert sent_msg.payload["context"] == "Need for a financial email reply"
        assert sent_msg.sender == "stal"

    @pytest.mark.asyncio
    async def test_request_research_no_results(self):
        """request_research() returns None when IPC returns error."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=IPCMessage(
            msg_type="research_response",
            payload={"error": "Empty research query"},
            sender="supervisor",
        ))

        ctx = _make_ctx(ipc_client=mock_ipc)
        cap = BossRequestCapability(ctx)
        await cap.setup()

        result = await cap.request_research("test query")
        assert result is None

    @pytest.mark.asyncio
    async def test_request_research_ipc_timeout(self):
        """request_research() returns None on IPC timeout."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=None)  # Timeout returns None

        ctx = _make_ctx(ipc_client=mock_ipc)
        cap = BossRequestCapability(ctx)
        await cap.setup()

        result = await cap.request_research("test query")
        assert result is None

    @pytest.mark.asyncio
    async def test_request_research_ipc_exception(self):
        """request_research() returns None on IPC exception."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(side_effect=Exception("Connection refused"))

        ctx = _make_ctx(ipc_client=mock_ipc)
        cap = BossRequestCapability(ctx)
        await cap.setup()

        result = await cap.request_research("test query")
        assert result is None

    @pytest.mark.asyncio
    async def test_request_research_not_configured(self):
        """request_research() returns None when not configured."""
        ctx = _make_ctx(ipc_client=None)
        cap = BossRequestCapability(ctx)
        await cap.setup()

        result = await cap.request_research("test query")
        assert result is None

    @pytest.mark.asyncio
    async def test_request_research_timeout_value(self):
        """request_research() uses 60s timeout for IPC."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=IPCMessage(
            msg_type="research_response",
            payload={"summary": "result"},
            sender="supervisor",
        ))

        ctx = _make_ctx(ipc_client=mock_ipc)
        cap = BossRequestCapability(ctx)
        await cap.setup()

        await cap.request_research("test")

        # Verify timeout parameter
        call_kwargs = mock_ipc.send.call_args.kwargs
        assert call_kwargs.get("timeout", 60.0) == 60.0

    @pytest.mark.asyncio
    async def test_teardown_is_noop(self):
        """teardown() does not raise."""
        ctx = _make_ctx()
        cap = BossRequestCapability(ctx)
        await cap.setup()
        await cap.teardown()  # Should not raise
