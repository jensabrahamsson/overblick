"""Tests for FastAPI application."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from overblick.gateway.models import ChatResponse, ChatMessage, Priority
from overblick.gateway.ollama_client import OllamaConnectionError


class TestFastAPIApp:
    """Tests for FastAPI endpoints."""

    @pytest.fixture
    def mock_queue_manager(self):
        qm = MagicMock()
        qm.is_running = True
        qm.queue_size = 0
        qm.client = AsyncMock()
        qm.client.health_check = AsyncMock(return_value=True)
        qm.client.list_models = AsyncMock(return_value=["qwen3:8b"])
        qm.submit = AsyncMock(return_value=ChatResponse.from_message(
            model="qwen3:8b",
            content="Test response",
        ))
        qm.get_stats = MagicMock(return_value=MagicMock(
            queue_size=0,
            requests_processed=10,
            requests_high_priority=5,
            requests_low_priority=5,
            avg_response_time_ms=100.0,
            is_processing=False,
            uptime_seconds=3600.0,
        ))
        return qm

    @pytest.fixture
    def client(self, mock_queue_manager):
        with patch("overblick.gateway.app._queue_manager", mock_queue_manager):
            with patch("overblick.gateway.app.get_queue_manager", return_value=mock_queue_manager):
                from overblick.gateway.app import app
                with TestClient(app, raise_server_exceptions=False) as client:
                    yield client

    def test_health_check(self, client, mock_queue_manager):
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["gateway"] == "running"
        assert data["ollama"] == "connected"

    def test_health_check_degraded(self, client, mock_queue_manager):
        mock_queue_manager.client.health_check = AsyncMock(return_value=False)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["ollama"] == "disconnected"

    def test_get_stats(self, client, mock_queue_manager):
        response = client.get("/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["requests_processed"] == 10
        assert data["queue_size"] == 0

    def test_list_models(self, client, mock_queue_manager):
        response = client.get("/models")

        assert response.status_code == 200
        data = response.json()
        assert "qwen3:8b" in data["models"]

    def test_chat_completion(self, client, mock_queue_manager):
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }

        response = client.post("/v1/chat/completions?priority=low", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "qwen3:8b"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["content"] == "Test response"

    def test_chat_completion_high_priority(self, client, mock_queue_manager):
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Urgent!"}],
        }

        response = client.post("/v1/chat/completions?priority=high", json=payload)

        assert response.status_code == 200
        call_args = mock_queue_manager.submit.call_args
        assert call_args[0][1] == Priority.HIGH

    def test_chat_completion_connection_error(self, client, mock_queue_manager):
        mock_queue_manager.submit = AsyncMock(
            side_effect=OllamaConnectionError("Cannot connect")
        )

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }

        response = client.post("/v1/chat/completions", json=payload)

        assert response.status_code == 503

    def test_chat_completion_timeout(self, client, mock_queue_manager):
        mock_queue_manager.submit = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }

        response = client.post("/v1/chat/completions", json=payload)

        assert response.status_code == 504

    def test_chat_completion_invalid_request(self, client):
        payload = {"model": "qwen3:8b"}

        response = client.post("/v1/chat/completions", json=payload)

        assert response.status_code == 422

    def test_chat_completion_default_priority(self, client, mock_queue_manager):
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }

        response = client.post("/v1/chat/completions", json=payload)

        assert response.status_code == 200
        call_args = mock_queue_manager.submit.call_args
        assert call_args[0][1] == Priority.LOW
