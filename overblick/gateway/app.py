"""
FastAPI application for the LLM Gateway.

Provides REST endpoints for:
- Chat completions (with priority queuing)
- Health checks
- Queue statistics
- Model listing
- Backend listing and per-backend operations
"""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from .backend_registry import BackendRegistry
from .config import get_config, reset_config
from .deepseek_client import DeepseekConnectionError, DeepseekError, DeepseekTimeoutError
from .models import ChatRequest, ChatResponse, DetailedStats, GatewayStats, Priority, QueueItemInfo
from .ollama_client import OllamaConnectionError, OllamaError, OllamaTimeoutError
from .queue_manager import QueueManager
from .router import RequestRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Global instances
_queue_manager: QueueManager | None = None
_backend_registry: BackendRegistry | None = None
_router: RequestRouter | None = None
_token_logger = None


def get_queue_manager() -> QueueManager:
    """Get the global queue manager instance."""
    if _queue_manager is None:
        raise RuntimeError("Queue manager not initialized")
    return _queue_manager


def get_backend_registry() -> BackendRegistry:
    """Get the global backend registry instance."""
    if _backend_registry is None:
        raise RuntimeError("Backend registry not initialized")
    return _backend_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global _queue_manager, _backend_registry, _router, _token_logger

    config = get_config()
    logger.info("Starting LLM Gateway on %s:%d", config.api_host, config.api_port)

    # Initialize backend registry (multi-backend support)
    _backend_registry = BackendRegistry(config)
    logger.info(
        "Backend registry: %d backend(s) — %s (default: %s)",
        len(_backend_registry.available_backends),
        ", ".join(_backend_registry.available_backends),
        _backend_registry.default_backend,
    )

    # Initialize request router
    _router = RequestRouter(_backend_registry)

    # Initialize token logger
    from .token_logger import TokenLogger
    data_dir = Path("data/gateway")
    _token_logger = TokenLogger(data_dir / "tokens.db")

    # Initialize queue manager with default backend client + token logger
    default_client = _backend_registry.get_client()
    _queue_manager = QueueManager(
        config, client=default_client, registry=_backend_registry,
        token_logger=_token_logger,
    )
    await _queue_manager.start()

    # Health check all backends
    health = await _backend_registry.health_check_all()
    _health_cache.update(health)
    import time as _time
    global _health_cache_time
    _health_cache_time = _time.time()
    for name, healthy in health.items():
        if healthy:
            logger.info("Backend '%s': connected", name)
        else:
            logger.warning("Backend '%s': not reachable at startup", name)

    # Start lightweight health server on port+1 (non-blocking, separate thread)
    _start_health_server(config.api_port + 1)

    yield

    logger.info("Shutting down LLM Gateway...")
    if _queue_manager is not None:
        await _queue_manager.stop()
    if _backend_registry is not None:
        await _backend_registry.close_all()
    if _token_logger is not None:
        _token_logger.close()
    logger.info("LLM Gateway stopped")


app = FastAPI(
    title="Överblick LLM Gateway",
    description="Priority-based request queue for shared LLM inference with multi-backend routing",
    version="0.2.0",
    lifespan=lifespan,
)

# Allowed origins for the gateway (localhost only)
_ALLOWED_ORIGINS = {
    "http://127.0.0.1",
    "http://localhost",
    "https://127.0.0.1",
    "https://localhost",
}


@app.middleware("http")
async def check_origin(request, call_next):
    """Reject requests with non-localhost Origin headers.

    The gateway is designed for localhost-only operation. Requests from
    remote origins indicate misconfiguration or CSRF attempts.
    """
    origin = request.headers.get("origin")
    if origin:
        # Parse origin to check host (ignore port)
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        origin_base = f"{parsed.scheme}://{parsed.hostname}"
        if origin_base not in _ALLOWED_ORIGINS:
            logger.warning("Gateway: rejected non-localhost Origin: %s", origin)
            return JSONResponse(
                status_code=403,
                content={"detail": "Non-localhost origin rejected"},
            )
    return await call_next(request)


