"""
Abstract LLM client interface.

All LLM backends (Ollama, Gateway, future providers) implement this interface.
Plugins interact with LLMs exclusively through this abstraction.

Reasoning policy:
    Production clients (OllamaClient, GatewayClient) keep Qwen3 reasoning ON
    by default — agents writing posts and analyzing content benefit from deep
    thinking. For interactive chat where speed matters, use think=false via
    Ollama's native API (see chat.py).
"""

from abc import ABC, abstractmethod
from typing import Optional


class LLMClient(ABC):
    """Abstract LLM client interface."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        priority: str = "low",
    ) -> Optional[dict]:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Override default temperature
            max_tokens: Override default max tokens
            top_p: Override default top_p
            priority: Request priority ("high" or "low"). Used by GatewayClient
                      for queue ordering. OllamaClient ignores this parameter.

        Returns:
            Dict with 'content' key containing the response, or None on error
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the LLM backend is available.

        Returns:
            True if the backend is reachable and model is loaded
        """

    @abstractmethod
    async def close(self) -> None:
        """Close any open connections."""
