"""
Tests for the Telegram notifier capability.

Verifies:
- Initialization with context and secrets loading
- Notification sending (success, failure, network error)
- Tracked notification sending (returns message_id)
- Fetch updates (offset tracking, chat filtering, bot filtering, owner filtering)
- Graceful degradation when secrets are missing
- Persistent session lifecycle (close)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.communication.telegram_notifier import (
    TelegramNotifier,
    TelegramUpdate,
)


def _make_ctx(bot_token="test-token", chat_id="12345", owner_id=None):
    """Create a mock capability context with Telegram secrets."""
    ctx = MagicMock()
    ctx.identity_name = "stal"
    ctx.identity.display_name = "Stål"

    secrets = {
        "telegram_bot_token": bot_token,
        "telegram_chat_id": chat_id,
    }
    if owner_id:
        secrets["telegram_owner_id"] = owner_id

    def get_secret(key):
        if key in secrets and secrets[key]:
            return secrets[key]
        raise KeyError(f"Secret not found: {key}")

    ctx.get_secret = MagicMock(side_effect=get_secret)
    return ctx


def _make_mock_session():
    """Create a mock aiohttp session that supports async context manager on .post/.get."""
    return MagicMock()


def _mock_response(status=200, json_data=None, text_data=""):
    """Create a mock HTTP response as async context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.text = AsyncMock(return_value=text_data)
    # Make it work as async context manager
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _setup_notifier(ctx=None):
    """Create and set up a notifier with a mock session."""
    if ctx is None:
        ctx = _make_ctx()
    notifier = TelegramNotifier(ctx)
    await notifier.setup()
    return notifier


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
        assert notifier._owner_id == "12345"  # Falls back to chat_id
        assert notifier._base_url == "https://api.telegram.org/bottest-token"
        assert notifier.configured is True

    @pytest.mark.asyncio
    async def test_setup_loads_explicit_owner_id(self):
        """setup() loads explicit owner ID from secrets."""
        ctx = _make_ctx(owner_id="67890")
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        assert notifier._chat_id == "12345"
        assert notifier._owner_id == "67890"

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

    @pytest.mark.asyncio
    async def test_update_offset_starts_at_zero(self):
        """Update offset starts at zero."""
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()
        assert notifier._update_offset == 0

    @pytest.mark.asyncio
    async def test_session_starts_none(self):
        """Session is None before first use."""
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        assert notifier._session is None

    @pytest.mark.asyncio
    async def test_close_session(self):
        """close() closes the persistent session."""
        notifier = await _setup_notifier()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        notifier._session = mock_session

        await notifier.close()
        mock_session.close.assert_awaited_once()
        assert notifier._session is None