# API key authentication
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    """Verify API key if one is configured."""
    config = get_config()
    if not config.api_key:
        return  # No key configured — allow (localhost-only)
    if not api_key or not hmac.compare_digest(api_key, config.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


_health_cache: dict = {}
_health_cache_time: float = 0
_health_lock = asyncio.Lock()


@app.get("/health")
async def health_check() -> dict:
    """
    Health check with per-backend status and GPU starvation risk.

    Uses cached health data (max 30s old) to prevent blocking when
    backends are slow or unreachable. Health checks run in background.

    Starvation risk levels:
    - low: queue_size < 3
    - medium: queue_size 3-7
    - high: queue_size >= 8
    """
    import time as _time

    global _health_cache, _health_cache_time

    qm = get_queue_manager()
    registry = get_backend_registry()

    # Use cached health if fresh (< 30s) to avoid blocking
    now = _time.time()
    if now - _health_cache_time < 30 and _health_cache:
        backend_health = _health_cache
    else:
        # Run health checks with a 5s total timeout
        try:
            backend_health = await asyncio.wait_for(
                registry.health_check_all(), timeout=5.0
            )
            _health_cache = backend_health
            _health_cache_time = now
        except asyncio.TimeoutError:
            # Use stale cache or assume all healthy if no cache
            if _health_cache:
                backend_health = _health_cache
            else:
                backend_health = {name: True for name in registry.get_backend_info()}

    any_healthy = any(backend_health.values())

    status = "healthy" if any_healthy else "degraded"
    queue_size = qm.queue_size

    if queue_size < 3:
        starvation_risk = "low"
    elif queue_size < 8:
        starvation_risk = "medium"
    else:
        starvation_risk = "high"

    stats = qm.get_stats()
    backend_info = registry.get_backend_info()

    # Determine status label per backend: cloud backends with a key
    # show "cloud_configured", local backends show "connected"/"disconnected".
    backends_out = {}
    for name, h in backend_health.items():
        btype = backend_info.get(name, {}).get("type", "unknown")
        if h and btype == "deepseek":
            status_label = "cloud_configured"
        elif h:
            status_label = "connected"
        else:
            status_label = "disconnected"
        backends_out[name] = {
            "status": status_label,
            "type": btype,
            "model": backend_info.get(name, {}).get("model", "unknown"),
            "default": name == registry.default_backend,
        }

    config = get_config()
    return {
        "status": status,
        "gateway": "running" if qm.is_running else "stopped",
        "backends": backends_out,
        "default_backend": registry.default_backend,
        "queue_size": queue_size,
        "max_queue_size": config.max_queue_size,
        "gpu_starvation_risk": starvation_risk,
        "avg_response_time_ms": stats.avg_response_time_ms,
        "active_requests": 1 if stats.is_processing else 0,
    }


@app.get("/backends", dependencies=[Depends(verify_api_key)])
async def list_backends() -> dict:
    """List all configured backends with health status."""
    registry = get_backend_registry()
    health = await registry.health_check_all()
    return {
        "default": registry.default_backend,
        "backends": {
            name: {
                "healthy": health.get(name, False),
                "model": registry.get_model(name),
            }
            for name in registry.available_backends
        },
    }


@app.get("/stats", response_model=GatewayStats, dependencies=[Depends(verify_api_key)])
async def get_stats() -> GatewayStats:
    """Get gateway statistics: queue size, request counts, response times."""
    qm = get_queue_manager()
    return qm.get_stats()


@app.get("/stats/tokens", dependencies=[Depends(verify_api_key)])
async def get_token_stats(
    period: str | None = Query(default="24h", description="Period: 1h, 6h, 24h, 7d, 30d"),
    start: str | None = Query(default=None, description="Start date (ISO format)"),
    end: str | None = Query(default=None, description="End date (ISO format)"),
    backend: str | None = Query(default=None, description="Filter by backend"),
    format: str = Query(default="json", description="Output format: json or csv"),
):
    """Token usage log with time-range filtering and CSV export."""
    if _token_logger is None:
        return {"error": "Token logger not initialized"}
    rows = _token_logger.query(period=period, start=start, end=end, backend=backend)
    if format == "csv":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            content=_token_logger.to_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=tokens.csv"},
        )
    return rows


