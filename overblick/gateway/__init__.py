"""LLM Gateway — Priority-based request queuing for shared Ollama inference.

The gateway serializes LLM requests from multiple identity agents through a
priority queue, protecting the GPU from concurrent access. Interactive requests
(HIGH priority) preempt background tasks (LOW priority).

Runs as a standalone FastAPI server on port 8200 (configurable).
"""

__version__ = "0.1.0"

from .config import GatewayConfig
from .models import ChatMessage, ChatRequest, ChatResponse, Priority

__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "GatewayConfig",
    "Priority",
]
