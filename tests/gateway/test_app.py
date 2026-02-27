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
    def mock_backend_registry(self):
        registry = MagicMock()
        registry.available_backends = ["local"]
        registry.default_backend = "local"
        registry.health_check_all = AsyncMock(return_value={"local": True})
        registry.get_client = MagicMock()
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(return_value={
            "local": {"type": "ollama", "model": "qwen3:8b"},
        })
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.list_models = AsyncMock(return_value=["qwen3:8b"])
        registry.get_client.return_value = mock_client
        return registry

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
    def client(self, mock_queue_manager, mock_backend_registry):
        with patch("overblick.gateway.app._queue_manager", mock_queue_manager), \
             patch("overblick.gateway.app._backend_registry", mock_backend_registry), \
             patch("overblick.gateway.app.get_queue_manager", return_value=mock_queue_manager), \
             patch("overblick.gateway.app.get_backend_registry", return_value=mock_backend_registry):
            from overblick.gateway.app import app
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client

    def test_health_check(self, client, mock_queue_manager, mock_backend_registry):
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["gateway"] == "running"
        assert data["backends"]["local"]["status"] == "connected"
        assert data["backends"]["local"]["type"] == "ollama"
        assert data["backends"]["local"]["model"] == "qwen3:8b"
        assert data["backends"]["local"]["default"] is True

    def test_health_check_degraded(self, client, mock_queue_manager, mock_backend_registry):
        mock_backend_registry.health_check_all = AsyncMock(return_value={"local": False})

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["backends"]["local"]["status"] == "disconnected"

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

    def test_chat_completion_with_complexity(self, client, mock_queue_manager):
        """Complexity parameter is accepted and processed."""
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Complex task"}],
        }

        response = client.post(
            "/v1/chat/completions?priority=low&complexity=high",
            json=payload,
        )

        assert response.status_code == 200

    def test_chat_completion_without_complexity(self, client, mock_queue_manager):
        """Requests without complexity still work (backward compat)."""
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Simple task"}],
        }

        response = client.post(
            "/v1/chat/completions?priority=low",
            json=payload,
        )

        assert response.status_code == 200

    def test_chat_completion_with_ultra_complexity(self, client, mock_queue_manager):
        """Ultra complexity parameter is accepted and processed."""
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Precision task"}],
        }

        response = client.post(
            "/v1/chat/completions?priority=high&complexity=ultra",
            json=payload,
        )

        assert response.status_code == 200


class TestOriginMiddleware:
    """Tests for Origin header check middleware (Pass 1, fix 1.8)."""

    @pytest.fixture
    def mock_backend_registry(self):
        registry = MagicMock()
        registry.available_backends = ["local"]
        registry.default_backend = "local"
        registry.health_check_all = AsyncMock(return_value={"local": True})
        registry.get_client = MagicMock()
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(return_value={
            "local": {"type": "ollama", "model": "qwen3:8b"},
        })
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.list_models = AsyncMock(return_value=["qwen3:8b"])
        registry.get_client.return_value = mock_client
        return registry

    @pytest.fixture
    def mock_queue_manager(self):
        qm = MagicMock()
        qm.is_running = True
        qm.queue_size = 0
        qm.client = AsyncMock()
        return qm

    @pytest.fixture
    def client(self, mock_queue_manager, mock_backend_registry):
        with patch("overblick.gateway.app._queue_manager", mock_queue_manager), \
             patch("overblick.gateway.app._backend_registry", mock_backend_registry), \
             patch("overblick.gateway.app.get_queue_manager", return_value=mock_queue_manager), \
             patch("overblick.gateway.app.get_backend_registry", return_value=mock_backend_registry):
            from overblick.gateway.app import app
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client

    def test_no_origin_allowed(self, client):
        """Requests without Origin header are allowed."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_localhost_origin_allowed(self, client):
        """Requests from localhost are allowed."""
        response = client.get("/health", headers={"Origin": "http://localhost:8080"})
        assert response.status_code == 200

    def test_127_origin_allowed(self, client):
        """Requests from 127.0.0.1 are allowed."""
        response = client.get("/health", headers={"Origin": "http://127.0.0.1:3000"})
        assert response.status_code == 200

    def test_external_origin_rejected(self, client):
        """Requests from non-localhost origins are rejected."""
        response = client.get("/health", headers={"Origin": "https://evil.com"})
        assert response.status_code == 403
        assert "origin" in response.json()["detail"].lower()

    def test_external_origin_rejected_on_post(self, client):
        """POST requests with external origin are also rejected."""
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Origin": "https://attacker.com"},
        )
        assert response.status_code == 403
