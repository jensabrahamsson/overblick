"""
Pydantic models for the LLM Gateway.

Defines request/response models, priority levels, and queue items
for the priority-based request queuing system.
"""

from asyncio import Future
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Priority(IntEnum):
    """Request priority levels. Lower number = higher priority."""
    HIGH = 1   # Interactive requests (identity agents responding to users)
    LOW = 5    # Background tasks (scheduled ticks, housekeeping)


class ChatMessage(BaseModel):
    """A single message in a chat conversation."""
    role: str = Field(..., description="Message role: system, user, or assistant")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Request for chat completion, compatible with OpenAI format."""
    model: str = Field(default="qwen3:8b", description="Model name to use")
    messages: list[ChatMessage] = Field(..., description="Conversation messages")
    max_tokens: int = Field(default=2000, ge=1, le=8192, description="Max tokens to generate")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")

    model_config = {"json_schema_extra": {
        "example": {
            "model": "qwen3:8b",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello!"},
            ],
            "max_tokens": 2000,
            "temperature": 0.7,
        }
    }}


class ChatResponseChoice(BaseModel):
    """A single choice in a chat completion response."""
    index: int = Field(default=0)
    message: ChatMessage
    finish_reason: str = Field(default="stop")


class ChatResponseUsage(BaseModel):
    """Token usage information for a chat completion."""
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)


class ChatResponse(BaseModel):
    """OpenAI-compatible chat completion response."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid4().hex[:12]}")
    object: str = Field(default="chat.completion")
    created: int = Field(default_factory=lambda: int(datetime.now().timestamp()))
    model: str
    choices: list[ChatResponseChoice]
    usage: ChatResponseUsage = Field(default_factory=ChatResponseUsage)

    @classmethod
    def from_message(
        cls,
        model: str,
        content: str,
        usage: Optional[dict[str, int]] = None,
    ) -> "ChatResponse":
        """Create a ChatResponse from a simple message string."""
        return cls(
            model=model,
            choices=[
                ChatResponseChoice(
                    message=ChatMessage(role="assistant", content=content)
                )
            ],
            usage=ChatResponseUsage(**(usage or {})),
        )


@dataclass(order=True)
class QueuedRequest:
    """
    A request queued for processing.

    Ordering is by (priority, timestamp) to ensure FIFO within same priority.
    The request_id, request, and future are excluded from comparison.
    """
    priority: Priority
    timestamp: float = field(compare=True)
    request_id: UUID = field(compare=False, default_factory=uuid4)
    request: ChatRequest = field(compare=False, default=None)
    future: Future = field(compare=False, default=None, repr=False)


class GatewayStats(BaseModel):
    """Gateway statistics and metrics."""
    queue_size: int = Field(default=0, description="Current queue size")
    requests_processed: int = Field(default=0, description="Total requests processed")
    requests_high_priority: int = Field(default=0, description="HIGH priority requests")
    requests_low_priority: int = Field(default=0, description="LOW priority requests")
    avg_response_time_ms: float = Field(default=0.0, description="Average response time in ms")
    is_processing: bool = Field(default=False, description="Whether worker is busy")
    uptime_seconds: float = Field(default=0.0, description="Gateway uptime")
