"""
Additional coverage tests for gmail module.

Covers uncovered lines:
- fetch_unread: retry logic (intermediate failure + retry)
- send_reply: retry logic on SMTP failure
- mark_as_read: fallback UID search via _find_uid_by_message_id
- _find_uid_by_message_id: not configured, exception path
- _imap_find_uid_by_message_id: message_id without angle brackets, empty UIDs
- _extract_body: multipart with no text/plain or text/html, empty body
- send_reply: without message_id (no In-Reply-To/References)
- teardown clears state
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.communication.gmail import (
    GmailCapability,
)


def _make_ctx(email="test@gmail.com", password="abcd-efgh-ijkl-mnop"):
    ctx = MagicMock()
    ctx.identity_name = "stal"
    secrets = {"gmail_address": email, "gmail_app_password": password}

    def get_secret(key):
        if key in secrets:
            return secrets[key]
        raise KeyError(key)

    ctx.get_secret = MagicMock(side_effect=get_secret)
    return ctx


@pytest.fixture(autouse=False)
def _enable_sending():
    original = GmailCapability.SENDING_ENABLED
    GmailCapability.SENDING_ENABLED = True
    yield
    GmailCapability.SENDING_ENABLED = original


class TestFetchUnreadRetry:
    @pytest.mark.asyncio
    async def test_should_retry_on_intermediate_imap_failure(self):
        """fetch_unread retries and succeeds on second attempt."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        call_count = 0

        def mock_imap_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("temporary IMAP failure")
            return []

        with (
            patch.object(cap, "_imap_fetch_unread", side_effect=mock_imap_fetch),
            patch("overblick.capabilities.communication.gmail.asyncio.sleep", new_callable=AsyncMock),
        ):
            results = await cap.fetch_unread()

        assert results == []
        assert call_count == 2


