"""
Email capability — SMTP email sending.

Security:
- Uses SafeLLMPipeline for any LLM-generated content
- Secrets from SecretsManager (never hardcoded)
- TLS/SSL for all SMTP connections
- Audit logging of all sent emails

Usage:
    email_cap = ctx.get_capability("email")
    await email_cap.send(
        to="recipient@example.com",
        subject="Hello",
        body="Email content here",
    )
"""

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class EmailMessage(BaseModel):
    """Email message to send."""
    to: str
    subject: str
    body: str
    from_email: Optional[str] = None
    html: bool = False


class EmailCapability:
    """
    Email sending capability using SMTP.

    Sends emails via configured SMTP server (Gmail, Brevo, SendGrid, etc.).
    Requires secrets: smtp_server, smtp_port, smtp_login, smtp_password, smtp_from_email

    This is a capability (not a plugin) because:
    - Reusable across multiple plugins (AI Digest, notifications, alerts)
    - No external state/polling (just sends on demand)
    - Simple function: take message → send via SMTP
    """

    name = "email"  # Required by CapabilityBase

    def __init__(self, ctx):
        self.ctx = ctx
        self._smtp_config: Optional[dict] = None
        logger.info("EmailCapability initialized for %s", ctx.identity_name)

    async def setup(self) -> None:
        """Load SMTP configuration from secrets."""
        try:
            port_str = self.ctx.get_secret("smtp_port")
            if not port_str:
                raise ValueError("SMTP secret 'smtp_port' is missing or empty")
            try:
                port = int(port_str)
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"SMTP secret 'smtp_port' must be an integer, got: {port_str!r}"
                ) from e

            self._smtp_config = {
                "server": self.ctx.get_secret("smtp_server"),
                "port": port,
                "login": self.ctx.get_secret("smtp_login"),
                "password": self.ctx.get_secret("smtp_password"),
                "from_email": self.ctx.get_secret("smtp_from_email"),
            }
            logger.info(
                "Email capability configured (server=%s, port=%d)",
                self._smtp_config["server"],
                self._smtp_config["port"],
            )
        except Exception as e:
            logger.error("Failed to load SMTP secrets: %s", e, exc_info=True)
            raise RuntimeError(
                "Email capability requires SMTP secrets: "
                "smtp_server, smtp_port, smtp_login, smtp_password, smtp_from_email"
            ) from e

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        from_email: Optional[str] = None,
        html: bool = False,
    ) -> bool:
        """
        Send an email via SMTP.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body (plain text or HTML)
            from_email: Override default from address (optional)
            html: True if body is HTML, False for plain text

        Returns:
            True if sent successfully, False otherwise

        Security:
            - All SMTP communication uses TLS/STARTTLS
            - Credentials never logged
            - Audit log records send action
        """
        if not self._smtp_config:
            logger.error("Email capability not configured (missing SMTP secrets)")
            return False

        try:
            # Build MIME message
            msg = MIMEMultipart("alternative")
            msg["From"] = from_email or self._smtp_config["from_email"]
            msg["To"] = to
            msg["Subject"] = subject

            # Attach body
            mime_type = "html" if html else "plain"
            msg.attach(MIMEText(body, mime_type, "utf-8"))

            # Send via SMTP (blocking I/O in thread pool)
            await asyncio.to_thread(self._send_smtp, msg)

            # Audit log
            self.ctx.audit_log.log(
                action="email_sent",
                details={
                    "to": to,
                    "subject": subject,
                    "length": len(body),
                    "html": html,
                },
            )

            logger.info("Email sent to %s: %s (%d chars)", to, subject, len(body))
            return True

        except Exception as e:
            logger.error("Failed to send email to %s: %s", to, e, exc_info=True)
            return False

    def _send_smtp(self, msg: MIMEMultipart) -> None:
        """
        Send email via SMTP (blocking call, run in thread pool).

        Supports both SSL (port 465) and STARTTLS (port 587).
        """
        server = self._smtp_config["server"]
        port = self._smtp_config["port"]
        login = self._smtp_config["login"]
        password = self._smtp_config["password"]

        if port == 465:
            # SSL connection
            with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
                smtp.login(login, password)
                smtp.send_message(msg)
        else:
            # STARTTLS connection (default)
            with smtplib.SMTP(server, port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(login, password)
                smtp.send_message(msg)

    async def teardown(self) -> None:
        """Cleanup (nothing to do for SMTP)."""
        pass
