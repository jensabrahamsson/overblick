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
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from .backend_registry import BackendRegistry
from .config import get_config
from .deepseek_client import DeepseekError, DeepseekConnectionError
from .models import ChatRequest, ChatResponse, Priority, GatewayStats
from .queue_manager import QueueManager
from .ollama_client import OllamaError, OllamaConnectionError
from .router import RequestRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Global instances
_queue_manager: Optional[QueueManager] = None
_backend_registry: Optional[BackendRegistry] = None
_router: Optional[RequestRouter] = None


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
    global _queue_manager, _backend_registry, _router

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

    # Initialize queue manager with default backend client
    default_client = _backend_registry.get_client()
    _queue_manager = QueueManager(config, client=default_client, registry=_backend_registry)
    await _queue_manager.start()

    # Health check all backends
    health = await _backend_registry.health_check_all()
    for name, healthy in health.items():
        if healthy:
            logger.info("Backend '%s': connected", name)
        else:
            logger.warning("Backend '%s': not reachable at startup", name)

    yield

    logger.info("Shutting down LLM Gateway...")
    if _queue_manager is not None:
        await _queue_manager.stop()
    if _backend_registry is not None:
        await _backend_registry.close_all()
    logger.info("LLM Gateway stopped")


app = FastAPI(
    title="Överblick LLM Gateway",
    description="Priority-based request queue for shared LLM inference with multi-backend routing",
    version="0.2.0",
    lifespan=lifespan,
)

# API key authentication
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Security(_api_key_header)) -> None:
    """Verify API key if one is configured."""
    config = get_config()
    if not config.api_key:
        return  # No key configured — allow (localhost-only)
    if not api_key or not hmac.compare_digest(api_key, config.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.get("/health")
async def health_check() -> dict:
    """
    Health check with per-backend status and GPU starvation risk.

    Starvation risk levels:
    - low: queue_size < 3
    - medium: queue_size 3-7
    - high: queue_size >= 8
    """
    qm = get_queue_manager()
    registry = get_backend_registry()

    backend_health = await registry.health_check_all()
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

    return {
        "status": status,
        "gateway": "running" if qm.is_running else "stopped",
        "backends": {name: "connected" if h else "disconnected" for name, h in backend_health.items()},
        "default_backend": registry.default_backend,
        "queue_size": queue_size,
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


@app.get("/models", dependencies=[Depends(verify_api_key)])
async def list_models(
    backend: Optional[str] = Query(default=None, description="Backend to list models from"),
) -> dict:
    """List available models from a specific backend (or default)."""
    registry = get_backend_registry()
    try:
        client = registry.get_client(backend)
        models = await client.list_models()
        return {"backend": backend or registry.default_backend, "models": models}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OllamaConnectionError as e:
        logger.error("Backend connection error in list_models: %s", e, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to LLM backend. Check gateway logs for details.",
        )


@app.post("/v1/chat/completions", response_model=ChatResponse, dependencies=[Depends(verify_api_key)])
async def chat_completion(
    request: ChatRequest,
    priority: str = Query(default="low", description="Priority: high or low"),
    backend: Optional[str] = Query(default=None, description="Backend to route to"),
    complexity: Optional[str] = Query(default=None, description="Complexity: ultra, high, or low (for backend routing)"),
) -> ChatResponse:
    """
    OpenAI-compatible chat completion endpoint with priority queuing.

    Priority levels:
    - high: Interactive requests (identity agents responding to users)
    - low: Background tasks (scheduled ticks, housekeeping)

    Complexity levels (for backend routing):
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
    if _router and not backend:
        resolved_backend = _router.resolve_backend(
            priority=priority.lower() if priority else "low",
            complexity=complexity,
            explicit_backend=None,
        )
        # Only set if router chose something other than default
        if resolved_backend == _backend_registry.default_backend:
            resolved_backend = None  # let queue manager use default
    elif _router and backend:
        resolved_backend = _router.resolve_backend(
            explicit_backend=backend,
        )

    logger.info(
        "Received chat request: model=%s, messages=%d, priority=%s, backend=%s, complexity=%s",
        request.model, len(request.messages), prio.name,
        resolved_backend or "default", complexity or "none",
    )

    try:
        response = await qm.submit(request, prio, backend=resolved_backend)
        return response

    except asyncio.QueueFull:
        logger.warning("Queue full, request rejected")
        raise HTTPException(
            status_code=503,
            detail="Queue is full. Try again later.",
        )

    except asyncio.TimeoutError:
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

    except (OllamaError, DeepseekError) as e:
        logger.error("LLM error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="LLM inference error. Check gateway logs for details.",
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    logger.error("Unexpected error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


def run_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
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