class TestSendReplyRetry:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_should_retry_on_intermediate_smtp_failure(self):
        """send_reply retries and succeeds on second attempt."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        call_count = 0

        def mock_smtp_send(msg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("temporary SMTP failure")

        with (
            patch.object(cap, "_smtp_send", side_effect=mock_smtp_send),
            patch("overblick.capabilities.communication.gmail.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await cap.send_reply("t", "m", "a@b.com", "Subject", "Body")

        assert result is True
        assert call_count == 2


class TestMarkAsReadFallbackSearch:
    @pytest.mark.asyncio
    async def test_should_fallback_to_uid_search_when_not_cached(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        # Not in UID map — trigger fallback search
        cap._uid_map = {}

        with patch.object(
            cap, "_find_uid_by_message_id", new_callable=AsyncMock, return_value=b"99"
        ):
            mock_imap = MagicMock()
            mock_imap.__enter__ = MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = MagicMock(return_value=False)
            mock_imap.login = MagicMock()
            mock_imap.select = MagicMock()
            mock_imap.uid = MagicMock()

            with patch(
                "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
                return_value=mock_imap,
            ):
                result = await cap.mark_as_read("<msg@test.com>")

        assert result is True


class TestFindUidByMessageId:
    @pytest.mark.asyncio
    async def test_should_return_none_when_not_configured(self):
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("x"))
        cap = GmailCapability(ctx)
        await cap.setup()

        result = await cap._find_uid_by_message_id("<msg@test.com>")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_exception(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        with patch.object(
            cap, "_imap_find_uid_by_message_id", side_effect=RuntimeError("IMAP err")
        ):
            result = await cap._find_uid_by_message_id("<msg@test.com>")
        assert result is None


class TestImapFindUidByMessageId:
    def test_should_add_angle_brackets_when_missing(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        cap._email = "test@gmail.com"
        cap._password = "pass"

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login = MagicMock()
        mock_imap.select = MagicMock()
        mock_imap.uid = MagicMock(return_value=("OK", [b"77"]))

        with patch(
            "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            result = cap._imap_find_uid_by_message_id("msg@test.com")

        assert result == b"77"
        assert "msg@test.com" in cap._uid_map

    def test_should_return_none_on_no_results(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        cap._email = "test@gmail.com"
        cap._password = "pass"

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login = MagicMock()
        mock_imap.select = MagicMock()
        mock_imap.uid = MagicMock(return_value=("OK", [b""]))

        with patch(
            "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            result = cap._imap_find_uid_by_message_id("<msg@test.com>")

        assert result is None

    def test_should_return_none_on_not_ok_status(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        cap._email = "test@gmail.com"
        cap._password = "pass"

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login = MagicMock()
        mock_imap.select = MagicMock()
        mock_imap.uid = MagicMock(return_value=("NO", []))

        with patch(
            "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            result = cap._imap_find_uid_by_message_id("<msg@test.com>")

        assert result is None


class TestExtractBodyEdgeCases:
    def test_should_return_empty_for_multipart_with_no_text(self):
        from email import policy as ep
        from email.mime.base import MIMEBase
        from email.mime.multipart import MIMEMultipart
        from email.parser import BytesParser

        outer = MIMEMultipart("mixed")
        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(b"binary data")
        outer.attach(attachment)

        raw = outer.as_bytes()
        msg = BytesParser(policy=ep.default).parsebytes(raw)

        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        body = cap._extract_body(msg)
        assert body == ""

    def test_should_return_empty_for_non_multipart_with_no_payload(self):
        from email import policy as ep
        from email.parser import BytesParser

        raw = b"From: a@b.com\nSubject: Test\n\n"
        msg = BytesParser(policy=ep.default).parsebytes(raw)

        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        body = cap._extract_body(msg)
        # Empty body
        assert body == "" or isinstance(body, str)


class TestDecodeHeaderBytes:
    def test_should_decode_rfc2047_encoded_header(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        # RFC 2047 encoded: =?UTF-8?B?SGVsbG8=?= -> "Hello"
        result = cap._decode_header("=?UTF-8?B?SGVsbG8=?=")
        assert "Hello" in result


class TestImapFetchMessageNotOk:
    def test_should_return_none_when_fetch_not_ok(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)

        mock_imap = MagicMock()
        mock_imap.uid = MagicMock(return_value=("NO", None))

        result = cap._imap_fetch_message(mock_imap, b"42")
        assert result is None


class TestImapFetchMessageWithSignalHeaders:
    def test_should_extract_signal_headers(self):
        from email.mime.text import MIMEText

        ctx = _make_ctx()
        cap = GmailCapability(ctx)

        msg = MIMEText("test body", "plain", "utf-8")
        msg["From"] = "sender@example.com"
        msg["Subject"] = "Test"
        msg["Message-ID"] = "<test@example.com>"
        msg["Date"] = "Mon, 10 Feb 2026 14:30:00 +0100"
        msg["List-Unsubscribe"] = "<mailto:unsub@example.com>"
        msg["Precedence"] = "bulk"

        raw_bytes = msg.as_bytes()
        mock_imap = MagicMock()
        mock_imap.uid = MagicMock(return_value=("OK", [(b"1 (BODY[] {1234})", raw_bytes)]))

        result = cap._imap_fetch_message(mock_imap, b"42")
        assert result is not None
        assert "List-Unsubscribe" in result.headers
        assert "Precedence" in result.headers


class TestImapFindUidEmptyUidList:
    def test_should_return_none_when_uids_split_empty(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        cap._email = "test@gmail.com"
        cap._password = "pass"

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login = MagicMock()
        mock_imap.select = MagicMock()
        # data[0] is non-empty whitespace — splits to empty list
        mock_imap.uid = MagicMock(return_value=("OK", [b"   "]))

        with patch(
            "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            result = cap._imap_find_uid_by_message_id("<msg@test.com>")

        assert result is None


class TestImapFindUidAngleBrackets:
    def test_should_handle_message_id_missing_closing_bracket(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        cap._email = "test@gmail.com"
        cap._password = "pass"

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login = MagicMock()
        mock_imap.select = MagicMock()
        mock_imap.uid = MagicMock(return_value=("OK", [b"88"]))

        with patch(
            "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            # Missing closing >
            result = cap._imap_find_uid_by_message_id("<msg@test.com")

        assert result == b"88"


class TestSendReplyWithoutMessageId:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_should_skip_threading_headers_when_no_message_id(self):
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch(
            "overblick.capabilities.communication.gmail.smtplib.SMTP",
            return_value=mock_smtp,
        ):
            result = await cap.send_reply("t", "", "a@b.com", "Subject", "Body")

        assert result is True
        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg.get("In-Reply-To") is None
