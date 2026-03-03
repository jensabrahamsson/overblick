"""Tests for the Internet Gateway (secure reverse proxy)."""

import json
import time
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from overblick.gateway.inet_auth import APIKeyManager
from overblick.gateway.inet_audit import InetAuditLog
from overblick.gateway.inet_config import InternetGatewayConfig, reset_inet_config
from overblick.gateway.inet_middleware import ViolationTracker
from overblick.gateway.internet_gateway import app, _error_json


@pytest.fixture(autouse=True)
def _reset() -> Generator[None, None, None]:
    """Reset config singleton before each test."""
    reset_inet_config()
    yield
    reset_inet_config()


@pytest.fixture
def test_config(tmp_path: Path) -> InternetGatewayConfig:
    """Create a test config with temp data dir."""
    return InternetGatewayConfig(
        host="127.0.0.1",
        port=8201,
        tls_auto_selfsigned=False,
        internal_gateway_url="http://127.0.0.1:8200",
        data_dir=str(tmp_path),
        global_rpm=60,
        per_key_rpm=30,
        max_tokens_cap=4096,
        auto_ban_threshold=5,
        auto_ban_window=300,
        auto_ban_duration=3600,
    )


@pytest.fixture
def key_manager(tmp_path: Path) -> Generator[APIKeyManager, None, None]:
    """Create a key manager with temp database."""
    mgr = APIKeyManager(tmp_path / "keys.db")
    yield mgr
    mgr.close()


@pytest.fixture
def audit_log(tmp_path: Path) -> Generator[InetAuditLog, None, None]:
    """Create an audit log with temp database."""
    audit = InetAuditLog(tmp_path / "audit.db")
    yield audit
    audit.close()


@pytest.fixture
def violation_tracker() -> ViolationTracker:
    """Create a violation tracker."""
    return ViolationTracker(window_seconds=300, threshold=5, ban_duration=3600)


