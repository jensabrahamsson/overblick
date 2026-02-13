"""Tests for LLM Gateway models."""

import pytest
from pydantic import ValidationError

from overblick.gateway.models import (
    Priority,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatResponseChoice,
    ChatResponseUsage,
    QueuedRequest,
    GatewayStats,
)


class TestPriority:
    """Tests for Priority enum."""

    def test_priority_values(self):
        assert Priority.HIGH == 1
        assert Priority.LOW == 5

    def test_priority_ordering(self):
        assert Priority.HIGH < Priority.LOW

    def test_priority_comparison(self):
        assert Priority.HIGH != Priority.LOW
        assert Priority.HIGH <= Priority.LOW


class TestChatMessage:
    """Tests for ChatMessage model."""

    def test_valid_message(self):
        msg = ChatMessage(role="user", content="Hello!")
        assert msg.role == "user"
        assert msg.content == "Hello!"

    def test_empty_content(self):
        msg = ChatMessage(role="assistant", content="")
        assert msg.content == ""

    def test_missing_role(self):
        with pytest.raises(ValidationError):
            ChatMessage(content="Hello!")

    def test_missing_content(self):
        with pytest.raises(ValidationError):
            ChatMessage(role="user")


class TestChatRequest:
    """Tests for ChatRequest model."""

    def test_valid_request(self):
        request = ChatRequest(
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        assert request.model == "qwen3:8b"
        assert len(request.messages) == 1

    def test_default_values(self):
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Hi")]
        )
        assert request.model == "qwen3:8b"
        assert request.max_tokens == 2000
        assert request.temperature == 0.7

    def test_max_tokens_validation(self):
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Hi")],
            max_tokens=100,
        )
        assert request.max_tokens == 100

        with pytest.raises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="Hi")],
                max_tokens=0,
            )

        with pytest.raises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="Hi")],
                max_tokens=10000,
            )

    def test_temperature_validation(self):
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=1.5,
        )
        assert request.temperature == 1.5

        with pytest.raises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="Hi")],
                temperature=-0.1,
            )

        with pytest.raises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="Hi")],
                temperature=2.5,
            )


class TestChatResponse:
    """Tests for ChatResponse model."""

    def test_from_message(self):
        response = ChatResponse.from_message(
            model="qwen3:8b",
            content="Hello!",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        assert response.model == "qwen3:8b"
        assert len(response.choices) == 1
        assert response.choices[0].message.content == "Hello!"
        assert response.usage.total_tokens == 15

    def test_auto_generated_fields(self):
        response = ChatResponse.from_message("qwen3:8b", "Hi")
        assert response.id.startswith("chatcmpl-")
        assert response.created > 0
        assert response.object == "chat.completion"


class TestQueuedRequest:
    """Tests for QueuedRequest dataclass."""

    def test_ordering_by_priority(self):
        import time
        high = QueuedRequest(priority=Priority.HIGH, timestamp=time.time())
        low = QueuedRequest(priority=Priority.LOW, timestamp=time.time())
        assert high < low

    def test_ordering_fifo_same_priority(self):
        first = QueuedRequest(priority=Priority.LOW, timestamp=1000.0)
        second = QueuedRequest(priority=Priority.LOW, timestamp=1001.0)
        assert first < second

    def test_high_priority_jumps_queue(self):
        high_later = QueuedRequest(priority=Priority.HIGH, timestamp=2000.0)
        low_earlier = QueuedRequest(priority=Priority.LOW, timestamp=1000.0)
        assert high_later < low_earlier


class TestGatewayStats:
    """Tests for GatewayStats model."""

    def test_default_values(self):
        stats = GatewayStats()
        assert stats.queue_size == 0
        assert stats.requests_processed == 0
        assert stats.is_processing is False

    def test_custom_values(self):
        stats = GatewayStats(
            queue_size=5,
            requests_processed=100,
            requests_high_priority=60,
            requests_low_priority=40,
            avg_response_time_ms=1500.5,
            is_processing=True,
            uptime_seconds=3600.0,
        )
        assert stats.queue_size == 5
        assert stats.requests_processed == 100
        assert stats.requests_high_priority == 60
        assert stats.requests_low_priority == 40
