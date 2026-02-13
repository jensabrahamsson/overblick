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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from blick.core.plugin_base import PluginBase, PluginContext
from blick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)


class EmailAction(Enum):
    """Actions the Gmail agent can take."""
    READ = "read"
    REPLY = "reply"
    COMPOSE = "compose"
    FORWARD = "forward"
    LABEL = "label"
    ARCHIVE = "archive"


@dataclass
class EmailMessage:
    """Represents an email message."""
    message_id: str
    thread_id: str
    subject: str = ""
    sender: str = ""
    recipient: str = ""
    body: str = ""
    snippet: str = ""
    labels: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    is_unread: bool = False

    @property
    def is_reply(self) -> bool:
        """Check if this is a reply (Re: prefix)."""
        return self.subject.lower().startswith("re:")


@dataclass
class EmailDraft:
    """A draft email pending approval."""
    to: str
    subject: str
    body: str
    thread_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    approved: bool = False
    sent: bool = False


@dataclass
class RecipientRateLimit:
    """Per-recipient rate limiting."""
    email: str
    send_timestamps: list[float] = field(default_factory=list)
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

        # Load OAuth credentials or app password
        oauth_json = self.ctx.get_secret("gmail_oauth_credentials")
        app_password = self.ctx.get_secret("gmail_app_password")
        self._email_address = self.ctx.get_secret("gmail_email_address")

        if not (oauth_json or app_password):
            raise RuntimeError(
                f"Missing gmail credentials for identity {identity.name}. "
                "Set gmail_oauth_credentials or gmail_app_password in secrets."
            )

        if not self._email_address:
            raise RuntimeError(
                f"Missing gmail_email_address for identity {identity.name}."
            )

        # Build personality-driven system prompt
        self._system_prompt = self._build_system_prompt(identity)

        # Load config
        raw_config = identity.raw_config
        gmail_config = raw_config.get("gmail", {})
        self._draft_mode = gmail_config.get("draft_mode", True)
        self._check_interval = gmail_config.get("check_interval_seconds", 300)
        allowed = gmail_config.get("allowed_senders", [])
        self._allowed_senders = set(allowed)

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
        Send an email via Gmail API.

        In production, this would use the Gmail API send endpoint.
        Currently a stub — implement with google-api-python-client.
        """
        # TODO: Implement with Gmail API
        logger.info(
            "Gmail: would send email to %s (subject: %s)",
            draft.to, draft.subject,
        )
        draft.sent = True
        return True

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
        from blick.personalities import load_personality, build_system_prompt
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
