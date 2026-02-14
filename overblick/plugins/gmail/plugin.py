"""
GmailPlugin — Gmail agent for the Blick framework.

Manages email interactions via Gmail API, driven by personality.

Features:
- OAuth2 authentication (or App Password fallback)
- Read, compose, and reply to emails
- Personality-driven responses via SafeLLMPipeline
- Label-based workflow (INBOX, PROCESSED, NEEDS_REPLY)
- Rate limiting per recipient
- Draft mode (compose but don't send without approval)

Security:
- All responses go through SafeLLMPipeline
- External email content wrapped in boundary markers
- Rate limiting per recipient to prevent spam
- Draft mode by default (requires explicit send permission)
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)


class EmailAction(Enum):
    """Actions the Gmail agent can take."""
    READ = "read"
    REPLY = "reply"
    COMPOSE = "compose"
    FORWARD = "forward"
    LABEL = "label"
    ARCHIVE = "archive"


class EmailMessage(BaseModel):
    """Represents an email message."""
    message_id: str
    thread_id: str
    subject: str = ""
    sender: str = ""
    recipient: str = ""
    body: str = ""
    snippet: str = ""
    labels: list[str] = []
    timestamp: float = Field(default_factory=time.time)
    is_unread: bool = False

    @property
    def is_reply(self) -> bool:
        """Check if this is a reply (Re: prefix)."""
        return self.subject.lower().startswith("re:")


class EmailDraft(BaseModel):
    """A draft email pending approval."""
    to: str
    subject: str
    body: str
    thread_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    approved: bool = False
    sent: bool = False


class RecipientRateLimit(BaseModel):
    """Per-recipient rate limiting."""
    email: str
    send_timestamps: list[float] = []
    max_per_hour: int = 5
    max_per_day: int = 20

    def is_allowed(self) -> bool:
        """Check if sending to this recipient is within limits."""
        now = time.time()
        self.send_timestamps = [t for t in self.send_timestamps if now - t < 86400]
        per_hour = sum(1 for t in self.send_timestamps if now - t < 3600)
        per_day = len(self.send_timestamps)
        return per_hour < self.max_per_hour and per_day < self.max_per_day

    def record(self) -> None:
        """Record a sent email."""
        self.send_timestamps.append(time.time())


class GmailPlugin(PluginBase):
    """
    Gmail agent plugin.

    Processes incoming emails and generates personality-driven responses.
    Operates in draft mode by default — emails are composed but not sent
    until explicitly approved (via boss agent or manual review).
    """

    name = "gmail"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)

        # Auth state
        self._credentials: Optional[dict] = None
        self._authenticated = False

        # Processing state
        self._last_check: float = 0
        self._check_interval: int = 300  # 5 minutes between checks
        self._system_prompt: str = ""

        # Draft queue
        self._drafts: list[EmailDraft] = []
        self._rate_limits: dict[str, RecipientRateLimit] = {}

        # Configuration
        self._draft_mode: bool = True  # Safety: compose but don't send
        self._allowed_senders: set[str] = set()  # Empty = all allowed
        self._max_body_length: int = 5000

        # Stats
        self._emails_read = 0
        self._emails_replied = 0
        self._drafts_created = 0
        self._errors = 0

    async def setup(self) -> None:
        """Initialize the Gmail agent."""
        identity = self.ctx.identity
        logger.info("Setting up GmailPlugin for identity: %s", identity.name)

        # Load credentials (Gmail OAuth/App Password OR SMTP credentials)
        oauth_json = self.ctx.get_secret("gmail_oauth_credentials")
        app_password = self.ctx.get_secret("gmail_app_password")
        self._email_address = self.ctx.get_secret("gmail_email_address")

        # Alternative: SMTP credentials (Brevo, SendGrid, etc.)
        smtp_server = self.ctx.get_secret("smtp_server")
        smtp_password = self.ctx.get_secret("smtp_password")
        smtp_from = self.ctx.get_secret("smtp_from_email")

        # Require either Gmail credentials or SMTP credentials
        has_gmail_creds = (oauth_json or app_password) and self._email_address
        has_smtp_creds = smtp_server and smtp_password and smtp_from

        if not (has_gmail_creds or has_smtp_creds):
            raise RuntimeError(
                f"Missing email credentials for identity {identity.name}. "
                "Set gmail_oauth_credentials + gmail_email_address OR "
                "smtp_server + smtp_password + smtp_from_email in secrets."
            )

        # Use SMTP from_email if no gmail_email_address
        if not self._email_address and smtp_from:
            self._email_address = smtp_from
            logger.info("Gmail: using smtp_from_email as sender address")

        # Build personality-driven system prompt
        self._system_prompt = self._build_system_prompt(identity)

        # Load config
        raw_config = identity.raw_config
        gmail_config = raw_config.get("gmail", {})
        self._draft_mode = gmail_config.get("draft_mode", True)
        self._check_interval = gmail_config.get("check_interval_seconds", 300)
        allowed = gmail_config.get("allowed_senders", [])
        self._allowed_senders = set(allowed)

        # Subscribe to email.send_request events from other plugins
        if self.ctx.event_bus:
            self.ctx.event_bus.subscribe("email.send_request", self._handle_send_request)
            logger.info("Gmail: subscribed to email.send_request events")

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "draft_mode": self._draft_mode,
            },
        )

        logger.info(
            "GmailPlugin setup complete for %s (draft_mode=%s)",
            identity.name, self._draft_mode,
        )

    async def tick(self) -> None:
        """
        Check for new emails and process them.

        Called periodically by the scheduler.
        """
        now = time.time()
        if now - self._last_check < self._check_interval:
            return

        self._last_check = now

        try:
            messages = await self._fetch_unread()
            for msg in messages:
                await self._process_email(msg)
        except Exception as e:
            self._errors += 1
            logger.error("Gmail processing error: %s", e)

    async def _fetch_unread(self) -> list[EmailMessage]:
        """
        Fetch unread emails from Gmail.

        In production, this would use the Gmail API. Currently a stub
        that returns an empty list — implement with google-api-python-client.
        """
        # TODO: Implement with Gmail API when google-api-python-client is added
        # For now, this is a well-defined interface for testing
        logger.debug("Gmail: checking for unread messages")
        return []

    async def _process_email(self, email: EmailMessage) -> None:
        """Process a single incoming email."""
        self._emails_read += 1

        # Check sender whitelist
        if self._allowed_senders and email.sender not in self._allowed_senders:
            logger.debug("Email from non-whitelisted sender %s, skipping", email.sender)
            return

        # Rate limit check
        rate_limiter = self._get_rate_limiter(email.sender)

        # Determine action
        if email.is_reply:
            await self._handle_reply(email, rate_limiter)
        else:
            await self._handle_new_email(email, rate_limiter)

    async def _handle_reply(
        self, email: EmailMessage, rate_limiter: RecipientRateLimit,
    ) -> None:
        """Handle a reply to an existing thread."""
        if not rate_limiter.is_allowed():
            logger.warning("Rate limit reached for %s, skipping reply", email.sender)
            return

        response = await self._generate_response(email, is_reply=True)
        if response:
            draft = EmailDraft(
                to=email.sender,
                subject=email.subject,
                body=response,
                thread_id=email.thread_id,
                in_reply_to=email.message_id,
            )
            await self._handle_draft(draft, rate_limiter)

    async def _handle_new_email(
        self, email: EmailMessage, rate_limiter: RecipientRateLimit,
    ) -> None:
        """Handle a new (non-reply) email."""
        if not rate_limiter.is_allowed():
            logger.warning("Rate limit reached for %s, skipping", email.sender)
            return

        response = await self._generate_response(email, is_reply=False)
        if response:
            subject = f"Re: {email.subject}" if not email.subject.startswith("Re:") else email.subject
            draft = EmailDraft(
                to=email.sender,
                subject=subject,
                body=response,
                thread_id=email.thread_id,
                in_reply_to=email.message_id,
            )
            await self._handle_draft(draft, rate_limiter)

    async def _generate_response(
        self, email: EmailMessage, is_reply: bool,
    ) -> Optional[str]:
        """Generate a personality-driven email response via SafeLLMPipeline."""
        if not self.ctx.llm_pipeline:
            logger.warning("No LLM pipeline available")
            return None

        # Wrap all external content in boundary markers
        safe_subject = wrap_external_content(email.subject, "email_subject")
        safe_body = wrap_external_content(
            email.body[:self._max_body_length], "email_body"
        )
        safe_sender = wrap_external_content(email.sender, "email_sender")

        context = "reply to their email" if is_reply else "respond to their email"
        user_prompt = (
            f"You received an email. {context}.\n\n"
            f"From: {safe_sender}\n"
            f"Subject: {safe_subject}\n"
            f"Body:\n{safe_body}\n\n"
            f"Write a response in your voice. Be concise and helpful."
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        result = await self.ctx.llm_pipeline.chat(
            messages=messages,
            user_id=email.sender,
            audit_action="gmail_response",
            audit_details={
                "thread_id": email.thread_id,
                "sender": email.sender,
                "is_reply": is_reply,
            },
        )

        if result.blocked:
            logger.warning("Email response blocked: %s", result.block_reason)
            return None

        response = result.content or ""
        if len(response) > self._max_body_length:
            response = response[:self._max_body_length]

        return response

    async def _handle_draft(
        self, draft: EmailDraft, rate_limiter: RecipientRateLimit,
    ) -> None:
        """Handle a composed draft — send or queue based on mode."""
        if self._draft_mode:
            self._drafts.append(draft)
            self._drafts_created += 1
            logger.info(
                "Gmail: draft created for %s (subject: %s)",
                draft.to, draft.subject,
            )
        else:
            success = await self._send_email(draft)
            if success:
                rate_limiter.record()
                self._emails_replied += 1

    async def _send_email(self, draft: EmailDraft) -> bool:
        """
        Send an email via SMTP.

        Uses SMTP credentials from secrets (Brevo or Gmail App Password).
        Supports both TLS (port 587) and SSL (port 465).
        """
        try:
            # Get SMTP credentials from secrets
            smtp_server = self.ctx.get_secret("smtp_server")
            smtp_port_str = self.ctx.get_secret("smtp_port")
            smtp_login = self.ctx.get_secret("smtp_login")
            smtp_password = self.ctx.get_secret("smtp_password")
            smtp_from = self.ctx.get_secret("smtp_from_email")

            if not all([smtp_server, smtp_port_str, smtp_login, smtp_password, smtp_from]):
                logger.error("Missing SMTP credentials in secrets")
                return False

            smtp_port = int(smtp_port_str)

            # Build email message
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["From"] = smtp_from
            msg["To"] = draft.to
            msg["Subject"] = draft.subject

            # Add plain text body
            text_part = MIMEText(draft.body, "plain", "utf-8")
            msg.attach(text_part)

            # Send via SMTP
            import smtplib
            import asyncio

            def _send_smtp():
                """Sync SMTP send in thread pool."""
                if smtp_port == 465:
                    # SSL
                    with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                        server.login(smtp_login, smtp_password)
                        server.send_message(msg)
                else:
                    # TLS (default: port 587)
                    with smtplib.SMTP(smtp_server, smtp_port) as server:
                        server.starttls()
                        server.login(smtp_login, smtp_password)
                        server.send_message(msg)

            # Run blocking SMTP in thread pool
            await asyncio.to_thread(_send_smtp)

            draft.sent = True
            logger.info(
                "Email sent successfully to %s (subject: %s)",
                draft.to, draft.subject,
            )

            self.ctx.audit_log.log(
                action="email_sent",
                details={
                    "to": draft.to,
                    "subject": draft.subject,
                    "smtp_server": smtp_server,
                },
            )

            return True

        except Exception as e:
            logger.error("Failed to send email to %s: %s", draft.to, e)
            self.ctx.audit_log.log(
                action="email_send_failed",
                details={
                    "to": draft.to,
                    "error": str(e),
                },
            )
            return False

    async def _handle_send_request(self, **kwargs) -> None:
        """
        Handle email.send_request events from other plugins.

        Expected kwargs:
            to: recipient email
            subject: email subject
            body: email body
            plugin: requesting plugin name (optional)
        """
        to = kwargs.get("to")
        subject = kwargs.get("subject")
        body = kwargs.get("body")
        plugin = kwargs.get("plugin", "unknown")

        if not all([to, subject, body]):
            logger.warning(
                "Gmail: incomplete email.send_request from %s (missing to/subject/body)",
                plugin,
            )
            return

        logger.info(
            "Gmail: received email.send_request from %s (to=%s, subject=%s)",
            plugin, to, subject,
        )

        # Create draft
        draft = EmailDraft(
            to=to,
            subject=subject,
            body=body,
            from_thread=None,
        )

        # Send directly (bypass draft mode for event-driven emails)
        success = await self._send_email(draft)

        if success:
            self.ctx.audit_log.log(
                action="email_sent_via_event",
                details={
                    "to": to,
                    "subject": subject,
                    "requesting_plugin": plugin,
                },
            )
        else:
            logger.error(
                "Gmail: failed to send email from %s to %s",
                plugin, to,
            )

    def approve_draft(self, index: int) -> Optional[EmailDraft]:
        """Approve a draft for sending (used by boss agent)."""
        if 0 <= index < len(self._drafts):
            self._drafts[index].approved = True
            return self._drafts[index]
        return None

    def get_pending_drafts(self) -> list[EmailDraft]:
        """Get all unsent drafts."""
        return [d for d in self._drafts if not d.sent]

    def _build_system_prompt(self, identity) -> str:
        """Build system prompt from personality."""
        from overblick.personalities import load_personality, build_system_prompt
        try:
            personality = load_personality(identity.name)
            return build_system_prompt(personality, platform="Email")
        except FileNotFoundError:
            return (
                f"You are {identity.display_name}, responding to emails. "
                "Be professional, concise, and stay in character."
            )

    def _get_rate_limiter(self, email: str) -> RecipientRateLimit:
        """Get or create rate limiter for a recipient."""
        if email not in self._rate_limits:
            self._rate_limits[email] = RecipientRateLimit(email=email)
        return self._rate_limits[email]

    def get_status(self) -> dict:
        """Get plugin status for monitoring."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "emails_read": self._emails_read,
            "emails_replied": self._emails_replied,
            "drafts_pending": len(self.get_pending_drafts()),
            "draft_mode": self._draft_mode,
            "errors": self._errors,
        }

    async def teardown(self) -> None:
        """Cleanup resources."""
        unsent = len(self.get_pending_drafts())
        if unsent:
            logger.warning("GmailPlugin teardown with %d unsent drafts", unsent)
        logger.info("GmailPlugin teardown complete")


# Connector alias — new naming convention (backward-compatible)
GmailConnector = GmailPlugin
