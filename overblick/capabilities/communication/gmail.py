"""
Gmail capability — IMAP/SMTP integration via App Password.

Reads email via IMAP and sends replies via SMTP using a Google App Password.
No OAuth2, no Google Cloud Console, no external dependencies — stdlib only.

Security:
- App Password stored in SecretsManager (never hardcoded)
- TLS/SSL for all IMAP and SMTP connections
- Audit logging of all sent emails

Setup:
    1. Enable 2-step verification on your Google Account
    2. Create an App Password: Google Account → Security → App Passwords
    3. Store it: python scripts/setup_gmail.py --identity stal

Usage:
    gmail = ctx.get_capability("gmail")
    messages = await gmail.fetch_unread(max_results=10)
    await gmail.send_reply(thread_id, message_id, to, subject, body)
    await gmail.mark_as_read(message_id)
"""

import asyncio
import imaplib
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header as _decode_header
from email.mime.text import MIMEText
from email.parser import BytesParser
from email import policy
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


class GmailMessage(BaseModel):
    """Parsed email message from Gmail."""
    message_id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    snippet: str
    timestamp: str
    labels: list[str] = []
    headers: dict[str, str] = {}  # Key email headers for classification signals


class GmailCapability:
    """
    Gmail integration via IMAP (read) and SMTP (send).

    Uses Google App Password — no OAuth2, no Google Cloud Console.
    All I/O is run in thread pool via asyncio.to_thread() to avoid blocking.

    Requires secrets (per-identity):
    - gmail_address: Gmail email address
    - gmail_app_password: App Password from Google Account

    This is a capability (not a plugin) because:
    - Reusable across multiple plugins (email_agent, notifications)
    - No external state/polling (operates on demand)
    - Simple API: fetch, send, mark-as-read
    """

    name = "gmail"

    def __init__(self, ctx):
        self.ctx = ctx
        self._email: Optional[str] = None
        self._password: Optional[str] = None
        self._send_as: Optional[str] = None  # Optional From address (Gmail alias)
        self._uid_map: dict[str, bytes] = {}  # RFC Message-ID → IMAP UID

    async def setup(self) -> None:
        """Load Gmail credentials from secrets."""
        try:
            self._email = self.ctx.get_secret("gmail_address")
            self._password = self.ctx.get_secret("gmail_app_password")
        except (KeyError, Exception):
            pass

        # Optional: send as a different address (Gmail "Send mail as" alias)
        try:
            self._send_as = self.ctx.get_secret("gmail_send_as")
        except (KeyError, Exception):
            pass

        if not self._email or not self._password:
            logger.warning(
                "GmailCapability: missing gmail_address or gmail_app_password "
                "for identity %s — gmail disabled. "
                "Run: python scripts/setup_gmail.py --identity %s",
                self.ctx.identity_name,
                self.ctx.identity_name,
            )
            return

        from_addr = self._send_as or self._email
        logger.info(
            "GmailCapability ready for %s (login: %s, from: %s)",
            self.ctx.identity_name,
            self._email,
            from_addr,
        )

    @property
    def configured(self) -> bool:
        """Whether the capability has valid credentials."""
        return bool(self._email and self._password)

    # ── Read (IMAP) ──────────────────────────────────────────────────────

    async def fetch_unread(
        self,
        max_results: int = 10,
        since_days: Optional[int] = None,
    ) -> list[GmailMessage]:
        """
        Fetch unread messages from Gmail inbox via IMAP.

        Args:
            max_results: Maximum number of messages to return.
            since_days: Only fetch messages from the last N days (IMAP SINCE
                filter). When set, old messages are excluded at the server
                level — they are never transferred over the network.

        Returns:
            List of GmailMessage objects, newest first.
        """
        if not self.configured:
            logger.warning("GmailCapability: not configured, cannot fetch")
            return []

        try:
            return await asyncio.to_thread(
                self._imap_fetch_unread, max_results, since_days,
            )
        except Exception as e:
            logger.error("Gmail fetch_unread failed: %s", e, exc_info=True)
            return []

    def _imap_fetch_unread(
        self, max_results: int, since_days: Optional[int] = None,
    ) -> list[GmailMessage]:
        """Fetch unread messages via IMAP (blocking, run in thread pool)."""
        results = []

        with imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT) as imap:
            imap.login(self._email, self._password)
            imap.select("INBOX")

            # Build search criteria — optionally restrict to recent messages
            # at the server level to avoid fetching ancient unread emails.
            search_criteria = "UNSEEN"
            if since_days is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
                imap_date = cutoff.strftime("%d-%b-%Y")  # e.g. "14-Feb-2026"
                search_criteria = f"UNSEEN SINCE {imap_date}"
                logger.debug(
                    "GmailCapability: fetching unread since %s (last %d day(s))",
                    imap_date, since_days,
                )

            status, data = imap.uid("search", None, search_criteria)
            if status != "OK" or not data[0]:
                return []

            uids = data[0].split()

            # Newest first, limited to max_results
            for uid in reversed(uids[-max_results:]):
                msg = self._imap_fetch_message(imap, uid)
                if msg:
                    results.append(msg)

        return results

    def _imap_fetch_message(
        self, imap: imaplib.IMAP4_SSL, uid: bytes,
    ) -> Optional[GmailMessage]:
        """Fetch and parse a single message by IMAP UID."""
        status, data = imap.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not data[0]:
            return None

        raw_bytes = data[0][1]
        parser = BytesParser(policy=policy.default)
        msg = parser.parsebytes(raw_bytes)

        # Extract headers
        message_id = msg.get("Message-ID", "")
        sender = str(msg.get("From", ""))
        subject = self._decode_header(msg.get("Subject", ""))
        date = str(msg.get("Date", ""))

        # Extract classification-relevant headers (soft signals for LLM)
        signal_headers = {}
        for hdr in ("List-Unsubscribe", "Precedence", "List-Id", "X-Mailer"):
            val = msg.get(hdr)
            if val:
                signal_headers[hdr] = str(val)

        # Extract body
        body = self._extract_body(msg)
        snippet = body[:200].replace("\n", " ").strip()

        # Cache UID for mark_as_read
        if message_id:
            self._uid_map[message_id] = uid

        return GmailMessage(
            message_id=message_id,
            thread_id=message_id,  # SMTP threading uses Message-ID directly
            sender=sender,
            subject=subject,
            body=body,
            snippet=snippet,
            timestamp=date,
            headers=signal_headers,
        )

    def _extract_body(self, msg) -> str:
        """Extract plain text body from email message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")

            # Fallback: text/html
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")

        return ""

    def _decode_header(self, header) -> str:
        """Decode RFC 2047 encoded header (e.g. =?UTF-8?Q?...?=)."""
        if not header:
            return ""

        raw = str(header)
        parts = _decode_header(raw)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    # ── Send (SMTP) ─────────────────────────────────────────────────────

    async def send_reply(
        self,
        thread_id: str,
        message_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> bool:
        """
        Send a reply via SMTP with proper threading headers.

        Sets In-Reply-To and References headers so Gmail groups the reply
        into the original conversation thread.

        Args:
            thread_id: Thread identifier (unused for SMTP, kept for API compat).
            message_id: RFC 2822 Message-ID of the message being replied to.
            to: Recipient email address.
            subject: Email subject (Re: prepended if needed).
            body: Plain text reply body.

        Returns:
            True if sent successfully.
        """
        if not self.configured:
            logger.warning("GmailCapability: not configured, cannot send")
            return False

        reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"

        # Build MIME message with threading headers
        mime_msg = MIMEText(body, "plain", "utf-8")
        mime_msg["To"] = to
        mime_msg["From"] = self._send_as or self._email
        mime_msg["Subject"] = reply_subject
        if message_id:
            mime_msg["In-Reply-To"] = message_id
            mime_msg["References"] = message_id

        try:
            await asyncio.to_thread(self._smtp_send, mime_msg)
            logger.info("Gmail reply sent to %s: %s", to, reply_subject)
            return True
        except Exception as e:
            logger.error("Gmail send_reply failed: %s", e, exc_info=True)
            return False

    def _smtp_send(self, msg: MIMEText) -> None:
        """Send email via SMTP with STARTTLS (blocking, run in thread pool)."""
        with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self._email, self._password)
            smtp.send_message(msg)

    # ── Mark as read (IMAP) ──────────────────────────────────────────────

    async def mark_as_read(self, message_id: str) -> bool:
        """
        Mark a message as read by setting the \\Seen flag via IMAP.

        Args:
            message_id: RFC 2822 Message-ID (looked up from internal UID cache).

        Returns:
            True if successful.
        """
        if not self.configured:
            logger.warning("GmailCapability: not configured, cannot modify")
            return False

        uid = self._uid_map.get(message_id)
        if not uid:
            logger.warning(
                "GmailCapability: no IMAP UID cached for message %s",
                message_id,
            )
            return False

        try:
            await asyncio.to_thread(self._imap_mark_read, uid)
            logger.debug("Gmail message marked as read: %s", message_id)
            return True
        except Exception as e:
            logger.error("Gmail mark_as_read failed: %s", e, exc_info=True)
            return False

    def _imap_mark_read(self, uid: bytes) -> None:
        """Set \\Seen flag on a message via IMAP (blocking, run in thread pool)."""
        with imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT) as imap:
            imap.login(self._email, self._password)
            imap.select("INBOX")
            imap.uid("store", uid, "+FLAGS", "\\Seen")

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def teardown(self) -> None:
        """Cleanup internal state."""
        self._uid_map.clear()
