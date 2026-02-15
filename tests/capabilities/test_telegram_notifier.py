"""
Tests for the Telegram notifier capability.

Verifies:
- Initialization with context and secrets loading
- Notification sending (success, failure, network error)
- Graceful degradation when secrets are missing
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.communication.telegram_notifier import TelegramNotifier


def _make_ctx(bot_token="test-token", chat_id="12345"):
    """Create a mock capability context with Telegram secrets."""
    ctx = MagicMock()
    ctx.identity_name = "stal"

    secrets = {
        "telegram_bot_token": bot_token,
        "telegram_chat_id": chat_id,
    }

    def get_secret(key):
        if key in secrets and secrets[key]:
            return secrets[key]
        raise KeyError(f"Secret not found: {key}")

    ctx.get_secret = MagicMock(side_effect=get_secret)
    return ctx


class TestTelegramNotifierSetup:
    """Test initialization and secrets loading."""

    @pytest.mark.asyncio
    async def test_setup_loads_secrets(self):
        """setup() loads bot token and chat ID from secrets."""
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        assert notifier._bot_token == "test-token"
        assert notifier._chat_id == "12345"
        assert notifier._base_url == "https://api.telegram.org/bottest-token"
        assert notifier.configured is True

    @pytest.mark.asyncio
    async def test_setup_missing_secrets_disables(self):
        """setup() disables notifier when secrets are missing."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("not found"))

        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        assert notifier.configured is False

    def test_name(self):
        """Capability name is set correctly."""
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        assert notifier.name == "telegram_notifier"


class TestTelegramNotifierSend:
    """Test notification sending."""

    @pytest.mark.asyncio
    async def test_send_notification_success(self):
        """send_notification() returns True on HTTP 200."""
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        mock_response = AsyncMock()
        mock_response.status = 200

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False),
        )):
            result = await notifier.send_notification("Test message")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_notification_failure(self):
        """send_notification() returns False on HTTP error."""
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.text = AsyncMock(return_value="Forbidden")

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False),
        )):
            result = await notifier.send_notification("Test message")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_network_error(self):
        """send_notification() returns False on network error."""
        import aiohttp

        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        with patch("aiohttp.ClientSession", side_effect=aiohttp.ClientError("Connection refused")):
            result = await notifier.send_notification("Test message")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_returns_false_when_not_configured(self):
        """send_notification() returns False when secrets are missing."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("not found"))

        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        result = await notifier.send_notification("Test message")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_html_returns_false_when_not_configured(self):
        """send_html() returns False when secrets are missing."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("not found"))

        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        result = await notifier.send_html("<b>Test</b>")
        assert result is False
