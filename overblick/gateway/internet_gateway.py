"""
Internet Gateway — Secure reverse proxy for the internal LLM Gateway.

Authenticates requests via API keys (Bearer token), enforces rate limits,
and proxies to the internal gateway on 127.0.0.1:8200. Never leaks
internal details in error responses.

Usage:
    python -m overblick internet-gateway
    python -m overblick internet-gateway --no-tls  # dev mode, localhost only
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from overblick.core.security.rate_limiter import RateLimiter

from .inet_audit import InetAuditLog
from .inet_auth import APIKeyManager
from .inet_config import InternetGatewayConfig, get_inet_config
from .inet_middleware import (
    GlobalRateLimitMiddleware,
    IPAllowlistMiddleware,
    IPBanMiddleware,
    RequestSizeLimitMiddleware,
    ViolationTracker,
)
from .inet_models import APIKeyRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Global instances (initialized in lifespan)
_key_manager: Optional[APIKeyManager] = None
_audit_log: Optional[InetAuditLog] = None
_http_client: Optional[httpx.AsyncClient] = None
_violation_tracker: Optional[ViolationTracker] = None
_per_key_limiter: Optional[RateLimiter] = None
_config: Optional[InternetGatewayConfig] = None


def _error_json(status: int, message: str, error_type: str) -> JSONResponse:
    """OpenAI-compatible error response."""
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type, "code": str(status)}},
    )


class ProxiedChatRequest(BaseModel):
    """Strict request body for chat completions (extra fields forbidden)."""
    model: str = Field(default="qwen3:8b")
    messages: list[dict] = Field(...)
    max_tokens: int = Field(default=2000, ge=1, le=32768)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)

    model_config = {"extra": "forbid"}


class EmbeddingRequest(BaseModel):
    """Strict request body for embeddings (extra fields forbidden)."""
    input: str | list[str] = Field(...)
    model: str = Field(default="nomic-embed-text")

    model_config = {"extra": "forbid"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and tear down global resources."""
    global _key_manager, _audit_log, _http_client, _violation_tracker, _per_key_limiter, _config

    _config = get_inet_config()
    data_dir = _config.resolved_data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    _key_manager = APIKeyManager(data_dir / "api_keys.db")
    _audit_log = InetAuditLog(data_dir / "audit.db")
    _audit_log.start_background_cleanup()

    _violation_tracker = ViolationTracker(
        window_seconds=_config.auto_ban_window,
        threshold=_config.auto_ban_threshold,
        ban_duration=_config.auto_ban_duration,
    )

    # Per-key rate limiter (keyed by key_id)
    _per_key_limiter = RateLimiter(
        max_tokens=float(_config.per_key_rpm),
        refill_rate=_config.per_key_rpm / 60.0,
    )

    _http_client = httpx.AsyncClient(
        base_url=_config.internal_gateway_url,
        timeout=httpx.Timeout(_config.request_timeout, connect=10.0),
    )

    logger.info(
        "Internet Gateway starting on %s:%d (TLS: %s, internal: %s)",
        _config.host, _config.port,
        "enabled" if _config.tls_enabled else "disabled",
        _config.internal_gateway_url,
    )

    yield

    logger.info("Shutting down Internet Gateway...")
    if _http_client:
        await _http_client.aclose()
    if _audit_log:
        _audit_log.close()
    if _key_manager:
        _key_manager.close()
    logger.info("Internet Gateway stopped")


app = FastAPI(
    title="Överblick Internet Gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def _setup_middleware(app: FastAPI) -> None:
    """Add middleware stack (outermost first = added last)."""
    config = get_inet_config()

    # Order matters: last added = outermost = runs first
    # We need a ViolationTracker that persists, so we create it here
    # and also use it in lifespan. The middleware instances are created
    # fresh here.
    tracker = ViolationTracker(
        window_seconds=config.auto_ban_window,
        threshold=config.auto_ban_threshold,
        ban_duration=config.auto_ban_duration,
    )

    # Innermost first (executed last):
    app.add_middleware(GlobalRateLimitMiddleware, rpm=config.global_rpm)
    if config.ip_allowlist:
        app.add_middleware(IPAllowlistMiddleware, allowlist=config.ip_allowlist)
    app.add_middleware(IPBanMiddleware, tracker=tracker)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=config.max_request_bytes)

    # Store tracker reference for use in route handlers
    app.state.violation_tracker = tracker


