"""
Cloud LLM client stub.

This module provides the interface for cloud-based LLM providers
(OpenAI, Anthropic, Google, etc.). The actual implementation is
left to the user — this stub ensures the routing infrastructure
works and provides a clear contract.

To implement:
1. Subclass CloudLLMClient or replace this file
2. Implement chat(), health_check(), close()
3. Load API key via ctx.get_secret(cloud_secret_key)
"""

import logging
from typing import Optional

from overblick.core.llm.client import LLMClient

logger = logging.getLogger(__name__)


class CloudLLMClient(LLMClient):
    """
    Stub client for cloud LLM providers.

    Raises NotImplementedError on all operations. Replace this
    implementation to connect to your cloud provider of choice
    (OpenAI, Anthropic, Google, etc.).

    Constructor args match what the orchestrator passes:
        api_url:    Base API URL (e.g. "https://api.openai.com/v1")
        model:      Model identifier (e.g. "gpt-4o", "claude-sonnet-4-5-20250929")
        api_key:    API key loaded from SecretsManager
        temperature: Default temperature
        max_tokens:  Default max tokens
        top_p:       Default top_p
        timeout_seconds: Request timeout
    """

    def __init__(
        self,
        api_url: str,
        model: str,
        api_key: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        top_p: float = 0.9,
        timeout_seconds: int = 180,
    ):
        self.api_url = api_url
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.timeout_seconds = timeout_seconds

    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        priority: str = "low",
        complexity: Optional[str] = None,
    ) -> Optional[dict]:
        """Send a chat completion request to the cloud provider.

        Not yet implemented — raises NotImplementedError with guidance.
        """
        raise NotImplementedError(
            "Cloud LLM client not yet implemented. "
            "See overblick/core/llm/cloud_client.py for the interface contract. "
            "Implement chat() to connect to your cloud provider."
        )

    async def health_check(self) -> bool:
        """Check if the cloud provider is reachable.

        Stub returns True if api_url and api_key are configured, False otherwise.
        Override this to verify actual cloud provider connectivity.
        """
        configured = bool(self.api_url and self.api_key)
        if not configured:
            logger.warning(
                "Cloud LLM health_check: not configured (api_url=%s, api_key=%s)",
                bool(self.api_url), bool(self.api_key),
            )
        return configured

    async def close(self) -> None:
        """Close any open connections. No-op by default."""
        pass
