"""
Telegram notification capability — send and receive.

Wrapper around the Telegram Bot API for sending notifications and
receiving feedback from the principal. Supports tracked notifications
(correlating replies to specific messages) and offset-based polling.

Security:
- Bot token and chat ID loaded from SecretsManager (never hardcoded)
- Audit logging of all sent notifications
- TLS for all API calls (Telegram API enforces HTTPS)
- Only processes messages from the configured chat_id
- Owner ID filtering: only accepts messages from the instance owner
"""

import logging
from typing import Optional

import aiohttp
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TelegramUpdate(BaseModel):
    """A message received from Telegram."""
    message_id: int
    text: str
    reply_to_message_id: Optional[int] = None
    timestamp: str = ""


class TelegramNotifier:
    """
    Send notifications and receive feedback via Telegram Bot API.

    Requires secrets:
    - telegram_bot_token: Bot token from BotFather
    - telegram_chat_id: Chat ID to send notifications to

    This is a capability (not a plugin) because:
    - Reusable across multiple plugins (email_agent, alerts, monitoring)
    - Lightweight: no persistent event loop, just on-demand calls
    - Simple function: take message -> send via Telegram API
    """

    name = "telegram_notifier"

    def __init__(self, ctx):
        self.ctx = ctx
        self._bot_token: Optional[str] = None
        self._chat_id: Optional[str] = None
        self._owner_id: Optional[str] = None
        self._base_url: Optional[str] = None
        self._update_offset: int = 0
        self._bot_id: Optional[int] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def setup(self) -> None:
        """Load Telegram credentials from secrets."""
        try:
            self._bot_token = self.ctx.get_secret("telegram_bot_token")
            self._chat_id = self.ctx.get_secret("telegram_chat_id")
        except (KeyError, Exception):
            pass

        # Owner ID is optional but strongly recommended — restricts who the bot
        # accepts messages from. Falls back to chat_id for private chats.
        try:
            self._owner_id = self.ctx.get_secret("telegram_owner_id")
        except (KeyError, Exception):
            self._owner_id = self._chat_id  # Private chat: chat_id == user_id

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

    @property
    def chat_id(self) -> str:
        """The configured Telegram chat ID, or empty string if not set."""
        return self._chat_id or ""

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure a persistent HTTP session exists and return it."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the persistent HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _prefix_identity(self, message: str) -> str:
        """Prefix message with identity display name so the recipient knows who sent it."""
        display_name = ""
        if self.ctx.identity:
            display_name = getattr(self.ctx.identity, "display_name", "") or ""
        if not display_name:
            display_name = self.ctx.identity_name.capitalize()
        return f"*[{display_name}]*\n{message}"

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
            "text": self._prefix_identity(message),
            "parse_mode": "Markdown",
        }

        try:
            session = await self._ensure_session()
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
            logger.error("Telegram notification failed: %s", e, exc_info=True)
            return False

    async def send_notification_tracked(
        self, message: str, ref_id: str = "",
    ) -> Optional[int]:
        """
        Send a notification and return the Telegram message_id for tracking.

        Args:
            message: Text to send (supports Markdown formatting).
            ref_id: Optional reference ID for correlating replies.

        Returns:
            The sent message's Telegram message_id, or None on failure.
        """
        if not self._base_url:
            logger.warning("TelegramNotifier: not configured, cannot send")
            return None

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": self._prefix_identity(message),
            "parse_mode": "Markdown",
        }

        try:
            session = await self._ensure_session()
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tg_message_id = data.get("result", {}).get("message_id")
                    logger.info(
                        "Tracked notification sent (tg_msg=%s, ref=%s)",
                        tg_message_id, ref_id,
                    )
                    return tg_message_id
                else:
                    body = await resp.text()
                    logger.error(
                        "Telegram API error %d: %s", resp.status, body[:200],
                    )
                    return None
        except aiohttp.ClientError as e:
            logger.error("Tracked notification failed: %s", e, exc_info=True)
            return None

    async def fetch_updates(self, limit: int = 10) -> list[TelegramUpdate]:
        """
        Fetch new messages from the configured chat via getUpdates.

        Uses offset tracking to avoid re-processing old messages.
        Only returns messages from the configured chat_id (filters
        out messages from other chats and our own bot messages).

        Args:
            limit: Maximum number of updates to fetch.

        Returns:
            List of new TelegramUpdate objects.
        """
        if not self._base_url:
            return []

        url = f"{self._base_url}/getUpdates"
        params: dict = {
            "limit": limit,
            "timeout": 0,  # Non-blocking poll
        }
        if self._update_offset:
            params["offset"] = self._update_offset

        try:
            session = await self._ensure_session()
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                if not data.get("ok"):
                    return []

                updates = []
                for update in data.get("result", []):
                    update_id = update.get("update_id", 0)
                    # Always advance offset past this update
                    self._update_offset = max(
                        self._update_offset, update_id + 1,
                    )

                    msg = update.get("message", {})
                    if not msg:
                        continue

                    # Filter: only from our configured chat
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if chat_id != str(self._chat_id):
                        continue

                    # Filter: skip our own bot messages
                    from_user = msg.get("from", {})
                    if from_user.get("is_bot", False):
                        continue

                    # Filter: only accept messages from the owner
                    if self._owner_id:
                        sender_id = str(from_user.get("id", ""))
                        if sender_id != str(self._owner_id):
                            continue

                    text = msg.get("text", "")
                    if not text:
                        continue

                    reply_to = msg.get("reply_to_message", {})
                    reply_to_id = reply_to.get("message_id") if reply_to else None

                    updates.append(TelegramUpdate(
                        message_id=msg.get("message_id", 0),
                        text=text,
                        reply_to_message_id=reply_to_id,
                        timestamp=str(msg.get("date", "")),
                    ))

                return updates

        except aiohttp.ClientError as e:
            logger.error("Telegram getUpdates failed: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("Unexpected error fetching Telegram updates: %s", e, exc_info=True)
            return []

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

        display_name = ""
        if self.ctx.identity:
            display_name = getattr(self.ctx.identity, "display_name", "") or ""
        if not display_name:
            display_name = self.ctx.identity_name.capitalize()
        prefixed = f"<b>[{display_name}]</b>\n{message}"

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": prefixed,
            "parse_mode": "HTML",
        }

        try:
            session = await self._ensure_session()
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except aiohttp.ClientError as e:
            logger.error("Telegram HTML notification failed: %s", e, exc_info=True)
            return False
