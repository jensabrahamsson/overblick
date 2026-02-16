"""
LLM Gateway client — connects to the priority queue gateway on port 8200.

The gateway provides HIGH/LOW priority queuing for local Ollama inference.
HIGH priority (interactive) preempts LOW priority (background tasks).

Reasoning policy:
    Thinking is ON by default (Qwen3's default). The gateway forwards requests
    to Ollama which runs with reasoning enabled. This is intentional — agents
    writing posts and analyzing content benefit from deep thinking.

    For interactive chat (chat.py CLI), use Ollama's native /api/chat with
    think=false instead — see chat.py for that implementation.
"""

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from overblick.core.exceptions import LLMConnectionError, LLMTimeoutError
from overblick.core.llm.client import LLMClient

logger = logging.getLogger(__name__)


class GatewayClient(LLMClient):
    """
    LLM Gateway client (OpenAI-compatible with priority support).

    Usage:
        client = GatewayClient(priority="high")
        result = await client.chat(messages=[...])
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8200",
        model: str = "qwen3:8b",
        default_priority: str = "low",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        top_p: float = 0.9,
        timeout_seconds: int = 300,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.default_priority = default_priority
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.timeout_seconds = timeout_seconds
        self._session: Optional[aiohttp.ClientSession] = None

        logger.info(f"GatewayClient: {base_url}, default_priority={default_priority}, model={model}")

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        priority: str = "",
    ) -> Optional[dict]:
        """Send chat completion through the gateway with per-request priority."""
        await self._ensure_session()

        prio = priority if priority else self.default_priority
        url = f"{self.base_url}/v1/chat/completions?priority={prio}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "top_p": top_p if top_p is not None else self.top_p,
        }

        start_time = time.monotonic()

        try:
            async with self._session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning("Gateway: API error %d: %s", response.status, error_text)
                    raise LLMConnectionError(
                        f"Gateway API error {response.status}: {error_text[:200]}"
                    )

                data = await response.json()

            choices = data.get("choices", [])
            if not choices:
                logger.warning("Gateway: No choices in response")
                return None

            content = choices[0].get("message", {}).get("content", "")
            elapsed = time.monotonic() - start_time

            logger.info(f"Gateway: Response in {elapsed:.1f}s ({len(content)} chars)")

            return {
                "content": content,
                "model": data.get("model", self.model),
                "tokens_used": data.get("usage", {}).get("total_tokens", 0),
                "finish_reason": choices[0].get("finish_reason"),
            }

        except asyncio.TimeoutError:
            logger.error(f"Gateway: Timeout ({self.timeout_seconds}s)", exc_info=True)
            raise LLMTimeoutError(f"Gateway request timeout ({self.timeout_seconds}s)")
        except aiohttp.ClientError as e:
            logger.error(f"Gateway: Connection error: {e}", exc_info=True)
            raise LLMConnectionError(f"Gateway connection error: {e}") from e
        except (LLMTimeoutError, LLMConnectionError):
            raise
        except Exception as e:
            logger.error(f"Gateway: Unexpected error: {e}", exc_info=True)
            raise LLMConnectionError(f"Gateway unexpected error: {e}") from e

    async def health_check(self) -> bool:
        """Check if the gateway is available."""
        await self._ensure_session()

        try:
            url = f"{self.base_url}/health"
            async with self._session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Gateway: Health OK — {data}")
                    return True
                return False
        except Exception as e:
            logger.warning(f"Gateway: Health check failed: {e}")
            return False

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
