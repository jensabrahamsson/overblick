"""
Additional Telegram plugin tests to achieve 100% line coverage.

Covers:
- tick() success and error paths (lines 197-205)
- _poll_updates() full flow: session missing, non-200, not-ok, empty/non-empty results (209-237)
- _handle_conversation() with shared tracker (340-341, 377)
- _send_message() all branches: no session, reply_to, non-200 retry success/fail, exception (392, 401, 408-409, 415-416, 419-422)
- _build_system_prompt() FileNotFoundError fallback (429-430)
- teardown() with active polling task (472)
"""

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.telegram.plugin import (
    ConversationContext,
    TelegramPlugin,
)
from tests.plugins.telegram.conftest import make_update


# ---------------------------------------------------------------------------
# tick() coverage — lines 197-205
# ---------------------------------------------------------------------------


class TestTickCoverage:
    """Test tick() success and error paths."""

    @pytest.mark.asyncio
    async def test_tick_processes_updates(self, telegram_plugin):
        """tick() calls _poll_updates and _handle_update for each update."""
        updates = [make_update("Hello"), make_update("World")]
        with (
            patch.object(
                telegram_plugin, "_poll_updates", new_callable=AsyncMock, return_value=updates
            ),
            patch.object(telegram_plugin, "_handle_update", new_callable=AsyncMock) as mock_handle,
            patch.object(telegram_plugin, "_send_message", new_callable=AsyncMock),
        ):
            await telegram_plugin.tick()
            assert mock_handle.call_count == 2

    @pytest.mark.asyncio
    async def test_tick_error_increments_error_counter(self, telegram_plugin):
        """tick() catches exceptions and increments error counter."""
        initial = telegram_plugin._errors
        with patch.object(
            telegram_plugin,
            "_poll_updates",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Network error"),
        ):
            await telegram_plugin.tick()
        assert telegram_plugin._errors == initial + 1


# ---------------------------------------------------------------------------
# _poll_updates() coverage — lines 209-237
# ---------------------------------------------------------------------------


class TestPollUpdates:
    """Test _poll_updates() branches."""

    @pytest.mark.asyncio
    async def test_poll_no_session_returns_empty(self, telegram_plugin):
        """Returns empty list when session is None."""
        telegram_plugin._session = None
        result = await telegram_plugin._poll_updates()
        assert result == []

    @pytest.mark.asyncio
    async def test_poll_non_200_returns_empty(self, telegram_plugin):
        """Returns empty list on non-200 response."""
        mock_resp = AsyncMock()
        mock_resp.status = 500

        @asynccontextmanager
        async def _mock_get(*args, **kwargs):
            yield mock_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.get = _mock_get
        result = await telegram_plugin._poll_updates()
        assert result == []

    @pytest.mark.asyncio
    async def test_poll_not_ok_returns_empty(self, telegram_plugin):
        """Returns empty list when API response ok=false."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": False, "description": "Bad Request"})

        @asynccontextmanager
        async def _mock_get(*args, **kwargs):
            yield mock_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.get = _mock_get
        result = await telegram_plugin._poll_updates()
        assert result == []

    @pytest.mark.asyncio
    async def test_poll_returns_updates_and_tracks_id(self, telegram_plugin):
        """Returns updates and tracks last update_id."""
        updates_data = [
            {"update_id": 10, "message": {"text": "hi"}},
            {"update_id": 12, "message": {"text": "bye"}},
        ]
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True, "result": updates_data})

        @asynccontextmanager
        async def _mock_get(*args, **kwargs):
            yield mock_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.get = _mock_get
        result = await telegram_plugin._poll_updates()
        assert len(result) == 2
        assert telegram_plugin._last_update_id == 12

    @pytest.mark.asyncio
    async def test_poll_empty_results(self, telegram_plugin):
        """Returns empty list with no updates and doesn't update last_update_id."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True, "result": []})

        @asynccontextmanager
        async def _mock_get(*args, **kwargs):
            yield mock_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.get = _mock_get
        old_id = telegram_plugin._last_update_id
        result = await telegram_plugin._poll_updates()
        assert result == []
        assert telegram_plugin._last_update_id == old_id


# ---------------------------------------------------------------------------
# _handle_conversation() with shared tracker — lines 340-341, 377
# ---------------------------------------------------------------------------


class TestSharedTracker:
    """Test conversation handling with shared conversation tracker capability."""

    @pytest.mark.asyncio
    async def test_uses_shared_tracker(self, telegram_plugin):
        """When conversation_tracker capability is present, uses it instead of local context."""
        tracker = MagicMock()
        tracker.add_user_message = MagicMock()
        tracker.get_messages = MagicMock(
            return_value=[
                {"role": "system", "content": "prompt"},
                {"role": "user", "content": "hi"},
            ]
        )
        tracker.add_assistant_message = MagicMock()

        telegram_plugin.ctx.capabilities = {"conversation_tracker": tracker}

        telegram_plugin.ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Reply from bot")
        )

        with patch.object(telegram_plugin, "_send_message", new_callable=AsyncMock):
            await telegram_plugin._handle_conversation(123, 456, "user", "hello", 1)

        tracker.add_user_message.assert_called_once()
        tracker.get_messages.assert_called_once()
        tracker.add_assistant_message.assert_called_once_with("123", "Reply from bot")