# Middleware must be added at import time (before first request)
# We defer this to avoid requiring config at import time
_middleware_initialized = False


@app.middleware("http")
async def ensure_middleware_and_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    return request.client.host if request.client else "unknown"


def _verify_bearer_token(request: Request) -> Optional[APIKeyRecord]:
    """Extract and verify Bearer token from Authorization header.

    Returns APIKeyRecord if valid, None otherwise.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    raw_key = auth_header[7:]  # Strip "Bearer " prefix
    return _key_manager.verify_key(raw_key)


async def _proxy_request(
    method: str,
    path: str,
    body: Optional[bytes] = None,
    headers: Optional[dict] = None,
) -> httpx.Response:
    """Forward a request to the internal gateway.

    Strips auth headers and adds internal API key if configured.
    """
    proxy_headers = {"Content-Type": "application/json"}

    # Add internal API key if configured
    if _config and _config.internal_api_key:
        proxy_headers["X-API-Key"] = _config.internal_api_key

    return await _http_client.request(
        method=method,
        url=path,
        content=body,
        headers=proxy_headers,
    )


# --- Health endpoint (no auth) ---

@app.get("/health")
async def health():
    """Public health check — reveals nothing about internals."""
    return {"status": "ok", "service": "internet-gateway"}


# --- Chat completions (auth required) ---

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy chat completion requests to internal gateway."""
    start_time = time.time()
    client_ip = _get_client_ip(request)
    key_record: Optional[APIKeyRecord] = None
    model_name = ""

    try:
        # 1. Authenticate
        key_record = _verify_bearer_token(request)
        if not key_record:
            _record_violation(client_ip, "auth_failure")
            _audit_log.log(
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                status_code=401, violation="auth_failure",
            )
            return _error_json(401, "Invalid or missing API key", "authentication_error")

        # 2. Per-key rate limit
        if not _per_key_limiter.allow(key_record.key_id):
            retry_after = _per_key_limiter.retry_after(key_record.key_id)
            _audit_log.log(
                key_id=key_record.key_id, key_name=key_record.name,
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                status_code=429, violation="rate_limit",
            )
            response = _error_json(429, "Rate limit exceeded", "rate_limit_error")
            response.headers["Retry-After"] = str(int(retry_after) + 1)
            response.headers["X-RateLimit-Limit"] = str(key_record.requests_per_minute)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        # 3. Parse and validate body
        try:
            body = await request.body()
            parsed = ProxiedChatRequest.model_validate_json(body)
        except Exception:
            _audit_log.log(
                key_id=key_record.key_id, key_name=key_record.name,
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                status_code=400, error="Invalid request body",
            )
            return _error_json(400, "Invalid request body", "invalid_request_error")

        model_name = parsed.model

        # 4. Permission checks
        if key_record.allowed_models and parsed.model not in key_record.allowed_models:
            _audit_log.log(
                key_id=key_record.key_id, key_name=key_record.name,
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                model=model_name, status_code=403, violation="model_not_allowed",
            )
            return _error_json(403, "Model not allowed for this key", "permission_error")

        # 5. Clamp max_tokens
        effective_cap = min(
            parsed.max_tokens,
            key_record.max_tokens_cap,
            _config.max_tokens_cap if _config else 4096,
        )
        if parsed.max_tokens != effective_cap:
            parsed.max_tokens = effective_cap

        # 6. Proxy to internal gateway
        proxy_body = parsed.model_dump_json().encode()
        try:
            upstream = await _proxy_request("POST", "/v1/chat/completions", body=proxy_body)
        except httpx.ConnectError:
            logger.error("Internal gateway unreachable")
            _audit_log.log(
                key_id=key_record.key_id, key_name=key_record.name,
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                model=model_name, status_code=502, error="upstream_unreachable",
            )
            return _error_json(502, "Service temporarily unavailable", "server_error")
        except httpx.TimeoutException:
            logger.error("Internal gateway timeout")
            _audit_log.log(
                key_id=key_record.key_id, key_name=key_record.name,
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                model=model_name, status_code=504, error="upstream_timeout",
            )
            return _error_json(504, "Request timed out", "server_error")

        # 7. Process upstream response
        latency_ms = (time.time() - start_time) * 1000

        if upstream.status_code >= 500:
            # Mask internal errors
            _audit_log.log(
                key_id=key_record.key_id, key_name=key_record.name,
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                model=model_name, status_code=502,
                latency_ms=latency_ms, error=f"upstream_{upstream.status_code}",
            )
            return _error_json(502, "Service temporarily unavailable", "server_error")

        # Extract token usage from response for audit
        req_tokens = 0
        resp_tokens = 0
        try:
            resp_data = upstream.json()
            usage = resp_data.get("usage", {})
            req_tokens = usage.get("prompt_tokens", 0)
            resp_tokens = usage.get("completion_tokens", 0)
        except Exception:
            pass

        # 8. Update usage stats
        _key_manager.update_usage(
            key_record.key_id,
            tokens=req_tokens + resp_tokens,
            ip=client_ip,
        )

        # 9. Audit success
        _audit_log.log(
            key_id=key_record.key_id, key_name=key_record.name,
            source_ip=client_ip, method="POST", path="/v1/chat/completions",
            model=model_name, status_code=upstream.status_code,
            request_tokens=req_tokens, response_tokens=resp_tokens,
            latency_ms=latency_ms,
        )

        # Pass through upstream response
        return JSONResponse(
            status_code=upstream.status_code,
            content=upstream.json(),
        )

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error("Unexpected error in chat_completions: %s", e, exc_info=True)
        if _audit_log:
            _audit_log.log(
                key_id=key_record.key_id if key_record else "",
                key_name=key_record.name if key_record else "",
                source_ip=client_ip, method="POST", path="/v1/chat/completions",
                model=model_name, status_code=500,
                latency_ms=latency_ms, error="internal_error",
            )
        return _error_json(500, "Internal server error", "server_error")


