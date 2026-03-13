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

import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Optional

from overblick.core.exceptions import SecurityError

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMClient(ABC):
    """Abstract LLM client interface."""

    # Security: direct instantiation is restricted to core framework
    _ALLOW_DIRECT_INSTANTIATION = False

    @staticmethod
    def strip_think_tokens(text: str) -> str:
        """Strip Qwen3 <think>...</think> reasoning blocks from output."""
        return _THINK_RE.sub("", text).strip()

    @classmethod
    def _check_instantiation_allowed(cls) -> None:
        """Raise SecurityError if direct instantiation is not allowed.

        This prevents plugins from bypassing the SafeLLMPipeline by creating
        their own LLM client instances.
        """
        import os

        # Allow tests to bypass this check via environment variable
        if os.getenv("OVERBLICK_ALLOW_DIRECT_LLM") == "1":
            return

        if not cls._ALLOW_DIRECT_INSTANTIATION:
            raise SecurityError(
                "Direct LLM client instantiation is FORBIDDEN. "
                "Plugins must use PluginContext.llm_pipeline for secure LLM calls. "
                "Only the core framework may instantiate LLM clients."
            )

    @classmethod
    def _allow_instantiation(cls) -> None:
        """Allow direct instantiation (core framework use only)."""
        cls._ALLOW_DIRECT_INSTANTIATION = True

    @classmethod
    def _disallow_instantiation(cls) -> None:
        """Disallow direct instantiation (default state)."""
        cls._ALLOW_DIRECT_INSTANTIATION = False

    @classmethod
    @contextmanager
    def _instantiation_allowed(cls):
        """Context manager for allowing instantiation (core framework use only).

        Example:
            with GatewayClient._instantiation_allowed():
                client = GatewayClient(...)
        """
        cls._allow_instantiation()
        try:
            yield
        finally:
            cls._disallow_instantiation()

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        priority: str = "low",
        complexity: str | None = None,
    ) -> dict | None:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Override default temperature
            max_tokens: Override default max tokens
            top_p: Override default top_p
            priority: Request priority ("high" or "low"). Used by GatewayClient
                      for queue ordering. OllamaClient ignores this parameter.
            complexity: Request complexity ("high" or "low"). Used by GatewayClient
                        for backend routing. Other clients ignore this parameter.

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
