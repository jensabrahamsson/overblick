"""
Ollama client for LLM inference.

Wraps the Ollama HTTP API with proper error handling and timeout management.
"""

import logging
from typing import Optional

import httpx

from .config import GatewayConfig, get_config
from .models import ChatRequest, ChatResponse, ChatMessage, ChatResponseChoice, ChatResponseUsage

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Base exception for Ollama client errors."""
    pass


class OllamaConnectionError(OllamaError):
    """Raised when Ollama server is unreachable."""
    pass


class OllamaTimeoutError(OllamaError):
    """Raised when request times out."""
    pass


class OllamaClient:
    """
    Async client for Ollama API.

    Uses httpx for async HTTP requests with configurable timeouts.
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        """Initialize the Ollama client."""
        self.config = config or get_config()
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.ollama_base_url,
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=self.config.request_timeout_seconds,
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
        """Check if Ollama server is reachable."""
        try:
            client = await self._get_client()
            response = await client.get("/v1/models")
            return response.status_code == 200
        except Exception as e:
            logger.warning("Ollama health check failed: %s", e)
            return False

    async def list_models(self) -> list[str]:
        """Get list of available models."""
        try:
            client = await self._get_client()
            response = await client.get("/v1/models")
            response.raise_for_status()
            data = response.json()
            return [m.get("id", m.get("name", "unknown")) for m in data.get("data", [])]
        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Cannot connect to Ollama: {e}") from e
        except Exception as e:
            logger.error("Failed to list models: %s", e, exc_info=True)
            return []

    async def chat_completion(self, request: ChatRequest) -> ChatResponse:
        """
        Send a chat completion request to Ollama.

        Args:
            request: The chat request with messages and parameters

        Returns:
            ChatResponse with the model's response

        Raises:
            OllamaConnectionError: If server is unreachable
            OllamaTimeoutError: If request times out
            OllamaError: For other errors
        """
        try:
            client = await self._get_client()

            payload = {
                "model": request.model,
                "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "top_p": request.top_p,
                "stream": False,
            }

            logger.debug("Sending request to Ollama: model=%s, messages=%d", request.model, len(request.messages))

            response = await client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()

            data = response.json()

            choices = []
            for idx, choice in enumerate(data.get("choices", [])):
                message = choice.get("message", {})
                choices.append(ChatResponseChoice(
                    index=idx,
                    message=ChatMessage(
                        role=message.get("role", "assistant"),
                        content=message.get("content", ""),
                    ),
                    finish_reason=choice.get("finish_reason") or "stop",
                ))

            usage_data = data.get("usage", {})
            usage = ChatResponseUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

            return ChatResponse(
                id=data.get("id", f"chatcmpl-{request.model}"),
                model=data.get("model", request.model),
                choices=choices or [ChatResponseChoice(
                    message=ChatMessage(role="assistant", content="No response generated")
                )],
                usage=usage,
            )

        except httpx.ConnectError as e:
            logger.error("Connection to Ollama failed: %s", e, exc_info=True)
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self.config.ollama_base_url}: {e}"
            ) from e

        except httpx.TimeoutException as e:
            logger.error("Ollama request timed out: %s", e, exc_info=True)
            raise OllamaTimeoutError(
                f"Request timed out after {self.config.request_timeout_seconds}s: {e}"
            ) from e

        except httpx.HTTPStatusError as e:
            logger.error("Ollama HTTP error: %s - %s", e.response.status_code, e.response.text, exc_info=True)
            raise OllamaError(
                f"Ollama returned error {e.response.status_code}: {e.response.text}"
            ) from e

        except Exception as e:
            logger.error("Unexpected error calling Ollama: %s", e, exc_info=True)
            raise OllamaError(f"Failed to call Ollama: {e}") from e

    async def embed(self, text: str, model: str = "nomic-embed-text") -> list[float]:
        """
        Generate an embedding vector via Ollama's /api/embed endpoint.

        Args:
            text: Text to embed
            model: Embedding model name (default: nomic-embed-text)

        Returns:
            List of floats representing the embedding vector

        Raises:
            OllamaConnectionError: If server is unreachable
            OllamaError: For other errors
        """
        if not text:
            return []

        try:
            client = await self._get_client()
            response = await client.post(
                "/api/embed",
                json={"model": model, "input": text},
            )
            response.raise_for_status()
            data = response.json()

            # Ollama returns {"embeddings": [[...float values...]]}
            embeddings = data.get("embeddings", [])
            if embeddings and isinstance(embeddings[0], list):
                return embeddings[0]
            return []

        except httpx.ConnectError as e:
            raise OllamaConnectionError(
                f"Cannot connect to Ollama for embedding: {e}"
            ) from e

        except httpx.HTTPStatusError as e:
            raise OllamaError(
                f"Embedding request failed ({e.response.status_code}): {e.response.text}"
            ) from e

        except Exception as e:
            raise OllamaError(f"Failed to generate embedding: {e}") from e
