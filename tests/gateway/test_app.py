"""Tests for FastAPI application."""

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from overblick.gateway.deepseek_client import (
    DeepseekConnectionError,
    DeepseekError,
    DeepseekTimeoutError,
)
from overblick.gateway.models import ChatMessage, ChatResponse, Priority
from overblick.gateway.ollama_client import (
    OllamaConnectionError,
    OllamaError,
    OllamaTimeoutError,
)


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
        registry.get_backend_info = MagicMock(
            return_value={
                "local": {"type": "ollama", "model": "qwen3:8b"},
            }
        )
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
        qm.submit = AsyncMock(
            return_value=ChatResponse.from_message(
                model="qwen3:8b",
                content="Test response",
            )
        )
        qm.get_stats = MagicMock(
            return_value=MagicMock(
                queue_size=0,
                requests_processed=10,
                requests_high_priority=5,
                requests_low_priority=5,
                avg_response_time_ms=100.0,
                is_processing=False,
                uptime_seconds=3600.0,
            )
        )
        return qm

    @pytest.fixture
    def client(self, mock_queue_manager, mock_backend_registry) -> Generator[TestClient]:
        with (
            patch("overblick.gateway.app._queue_manager", mock_queue_manager),
            patch("overblick.gateway.app._backend_registry", mock_backend_registry),
            patch(
                "overblick.gateway.app.get_queue_manager",
                return_value=mock_queue_manager,
            ),
            patch(
                "overblick.gateway.app.get_backend_registry",
                return_value=mock_backend_registry,
            ),
        ):
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
        mock_queue_manager.submit = AsyncMock(side_effect=OllamaConnectionError("Cannot connect"))

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }

        response = client.post("/v1/chat/completions", json=payload)

        assert response.status_code == 503

    def test_chat_completion_timeout(self, client, mock_queue_manager):
        mock_queue_manager.submit = AsyncMock(side_effect=TimeoutError())

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
        registry.get_backend_info = MagicMock(
            return_value={
                "local": {"type": "ollama", "model": "qwen3:8b"},
            }
        )
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
    def client(self, mock_queue_manager, mock_backend_registry) -> Generator[TestClient]:
        with (
            patch("overblick.gateway.app._queue_manager", mock_queue_manager),
            patch("overblick.gateway.app._backend_registry", mock_backend_registry),
            patch(
                "overblick.gateway.app.get_queue_manager",
                return_value=mock_queue_manager,
            ),
            patch(
                "overblick.gateway.app.get_backend_registry",
                return_value=mock_backend_registry,
            ),
        ):
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


class TestGetGlobalAccessors:
    """Tests for get_queue_manager/get_backend_registry when not initialized."""

    def test_should_raise_when_queue_manager_not_initialized(self):
        import overblick.gateway.app as app_mod

        saved = app_mod._queue_manager
        try:
            app_mod._queue_manager = None
            with pytest.raises(RuntimeError, match="Queue manager not initialized"):
                app_mod.get_queue_manager()
        finally:
            app_mod._queue_manager = saved

    def test_should_raise_when_backend_registry_not_initialized(self):
        import overblick.gateway.app as app_mod

        saved = app_mod._backend_registry
        try:
            app_mod._backend_registry = None
            with pytest.raises(RuntimeError, match="Backend registry not initialized"):
                app_mod.get_backend_registry()
        finally:
            app_mod._backend_registry = saved

    def test_should_return_queue_manager_when_initialized(self):
        import overblick.gateway.app as app_mod

        mock_qm = MagicMock()
        saved = app_mod._queue_manager
        try:
            app_mod._queue_manager = mock_qm
            assert app_mod.get_queue_manager() is mock_qm
        finally:
            app_mod._queue_manager = saved

    def test_should_return_backend_registry_when_initialized(self):
        import overblick.gateway.app as app_mod

        mock_registry = MagicMock()
        saved = app_mod._backend_registry
        try:
            app_mod._backend_registry = mock_registry
            assert app_mod.get_backend_registry() is mock_registry
        finally:
            app_mod._backend_registry = saved


