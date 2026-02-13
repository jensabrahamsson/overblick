"""
TelegramPlugin — Telegram bot agent for the Blick framework.

Receives messages via Telegram Bot API, routes them through the
personality-driven LLM pipeline, and responds in character.

Features:
- Webhook or polling mode for receiving messages
- Personality-driven responses via SafeLLMPipeline
- Conversation context tracking (per-chat history)
- Permission-gated actions (send, forward, admin)
- Rate limiting per user
- Command routing (/start, /help, /ask, /status)
- Department routing (forward to other agents via event bus)

Security:
- All responses go through SafeLLMPipeline (sanitize → preflight → LLM → output safety)
- User input wrapped in boundary markers
- Rate limiting per user to prevent abuse
- Allowed chat IDs whitelist (optional)
"""

import asyncio
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from blick.core.plugin_base import PluginBase, PluginContext
from blick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)


class TelegramMessage(BaseModel):
    """Represents an incoming Telegram message."""
    chat_id: int
    user_id: int
    username: str = ""
    text: str = ""
    message_id: int = 0
    reply_to_message_id: Optional[int] = None
    timestamp: float = Field(default_factory=time.time)


class ConversationContext(BaseModel):
    """Tracks conversation history per chat for context-aware responses."""
    chat_id: int
    messages: list[dict[str, str]] = []
    last_active: float = Field(default_factory=time.time)
    max_history: int = 10

    def add_user_message(self, text: str, username: str = "") -> None:
        """Add a user message to the conversation history."""
        self.messages.append({"role": "user", "content": text})
        if len(self.messages) > self.max_history * 2:
            # Keep only the most recent messages
            self.messages = self.messages[-self.max_history * 2:]
        self.last_active = time.time()

    def add_assistant_message(self, text: str) -> None:
        """Add the bot's response to the conversation history."""
        self.messages.append({"role": "assistant", "content": text})
        self.last_active = time.time()

    def get_messages(self, system_prompt: str) -> list[dict[str, str]]:
        """Get full message list including system prompt."""
        return [{"role": "system", "content": system_prompt}] + self.messages

    @property
    def is_stale(self) -> bool:
        """Conversation is stale if inactive for > 1 hour."""
        return (time.time() - self.last_active) > 3600


class UserRateLimit(BaseModel):
    """Per-user rate limiting."""
    user_id: int
    message_timestamps: list[float] = []
    max_per_minute: int = 10
    max_per_hour: int = 60

    def is_allowed(self) -> bool:
        """Check if user is within rate limits."""
        now = time.time()
        # Prune old timestamps
        self.message_timestamps = [
            t for t in self.message_timestamps if now - t < 3600
        ]
        per_minute = sum(1 for t in self.message_timestamps if now - t < 60)
        per_hour = len(self.message_timestamps)
        return per_minute < self.max_per_minute and per_hour < self.max_per_hour

    def record(self) -> None:
        """Record a message."""
        self.message_timestamps.append(time.time())


# Telegram Bot API commands
COMMANDS = {
    "/start": "Start a conversation with the bot",
    "/help": "Show available commands",
    "/ask": "Ask the bot a question",
    "/status": "Check bot status",
    "/reset": "Reset conversation history",
}


