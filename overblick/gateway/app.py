"""
FastAPI application for the LLM Gateway.

Provides REST endpoints for:
- Chat completions (with priority queuing)
- Health checks
- Queue statistics
- Model listing
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .config import get_config
from .models import ChatRequest, ChatResponse, Priority, GatewayStats
from .queue_manager import QueueManager
from .ollama_client import OllamaError, OllamaConnectionError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Global queue manager instance
_queue_manager: Optional[QueueManager] = None


def get_queue_manager() -> QueueManager:
    """Get the global queue manager instance."""
    if _queue_manager is None:
        raise RuntimeError("Queue manager not initialized")
    return _queue_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global _queue_manager

    config = get_config()
    logger.info("Starting LLM Gateway on %s:%d", config.api_host, config.api_port)
    logger.info("Ollama backend: %s", config.ollama_base_url)
    logger.info("Default model: %s", config.default_model)

    _queue_manager = QueueManager(config)
    await _queue_manager.start()

    if await _queue_manager.client.health_check():
        models = await _queue_manager.client.list_models()
        logger.info("Connected to Ollama. Available models: %s", models)
    else:
        logger.warning("Ollama not reachable at startup (will retry on requests)")

    yield

    logger.info("Shutting down LLM Gateway...")
    if _queue_manager is not None:
        await _queue_manager.stop()
    logger.info("LLM Gateway stopped")


app = FastAPI(
    title="Överblick LLM Gateway",
    description="Priority-based request queue for shared Ollama LLM inference",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint. Returns status of gateway and Ollama backend."""
    qm = get_queue_manager()
    ollama_healthy = await qm.client.health_check()

    status = "healthy" if ollama_healthy else "degraded"

    return {
        "status": status,
        "gateway": "running" if qm.is_running else "stopped",
        "ollama": "connected" if ollama_healthy else "disconnected",
        "queue_size": qm.queue_size,
    }


@app.get("/stats", response_model=GatewayStats)
async def get_stats() -> GatewayStats:
    """Get gateway statistics: queue size, request counts, response times."""
    qm = get_queue_manager()
    return qm.get_stats()


@app.get("/models")
async def list_models() -> dict:
    """List available Ollama models."""
    qm = get_queue_manager()
    try:
        models = await qm.client.list_models()
        return {"models": models}
    except OllamaConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/v1/chat/completions", response_model=ChatResponse)
async def chat_completion(
    request: ChatRequest,
    priority: str = Query(default="low", description="Priority: high or low"),
) -> ChatResponse:
    """
    OpenAI-compatible chat completion endpoint with priority queuing.

    Priority levels:
    - high: Interactive requests (identity agents responding to users)
    - low: Background tasks (scheduled ticks, housekeeping)
    """
    qm = get_queue_manager()

    try:
        prio = Priority.HIGH if priority.lower() == "high" else Priority.LOW
    except (ValueError, AttributeError):
        prio = Priority.LOW

    logger.info(
        "Received chat request: model=%s, messages=%d, priority=%s",
        request.model, len(request.messages), prio.name,
    )

    try:
        response = await qm.submit(request, prio)
        return response

    except asyncio.QueueFull:
        logger.warning("Queue full, request rejected")
        raise HTTPException(
            status_code=503,
            detail="Queue is full. Try again later.",
        )

    except asyncio.TimeoutError:
        logger.error("Request timed out")
        raise HTTPException(
            status_code=504,
            detail="Request timed out waiting for LLM response.",
        )

    except OllamaConnectionError as e:
        logger.error("Ollama connection error: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to Ollama: {e}",
        )

    except OllamaError as e:
        logger.error("Ollama error: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"LLM error: {e}",
        )


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
