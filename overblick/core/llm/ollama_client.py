"""
Local Ollama LLM client.

Ported from anomal_moltbook/llm/ollama_client.py.
Uses OpenAI-compatible API endpoint for chat completions.
Supports Qwen3's thinking mode (<think>...</think> token stripping).
"""

import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp

from overblick.core.llm.client import LLMClient

logger = logging.getLogger(__name__)


class OllamaClient(LLMClient):
    """
    Async client for local Ollama LLM inference.

    Features:
    - OpenAI-compatible chat completions API
    - Configurable timeouts for local inference
    - Qwen3 think-token stripping
    - Health check for model availability
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "qwen3:8b",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        top_p: float = 0.9,
        timeout_seconds: int = 180,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.timeout_seconds = timeout_seconds
        self._session: Optional[aiohttp.ClientSession] = None

        logger.info(f"OllamaClient: model={model}, url={base_url}")

    async def _ensure_session(self) -> None:
        """Ensure HTTP session exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
    ) -> Optional[dict]:
        """Send a chat completion request to Ollama."""
        await self._ensure_session()

        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens
        nucleus = top_p if top_p is not None else self.top_p

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": tokens,
            "top_p": nucleus,
            "stream": False,
        }

        start_time = time.monotonic()
        logger.debug(f"LLM: Sending request to {self.model}")

        try:
            async with self._session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"LLM: API error {response.status}: {error_text}")
                    return None

                data = await response.json()

            choices = data.get("choices", [])
            if not choices:
                logger.error("LLM: No choices in response")
                return None

            raw_content = choices[0].get("message", {}).get("content", "")
            elapsed = time.monotonic() - start_time

            # Strip Qwen3 thinking tokens
            content = self._strip_think_tokens(raw_content)

            if len(raw_content) != len(content):
                think_chars = len(raw_content) - len(content)
                logger.info(
                    f"LLM: Response in {elapsed:.1f}s "
                    f"({len(content)} chars output, {think_chars} chars reasoning)"
                )
            else:
                logger.info(f"LLM: Response in {elapsed:.1f}s ({len(content)} chars)")

            return {
                "content": content,
                "model": self.model,
                "tokens_used": data.get("usage", {}).get("total_tokens", 0),
                "finish_reason": choices[0].get("finish_reason"),
            }

        except asyncio.TimeoutError:
            logger.error(f"LLM: Request timeout ({self.timeout_seconds}s)")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"LLM: Connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM: Unexpected error: {e}", exc_info=True)
            return None

    @staticmethod
    def _strip_think_tokens(text: str) -> str:
        """Strip Qwen3 <think>...</think> reasoning blocks."""
        stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return stripped.strip()

    async def health_check(self) -> bool:
        """Check if Ollama is running and model is available."""
        await self._ensure_session()

        try:
            url = f"{self.base_url.replace('/v1', '')}/api/tags"
            async with self._session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    model_base = self.model.split(":")[0]
                    available = any(model_base in m for m in models)
                    if not available:
                        logger.warning(f"LLM: Model {self.model} not found in {models}")
                    return available
                else:
                    logger.warning(f"LLM: Health check failed: {response.status}")
                    return False
        except Exception as e:
            logger.warning(f"LLM: Health check error: {e}")
            return False

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