@app.get("/stats/tokens/summary", dependencies=[Depends(verify_api_key)])
async def get_token_summary(
    period: str | None = Query(default="24h", description="Period: 1h, 6h, 24h, 7d, 30d"),
    start: str | None = Query(default=None, description="Start date (ISO format)"),
    end: str | None = Query(default=None, description="End date (ISO format)"),
):
    """Aggregated token usage: totals, per-backend, per-identity."""
    if _token_logger is None:
        return {"error": "Token logger not initialized"}
    return _token_logger.summary(period=period, start=start, end=end)


@app.get("/models", dependencies=[Depends(verify_api_key)])
async def list_models(
    backend: str | None = Query(default=None, description="Backend to list models from"),
) -> dict:
    """List available models from a specific backend (or default)."""
    registry = get_backend_registry()
    try:
        client = registry.get_client(backend)
        models = await client.list_models()
        return {"backend": backend or registry.default_backend, "models": models}
    except ValueError as e:
        logger.warning("Invalid backend in list_models: %s", e)
        raise HTTPException(status_code=400, detail="Invalid backend name.")
    except OllamaConnectionError as e:
        logger.error("Backend connection error in list_models: %s", e, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to LLM backend. Check gateway logs for details.",
        )


@app.post(
    "/v1/chat/completions", response_model=ChatResponse, dependencies=[Depends(verify_api_key)]
)
async def chat_completion(
    request: ChatRequest,
    priority: str = Query(default="low", description="Priority: high or low"),
    backend: str | None = Query(default=None, description="Backend to route to"),
    complexity: str | None = Query(
        default=None,
        description="Complexity: einstein, ultra, high, or low (for backend routing)",
        pattern="^(einstein|ultra|high|low)$",
    ),
) -> ChatResponse:
    """
    OpenAI-compatible chat completion endpoint with priority queuing.

    Priority levels:
    - high: Interactive requests (identity agents responding to users)
    - low: Background tasks (scheduled ticks, housekeeping)

    Complexity levels (for backend routing):
    - einstein: Deep reasoning — uses deepseek-reasoner model. Response includes
      reasoning_content (thinking process) alongside content. DeepSeek only,
      no fallback. Use for complex analysis, architecture decisions, code review.
    - ultra: Highest capability — prefer deepseek for precision tasks (math, challenges)
    - high: Complex tasks — prefer cloud/deepseek backends
    - low: Simple tasks — local inference is fine

    Backend selection:
    - Defaults to intelligent routing based on complexity/priority
    - Can be overridden per-request via ?backend=local or ?backend=cloud
    """
    qm = get_queue_manager()

    try:
        prio = Priority.HIGH if priority.lower() == "high" else Priority.LOW
    except (ValueError, AttributeError):
        prio = Priority.LOW

    # Use router to resolve backend if not explicitly specified
    resolved_backend = backend
    actual_backend_name = backend  # Track the real name for fallback
    if _router and not backend:
        resolved_backend = _router.resolve_backend(
            priority=priority.lower() if priority else "low",
            complexity=complexity,
            explicit_backend=None,
        )
        actual_backend_name = resolved_backend
        # Only set if router chose something other than default
        if resolved_backend == _backend_registry.default_backend:
            resolved_backend = None  # let queue manager use default
    elif _router and backend:
        resolved_backend = _router.resolve_backend(
            explicit_backend=backend,
        )

    # Einstein complexity: override model to deepseek-reasoner
    # The reasoner is a separate model with different response format
    if complexity == "einstein" and resolved_backend == "deepseek":
        original_model = request.model
        request = request.model_copy(update={"model": "deepseek-reasoner"})
        logger.info(
            "EINSTEIN MODE: model override %s → deepseek-reasoner "
            "(reasoning_content will be included in response)",
            original_model,
        )

    logger.info(
        "Received chat request: model=%s, messages=%d, priority=%s, backend=%s, complexity=%s",
        request.model,
        len(request.messages),
        prio.name,
        resolved_backend or "default",
        complexity or "none",
    )

    try:
        # Inner try: catch connection errors for fallback retry
        try:
            response = await qm.submit(request, prio, backend=resolved_backend)
            return response

        except (OllamaConnectionError, OllamaError, DeepseekConnectionError, DeepseekError) as e:
            # Backend-specific connection failure — retry with fallback backend
            # if router can find an alternative (exclude the failed backend).
            failed_backend = actual_backend_name or _backend_registry.default_backend
            if _router:
                fallback = _router.resolve_backend(
                    priority=priority.lower() if priority else "low",
                    complexity=complexity,
                    exclude={failed_backend},
                )
                if fallback != failed_backend:
                    logger.warning(
                        "Backend '%s' failed (%s), retrying with '%s'",
                        resolved_backend,
                        type(e).__name__,
                        fallback,
                    )
                    try:
                        fb_backend = (
                            None if fallback == _backend_registry.default_backend else fallback
                        )
                        response = await qm.submit(request, prio, backend=fb_backend)
                        return response
                    except Exception as retry_err:
                        logger.warning(
                            "Fallback backend '%s' also failed: %s",
                            fallback,
                            retry_err,
                        )
            raise  # No fallback available — propagate to outer handler

    except asyncio.QueueFull:
        logger.warning("Queue full, request rejected")
        raise HTTPException(
            status_code=503,
            detail="Queue is full. Try again later.",
        )

    except TimeoutError:
        logger.error("Request timed out", exc_info=True)
        raise HTTPException(
            status_code=504,
            detail="Request timed out waiting for LLM response.",
        )

    except (OllamaConnectionError, DeepseekConnectionError) as e:
        logger.error("Backend connection error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to LLM backend. Check gateway logs for details.",
        )

    except (OllamaTimeoutError, DeepseekTimeoutError) as e:
        logger.error("Backend timed out: %s", e, exc_info=True)
        raise HTTPException(
            status_code=504,
            detail="LLM backend timed out. Try again later.",
        )

    except (OllamaError, DeepseekError) as e:
        logger.error("LLM error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="LLM inference error. Check gateway logs for details.",
        )

    except ValueError as e:
        logger.warning("Invalid request in chat: %s", e)
        raise HTTPException(status_code=400, detail="Invalid request parameters")


