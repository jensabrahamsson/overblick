"""
Ollama client for LLM inference.

Wraps the Ollama HTTP API with proper error handling and timeout management.
"""

import asyncio
import logging

import httpx

from overblick.core.exceptions import LLMError

from .config import GatewayConfig, get_config
from .models import ChatMessage, ChatRequest, ChatResponse, ChatResponseChoice, ChatResponseUsage

logger = logging.getLogger(__name__)


class OllamaError(LLMError):
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

    def __init__(self, config: GatewayConfig | None = None):
        """Initialize the Ollama client."""
        self.config = config or get_config()
        self._client: httpx.AsyncClient | None = None

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

            # LM Studio (used for Gemma) shares max_tokens between reasoning and content.
            # Gemma can use 80%+ of tokens on reasoning, leaving nothing for content.
            # Multiply max_tokens to ensure room for both reasoning AND content output.
            # Minimum 4000 tokens to give Gemma enough headroom.
            base_max_tokens = request.max_tokens
            if base_max_tokens < 4000:
                base_max_tokens = 4000
            else:
                # Double the requested tokens to account for reasoning overhead
                base_max_tokens = min(base_max_tokens * 2, 32000)

            payload = {
                "model": self.config.default_model or request.model,
                "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                "max_tokens": base_max_tokens,
                "temperature": request.temperature,
                "top_p": request.top_p,
                "stream": False,
            }

            logger.debug(
                "Sending request to Ollama: model=%s, messages=%d",
                request.model,
                len(request.messages),
            )

            response = await client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()

            data = response.json()

            # Extract reasoning_content based on model type.
            # Different models return thinking/reasoning in different ways:
            # - Qwen3: <think> tags embedded in content
            # - DeepSeek reasoner: reasoning_content field
            # - Gemma 4: reasoning_content field (via LM Studio / Ollama-compatible API)
            # - Mistral/Dolphin: no reasoning, just content
            choices = []
            for idx, choice in enumerate(data.get("choices", [])):
                message = choice.get("message", {})
                content = message.get("content", "")
                reasoning = message.get("reasoning_content")
                finish = choice.get("finish_reason") or "stop"

                # Log response structure for troubleshooting
                logger.info(
                    "Ollama response: model=%s, content_len=%d, reasoning_len=%d, finish=%s",
                    request.model,
                    len(content) if content else 0,
                    len(reasoning) if reasoning else 0,
                    finish,
                )

                # Gemma (and other reasoning models) may use all tokens on reasoning
                # and produce zero content. Detect this and retry with think=False.
                if finish == "length" and (not content or len(content.strip()) < 10):
                    logger.warning(
                        "Model %s exhausted tokens on reasoning (content_len=%d, reasoning_len=%d). "
                        "Retrying with think=False.",
                        request.model,
                        len(content) if content else 0,
                        len(reasoning) if reasoning else 0,
                    )
                    # Retry with increased tokens and thinking disabled
                    retry_payload = dict(payload)
                    retry_payload["think"] = False
                    retry_payload["max_tokens"] = max(payload.get("max_tokens", 2000), 4000)

                    retry_response = await client.post("/v1/chat/completions", json=retry_payload)
                    retry_response.raise_for_status()
                    data = retry_response.json()

                    # Re-parse choices from retry
                    choices = []
                    for retry_idx, retry_choice in enumerate(data.get("choices", [])):
                        retry_msg = retry_choice.get("message", {})
                        retry_content = retry_msg.get("content", "")
                        retry_reasoning = retry_msg.get("reasoning_content")
                        retry_finish = retry_choice.get("finish_reason") or "stop"

                        logger.info(
                            "Ollama retry response: model=%s, content_len=%d, reasoning_len=%d, finish=%s",
                            request.model,
                            len(retry_content) if retry_content else 0,
                            len(retry_reasoning) if retry_reasoning else 0,
                            retry_finish,
                        )

                        choices.append(
                            ChatResponseChoice(
                                index=retry_idx,
                                message=ChatMessage(
                                    role=retry_msg.get("role", "assistant"),
                                    content=retry_content,
                                    reasoning_content=retry_reasoning,
                                ),
                                finish_reason=retry_finish,
                            )
                        )
                    break  # Exit original loop, use retry data

                choices.append(
                    ChatResponseChoice(
                        index=idx,
                        message=ChatMessage(
                            role=message.get("role", "assistant"),
                            content=content,
                            reasoning_content=reasoning,
                        ),
                        finish_reason=finish,
                    )
                )

            usage_data = data.get("usage", {})
            usage = ChatResponseUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

            return ChatResponse(
                id=data.get("id", f"chatcmpl-{request.model}"),
                model=data.get("model", request.model),
                choices=choices
                or [
                    ChatResponseChoice(
                        message=ChatMessage(role="assistant", content="No response generated")
                    )
                ],
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
            # LM Studio: auto-load model if "no models loaded"
            if e.response.status_code == 400:
                error_text = e.response.text.lower()
                if "no models loaded" in error_text or "model not found" in error_text:
                    logger.warning(
                        "LM Studio: model not loaded, sending trigger request to auto-load..."
                    )
                    try:
                        # Send a tiny request to trigger model loading
                        trigger_payload = {
                            "model": self.config.default_model or request.model,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        }
                        trigger_client = await self._get_client()
                        try:
                            await trigger_client.post(
                                "/v1/chat/completions",
                                json=trigger_payload,
                                timeout=10.0,
                            )
                        except Exception:
                            pass  # Model loading may take time

                        # Poll until model is ready (3s intervals, max 30s)
                        model_ready = False
                        for attempt in range(10):
                            await asyncio.sleep(3)
                            try:
                                poll_client = await self._get_client()
                                poll_resp = await poll_client.get("/v1/models", timeout=5.0)
                                if poll_resp.status_code == 200:
                                    models_data = poll_resp.json()
                                    if models_data.get("data"):
                                        model_ready = True
                                        logger.info(
                                            "LM Studio: model ready after %ds",
                                            (attempt + 1) * 3,
                                        )
                                        break
                            except Exception:
                                pass

                        if not model_ready:
                            logger.warning("LM Studio: model not ready after 30s")

                        # Retry original request
                        logger.info("LM Studio: retrying original request after auto-load")
                        retry_client = await self._get_client()
                        response = await retry_client.post("/v1/chat/completions", json=payload)
                        response.raise_for_status()
                        data = response.json()
                        # Parse response (same as above)
                        choices = []
                        for idx, choice in enumerate(data.get("choices", [])):
                            message = choice.get("message", {})
                            content = message.get("content", "")
                            reasoning = message.get("reasoning_content")
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
                        usage_data = data.get("usage", {})
                        usage = ChatResponseUsage(
                            prompt_tokens=usage_data.get("prompt_tokens", 0),
                            completion_tokens=usage_data.get("completion_tokens", 0),
                            total_tokens=usage_data.get("total_tokens", 0),
                        )
                        return ChatResponse(
                            id=data.get("id", f"chatcmpl-{request.model}"),
                            model=data.get("model", request.model),
                            choices=choices
                            or [
                                ChatResponseChoice(
                                    message=ChatMessage(
                                        role="assistant",
                                        content="No response generated",
                                    )
                                )
                            ],
                            usage=usage,
                        )
                    except Exception as retry_err:
                        logger.error("LM Studio auto-load retry failed: %s", retry_err)

            logger.error(
                "Ollama HTTP error: %s - %s", e.response.status_code, e.response.text, exc_info=True
            )
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
            raise OllamaConnectionError(f"Cannot connect to Ollama for embedding: {e}") from e

        except httpx.HTTPStatusError as e:
            raise OllamaError(
                f"Embedding request failed ({e.response.status_code}): {e.response.text}"
            ) from e

        except Exception as e:
            raise OllamaError(f"Failed to generate embedding: {e}") from e