class TelegramPlugin(PluginBase):
    """
    Telegram bot plugin.

    Drives a personality-driven conversational agent on Telegram.
    Uses polling mode by default, webhook mode for production.
    """

    name = "telegram"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)

        # Bot state
        self._bot_token: Optional[str] = None
        self._bot_username: Optional[str] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_update_id = 0

        # Conversation tracking
        self._conversations: dict[int, ConversationContext] = {}
        self._user_rate_limits: dict[int, UserRateLimit] = {}

        # Configuration
        self._allowed_chat_ids: set[int] = set()  # Empty = all allowed
        self._system_prompt: str = ""
        self._max_response_length: int = 4000  # Telegram message limit

        # Stats
        self._messages_received = 0
        self._messages_sent = 0
        self._errors = 0

    async def setup(self) -> None:
        """Initialize the Telegram bot."""
        identity = self.ctx.identity
        logger.info("Setting up TelegramPlugin for identity: %s", identity.name)

        # Load bot token from secrets
        self._bot_token = self.ctx.get_secret("telegram_bot_token")
        if not self._bot_token:
            raise RuntimeError(
                f"Missing telegram_bot_token for identity {identity.name}. "
                "Set it in config/secrets.yaml"
            )

        # Load personality-driven system prompt
        self._system_prompt = self._build_system_prompt(identity)

        # Load allowed chat IDs (optional whitelist)
        raw_config = identity.raw_config
        allowed = raw_config.get("telegram", {}).get("allowed_chat_ids", [])
        self._allowed_chat_ids = set(allowed)

        # Configure rate limits
        tg_config = raw_config.get("telegram", {})
        self._max_per_minute = tg_config.get("rate_limit_per_minute", 10)
        self._max_per_hour = tg_config.get("rate_limit_per_hour", 60)

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={"plugin": self.name, "identity": identity.name},
        )

        logger.info("TelegramPlugin setup complete for %s", identity.name)

    async def tick(self) -> None:
        """
        Poll for new messages from Telegram.

        Called periodically by the scheduler. In production, this would
        be replaced by webhook handling.
        """
        if not self._bot_token:
            return

        # Clean up stale conversations
        self._cleanup_stale_conversations()

        try:
            updates = await self._poll_updates()
            for update in updates:
                await self._handle_update(update)
        except Exception as e:
            self._errors += 1
            logger.error("Telegram polling error: %s", e)

    async def _poll_updates(self) -> list[dict]:
        """Poll Telegram Bot API for new updates."""
        import aiohttp

        url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 1,  # Short poll in tick mode
            "allowed_updates": ["message"],
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    logger.warning("Telegram API returned %d", resp.status)
                    return []
                data = await resp.json()

        if not data.get("ok"):
            logger.warning("Telegram API error: %s", data.get("description", "unknown"))
            return []

        updates = data.get("result", [])
        if updates:
            self._last_update_id = max(u["update_id"] for u in updates)

        return updates

    async def _handle_update(self, update: dict) -> None:
        """Handle a single Telegram update."""
        message = update.get("message")
        if not message or not message.get("text"):
            return

        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        username = message["from"].get("username", "")
        text = message["text"]
        message_id = message["message_id"]

        self._messages_received += 1

        # Check whitelist
        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            logger.debug("Message from non-whitelisted chat %d, ignoring", chat_id)
            return

        # Rate limit check
        rate_limiter = self._get_rate_limiter(user_id)
        if not rate_limiter.is_allowed():
            await self._send_message(
                chat_id,
                "Rate limit reached. Please wait before sending more messages.",
                reply_to=message_id,
            )
            return
        rate_limiter.record()

        # Handle commands
        if text.startswith("/"):
            await self._handle_command(chat_id, user_id, username, text, message_id)
            return

        # Handle regular message
        await self._handle_conversation(chat_id, user_id, username, text, message_id)

    async def _handle_command(
        self, chat_id: int, user_id: int, username: str, text: str, message_id: int
    ) -> None:
        """Handle a bot command."""
        command = text.split()[0].lower()
        args = text[len(command):].strip()

        if command == "/start":
            personality = self.ctx.identity.display_name
            await self._send_message(
                chat_id,
                f"Hello! I'm {personality}. Send me a message and I'll respond in character.\n\n"
                f"Commands: {', '.join(COMMANDS.keys())}",
            )

        elif command == "/help":
            help_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in COMMANDS.items())
            await self._send_message(chat_id, f"Available commands:\n{help_text}")

        elif command == "/ask":
            if args:
                await self._handle_conversation(chat_id, user_id, username, args, message_id)
            else:
                await self._send_message(chat_id, "Usage: /ask <your question>")

        elif command == "/status":
            status = (
                f"Identity: {self.ctx.identity.display_name}\n"
                f"Messages received: {self._messages_received}\n"
                f"Messages sent: {self._messages_sent}\n"
                f"Active conversations: {len(self._conversations)}\n"
                f"Errors: {self._errors}"
            )
            await self._send_message(chat_id, status)

        elif command == "/reset":
            self._conversations.pop(chat_id, None)
            await self._send_message(chat_id, "Conversation history cleared.")

        else:
            await self._send_message(
                chat_id, f"Unknown command. Try /help for available commands.",
                reply_to=message_id,
            )

    async def _handle_conversation(
        self, chat_id: int, user_id: int, username: str,
        text: str, message_id: int,
    ) -> None:
        """Handle a regular conversational message."""
        # Wrap user input in boundary markers (prompt injection prevention)
        safe_text = wrap_external_content(text, "telegram_message")

        # Use shared conversation tracker if available, else local context
        shared_caps = getattr(self.ctx, "capabilities", {}) or {}
        tracker = shared_caps.get("conversation_tracker")

        if tracker:
            tracker.add_user_message(str(chat_id), safe_text)
            messages = tracker.get_messages(str(chat_id), self._system_prompt)
        else:
            conv = self._get_conversation(chat_id)
            conv.add_user_message(safe_text, username)
            messages = conv.get_messages(self._system_prompt)

        # Generate response via SafeLLMPipeline
        if not self.ctx.llm_pipeline:
            logger.warning("No LLM pipeline available, using fallback")
            await self._send_message(chat_id, "I'm not available right now.", reply_to=message_id)
            return
        result = await self.ctx.llm_pipeline.chat(
            messages=messages,
            user_id=str(user_id),
            audit_action="telegram_response",
            audit_details={"chat_id": chat_id, "username": username},
        )

        if result.blocked:
            logger.warning("Response blocked by pipeline: %s", result.block_reason)
            if result.deflection:
                await self._send_message(chat_id, result.deflection, reply_to=message_id)
            else:
                await self._send_message(
                    chat_id, "I can't respond to that.", reply_to=message_id,
                )
            return

        response = result.content or ""
        if len(response) > self._max_response_length:
            response = response[:self._max_response_length - 3] + "..."

        # Store assistant response in conversation tracker
        if tracker:
            tracker.add_assistant_message(str(chat_id), response)
        else:
            conv.add_assistant_message(response)
        await self._send_message(chat_id, response, reply_to=message_id)

    async def _send_message(
        self, chat_id: int, text: str, reply_to: Optional[int] = None,
    ) -> bool:
        """Send a message via Telegram Bot API."""
        import aiohttp

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        self._messages_sent += 1
                        return True
                    else:
                        # Try without Markdown if it fails (Markdown can be finicky)
                        payload.pop("parse_mode", None)
                        async with session.post(url, json=payload) as retry_resp:
                            if retry_resp.status == 200:
                                self._messages_sent += 1
                                return True
                            logger.warning("Telegram send failed: %d", retry_resp.status)
                            return False
        except Exception as e:
            logger.error("Telegram send error: %s", e)
            self._errors += 1
            return False

    def _build_system_prompt(self, identity) -> str:
        """Build system prompt from personality."""
        from blick.personalities import load_personality, build_system_prompt
        try:
            personality = load_personality(identity.name)
            return build_system_prompt(personality, platform="Telegram")
        except FileNotFoundError:
            return (
                f"You are {identity.display_name}, responding on Telegram. "
                "Be conversational, helpful, and stay in character."
            )

    def _get_conversation(self, chat_id: int) -> ConversationContext:
        """Get or create conversation context for a chat."""
        if chat_id not in self._conversations:
            self._conversations[chat_id] = ConversationContext(chat_id=chat_id)
        return self._conversations[chat_id]

    def _get_rate_limiter(self, user_id: int) -> UserRateLimit:
        """Get or create rate limiter for a user."""
        if user_id not in self._user_rate_limits:
            self._user_rate_limits[user_id] = UserRateLimit(
                user_id=user_id,
                max_per_minute=getattr(self, "_max_per_minute", 10),
                max_per_hour=getattr(self, "_max_per_hour", 60),
            )
        return self._user_rate_limits[user_id]

    def _cleanup_stale_conversations(self) -> None:
        """Remove stale conversation contexts."""
        stale = [cid for cid, conv in self._conversations.items() if conv.is_stale]
        for cid in stale:
            del self._conversations[cid]

    def get_status(self) -> dict:
        """Get plugin status for monitoring."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "messages_received": self._messages_received,
            "messages_sent": self._messages_sent,
            "active_conversations": len(self._conversations),
            "errors": self._errors,
        }

    async def teardown(self) -> None:
        """Cleanup resources."""
        self._running = False
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
        self._conversations.clear()
        logger.info("TelegramPlugin teardown complete")


# Connector alias — new naming convention (backward-compatible)
TelegramConnector = TelegramPlugin