class TestHealthStarvationRisk:
    """Tests for health check starvation risk levels."""

    @pytest.fixture
    def mock_backend_registry(self):
        registry = MagicMock()
        registry.available_backends = ["local"]
        registry.default_backend = "local"
        registry.health_check_all = AsyncMock(return_value={"local": True})
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(
            return_value={"local": {"type": "ollama", "model": "qwen3:8b"}}
        )
        return registry

    @pytest.fixture
    def mock_queue_manager(self):
        qm = MagicMock()
        qm.is_running = True
        qm.get_stats = MagicMock(
            return_value=MagicMock(
                avg_response_time_ms=100.0,
                is_processing=False,
            )
        )
        return qm

    def _make_client(self, mock_queue_manager, mock_backend_registry):
        with (
            patch("overblick.gateway.app._queue_manager", mock_queue_manager),
            patch("overblick.gateway.app._backend_registry", mock_backend_registry),
            patch("overblick.gateway.app.get_queue_manager", return_value=mock_queue_manager),
            patch("overblick.gateway.app.get_backend_registry", return_value=mock_backend_registry),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                return client.get("/health").json()

    def test_should_report_medium_starvation_risk(self, mock_queue_manager, mock_backend_registry):
        mock_queue_manager.queue_size = 5
        data = self._make_client(mock_queue_manager, mock_backend_registry)
        assert data["gpu_starvation_risk"] == "medium"

    def test_should_report_high_starvation_risk(self, mock_queue_manager, mock_backend_registry):
        mock_queue_manager.queue_size = 10
        data = self._make_client(mock_queue_manager, mock_backend_registry)
        assert data["gpu_starvation_risk"] == "high"


class TestDeepseekBackendHealth:
    """Tests for deepseek backend status label in health check."""

    def test_should_show_cloud_configured_for_deepseek(self):
        registry = MagicMock()
        registry.available_backends = ["deepseek"]
        registry.default_backend = "deepseek"
        registry.health_check_all = AsyncMock(return_value={"deepseek": True})
        registry.get_model = MagicMock(return_value="deepseek-chat")
        registry.get_backend_info = MagicMock(
            return_value={"deepseek": {"type": "deepseek", "model": "deepseek-chat"}}
        )

        qm = MagicMock()
        qm.is_running = True
        qm.queue_size = 0
        qm.get_stats = MagicMock(
            return_value=MagicMock(avg_response_time_ms=50.0, is_processing=False)
        )

        with (
            patch("overblick.gateway.app._queue_manager", qm),
            patch("overblick.gateway.app._backend_registry", registry),
            patch("overblick.gateway.app.get_queue_manager", return_value=qm),
            patch("overblick.gateway.app.get_backend_registry", return_value=registry),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                data = client.get("/health").json()

        assert data["backends"]["deepseek"]["status"] == "cloud_configured"


class TestAPIKeyVerification:
    """Tests for API key verification middleware."""

    @pytest.fixture
    def mock_backend_registry(self):
        registry = MagicMock()
        registry.available_backends = ["local"]
        registry.default_backend = "local"
        registry.health_check_all = AsyncMock(return_value={"local": True})
        registry.get_client = MagicMock()
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(
            return_value={"local": {"type": "ollama", "model": "qwen3:8b"}}
        )
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
        qm.get_stats = MagicMock(
            return_value=MagicMock(
                queue_size=0, requests_processed=0, requests_high_priority=0,
                requests_low_priority=0, avg_response_time_ms=0, is_processing=False,
                uptime_seconds=0,
            )
        )
        return qm

    def _make_client(self, mock_queue_manager, mock_backend_registry, api_key):
        from overblick.gateway.config import GatewayConfig

        config = GatewayConfig(api_key=api_key)
        with (
            patch("overblick.gateway.app._queue_manager", mock_queue_manager),
            patch("overblick.gateway.app._backend_registry", mock_backend_registry),
            patch("overblick.gateway.app.get_queue_manager", return_value=mock_queue_manager),
            patch("overblick.gateway.app.get_backend_registry", return_value=mock_backend_registry),
            patch("overblick.gateway.app.get_config", return_value=config),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                yield client

    def test_should_reject_when_api_key_configured_but_missing(
        self, mock_queue_manager, mock_backend_registry
    ):
        for client in self._make_client(mock_queue_manager, mock_backend_registry, "secret-key"):
            response = client.get("/stats")
            assert response.status_code == 401

    def test_should_reject_wrong_api_key(
        self, mock_queue_manager, mock_backend_registry
    ):
        for client in self._make_client(mock_queue_manager, mock_backend_registry, "secret-key"):
            response = client.get("/stats", headers={"X-API-Key": "wrong-key"})
            assert response.status_code == 401


class TestListBackends:
    """Tests for the /backends endpoint."""

    def test_should_list_backends_with_health(self):
        registry = MagicMock()
        registry.available_backends = ["local", "cloud"]
        registry.default_backend = "local"
        registry.health_check_all = AsyncMock(return_value={"local": True, "cloud": False})
        registry.get_model = MagicMock(side_effect=lambda n: "qwen3:8b" if n == "local" else "gpt")
        registry.get_client = MagicMock()
        registry.get_backend_info = MagicMock(return_value={
            "local": {"type": "ollama", "model": "qwen3:8b"},
            "cloud": {"type": "ollama", "model": "gpt"},
        })

        qm = MagicMock()

        with (
            patch("overblick.gateway.app._queue_manager", qm),
            patch("overblick.gateway.app._backend_registry", registry),
            patch("overblick.gateway.app.get_queue_manager", return_value=qm),
            patch("overblick.gateway.app.get_backend_registry", return_value=registry),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/backends")

        assert response.status_code == 200
        data = response.json()
        assert data["default"] == "local"
        assert data["backends"]["local"]["healthy"] is True
        assert data["backends"]["cloud"]["healthy"] is False


class TestListModelsErrors:
    """Tests for /models endpoint error paths."""

    def _make_client_with_registry(self, registry):
        qm = MagicMock()
        with (
            patch("overblick.gateway.app._queue_manager", qm),
            patch("overblick.gateway.app._backend_registry", registry),
            patch("overblick.gateway.app.get_queue_manager", return_value=qm),
            patch("overblick.gateway.app.get_backend_registry", return_value=registry),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                yield client

    def test_should_return_400_for_invalid_backend(self):
        registry = MagicMock()
        registry.get_client = MagicMock(side_effect=ValueError("Unknown backend"))

        for client in self._make_client_with_registry(registry):
            response = client.get("/models?backend=nonexistent")
            assert response.status_code == 400

    def test_should_return_503_for_connection_error(self):
        mock_client = AsyncMock()
        mock_client.list_models = AsyncMock(side_effect=OllamaConnectionError("No conn"))

        registry = MagicMock()
        registry.default_backend = "local"
        registry.get_client = MagicMock(return_value=mock_client)

        for client in self._make_client_with_registry(registry):
            response = client.get("/models")
            assert response.status_code == 503


class TestChatCompletionAdvanced:
    """Tests for advanced chat completion paths."""

    @pytest.fixture
    def mock_backend_registry(self):
        registry = MagicMock()
        registry.available_backends = ["local", "deepseek"]
        registry.default_backend = "local"
        registry.health_check_all = AsyncMock(
            return_value={"local": True, "deepseek": True}
        )
        registry.get_client = MagicMock()
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(
            return_value={
                "local": {"type": "ollama", "model": "qwen3:8b"},
                "deepseek": {"type": "deepseek", "model": "deepseek-chat"},
            }
        )
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.list_models = AsyncMock(return_value=["qwen3:8b"])
        registry.get_client.return_value = mock_client
        registry.close_all = AsyncMock()
        return registry

    @pytest.fixture
    def mock_queue_manager(self):
        qm = MagicMock()
        qm.is_running = True
        qm.queue_size = 0
        qm.submit = AsyncMock(
            return_value=ChatResponse.from_message(model="qwen3:8b", content="ok")
        )
        qm.get_stats = MagicMock(
            return_value=MagicMock(
                avg_response_time_ms=100.0,
                is_processing=False,
            )
        )
        qm.start = AsyncMock()
        qm.stop = AsyncMock()
        return qm

    @pytest.fixture
    def mock_router(self):
        router = MagicMock()
        router.resolve_backend = MagicMock(return_value="deepseek")
        return router

    @pytest.fixture
    def client(
        self, mock_queue_manager, mock_backend_registry, mock_router
    ) -> Generator[TestClient]:
        import overblick.gateway.app as app_module

        with (
            patch(
                "overblick.gateway.app.get_queue_manager",
                return_value=mock_queue_manager,
            ),
            patch(
                "overblick.gateway.app.get_backend_registry",
                return_value=mock_backend_registry,
            ),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                # Override globals AFTER lifespan has run
                app_module._router = mock_router
                app_module._backend_registry = mock_backend_registry
                app_module._queue_manager = mock_queue_manager
                yield client

    def test_should_use_router_with_explicit_backend(
        self, client, mock_queue_manager, mock_router
    ):
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post(
            "/v1/chat/completions?priority=low&backend=deepseek", json=payload
        )
        assert response.status_code == 200
        mock_router.resolve_backend.assert_called_with(explicit_backend="deepseek")

    def test_should_override_model_for_einstein_complexity(
        self, client, mock_queue_manager, mock_router
    ):
        mock_router.resolve_backend.return_value = "deepseek"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Complex reasoning"}],
        }
        response = client.post(
            "/v1/chat/completions?priority=high&complexity=einstein", json=payload
        )
        assert response.status_code == 200
        # Verify model was overridden to deepseek-reasoner
        call_args = mock_queue_manager.submit.call_args
        assert call_args[0][0].model == "deepseek-reasoner"

    def test_should_fallback_on_connection_error(
        self, client, mock_queue_manager, mock_router, mock_backend_registry
    ):
        # First call fails, second succeeds
        mock_queue_manager.submit = AsyncMock(
            side_effect=[
                OllamaConnectionError("primary failed"),
                ChatResponse.from_message(model="qwen3:8b", content="from fallback"),
            ]
        )
        mock_router.resolve_backend.side_effect = [
            "deepseek",  # initial resolve
            "local",  # fallback resolve
        ]
        mock_backend_registry.default_backend = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post(
            "/v1/chat/completions?priority=low&complexity=high", json=payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "from fallback"

    def test_should_return_503_when_fallback_also_fails(
        self, client, mock_queue_manager, mock_router, mock_backend_registry
    ):
        mock_queue_manager.submit = AsyncMock(
            side_effect=OllamaConnectionError("all failed")
        )
        mock_router.resolve_backend.side_effect = [
            "deepseek",  # initial resolve
            "local",  # fallback resolve (different from original)
        ]
        mock_backend_registry.default_backend = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post(
            "/v1/chat/completions?priority=low&complexity=high", json=payload
        )
        assert response.status_code == 503

    def test_should_handle_deepseek_connection_error(
        self, client, mock_queue_manager, mock_router
    ):
        mock_queue_manager.submit = AsyncMock(
            side_effect=DeepseekConnectionError("DS failed")
        )
        mock_router.resolve_backend.return_value = "local"  # no fallback different from resolved

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post("/v1/chat/completions?priority=low", json=payload)
        assert response.status_code == 503

    def test_should_handle_queue_full(self, client, mock_queue_manager, mock_router):
        mock_queue_manager.submit = AsyncMock(side_effect=asyncio.QueueFull())
        mock_router.resolve_backend.return_value = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post("/v1/chat/completions?priority=low", json=payload)
        assert response.status_code == 503

    def test_should_handle_ollama_timeout_error(self, client, mock_queue_manager, mock_router):
        mock_queue_manager.submit = AsyncMock(side_effect=OllamaTimeoutError("slow"))
        mock_router.resolve_backend.return_value = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post("/v1/chat/completions?priority=low", json=payload)
        assert response.status_code == 504

    def test_should_handle_deepseek_timeout_error(self, client, mock_queue_manager, mock_router):
        mock_queue_manager.submit = AsyncMock(side_effect=DeepseekTimeoutError("slow"))
        mock_router.resolve_backend.return_value = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post("/v1/chat/completions?priority=low", json=payload)
        assert response.status_code == 504

    def test_should_handle_ollama_error(self, client, mock_queue_manager, mock_router):
        mock_queue_manager.submit = AsyncMock(side_effect=OllamaError("inference fail"))
        mock_router.resolve_backend.return_value = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post("/v1/chat/completions?priority=low", json=payload)
        assert response.status_code == 500

    def test_should_handle_deepseek_error(self, client, mock_queue_manager, mock_router):
        mock_queue_manager.submit = AsyncMock(side_effect=DeepseekError("inference fail"))
        mock_router.resolve_backend.return_value = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post("/v1/chat/completions?priority=low", json=payload)
        assert response.status_code == 500

    def test_should_handle_value_error(self, client, mock_queue_manager, mock_router):
        mock_queue_manager.submit = AsyncMock(side_effect=ValueError("bad request"))
        mock_router.resolve_backend.return_value = "local"

        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        response = client.post("/v1/chat/completions?priority=low", json=payload)
        assert response.status_code == 400


class TestEmbeddingsEndpoint:
    """Tests for the /v1/embeddings endpoint."""

    @pytest.fixture
    def client_with_embed(self):
        mock_client = AsyncMock()
        mock_client.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])

        registry = MagicMock()
        registry.default_backend = "local"
        registry.available_backends = ["local"]
        registry.get_client = MagicMock(return_value=mock_client)
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(
            return_value={"local": {"type": "ollama", "model": "qwen3:8b"}}
        )
        registry.health_check_all = AsyncMock(return_value={"local": True})

        qm = MagicMock()

        with (
            patch("overblick.gateway.app._queue_manager", qm),
            patch("overblick.gateway.app._backend_registry", registry),
            patch("overblick.gateway.app.get_queue_manager", return_value=qm),
            patch("overblick.gateway.app.get_backend_registry", return_value=registry),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, mock_client

    def test_should_create_embedding(self, client_with_embed):
        client, _ = client_with_embed
        response = client.post("/v1/embeddings?text=hello&model=nomic-embed-text")
        assert response.status_code == 200
        data = response.json()
        assert data["embedding"] == [0.1, 0.2, 0.3]
        assert data["model"] == "nomic-embed-text"

    def test_should_return_501_when_embed_not_supported(self):
        mock_client = MagicMock(spec=[])  # empty spec = no embed attr

        registry = MagicMock()
        registry.available_backends = ["local"]
        registry.default_backend = "local"
        registry.get_client = MagicMock(return_value=mock_client)
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(
            return_value={"local": {"type": "ollama", "model": "qwen3:8b"}}
        )
        registry.health_check_all = AsyncMock(return_value={"local": True})

        qm = MagicMock()

        with (
            patch("overblick.gateway.app._queue_manager", qm),
            patch("overblick.gateway.app._backend_registry", registry),
            patch("overblick.gateway.app.get_queue_manager", return_value=qm),
            patch("overblick.gateway.app.get_backend_registry", return_value=registry),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.post("/v1/embeddings?text=hello")
                assert response.status_code == 501

    def test_should_return_503_on_connection_error(self, client_with_embed):
        client, mock_client = client_with_embed
        mock_client.embed = AsyncMock(side_effect=OllamaConnectionError("no conn"))
        response = client.post("/v1/embeddings?text=hello")
        assert response.status_code == 503

    def test_should_return_500_on_ollama_error(self, client_with_embed):
        client, mock_client = client_with_embed
        mock_client.embed = AsyncMock(side_effect=OllamaError("embed fail"))
        response = client.post("/v1/embeddings?text=hello")
        assert response.status_code == 500


class TestGenericExceptionHandler:
    """Tests for the catch-all exception handler."""

    def test_should_return_500_on_unexpected_error(self):
        qm = MagicMock()
        qm.is_running = True
        qm.queue_size = 0
        qm.submit = AsyncMock(side_effect=TypeError("unexpected"))

        registry = MagicMock()
        registry.available_backends = ["local"]
        registry.default_backend = "local"
        registry.get_client = MagicMock()
        registry.get_model = MagicMock(return_value="qwen3:8b")
        registry.get_backend_info = MagicMock(
            return_value={"local": {"type": "ollama", "model": "qwen3:8b"}}
        )
        registry.health_check_all = AsyncMock(return_value={"local": True})

        with (
            patch("overblick.gateway.app._queue_manager", qm),
            patch("overblick.gateway.app._backend_registry", registry),
            patch("overblick.gateway.app._router", None),
            patch("overblick.gateway.app.get_queue_manager", return_value=qm),
            patch("overblick.gateway.app.get_backend_registry", return_value=registry),
        ):
            from overblick.gateway.app import app

            with TestClient(app, raise_server_exceptions=False) as client:
                payload = {
                    "model": "qwen3:8b",
                    "messages": [{"role": "user", "content": "Hello!"}],
                }
                response = client.post("/v1/chat/completions", json=payload)
                assert response.status_code == 500
                assert response.json()["detail"] == "Internal server error"


class TestPriorityParsing:
    """Tests for priority parsing edge cases in chat_completion."""

    @pytest.mark.asyncio
    async def test_should_default_to_low_on_attribute_error(self):
        """Cover the except (ValueError, AttributeError) path.

        FastAPI always passes a string, so we test by directly calling
        the endpoint function with a mock priority that has no .lower().
        """
        from overblick.gateway.app import chat_completion
        from overblick.gateway.models import ChatRequest

        qm = MagicMock()
        qm.submit = AsyncMock(
            return_value=ChatResponse.from_message(model="qwen3:8b", content="ok")
        )

        request = ChatRequest(
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Hello!")],
        )

        with (
            patch("overblick.gateway.app.get_queue_manager", return_value=qm),
            patch("overblick.gateway.app._router", None),
        ):
            # Pass a priority with no .lower() method (int, triggers AttributeError)
            await chat_completion(request=request, priority=42)  # type: ignore[arg-type]
            call_args = qm.submit.call_args
            assert call_args[0][1] == Priority.LOW


class TestLifespanCoverage:
    """Tests for lifespan startup/shutdown paths."""

    @pytest.mark.asyncio
    async def test_should_log_connected_backends_on_startup(self):
        """Cover the healthy backend logging in lifespan.

        We run the lifespan context manager directly with mocked dependencies
        to ensure the healthy-backend logging path is covered.
        """
        from overblick.gateway.app import lifespan

        mock_registry = MagicMock()
        mock_registry.health_check_all = AsyncMock(return_value={"local": True})
        mock_registry.get_client = MagicMock(return_value=MagicMock())
        mock_registry.close_all = AsyncMock()
        mock_registry.available_backends = ["local"]
        mock_registry.default_backend = "local"

        mock_qm_cls = MagicMock()
        mock_qm_instance = MagicMock()
        mock_qm_instance.start = AsyncMock()
        mock_qm_instance.stop = AsyncMock()
        mock_qm_cls.return_value = mock_qm_instance

        # Create a fresh app to avoid middleware conflicts
        from fastapi import FastAPI as FA
        fresh_app = FA()

        with (
            patch("overblick.gateway.app.BackendRegistry", return_value=mock_registry),
            patch("overblick.gateway.app.QueueManager", mock_qm_cls),
            patch("overblick.gateway.app.RequestRouter"),
        ):
            async with lifespan(fresh_app):
                pass


class TestMainBlock:
    """Tests for __main__ guard."""

    def test_should_cover_main_block(self):
        """Cover the `if __name__ == '__main__': run_server()` line."""
        import runpy
        import sys

        mock_uvicorn = MagicMock()
        sys.modules["uvicorn"] = mock_uvicorn

        try:
            from overblick.gateway.config import GatewayConfig

            with patch("overblick.gateway.app.get_config", return_value=GatewayConfig()):
                runpy.run_module("overblick.gateway.app", run_name="__main__")
                mock_uvicorn.run.assert_called()
        finally:
            del sys.modules["uvicorn"]


class TestRunServer:
    """Tests for run_server entry point."""

    def test_should_call_uvicorn_run(self):
        import sys

        from overblick.gateway.config import GatewayConfig

        mock_uvicorn = MagicMock()
        sys.modules["uvicorn"] = mock_uvicorn

        try:
            config = GatewayConfig(api_host="0.0.0.0", api_port=8200)
            with patch("overblick.gateway.app.get_config", return_value=config):
                from overblick.gateway.app import run_server

                run_server()

                mock_uvicorn.run.assert_called_once_with(
                    "overblick.gateway.app:app",
                    host="0.0.0.0",
                    port=8200,
                    reload=False,
                    log_level="info",
                )
        finally:
            del sys.modules["uvicorn"]

    def test_should_use_provided_host_and_port(self):
        import sys

        from overblick.gateway.config import GatewayConfig

        mock_uvicorn = MagicMock()
        sys.modules["uvicorn"] = mock_uvicorn

        try:
            config = GatewayConfig(api_host="0.0.0.0", api_port=8200)
            with patch("overblick.gateway.app.get_config", return_value=config):
                from overblick.gateway.app import run_server

                run_server(host="127.0.0.1", port=9999)

                mock_uvicorn.run.assert_called_once_with(
                    "overblick.gateway.app:app",
                    host="127.0.0.1",
                    port=9999,
                    reload=False,
                    log_level="info",
                )
        finally:
            del sys.modules["uvicorn"]
