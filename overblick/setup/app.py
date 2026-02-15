"""
FastAPI application factory for the setup wizard.

Creates an ephemeral, auth-free localhost server for first-time
onboarding. No CSRF needed (ephemeral + localhost only).
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — load personality data on startup."""
    logger.info("Setup wizard starting")
    yield
    logger.info("Setup wizard shutting down")


def create_setup_app(base_dir: Path | None = None) -> FastAPI:
    """
    Create the setup wizard FastAPI application.

    Args:
        base_dir: Project root directory (for config/secrets paths).
                  Defaults to two levels up from this file.

    Returns:
        Configured FastAPI application.
    """
    if base_dir is None:
        base_dir = _PKG_DIR.parent.parent

    app = FastAPI(
        title="Överblick Setup",
        description="First-time onboarding wizard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Store base_dir on app state for provisioner access
    app.state.base_dir = base_dir

    # Mount static files
    static_dir = _PKG_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Register routes
    from .wizard import register_routes
    register_routes(app)

    return app