# ---------------------------------------------------------------------------
# _send_message() coverage — lines 392, 401, 408-409, 415-416, 419-422
# ---------------------------------------------------------------------------


class TestSendMessage:
    """Test _send_message() all branches."""

    @pytest.mark.asyncio
    async def test_send_no_session(self, telegram_plugin):
        """Returns False when session is None."""
        telegram_plugin._session = None
        result = await telegram_plugin._send_message(123, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_with_reply_to(self, telegram_plugin):
        """Sets reply_to_message_id in payload when reply_to is given."""
        mock_resp = AsyncMock()
        mock_resp.status = 200

        @asynccontextmanager
        async def _mock_post(url, json=None, **kwargs):
            # Verify reply_to is in payload
            assert json.get("reply_to_message_id") == 42
            yield mock_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.post = _mock_post
        result = await telegram_plugin._send_message(123, "test", reply_to=42)
        assert result is True
        assert telegram_plugin._messages_sent >= 1

    @pytest.mark.asyncio
    async def test_send_200_success(self, telegram_plugin):
        """Returns True on 200 response and increments sent counter."""
        initial_sent = telegram_plugin._messages_sent
        mock_resp = AsyncMock()
        mock_resp.status = 200

        @asynccontextmanager
        async def _mock_post(url, json=None, **kwargs):
            yield mock_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.post = _mock_post
        result = await telegram_plugin._send_message(123, "test")
        assert result is True
        assert telegram_plugin._messages_sent == initial_sent + 1

    @pytest.mark.asyncio
    async def test_send_non200_retry_succeeds(self, telegram_plugin):
        """When first send fails (non-200), retries without Markdown and succeeds."""
        initial_sent = telegram_plugin._messages_sent
        first_resp = AsyncMock()
        first_resp.status = 400

        retry_resp = AsyncMock()
        retry_resp.status = 200

        call_count = 0

        @asynccontextmanager
        async def _mock_post(url, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield first_resp
            else:
                # Verify parse_mode removed on retry
                assert "parse_mode" not in json
                yield retry_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.post = _mock_post
        result = await telegram_plugin._send_message(123, "test")
        assert result is True
        assert telegram_plugin._messages_sent == initial_sent + 1

    @pytest.mark.asyncio
    async def test_send_non200_retry_fails(self, telegram_plugin):
        """When first send and retry both fail, returns False."""
        first_resp = AsyncMock()
        first_resp.status = 400

        retry_resp = AsyncMock()
        retry_resp.status = 500

        call_count = 0

        @asynccontextmanager
        async def _mock_post(url, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield first_resp
            else:
                yield retry_resp

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.post = _mock_post
        result = await telegram_plugin._send_message(123, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_exception_increments_errors(self, telegram_plugin):
        """Exception during send increments error counter and returns False."""
        initial_errors = telegram_plugin._errors

        @asynccontextmanager
        async def _mock_post(url, json=None, **kwargs):
            raise ConnectionError("Connection lost")
            yield  # pragma: no cover

        telegram_plugin._session = MagicMock()
        telegram_plugin._session.post = _mock_post
        result = await telegram_plugin._send_message(123, "test")
        assert result is False
        assert telegram_plugin._errors == initial_errors + 1


# ---------------------------------------------------------------------------
# _build_system_prompt() fallback — lines 429-430
# ---------------------------------------------------------------------------


class TestBuildSystemPromptFallback:
    """Test _build_system_prompt() when personality file not found."""

    def test_fallback_prompt_on_file_not_found(self, telegram_context):
        """Returns fallback prompt when load_identity raises FileNotFoundError."""
        # Patch the identities.load_identity that PluginContext.load_identity delegates to
        with patch(
            "overblick.identities.load_identity", side_effect=FileNotFoundError("not found")
        ):
            telegram_context._secrets_getter = lambda key: {
                "telegram_bot_token": "test-bot-token-123",
            }.get(key)
            plugin = TelegramPlugin(telegram_context)
            # _build_system_prompt is called during setup, but we want to test the fallback
            result = plugin._build_system_prompt(telegram_context.identity)
        assert "Blixt" in result
        assert "Telegram" in result


# ---------------------------------------------------------------------------
# teardown() with polling task — line 472
# ---------------------------------------------------------------------------


class TestTeardownPollingTask:
    """Test teardown with an active polling task."""

    @pytest.mark.asyncio
    async def test_teardown_cancels_polling_task(self, telegram_plugin):
        """teardown() cancels an active polling task."""

        async def _dummy():
            await asyncio.sleep(3600)

        task = asyncio.ensure_future(_dummy())
        telegram_plugin._polling_task = task
        assert not task.done()

        await telegram_plugin.teardown()
        assert task.cancelled()
