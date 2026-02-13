"""LLM Gateway — Priority-based request queuing for shared Ollama inference.

The gateway serializes LLM requests from multiple identity agents through a
priority queue, protecting the GPU from concurrent access. Interactive requests
(HIGH priority) preempt background tasks (LOW priority).

Runs as a standalone FastAPI server on port 8200 (configurable).
"""

__version__ = "0.1.0"

from .models import Priority, ChatMessage, ChatRequest, ChatResponse
from .config import GatewayConfig

__all__ = [
    "Priority",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "GatewayConfig",
]
