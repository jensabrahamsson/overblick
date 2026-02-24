"""
Tests for the Gmail capability (IMAP/SMTP via App Password).

Verifies:
- Initialization with context and secrets loading
- IMAP fetch_unread (mocked imaplib)
- SMTP send_reply with threading headers (mocked smtplib)
- IMAP mark_as_read (mocked imaplib)
- Email body extraction (plain, multipart, HTML fallback)
- Graceful degradation when credentials missing
"""

from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch, call

import pytest

from overblick.capabilities.communication.gmail import (
    GmailCapability,
    GmailMessage,
    GMAIL_IMAP_HOST,
    GMAIL_IMAP_PORT,
    GMAIL_SMTP_HOST,
    GMAIL_SMTP_PORT,
)


def _make_ctx(email="test@gmail.com", password="abcd-efgh-ijkl-mnop"):
    """Create a mock capability context with Gmail secrets."""
    ctx = MagicMock()
    ctx.identity_name = "stal"

    secrets = {
        "gmail_address": email,
        "gmail_app_password": password,
    }

    def get_secret(key):
        val = secrets.get(key)
        if val:
            return val
        raise KeyError(f"Secret not found: {key}")

    ctx.get_secret = MagicMock(side_effect=get_secret)
    return ctx


def _build_raw_email(
    sender="alice@example.com",
    subject="Test Subject",
    body="Hello, this is a test email.",
    message_id="<test-001@example.com>",
    content_type="text/plain",
):
    """Build a raw RFC 822 email as bytes."""
    msg = MIMEText(body, content_type.split("/")[1], "utf-8")
    msg["From"] = sender
    msg["To"] = "test@gmail.com"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 10 Feb 2026 14:30:00 +0100"
    return msg.as_bytes()


def _build_multipart_email(
    sender="bob@example.com",
    subject="Multipart Test",
    plain_text="Plain text version",
    html_text="<p>HTML version</p>",
    message_id="<test-002@example.com>",
):
    """Build a multipart email with text/plain and text/html parts."""
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = "test@gmail.com"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 10 Feb 2026 15:00:00 +0100"
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_text, "html", "utf-8"))
    return msg.as_bytes()


def _mock_imap(search_uids=None, fetch_data=None):
    """Create a mock IMAP4_SSL instance."""
    imap = MagicMock()
    imap.login = MagicMock(return_value=("OK", [b"LOGIN"]))
    imap.select = MagicMock(return_value=("OK", [b"1"]))
    imap.logout = MagicMock()
    imap.__enter__ = MagicMock(return_value=imap)
    imap.__exit__ = MagicMock(return_value=False)

    if search_uids is not None:
        uid_bytes = b" ".join(search_uids) if search_uids else b""
        imap.uid = MagicMock()

        def uid_handler(cmd, *args):
            if cmd == "search":
                return ("OK", [uid_bytes])
            elif cmd == "fetch":
                uid_arg = args[0]
                if fetch_data and uid_arg in fetch_data:
                    return ("OK", [(b"1 (RFC822 {1234})", fetch_data[uid_arg])])
                return ("OK", None)
            elif cmd == "store":
                return ("OK", [b"1"])
            return ("NO", [])

        imap.uid.side_effect = uid_handler

    return imap