@app.get("/queue", dependencies=[Depends(verify_api_key)])
async def get_queue() -> list[QueueItemInfo]:
    """View queued requests (for debugging bottlenecks)."""
    qm = get_queue_manager()
    return qm.get_queue_snapshot()


@app.get("/config", dependencies=[Depends(verify_api_key)])
async def get_effective_config() -> dict:
    """View effective gateway configuration (API key masked)."""
    config = get_config()
    return {
        "request_timeout_seconds": config.request_timeout_seconds,
        "max_concurrent_requests": config.max_concurrent_requests,
        "max_queue_size": config.max_queue_size,
        "default_model": config.default_model,
        "default_backend": config.default_backend,
        "api_host": config.api_host,
        "api_port": config.api_port,
        "api_key_configured": bool(config.api_key),
        "backends": list(config.backends.keys()) if config.backends else [],
    }


@app.post("/queue/cancel/{request_id}", dependencies=[Depends(verify_api_key)])
async def cancel_request(request_id: str) -> dict:
    """Cancel a queued request by ID."""
    qm = get_queue_manager()
    cancelled = qm.cancel_request(request_id)
    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail="Request not found or already completed.",
        )
    return {"cancelled": True, "request_id": request_id}


@app.post("/admin/reload-config", dependencies=[Depends(verify_api_key)])
async def reload_config() -> dict:
    """Reload gateway configuration from env/YAML without restart."""
    old_config = get_config()
    old_timeout = old_config.request_timeout_seconds
    old_concurrent = old_config.max_concurrent_requests

    reset_config()
    new_config = get_config()

    changes = {}
    if new_config.request_timeout_seconds != old_timeout:
        changes["request_timeout_seconds"] = {
            "old": old_timeout,
            "new": new_config.request_timeout_seconds,
        }
    if new_config.max_concurrent_requests != old_concurrent:
        changes["max_concurrent_requests"] = {
            "old": old_concurrent,
            "new": new_config.max_concurrent_requests,
        }

    logger.info("Config reloaded. Changes: %s", changes or "none")
    return {"reloaded": True, "changes": changes}


