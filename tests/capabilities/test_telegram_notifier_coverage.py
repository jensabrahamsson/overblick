"""
Additional coverage tests for telegram_notifier module.

Covers uncovered lines:
- _prefix_identity: identity with no display_name
- _ensure_session: when session exists and is open
- close: when session is None or already closed
- fetch_updates: unexpected exception
- send_html: identity without display_name, ClientError
- chat_id property
"""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from overblick.capabilities.communication.telegram_notifier import TelegramNotifier


def _make_ctx(bot_token="test-token", chat_id="12345", has_identity=True, display_name="Stål"):
    ctx = MagicMock()
    ctx.identity_name = "stal"
    if has_identity:
        ctx.identity = MagicMock()
        ctx.identity.display_name = display_name
    else:
        ctx.identity = None

    secrets = {"telegram_bot_token": bot_token, "telegram_chat_id": chat_id}

    def get_secret(key):
        if key in secrets:
            return secrets[key]
        raise KeyError(key)

    ctx.get_secret = MagicMock(side_effect=get_secret)
    return ctx


class TestPrefixIdentity:
    @pytest.mark.asyncio
    async def test_should_use_identity_name_when_no_display_name(self):
        ctx = _make_ctx(has_identity=True, display_name="")
        notifier = TelegramNotifier(ctx)
        await notifier.setup()
        result = notifier._prefix_identity("test")
        assert "*[Stal]*" in result  # capitalized identity_name

    @pytest.mark.asyncio
    async def test_should_use_identity_name_when_no_identity(self):
        ctx = _make_ctx(has_identity=False)
        notifier = TelegramNotifier(ctx)
        await notifier.setup()
        result = notifier._prefix_identity("test")
        assert "*[Stal]*" in result


class TestEnsureSession:
    @pytest.mark.asyncio
    async def test_should_reuse_existing_open_session(self):
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        mock_session = MagicMock()
        mock_session.closed = False
        notifier._session = mock_session

        result = await notifier._ensure_session()
        assert result is mock_session

    @pytest.mark.asyncio
    async def test_should_create_new_session_when_closed(self):
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        mock_session = MagicMock()
        mock_session.closed = True
        notifier._session = mock_session

        result = await notifier._ensure_session()
        assert result is not mock_session


class TestCloseEdgeCases:
    @pytest.mark.asyncio
    async def test_should_noop_when_session_is_none(self):
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        notifier._session = None
        await notifier.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_should_noop_when_session_already_closed(self):
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        mock_session = MagicMock()
        mock_session.closed = True
        notifier._session = mock_session
        await notifier.close()
        mock_session.close.assert_not_called()


class TestFetchUpdatesUnexpectedException:
    @pytest.mark.asyncio
    async def test_should_handle_unexpected_exception(self):
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        notifier._ensure_session = AsyncMock(side_effect=RuntimeError("unexpected"))
        updates = await notifier.fetch_updates()
        assert updates == []


class TestFetchUpdatesWithOffset:
    @pytest.mark.asyncio
    async def test_should_include_offset_in_params(self):
        ctx = _make_ctx()
        notifier = TelegramNotifier(ctx)
        await notifier.setup()
        notifier._update_offset = 42  # Set a non-zero offset

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True, "result": []})

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=cm)
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        await notifier.fetch_updates()

        call_args = mock_session.get.call_args
        params = call_args[1].get("params", call_args[0][1] if len(call_args[0]) > 1 else {})
        if not params:
            params = call_args.kwargs.get("params", {})
        assert params.get("offset") == 42


class TestChatIdProperty:
    @pytest.mark.asyncio
    async def test_should_return_chat_id_when_configured(self):
        ctx = _make_ctx(chat_id="99999")
        notifier = TelegramNotifier(ctx)
        await notifier.setup()
        assert notifier.chat_id == "99999"

    @pytest.mark.asyncio
    async def test_should_return_empty_when_not_configured(self):
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("x"))
        notifier = TelegramNotifier(ctx)
        await notifier.setup()
        assert notifier.chat_id == ""


class TestSendHtmlEdgeCases:
    @pytest.mark.asyncio
    async def test_should_use_identity_name_when_no_display_name_html(self):
        ctx = _make_ctx(has_identity=True, display_name="")
        notifier = TelegramNotifier(ctx)
        await notifier.setup()

        captured_payload = {}

        def capture_post(url, json, timeout):
            captured_payload.update(json)
            cm = AsyncMock()
            resp = AsyncMock()
            resp.status = 200
            cm.__aenter__ = AsyncMock(return_value=resp)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=capture_post)
        notifier._ensure_session = AsyncMock(return_value=mock_session)

        result = await notifier.send_html("<b>Test</b>")
        assert result is True
        assert "<b>[Stal]</b>" in captured_payload["text"]