class TestGmailCapabilitySetup:
    """Test initialization and secrets loading."""

    @pytest.mark.asyncio
    async def test_setup_loads_secrets(self):
        """setup() loads email and app password from secrets."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        assert cap._email == "test@gmail.com"
        assert cap._password == "abcd-efgh-ijkl-mnop"
        assert cap.configured is True

    @pytest.mark.asyncio
    async def test_setup_missing_secrets_disables(self):
        """setup() disables capability when secrets are missing."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("not found"))

        cap = GmailCapability(ctx)
        await cap.setup()

        assert cap.configured is False

    def test_name(self):
        """Capability name is set correctly."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        assert cap.name == "gmail"


class TestFetchUnread:
    """Test fetch_unread() with mocked IMAP."""

    @pytest.mark.asyncio
    async def test_fetch_returns_messages(self):
        """fetch_unread() returns parsed GmailMessage list."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email = _build_raw_email(
            sender="alice@example.com",
            subject="Hello",
            body="Test body content.",
            message_id="<msg-001@example.com>",
        )

        mock_imap = _mock_imap(
            search_uids=[b"42"],
            fetch_data={b"42": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread(max_results=5)

        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, GmailMessage)
        assert msg.message_id == "<msg-001@example.com>"
        assert msg.sender == "alice@example.com"
        assert msg.subject == "Hello"
        assert "Test body content." in msg.body

    @pytest.mark.asyncio
    async def test_fetch_empty_inbox(self):
        """fetch_unread() returns empty list when no unread messages."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_imap = _mock_imap(search_uids=[])

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread()

        assert results == []

    @pytest.mark.asyncio
    async def test_fetch_multipart_message(self):
        """fetch_unread() extracts text/plain from multipart emails."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email = _build_multipart_email(
            plain_text="Plain text content here",
            html_text="<p>HTML content here</p>",
        )

        mock_imap = _mock_imap(
            search_uids=[b"99"],
            fetch_data={b"99": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread()

        assert len(results) == 1
        assert results[0].body == "Plain text content here"

    @pytest.mark.asyncio
    async def test_fetch_caches_uid(self):
        """fetch_unread() caches IMAP UID for mark_as_read."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email = _build_raw_email(message_id="<cached@example.com>")
        mock_imap = _mock_imap(
            search_uids=[b"55"],
            fetch_data={b"55": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            await cap.fetch_unread()

        assert "<cached@example.com>" in cap._uid_map
        assert cap._uid_map["<cached@example.com>"] == b"55"

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_when_not_configured(self):
        """fetch_unread() returns empty list when not configured."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("x"))

        cap = GmailCapability(ctx)
        await cap.setup()

        results = await cap.fetch_unread()
        assert results == []

    @pytest.mark.asyncio
    async def test_fetch_handles_imap_error(self):
        """fetch_unread() returns empty list on IMAP error."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        with patch(
            "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
            side_effect=Exception("Connection refused"),
        ):
            results = await cap.fetch_unread()

        assert results == []


class TestSendingKillSwitch:
    """Test the SENDING_ENABLED hard kill switch."""

    def test_sending_disabled_by_default(self):
        """SENDING_ENABLED class constant is False by default."""
        assert GmailCapability.SENDING_ENABLED is False

    @pytest.mark.asyncio
    async def test_send_reply_blocked_when_disabled(self):
        """send_reply() returns False when SENDING_ENABLED is False."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()
        assert cap.configured is True

        result = await cap.send_reply(
            thread_id="<thread@example.com>",
            message_id="<msg@example.com>",
            to="alice@example.com",
            subject="Test",
            body="Should not be sent.",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_send_reply_works_when_enabled(self):
        """send_reply() proceeds to SMTP when SENDING_ENABLED is True."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        # Temporarily enable sending for this test
        original = GmailCapability.SENDING_ENABLED
        try:
            GmailCapability.SENDING_ENABLED = True

            mock_smtp = MagicMock()
            mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp.__exit__ = MagicMock(return_value=False)

            with patch("overblick.capabilities.communication.gmail.smtplib.SMTP", return_value=mock_smtp):
                result = await cap.send_reply(
                    thread_id="<thread@example.com>",
                    message_id="<msg@example.com>",
                    to="alice@example.com",
                    subject="Test",
                    body="This should be sent.",
                )

            assert result is True
            mock_smtp.send_message.assert_called_once()
        finally:
            GmailCapability.SENDING_ENABLED = original


@pytest.fixture(autouse=False)
def _enable_sending():
    """Temporarily enable SENDING_ENABLED for SMTP behavior tests."""
    original = GmailCapability.SENDING_ENABLED
    GmailCapability.SENDING_ENABLED = True
    yield
    GmailCapability.SENDING_ENABLED = original


class TestSendReply:
    """Test send_reply() with mocked SMTP (SENDING_ENABLED=True)."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_send_reply_success(self):
        """send_reply() returns True on successful SMTP send."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("overblick.capabilities.communication.gmail.smtplib.SMTP", return_value=mock_smtp):
            result = await cap.send_reply(
                thread_id="<thread@example.com>",
                message_id="<msg@example.com>",
                to="alice@example.com",
                subject="Meeting",
                body="Thank you for reaching out.",
            )

        assert result is True
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("test@gmail.com", "abcd-efgh-ijkl-mnop")
        mock_smtp.send_message.assert_called_once()

        # Verify threading headers in the sent message
        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg["In-Reply-To"] == "<msg@example.com>"
        assert sent_msg["References"] == "<msg@example.com>"
        assert sent_msg["Subject"] == "Re: Meeting"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_send_reply_adds_re_prefix(self):
        """send_reply() adds Re: prefix when missing."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("overblick.capabilities.communication.gmail.smtplib.SMTP", return_value=mock_smtp):
            await cap.send_reply("t", "m", "a@b.com", "Original Subject", "Body")

        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg["Subject"] == "Re: Original Subject"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_send_reply_preserves_existing_re(self):
        """send_reply() does not double the Re: prefix."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("overblick.capabilities.communication.gmail.smtplib.SMTP", return_value=mock_smtp):
            await cap.send_reply("t", "m", "a@b.com", "Re: Already replied", "Body")

        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg["Subject"] == "Re: Already replied"
        assert "Re: Re:" not in sent_msg["Subject"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_send_reply_smtp_failure(self):
        """send_reply() returns False on SMTP error."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        with patch(
            "overblick.capabilities.communication.gmail.smtplib.SMTP",
            side_effect=Exception("SMTP connection refused"),
        ):
            result = await cap.send_reply("t", "m", "a@b.com", "Test", "Body")

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_send_reply_returns_false_when_not_configured(self):
        """send_reply() returns False when not configured."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("x"))

        cap = GmailCapability(ctx)
        await cap.setup()

        result = await cap.send_reply("t", "m", "a@b.com", "Test", "Body")
        assert result is False


class TestMarkAsRead:
    """Test mark_as_read() with mocked IMAP."""

    @pytest.mark.asyncio
    async def test_mark_as_read_success(self):
        """mark_as_read() sets \\Seen flag via IMAP."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        # Pre-populate UID cache (normally done by fetch_unread)
        cap._uid_map["<msg@example.com>"] = b"42"

        mock_imap = _mock_imap(search_uids=[])
        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            result = await cap.mark_as_read("<msg@example.com>")

        assert result is True
        # Verify IMAP store was called with \\Seen
        mock_imap.uid.assert_called_with("store", b"42", "+FLAGS", "\\Seen")

    @pytest.mark.asyncio
    async def test_mark_as_read_unknown_message(self):
        """mark_as_read() returns False for unknown message_id."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        result = await cap.mark_as_read("<unknown@example.com>")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_as_read_imap_error(self):
        """mark_as_read() returns False on IMAP error."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        cap._uid_map["<msg@example.com>"] = b"42"

        with patch(
            "overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL",
            side_effect=Exception("IMAP error"),
        ):
            result = await cap.mark_as_read("<msg@example.com>")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_as_read_returns_false_when_not_configured(self):
        """mark_as_read() returns False when not configured."""
        ctx = MagicMock()
        ctx.identity_name = "stal"
        ctx.get_secret = MagicMock(side_effect=KeyError("x"))

        cap = GmailCapability(ctx)
        await cap.setup()

        result = await cap.mark_as_read("<msg@example.com>")
        assert result is False


class TestMessageParsing:
    """Test email body extraction and header decoding."""

    def test_extract_plain_text(self):
        """Extracts body from simple text/plain email."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)

        from email.parser import BytesParser
        from email import policy as ep

        raw = _build_raw_email(body="Simple text body.")
        msg = BytesParser(policy=ep.default).parsebytes(raw)
        body = cap._extract_body(msg)

        assert body == "Simple text body."

    def test_extract_multipart_prefers_plain(self):
        """Extracts text/plain from multipart email."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)

        from email.parser import BytesParser
        from email import policy as ep

        raw = _build_multipart_email(
            plain_text="Plain version",
            html_text="<b>HTML version</b>",
        )
        msg = BytesParser(policy=ep.default).parsebytes(raw)
        body = cap._extract_body(msg)

        assert body == "Plain version"

    def test_extract_html_fallback(self):
        """Falls back to text/html when text/plain is missing."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)

        from email.mime.multipart import MIMEMultipart
        from email.parser import BytesParser
        from email import policy as ep

        # Build multipart with only HTML
        outer = MIMEMultipart("alternative")
        outer.attach(MIMEText("<p>Only HTML</p>", "html", "utf-8"))
        raw = outer.as_bytes()

        msg = BytesParser(policy=ep.default).parsebytes(raw)
        body = cap._extract_body(msg)

        assert "Only HTML" in body

    def test_decode_header_plain(self):
        """Decodes a plain ASCII header."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        assert cap._decode_header("Hello World") == "Hello World"

    def test_decode_header_empty(self):
        """Handles empty/None header."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        assert cap._decode_header("") == ""
        assert cap._decode_header(None) == ""


class TestSendAsAlias:
    """Test Gmail send-as alias (sending from a different address)."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_setup_loads_send_as_alias(self):
        """setup() loads optional gmail_send_as secret."""
        ctx = MagicMock()
        ctx.identity_name = "stal"

        secrets = {
            "gmail_address": "login@gmail.com",
            "gmail_app_password": "abcd-efgh-ijkl-mnop",
            "gmail_send_as": "alias@example.com",
        }
        ctx.get_secret = MagicMock(side_effect=lambda key: secrets.get(key) or (_ for _ in ()).throw(KeyError(key)))

        cap = GmailCapability(ctx)
        await cap.setup()

        assert cap._send_as == "alias@example.com"
        assert cap.configured is True

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_send_reply_uses_send_as_from_address(self):
        """send_reply() uses send_as address in From header when set."""
        ctx = MagicMock()
        ctx.identity_name = "stal"

        secrets = {
            "gmail_address": "login@gmail.com",
            "gmail_app_password": "abcd-efgh-ijkl-mnop",
            "gmail_send_as": "alias@example.com",
        }
        ctx.get_secret = MagicMock(side_effect=lambda key: secrets.get(key) or (_ for _ in ()).throw(KeyError(key)))

        cap = GmailCapability(ctx)
        await cap.setup()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("overblick.capabilities.communication.gmail.smtplib.SMTP", return_value=mock_smtp):
            await cap.send_reply("t", "m", "recipient@example.com", "Subject", "Body")

        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg["From"] == "alias@example.com"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_enable_sending")
    async def test_send_reply_uses_login_address_when_no_alias(self):
        """send_reply() uses login address in From when no send_as is set."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("overblick.capabilities.communication.gmail.smtplib.SMTP", return_value=mock_smtp):
            await cap.send_reply("t", "m", "recipient@example.com", "Subject", "Body")

        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg["From"] == "test@gmail.com"


class TestFetchWithSinceDays:
    """Test IMAP SINCE filter (server-side date filtering)."""

    @pytest.mark.asyncio
    async def test_fetch_uses_since_in_search_criteria(self):
        """fetch_unread() passes SINCE date to IMAP when since_days is set."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email = _build_raw_email(message_id="<since@example.com>")
        mock_imap = _mock_imap(
            search_uids=[b"10"],
            fetch_data={b"10": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread(max_results=5, since_days=1)

        assert len(results) == 1
        # Verify IMAP search was called with UNSEEN SINCE criterion
        search_call = mock_imap.uid.call_args_list[0]
        search_args = search_call[0]
        assert search_args[0] == "search"
        assert "SINCE" in search_args[2]
        assert "UNSEEN" in search_args[2]

    @pytest.mark.asyncio
    async def test_fetch_without_since_days_uses_plain_unseen(self):
        """fetch_unread() uses plain UNSEEN when since_days is None."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email = _build_raw_email(message_id="<nosince@example.com>")
        mock_imap = _mock_imap(
            search_uids=[b"11"],
            fetch_data={b"11": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread(max_results=5)

        assert len(results) == 1
        search_call = mock_imap.uid.call_args_list[0]
        search_args = search_call[0]
        assert search_args[2] == "UNSEEN"

    @pytest.mark.asyncio
    async def test_fetch_since_imap_date_format(self):
        """SINCE date is formatted as DD-Mon-YYYY (IMAP RFC 3501 format)."""
        from datetime import datetime, timezone, timedelta
        import re

        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_imap = _mock_imap(search_uids=[])

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            await cap.fetch_unread(since_days=3)

        search_call = mock_imap.uid.call_args_list[0]
        criteria = search_call[0][2]
        # Extract the date portion after SINCE
        match = re.search(r"SINCE (\S+)", criteria)
        assert match, f"No date found in criteria: {criteria}"
        date_str = match.group(1)
        # Must match DD-Mon-YYYY (e.g. "14-Feb-2026")
        assert re.match(r"\d{2}-[A-Z][a-z]{2}-\d{4}", date_str), (
            f"IMAP date not in RFC 3501 format: {date_str}"
        )


class TestFetchMultipleMessages:
    """Test fetching multiple messages and max_results behavior."""

    @pytest.mark.asyncio
    async def test_fetch_multiple_messages(self):
        """fetch_unread() returns multiple messages, newest first."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email_1 = _build_raw_email(
            sender="alice@example.com",
            subject="First",
            body="First email body.",
            message_id="<first@example.com>",
        )
        raw_email_2 = _build_raw_email(
            sender="bob@example.com",
            subject="Second",
            body="Second email body.",
            message_id="<second@example.com>",
        )

        mock_imap = _mock_imap(
            search_uids=[b"10", b"20"],
            fetch_data={b"10": raw_email_1, b"20": raw_email_2},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread(max_results=10)

        assert len(results) == 2
        # Newest first (reversed order of UIDs)
        assert results[0].message_id == "<second@example.com>"
        assert results[1].message_id == "<first@example.com>"

    @pytest.mark.asyncio
    async def test_fetch_respects_max_results(self):
        """fetch_unread() limits results to max_results."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email_1 = _build_raw_email(
            message_id="<msg-1@example.com>",
            body="First",
        )
        raw_email_2 = _build_raw_email(
            message_id="<msg-2@example.com>",
            body="Second",
        )
        raw_email_3 = _build_raw_email(
            message_id="<msg-3@example.com>",
            body="Third",
        )

        mock_imap = _mock_imap(
            search_uids=[b"1", b"2", b"3"],
            fetch_data={b"1": raw_email_1, b"2": raw_email_2, b"3": raw_email_3},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread(max_results=2)

        # Only the last 2 UIDs should be fetched (newest)
        assert len(results) == 2


class TestSnippetGeneration:
    """Test that snippets are generated correctly from email bodies."""

    @pytest.mark.asyncio
    async def test_snippet_truncates_long_body(self):
        """Snippet is truncated to 200 characters."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        long_body = "A" * 300
        raw_email = _build_raw_email(body=long_body, message_id="<long@example.com>")

        mock_imap = _mock_imap(
            search_uids=[b"1"],
            fetch_data={b"1": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread()

        assert len(results) == 1
        assert len(results[0].snippet) <= 200

    @pytest.mark.asyncio
    async def test_snippet_strips_newlines(self):
        """Snippet replaces newlines with spaces."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        body_with_newlines = "Line one\nLine two\nLine three"
        raw_email = _build_raw_email(
            body=body_with_newlines,
            message_id="<newline@example.com>",
        )

        mock_imap = _mock_imap(
            search_uids=[b"1"],
            fetch_data={b"1": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread()

        assert "\n" not in results[0].snippet


class TestGmailMessageModel:
    """Test the GmailMessage model."""

    def test_basic_message(self):
        """GmailMessage can be created with required fields."""
        msg = GmailMessage(
            message_id="<test@example.com>",
            thread_id="<test@example.com>",
            sender="alice@example.com",
            subject="Hello",
            body="Body text",
            snippet="Body text",
            timestamp="Mon, 10 Feb 2026 14:30:00 +0100",
        )
        assert msg.message_id == "<test@example.com>"
        assert msg.sender == "alice@example.com"
        assert msg.labels == []

    def test_message_with_labels(self):
        """GmailMessage can include labels."""
        msg = GmailMessage(
            message_id="<test@example.com>",
            thread_id="<test@example.com>",
            sender="alice@example.com",
            subject="Hello",
            body="Body text",
            snippet="Body text",
            timestamp="Mon, 10 Feb 2026 14:30:00 +0100",
            labels=["INBOX", "UNREAD"],
        )
        assert msg.labels == ["INBOX", "UNREAD"]


class TestTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_clears_uid_map(self):
        """teardown() clears the UID cache."""
        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        cap._uid_map["<msg@test.com>"] = b"42"

        await cap.teardown()

        assert cap._uid_map == {}