@app.get(
    "/stats/detailed",
    response_model=DetailedStats,
    dependencies=[Depends(verify_api_key)],
)
async def get_detailed_stats() -> DetailedStats:
    """Extended statistics with per-backend and per-priority breakdowns."""
    qm = get_queue_manager()
    return qm.get_detailed_stats()


@app.post("/v1/embeddings", dependencies=[Depends(verify_api_key)])
async def create_embedding(
    text: str = Query(description="Text to embed"),
    model: str = Query(default="nomic-embed-text", description="Embedding model"),
) -> dict:
    """
    Generate a text embedding via the default backend.

    Uses Ollama's /api/embed endpoint for local embedding generation.
    """
    registry = get_backend_registry()
    client = registry.get_client()  # default backend

    if not hasattr(client, "embed"):
        raise HTTPException(
            status_code=501,
            detail="Default backend does not support embeddings",
        )

    try:
        embedding = await client.embed(text, model=model)
        return {"embedding": embedding, "model": model}

    except OllamaConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    except OllamaError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    logger.error("Unexpected error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


def _start_health_server(port: int) -> None:
    """Start a minimal HTTP health server on a separate thread.

    This server is never blocked by LLM requests because it runs
    in its own thread with its own event loop.
    """
    import json
    import time
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            # Return cached health data (updated by main app)
            registry = get_backend_registry()
            backend_info = registry.get_backend_info() if registry else {}
            backends_out = {}
            for name, h in _health_cache.items():
                btype = backend_info.get(name, {}).get("type", "unknown")
                if h and btype == "deepseek":
                    status_label = "cloud_configured"
                elif h:
                    status_label = "connected"
                else:
                    status_label = "disconnected"
                backends_out[name] = {
                    "status": status_label,
                    "type": btype,
                    "model": backend_info.get(name, {}).get("model", "unknown"),
                }
            qm = get_queue_manager()
            body = json.dumps({
                "status": "healthy" if any(_health_cache.values()) else "degraded",
                "backends": backends_out,
                "queue_size": qm.queue_size if qm else 0,
                "cache_age_s": round(time.time() - _health_cache_time, 1),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # Suppress access logs

    def serve():
        try:
            server = HTTPServer(("127.0.0.1", port), HealthHandler)
            server.serve_forever()
        except Exception as e:
            logger.warning("Health server failed on port %d: %s", port, e)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    logger.info("Health server started on port %d (separate thread)", port)


def run_server(host: str | None = None, port: int | None = None) -> None:
    """Run the gateway server with uvicorn."""
    import uvicorn

    config = get_config()
    uvicorn.run(
        "overblick.gateway.app:app",
        host=host or config.api_host,
        port=port or config.api_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run_server()
