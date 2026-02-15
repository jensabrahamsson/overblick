"""
Telegram notification capability — send-only.

Thin wrapper around the Telegram Bot API for sending notifications.
No conversation handling — just fire-and-forget messages to a configured chat.

Security:
- Bot token and chat ID loaded from SecretsManager (never hardcoded)
- Audit logging of all sent notifications
- TLS for all API calls (Telegram API enforces HTTPS)
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Send notifications via Telegram Bot API.

    Requires secrets:
    - telegram_bot_token: Bot token from BotFather
    - telegram_chat_id: Chat ID to send notifications to

    This is a capability (not a plugin) because:
    - Reusable across multiple plugins (email_agent, alerts, monitoring)
    - No external state/polling (just sends on demand)
    - Simple function: take message → send via Telegram API
    """

    name = "telegram_notifier"

    def __init__(self, ctx):
        self.ctx = ctx
        self._bot_token: Optional[str] = None
        self._chat_id: Optional[str] = None
        self._base_url: Optional[str] = None

    async def setup(self) -> None:
        """Load Telegram credentials from secrets."""
        try:
            self._bot_token = self.ctx.get_secret("telegram_bot_token")
            self._chat_id = self.ctx.get_secret("telegram_chat_id")
        except (KeyError, Exception):
            pass

        if not self._bot_token or not self._chat_id:
            logger.warning(
                "TelegramNotifier: missing telegram_bot_token or telegram_chat_id "
                "for identity %s — notifications disabled",
                self.ctx.identity_name,
            )
            return

        self._base_url = f"https://api.telegram.org/bot{self._bot_token}"
        logger.info("TelegramNotifier ready for identity %s", self.ctx.identity_name)

    @property
    def configured(self) -> bool:
        """Whether the notifier has valid credentials."""
        return self._base_url is not None

    async def send_notification(self, message: str) -> bool:
        """
        Send a text message to the configured Telegram chat.

        Args:
            message: Text to send (supports Markdown formatting).

        Returns:
            True if the message was sent successfully.
        """
        if not self._base_url:
            logger.warning("TelegramNotifier: not configured, cannot send")
            return False

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info("Telegram notification sent to chat %s", self._chat_id)
                        return True
                    else:
                        body = await resp.text()
                        logger.error(
                            "Telegram API error %d: %s", resp.status, body[:200],
                        )
                        return False
        except aiohttp.ClientError as e:
            logger.error("Telegram notification failed: %s", e)
            return False

    async def send_html(self, message: str) -> bool:
        """
        Send an HTML-formatted message to the configured Telegram chat.

        Args:
            message: HTML-formatted text to send.

        Returns:
            True if the message was sent successfully.
        """
        if not self._base_url:
            logger.warning("TelegramNotifier: not configured, cannot send")
            return False

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    return resp.status == 200
        except aiohttp.ClientError as e:
            logger.error("Telegram HTML notification failed: %s", e)
            return False