# --- Embeddings (auth required) ---

@app.post("/v1/embeddings")
async def embeddings(request: Request):
    """Proxy embedding requests to internal gateway."""
    client_ip = _get_client_ip(request)

    key_record = _verify_bearer_token(request)
    if not key_record:
        _record_violation(client_ip, "auth_failure")
        _audit_log.log(
            source_ip=client_ip, method="POST", path="/v1/embeddings",
            status_code=401, violation="auth_failure",
        )
        return _error_json(401, "Invalid or missing API key", "authentication_error")

    if not _per_key_limiter.allow(key_record.key_id):
        retry_after = _per_key_limiter.retry_after(key_record.key_id)
        response = _error_json(429, "Rate limit exceeded", "rate_limit_error")
        response.headers["Retry-After"] = str(int(retry_after) + 1)
        return response

    try:
        body = await request.body()
        parsed = EmbeddingRequest.model_validate_json(body)
    except Exception:
        return _error_json(400, "Invalid request body", "invalid_request_error")

    # Proxy — internal gateway expects query params for embeddings
    try:
        text = parsed.input if isinstance(parsed.input, str) else parsed.input[0]
        upstream = await _http_client.post(
            "/v1/embeddings",
            params={"text": text, "model": parsed.model},
            headers=_proxy_headers(),
        )
    except (httpx.ConnectError, httpx.TimeoutException):
        return _error_json(502, "Service temporarily unavailable", "server_error")

    if upstream.status_code >= 500:
        return _error_json(502, "Service temporarily unavailable", "server_error")

    _key_manager.update_usage(key_record.key_id, ip=client_ip)
    _audit_log.log(
        key_id=key_record.key_id, key_name=key_record.name,
        source_ip=client_ip, method="POST", path="/v1/embeddings",
        model=parsed.model, status_code=upstream.status_code,
    )

    return JSONResponse(status_code=upstream.status_code, content=upstream.json())


