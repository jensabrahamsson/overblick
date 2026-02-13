"""Tests for GatewayClient with per-request priority."""

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from overblick.core.llm.gateway_client import GatewayClient


class TestGatewayClient:
    """Tests for GatewayClient priority handling."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock aiohttp session with proper post response."""
        session = MagicMock()
        session.closed = False  # Prevent _ensure_session from replacing it

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
            "model": "qwen3:8b",
            "usage": {"total_tokens": 10},
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=mock_response)
        return session

    def _make_client(self, session, default_priority="low"):
        client = GatewayClient(
            base_url="http://127.0.0.1:8200",
            model="qwen3:8b",
            default_priority=default_priority,
        )
        client._session = session
        return client

    async def test_per_request_high_priority(self, mock_session):
        """Priority can be set per-request, overriding default."""
        client = self._make_client(mock_session)

        await client.chat(
            messages=[{"role": "user", "content": "Urgent!"}],
            priority="high",
        )

        url = mock_session.post.call_args[0][0]
        assert "priority=high" in url

    async def test_per_request_low_priority(self, mock_session):
        """Explicit low priority in request."""
        client = self._make_client(mock_session)

        await client.chat(
            messages=[{"role": "user", "content": "Background task"}],
            priority="low",
        )

        url = mock_session.post.call_args[0][0]
        assert "priority=low" in url

    async def test_default_priority_used(self, mock_session):
        """When no priority specified, default_priority is used."""
        client = self._make_client(mock_session, default_priority="low")

        await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        url = mock_session.post.call_args[0][0]
        assert "priority=low" in url

    async def test_default_priority_high(self, mock_session):
        """Client with high default priority."""
        client = self._make_client(mock_session, default_priority="high")

        await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        url = mock_session.post.call_args[0][0]
        assert "priority=high" in url

    async def test_per_request_overrides_default(self, mock_session):
        """Per-request priority overrides client default."""
        client = self._make_client(mock_session, default_priority="high")

        await client.chat(
            messages=[{"role": "user", "content": "Low priority task"}],
            priority="low",
        )

        url = mock_session.post.call_args[0][0]
        assert "priority=low" in url
