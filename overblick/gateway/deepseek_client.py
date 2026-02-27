"""
Deepseek API client for LLM inference.

OpenAI-compatible client with Bearer token authentication.
Structurally parallel to ollama_client.py but targeting the
Deepseek cloud API at https://api.deepseek.com/v1.
"""

import logging
from typing import Optional

import httpx

from .models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatResponseChoice,
    ChatResponseUsage,
)

logger = logging.getLogger(__name__)


class DeepseekError(Exception):
    """Base exception for Deepseek client errors."""


class DeepseekConnectionError(DeepseekError):
    """Raised when the Deepseek API is unreachable."""


class DeepseekTimeoutError(DeepseekError):
    """Raised when a Deepseek request times out."""


class DeepseekClient:
    """
    Async client for the Deepseek chat completions API.

    Uses httpx with Bearer token authentication. The API is
    OpenAI-compatible, so request/response formats match.
    """

    def __init__(
        self,
        api_url: str = "https://api.deepseek.com/v1",
        api_key: str = "",
        model: str = "deepseek-chat",
        timeout_seconds: float = 300.0,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with auth headers."""
        if self._client is None or self._client.is_closed:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                headers=headers,
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=self.timeout_seconds,
                    write=30.0,
                    pool=10.0,
                ),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """Check if the Deepseek backend is configured.

        Cloud backends are considered healthy if an API key is configured.
        Unlike local backends, cloud APIs may be transiently unreachable
        (rate limits, network blips) without indicating misconfiguration.
        Use connectivity_check() for a deeper reachability test.
        """
        return bool(self.api_key)

    async def connectivity_check(self) -> bool:
        """Check if the Deepseek API is actually reachable by listing models."""
        try:
            client = await self._get_client()
            response = await client.get("/models")
            return response.status_code == 200
        except Exception as e:
            logger.warning("Deepseek connectivity check failed: %s", e)
            return False

    async def list_models(self) -> list[str]:
        """Get list of available models from the Deepseek API."""
        try:
            client = await self._get_client()
            response = await client.get("/models")
            response.raise_for_status()
            data = response.json()
            return [m.get("id", "unknown") for m in data.get("data", [])]
        except httpx.ConnectError as e:
            raise DeepseekConnectionError(
                f"Cannot connect to Deepseek API: {e}"
            ) from e
        except Exception as e:
            logger.error("Failed to list Deepseek models: %s", e, exc_info=True)
            return []

    async def chat_completion(self, request: ChatRequest) -> ChatResponse:
        """
        Send a chat completion request to the Deepseek API.

        Supports both deepseek-chat and deepseek-reasoner models.
        The reasoner model returns reasoning_content (thinking process)
        alongside the final content — both are preserved in the response.

        Args:
            request: The chat request with messages and parameters

        Returns:
            ChatResponse with the model's response

        Raises:
            DeepseekConnectionError: If API is unreachable
            DeepseekTimeoutError: If request times out
            DeepseekError: For other errors
        """
        try:
            client = await self._get_client()
            selected_model = request.model or self.model
            is_reasoner = selected_model == "deepseek-reasoner"

            payload = {
                "model": selected_model,
                "messages": [
                    {"role": m.role, "content": m.content}
                    for m in request.messages
                ],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "stream": False,
            }

            if is_reasoner:
                logger.info(
                    "DeepSeek REASONER mode: model=%s, messages=%d "
                    "(reasoning_content will be captured)",
                    selected_model, len(request.messages),
                )
            else:
                logger.debug(
                    "Sending request to Deepseek: model=%s, messages=%d",
                    selected_model, len(request.messages),
                )

            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()

            data = response.json()

            choices = []
            for idx, choice in enumerate(data.get("choices", [])):
                message = choice.get("message", {})
                content = message.get("content", "")
                reasoning = message.get("reasoning_content")

                # deepseek-reasoner may return empty content with reasoning
                # in reasoning_content — use reasoning as fallback
                if not content and reasoning:
                    content = reasoning

                choices.append(
                    ChatResponseChoice(
                        index=idx,
                        message=ChatMessage(
                            role=message.get("role", "assistant"),
                            content=content,
                            reasoning_content=reasoning,
                        ),
                        finish_reason=choice.get("finish_reason") or "stop",
                    )
                )

            if is_reasoner and choices:
                rc = choices[0].message.reasoning_content
                logger.info(
                    "DeepSeek REASONER complete: reasoning=%d chars, answer=%d chars",
                    len(rc) if rc else 0,
                    len(choices[0].message.content),
                )

            usage_data = data.get("usage", {})
            usage = ChatResponseUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

            return ChatResponse(
                id=data.get("id", "chatcmpl-deepseek"),
                model=data.get("model", selected_model),
                choices=choices
                or [
                    ChatResponseChoice(
                        message=ChatMessage(
                            role="assistant", content="No response generated"
                        )
                    )
                ],
                usage=usage,
            )

        except httpx.ConnectError as e:
            logger.error(
                "Connection to Deepseek API failed: %s", e, exc_info=True
            )
            raise DeepseekConnectionError(
                f"Cannot connect to Deepseek API at {self.api_url}: {e}"
            ) from e

        except httpx.TimeoutException as e:
            logger.error(
                "Deepseek request timed out: %s", e, exc_info=True
            )
            raise DeepseekTimeoutError(
                f"Request timed out after {self.timeout_seconds}s: {e}"
            ) from e

        except httpx.HTTPStatusError as e:
            # Truncate response body to avoid logging sensitive API details
            error_text = e.response.text[:200] if e.response.text else "empty"
            logger.error(
                "Deepseek HTTP error: %s - %s",
                e.response.status_code,
                error_text,
            )
            raise DeepseekError(
                f"Deepseek returned error {e.response.status_code}"
            ) from e

        except Exception as e:
            logger.error(
                "Unexpected error calling Deepseek: %s", e, exc_info=True
            )
            raise DeepseekError(f"Failed to call Deepseek: {e}") from e