@pytest.fixture
def mock_upstream_response():
    """Create a mock upstream response for chat completions."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "qwen3:8b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


@pytest.fixture
def client(
    tmp_path: Path,
    test_config: InternetGatewayConfig,
    key_manager: APIKeyManager,
    audit_log: InetAuditLog,
    violation_tracker: ViolationTracker,
) -> Generator[TestClient, None, None]:
    """Create a test client with mocked dependencies."""
    import overblick.gateway.internet_gateway as gw
    from overblick.core.security.rate_limiter import RateLimiter

    # Inject test dependencies
    gw._config = test_config
    gw._key_manager = key_manager
    gw._audit_log = audit_log
    gw._violation_tracker = violation_tracker
    gw._per_key_limiter = RateLimiter(
        max_tokens=float(test_config.per_key_rpm),
        refill_rate=test_config.per_key_rpm / 60.0,
    )
    gw._http_client = AsyncMock(spec=httpx.AsyncClient)

    # Set up app state
    app.state.violation_tracker = violation_tracker

    tc = TestClient(app, raise_server_exceptions=False)
    yield tc

    # Cleanup
    gw._config = None
    gw._key_manager = None
    gw._audit_log = None
    gw._violation_tracker = None
    gw._per_key_limiter = None
    gw._http_client = None


def _create_key_and_header(key_manager: APIKeyManager, name: str = "test") -> tuple[str, dict]:
    """Helper: create a key and return (raw_key, auth_header_dict)."""
    raw_key, _ = key_manager.create_key(name=name)
    return raw_key, {"Authorization": f"Bearer {raw_key}"}


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_no_auth_required(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "internet-gateway"

    def test_health_returns_json(self, client: TestClient):
        response = client.get("/health")
        assert response.headers["content-type"] == "application/json"


class TestAuthentication:
    """Tests for API key authentication."""

    def test_no_auth_returns_401(self, client: TestClient):
        response = client.post(
            "/v1/chat/completions",
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["type"] == "authentication_error"

    def test_invalid_key_returns_401(self, client: TestClient):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-ob-invalidinvalidinvalidinvalidin"},
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 401

    def test_valid_key_accepted(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
        mock_upstream_response: dict,
    ):
        import overblick.gateway.internet_gateway as gw

        raw_key, headers = _create_key_and_header(key_manager)

        # Mock upstream success
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_upstream_response
        gw._http_client.request.return_value = mock_resp  # type: ignore

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert response.status_code == 200

    def test_bearer_prefix_required(self, client: TestClient, key_manager: APIKeyManager):
        raw_key, _ = _create_key_and_header(key_manager)

        # Missing "Bearer " prefix
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": raw_key},
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 401

    def test_models_endpoint_requires_auth(self, client: TestClient):
        response = client.get("/v1/models")
        assert response.status_code == 401

    def test_embeddings_endpoint_requires_auth(self, client: TestClient):
        response = client.post(
            "/v1/embeddings",
            json={"input": "test text", "model": "nomic-embed-text"},
        )
        assert response.status_code == 401


class TestChatCompletions:
    """Tests for the /v1/chat/completions proxy endpoint."""

    def test_successful_proxy(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
        mock_upstream_response: dict,
    ):
        import overblick.gateway.internet_gateway as gw

        raw_key, headers = _create_key_and_header(key_manager)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_upstream_response
        gw._http_client.request.return_value = mock_resp  # type: ignore

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "qwen3:8b"
        assert data["choices"][0]["message"]["content"] == "Hello!"

    def test_max_tokens_clamped(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
        mock_upstream_response: dict,
    ):
        import overblick.gateway.internet_gateway as gw

        raw_key, headers = _create_key_and_header(key_manager)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_upstream_response
        gw._http_client.request.return_value = mock_resp  # type: ignore

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 32000,  # over key cap (4096) but within Pydantic limit
            },
        )

        assert response.status_code == 200
        # Verify the proxied request had clamped tokens
        call_args = gw._http_client.request.call_args  # type: ignore
        proxied_body = json.loads(call_args.kwargs["content"])
        assert proxied_body["max_tokens"] <= 4096

    def test_invalid_body_returns_400(self, client: TestClient, key_manager: APIKeyManager):
        raw_key, headers = _create_key_and_header(key_manager)

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            content=b"not json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"

    def test_extra_fields_rejected(self, client: TestClient, key_manager: APIKeyManager):
        raw_key, headers = _create_key_and_header(key_manager)

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "hi"}],
                "unknown_field": "malicious",
            },
        )
        assert response.status_code == 400

    def test_model_not_allowed_returns_403(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
    ):
        raw_key, record = key_manager.create_key(
            name="restricted",
            allowed_models=["qwen3:8b"],
        )
        headers = {"Authorization": f"Bearer {raw_key}"}

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "not-allowed-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 403
        assert "not allowed" in response.json()["error"]["message"].lower()

    def test_upstream_500_returns_502(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
    ):
        import overblick.gateway.internet_gateway as gw

        raw_key, headers = _create_key_and_header(key_manager)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"detail": "Internal error"}
        gw._http_client.request.return_value = mock_resp  # type: ignore

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 502
        # Must not leak internal details
        data = response.json()
        assert "Internal error" not in json.dumps(data)
        assert data["error"]["type"] == "server_error"

    def test_upstream_connection_error_returns_502(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
    ):
        import overblick.gateway.internet_gateway as gw

        raw_key, headers = _create_key_and_header(key_manager)
        gw._http_client.request.side_effect = httpx.ConnectError("Connection refused")  # type: ignore

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 502

    def test_upstream_timeout_returns_504(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
    ):
        import overblick.gateway.internet_gateway as gw

        raw_key, headers = _create_key_and_header(key_manager)
        gw._http_client.request.side_effect = httpx.TimeoutException("Timed out")  # type: ignore

        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 504


class TestRateLimiting:
    """Tests for per-key rate limiting."""

    def test_rate_limit_exceeded(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
        mock_upstream_response: dict,
    ):
        import overblick.gateway.internet_gateway as gw
        from overblick.core.security.rate_limiter import RateLimiter

        # Set very low rate limit (1 RPM)
        gw._per_key_limiter = RateLimiter(max_tokens=1.0, refill_rate=1 / 60.0)

        raw_key, headers = _create_key_and_header(key_manager)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_upstream_response
        gw._http_client.request.return_value = mock_resp  # type: ignore

        # First request should succeed
        r1 = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r1.status_code == 200

        # Second request should be rate limited
        r2 = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r2.status_code == 429
        assert "Retry-After" in r2.headers


class TestErrorMasking:
    """Tests ensuring internal details are never leaked."""

    def test_404_for_unknown_routes(self, client: TestClient):
        response = client.get("/internal/secret/path")
        assert response.status_code == 404
        data = response.json()
        assert (
            "internal" not in json.dumps(data).lower()
            or "invalid_request_error" in data["error"]["type"]
        )

    def test_no_openapi_docs(self, client: TestClient):
        response = client.get("/docs")
        assert response.status_code == 404

    def test_no_redoc(self, client: TestClient):
        response = client.get("/redoc")
        assert response.status_code == 404

    def test_no_openapi_json(self, client: TestClient):
        response = client.get("/openapi.json")
        assert response.status_code == 404

    def test_security_headers_present(self, client: TestClient):
        response = client.get("/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Cache-Control") == "no-store"


class TestViolationTracker:
    """Tests for the auto-ban violation tracker."""

    def test_below_threshold_no_ban(self):
        tracker = ViolationTracker(threshold=5, window_seconds=300, ban_duration=3600)
        for _ in range(4):
            result = tracker.record_violation("1.2.3.4")
        assert result is False  # type: ignore
        assert tracker.is_banned("1.2.3.4") is False

    def test_at_threshold_triggers_ban(self):
        tracker = ViolationTracker(threshold=5, window_seconds=300, ban_duration=3600)
        for i in range(5):
            result = tracker.record_violation("1.2.3.4")
        assert result is True  # type: ignore
        assert tracker.is_banned("1.2.3.4") is True

    def test_ban_expires(self):
        tracker = ViolationTracker(threshold=2, window_seconds=300, ban_duration=1)

        tracker.record_violation("1.2.3.4")
        tracker.record_violation("1.2.3.4")

        assert tracker.is_banned("1.2.3.4") is True

        # Manually expire the ban
        tracker._bans["1.2.3.4"] = time.time() - 1
        assert tracker.is_banned("1.2.3.4") is False

    def test_different_ips_independent(self):
        tracker = ViolationTracker(threshold=3, window_seconds=300, ban_duration=3600)

        for _ in range(3):
            tracker.record_violation("1.1.1.1")

        assert tracker.is_banned("1.1.1.1") is True
        assert tracker.is_banned("2.2.2.2") is False


class TestAuditLogging:
    """Tests for audit trail writing."""

    def test_audit_entry_written(
        self,
        client: TestClient,
        key_manager: APIKeyManager,
        audit_log: InetAuditLog,
        mock_upstream_response: dict,
    ):
        import overblick.gateway.internet_gateway as gw

        raw_key, headers = _create_key_and_header(key_manager)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_upstream_response
        gw._http_client.request.return_value = mock_resp  # type: ignore

        client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )

        # Give the async write a moment (it's fire-and-forget)
        import time

        time.sleep(0.1)

        entries = audit_log.query(limit=10)
        assert len(entries) >= 1
        entry = entries[0]
        assert entry["path"] == "/v1/chat/completions"
        assert entry["status_code"] == 200

    def test_auth_failure_logged(
        self,
        client: TestClient,
        audit_log: InetAuditLog,
    ):
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-ob-invalidinvalidinvalidinvalidin"},
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )

        import time

        time.sleep(0.1)

        entries = audit_log.query(violation="auth_failure", limit=10)
        assert len(entries) >= 1