# --- Models list (auth required) ---

@app.get("/v1/models")
async def list_models(request: Request):
    """Proxy model listing to internal gateway."""
    client_ip = _get_client_ip(request)

    key_record = _verify_bearer_token(request)
    if not key_record:
        _record_violation(client_ip, "auth_failure")
        _audit_log.log(
            source_ip=client_ip, method="GET", path="/v1/models",
            status_code=401, violation="auth_failure",
        )
        return _error_json(401, "Invalid or missing API key", "authentication_error")

    if not _per_key_limiter.allow(key_record.key_id):
        retry_after = _per_key_limiter.retry_after(key_record.key_id)
        response = _error_json(429, "Rate limit exceeded", "rate_limit_error")
        response.headers["Retry-After"] = str(int(retry_after) + 1)
        return response

    try:
        upstream = await _http_client.get("/models", headers=_proxy_headers())
    except (httpx.ConnectError, httpx.TimeoutException):
        return _error_json(502, "Service temporarily unavailable", "server_error")

    if upstream.status_code >= 500:
        return _error_json(502, "Service temporarily unavailable", "server_error")

    _audit_log.log(
        key_id=key_record.key_id, key_name=key_record.name,
        source_ip=client_ip, method="GET", path="/v1/models",
        status_code=upstream.status_code,
    )

    return JSONResponse(status_code=upstream.status_code, content=upstream.json())


# --- Catch-all for undefined routes ---

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def catch_all(request: Request, path: str):
    """Return 404 for any undefined route — reveals nothing."""
    return _error_json(404, "Not found", "invalid_request_error")


# --- Helpers ---

def _proxy_headers() -> dict:
    """Build headers for proxied requests."""
    headers = {"Content-Type": "application/json"}
    if _config and _config.internal_api_key:
        headers["X-API-Key"] = _config.internal_api_key
    return headers


def _record_violation(ip: str, violation_type: str) -> None:
    """Record a violation and trigger auto-ban if threshold reached."""
    if _violation_tracker:
        _violation_tracker.record_violation(ip)
    # Also record via the app.state tracker (used by middleware)
    tracker = getattr(app.state, "violation_tracker", None)
    if tracker and tracker is not _violation_tracker:
        tracker.record_violation(ip)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch-all error handler — never leak internals."""
    logger.error("Unexpected error: %s", exc, exc_info=True)
    return _error_json(500, "Internal server error", "server_error")


# --- Server entry point ---

def run_internet_gateway(
    host: Optional[str] = None,
    port: Optional[int] = None,
    no_tls: bool = False,
) -> None:
    """Start the Internet Gateway with uvicorn."""
    import uvicorn

    from .inet_tls import resolve_tls

    config = get_inet_config()
    effective_host = host or config.host
    effective_port = port or config.port

    # Setup middleware with proper config
    _setup_middleware(app)

    # Safety check
    if not no_tls:
        config.validate_safety()

    # Resolve TLS
    ssl_kwargs = {}
    if not no_tls:
        tls_result = resolve_tls(
            tls_cert_path=config.tls_cert_path,
            tls_key_path=config.tls_key_path,
            tls_auto_selfsigned=config.tls_auto_selfsigned,
            data_dir=config.resolved_data_dir,
            host=effective_host,
        )
        if tls_result:
            ssl_kwargs["ssl_certfile"] = tls_result[0]
            ssl_kwargs["ssl_keyfile"] = tls_result[1]
    else:
        # no-tls flag: force localhost binding
        if effective_host != "127.0.0.1":
            logger.warning("--no-tls forces binding to 127.0.0.1")
            effective_host = "127.0.0.1"

    logger.info(
        "Starting Internet Gateway on %s:%d (TLS: %s)",
        effective_host, effective_port,
        "enabled" if ssl_kwargs else "disabled",
    )

    uvicorn.run(
        "overblick.gateway.internet_gateway:app",
        host=effective_host,
        port=effective_port,
        reload=False,
        log_level="info",
        **ssl_kwargs,
    )