class TestTelegramNotifierSend:
    """Test notification sending."""

    @pytest.mark.asyncio
    async def test_send_notification_success(self):
        """send_notification() returns True on HTTP 200."""
        notifier = await _setup_notifier()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_mock_response(200))
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        result = await notifier.send_notification("Test message")
        assert result is True

    @pytest.mark.asyncio
    async def test_send_notification_failure(self):
        """send_notification() returns False on HTTP error."""
        notifier = await _setup_notifier()

        mock_session = MagicMock()
        mock_session.post = MagicMock(
            return_value=_mock_response(403, text_data="Forbidden")
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        result = await notifier.send_notification("Test message")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_network_error(self):
        """send_notification() returns False on network error."""
        import aiohttp

        notifier = await _setup_notifier()
        notifier._ensure_session = AsyncMock(
            side_effect=aiohttp.ClientError("Connection refused")
        )

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


class TestTelegramNotifierTracked:
    """Test tracked notification sending."""

    @pytest.mark.asyncio
    async def test_send_tracked_returns_message_id(self):
        """send_notification_tracked() returns Telegram message_id on success."""
        notifier = await _setup_notifier()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_mock_response(
            200, json_data={"ok": True, "result": {"message_id": 42}},
        ))
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        result = await notifier.send_notification_tracked("Test", ref_id="email-1")
        assert result == 42

    @pytest.mark.asyncio
    async def test_send_tracked_returns_none_on_failure(self):
        """send_notification_tracked() returns None on HTTP error."""
        notifier = await _setup_notifier()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_mock_response(
            500, text_data="Internal Server Error",
        ))
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        result = await notifier.send_notification_tracked("Test")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_tracked_not_configured(self):
        """send_notification_tracked() returns None when not configured."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("not found"))

        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        result = await notifier.send_notification_tracked("Test")
        assert result is None


class TestTelegramNotifierFetchUpdates:
    """Test message receiving via getUpdates."""

    @pytest.mark.asyncio
    async def test_fetch_updates_returns_messages(self):
        """fetch_updates() returns messages from configured chat."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 50,
                        "text": "Great notification!",
                        "chat": {"id": 12345},
                        "from": {"id": 12345, "is_bot": False},
                        "date": 1700000000,
                        "reply_to_message": {"message_id": 42},
                    },
                },
            ],
        }

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data=api_response)
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()

        assert len(updates) == 1
        assert updates[0].text == "Great notification!"
        assert updates[0].message_id == 50
        assert updates[0].reply_to_message_id == 42

    @pytest.mark.asyncio
    async def test_fetch_updates_advances_offset(self):
        """fetch_updates() advances the offset past processed updates."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 50,
                        "text": "Hello",
                        "chat": {"id": 12345},
                        "from": {"id": 12345, "is_bot": False},
                        "date": 1700000000,
                    },
                },
                {
                    "update_id": 103,
                    "message": {
                        "message_id": 51,
                        "text": "World",
                        "chat": {"id": 12345},
                        "from": {"id": 12345, "is_bot": False},
                        "date": 1700000001,
                    },
                },
            ],
        }

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data=api_response)
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        await notifier.fetch_updates()

        # Offset should be max(update_id) + 1
        assert notifier._update_offset == 104

    @pytest.mark.asyncio
    async def test_fetch_updates_filters_other_chats(self):
        """fetch_updates() ignores messages from other chats."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 50,
                        "text": "Wrong chat",
                        "chat": {"id": 99999},  # Different chat
                        "from": {"id": 12345, "is_bot": False},
                        "date": 1700000000,
                    },
                },
            ],
        }

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data=api_response)
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert len(updates) == 0

    @pytest.mark.asyncio
    async def test_fetch_updates_filters_bot_messages(self):
        """fetch_updates() ignores messages from bots (including our own)."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 50,
                        "text": "I am a bot",
                        "chat": {"id": 12345},
                        "from": {"id": 888, "is_bot": True},
                        "date": 1700000000,
                    },
                },
            ],
        }

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data=api_response)
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert len(updates) == 0

    @pytest.mark.asyncio
    async def test_fetch_updates_filters_non_owner_messages(self):
        """fetch_updates() ignores messages from users other than the owner."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345", owner_id="12345"))

        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 50,
                        "text": "I'm not the owner",
                        "chat": {"id": 12345},
                        "from": {"id": 77777, "is_bot": False},  # Not the owner
                        "date": 1700000000,
                    },
                },
            ],
        }

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data=api_response)
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert len(updates) == 0

    @pytest.mark.asyncio
    async def test_fetch_updates_not_configured(self):
        """fetch_updates() returns empty list when not configured."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("not found"))

        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        updates = await notifier.fetch_updates()
        assert updates == []

    @pytest.mark.asyncio
    async def test_fetch_updates_empty_result(self):
        """fetch_updates() handles empty results gracefully."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data={"ok": True, "result": []})
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert updates == []

    @pytest.mark.asyncio
    async def test_fetch_updates_api_error(self):
        """fetch_updates() returns empty list on HTTP error from Telegram API."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_mock_response(502))
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert updates == []

    @pytest.mark.asyncio
    async def test_fetch_updates_not_ok_response(self):
        """fetch_updates() returns empty list when API response ok=false."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_mock_response(
            200, json_data={"ok": False, "description": "Unauthorized"},
        ))
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert updates == []

    @pytest.mark.asyncio
    async def test_fetch_updates_network_error(self):
        """fetch_updates() returns empty list on network error."""
        import aiohttp

        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))
        notifier._ensure_session = AsyncMock(
            side_effect=aiohttp.ClientError("timeout")
        )

        updates = await notifier.fetch_updates()
        assert updates == []

    @pytest.mark.asyncio
    async def test_fetch_updates_skips_empty_text_messages(self):
        """fetch_updates() skips messages with empty text."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 50,
                        "text": "",
                        "chat": {"id": 12345},
                        "from": {"id": 12345, "is_bot": False},
                        "date": 1700000000,
                    },
                },
            ],
        }

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data=api_response)
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert len(updates) == 0

    @pytest.mark.asyncio
    async def test_fetch_updates_skips_updates_without_message(self):
        """fetch_updates() skips updates that have no message field."""
        notifier = await _setup_notifier(_make_ctx(chat_id="12345"))

        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    # No "message" key — could be an edited_message or callback_query
                },
            ],
        }

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=_mock_response(200, json_data=api_response)
        )
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        updates = await notifier.fetch_updates()
        assert len(updates) == 0
        # Offset should still advance past this update
        assert notifier._update_offset == 101


class TestTelegramNotifierSendHtml:
    """Test HTML notification sending."""

    @pytest.mark.asyncio
    async def test_send_html_success(self):
        """send_html() returns True on HTTP 200."""
        notifier = await _setup_notifier()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_mock_response(200))
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        result = await notifier.send_html("<b>Bold</b> message")
        assert result is True

    @pytest.mark.asyncio
    async def test_send_html_failure(self):
        """send_html() returns False on HTTP error."""
        notifier = await _setup_notifier()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_mock_response(400))
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        result = await notifier.send_html("<b>Bold</b>")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_html_network_error(self):
        """send_html() returns False on network error."""
        import aiohttp

        notifier = await _setup_notifier()
        notifier._ensure_session = AsyncMock(
            side_effect=aiohttp.ClientError("fail")
        )

        result = await notifier.send_html("<b>Bold</b>")
        assert result is False


class TestTelegramNotifierPayloads:
    """Test that correct payloads are sent to the Telegram API."""

    @pytest.mark.asyncio
    async def test_send_notification_uses_markdown_parse_mode(self):
        """send_notification() sends Markdown parse_mode in payload."""
        notifier = await _setup_notifier()

        captured_payload = {}

        def capture_post(url, json, timeout):
            captured_payload.update(json)
            return _mock_response(200)

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=capture_post)
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        await notifier.send_notification("*bold* _italic_")

        assert captured_payload["parse_mode"] == "Markdown"
        assert captured_payload["chat_id"] == "12345"
        assert "*bold* _italic_" in captured_payload["text"]
        assert "*[Stål]*" in captured_payload["text"]

    @pytest.mark.asyncio
    async def test_send_html_uses_html_parse_mode(self):
        """send_html() sends HTML parse_mode in payload."""
        notifier = await _setup_notifier()

        captured_payload = {}

        def capture_post(url, json, timeout):
            captured_payload.update(json)
            return _mock_response(200)

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=capture_post)
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        await notifier.send_html("<b>Bold</b>")

        assert captured_payload["parse_mode"] == "HTML"
        assert "<b>Bold</b>" in captured_payload["text"]
        assert "<b>[Stål]</b>" in captured_payload["text"]


class TestTelegramNotifierTrackedNetworkError:
    """Test tracked notification error scenarios."""

    @pytest.mark.asyncio
    async def test_send_tracked_network_error(self):
        """send_notification_tracked() returns None on network error."""
        import aiohttp

        notifier = await _setup_notifier()
        notifier._ensure_session = AsyncMock(
            side_effect=aiohttp.ClientError("timeout")
        )

        result = await notifier.send_notification_tracked("Test", ref_id="ref-1")
        assert result is None


class TestTelegramUpdateModel:
    """Test the TelegramUpdate model."""

    def test_basic_update(self):
        """TelegramUpdate can be created with required fields."""
        update = TelegramUpdate(message_id=1, text="hello")
        assert update.message_id == 1
        assert update.text == "hello"
        assert update.reply_to_message_id is None

    def test_update_with_reply(self):
        """TelegramUpdate can include reply_to_message_id."""
        update = TelegramUpdate(
            message_id=1, text="reply", reply_to_message_id=42,
        )
        assert update.reply_to_message_id == 42

    def test_update_default_timestamp(self):
        """TelegramUpdate has empty timestamp by default."""
        update = TelegramUpdate(message_id=1, text="hello")
        assert update.timestamp == ""

    def test_update_with_timestamp(self):
        """TelegramUpdate can include a timestamp."""
        update = TelegramUpdate(
            message_id=1, text="hello", timestamp="1700000000",
        )
        assert update.timestamp == "1700000000"
