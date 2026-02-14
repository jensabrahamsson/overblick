"""
FastAPI application for the Överblick Web Dashboard.

Provides server-rendered HTML pages via Jinja2 + htmx for:
- Agent monitoring (read-only)
- Audit trail browsing
- Identity/personality viewing
- Onboarding wizard (create new identities)

Security: Bound to 127.0.0.1, CSRF on all forms, autoescape on all templates.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import AuthMiddleware, SessionManager
from .config import DashboardConfig, get_config
from .security import RateLimiter

logger = logging.getLogger(__name__)

# Package directory (for templates and static files)
_PKG_DIR = Path(__file__).parent


def _format_uptime(seconds: int | float) -> str:
    """Format uptime seconds into human-readable string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_min = minutes % 60
    if hours < 24:
        return f"{hours}h {remaining_min}m"
    days = hours // 24
    remaining_hrs = hours % 24
    return f"{days}d {remaining_hrs}h"


def _format_epoch(value: int | float) -> str:
    """Format epoch timestamp to human-readable local time."""
    from datetime import datetime

    try:
        dt = datetime.fromtimestamp(float(value))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(value)


def _create_templates() -> Jinja2Templates:
    """Create Jinja2 templates with autoescape enabled and global functions."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(_PKG_DIR / "templates")),
        autoescape=True,
    )
    # Register global template functions
    env.globals["_format_uptime"] = _format_uptime

    # Register filters
    env.filters["epoch_to_datetime"] = _format_epoch

    templates = Jinja2Templates(env=env)
    return templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize and cleanup services."""
    config: DashboardConfig = app.state.config

    logger.info(
        "Starting Överblick Dashboard on %s:%d",
        config.host, config.port,
    )

    # Initialize services on app state
    app.state.session_manager = SessionManager(
        secret_key=config.secret_key,
        max_age_hours=config.session_hours,
    )
    app.state.rate_limiter = RateLimiter()
    app.state.templates = _create_templates()

    # Initialize service layer
    from .services import init_services
    await init_services(app, config)

    yield

    # Cleanup
    from .services import cleanup_services
    await cleanup_services(app)
    logger.info("Överblick Dashboard stopped")


def create_app(config: DashboardConfig | None = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        config: Dashboard configuration (uses singleton if None)

    Returns:
        Configured FastAPI application
    """
    if config is None:
        config = get_config()

    app = FastAPI(
        title="Överblick Dashboard",
        description="Security-focused agent monitoring and onboarding",
        version="0.1.0",
        docs_url=None,    # Disable Swagger UI (security)
        redoc_url=None,   # Disable ReDoc (security)
        lifespan=lifespan,
    )

    # Store config on app state
    app.state.config = config

    # Auth middleware (must be added before routes)
    app.add_middleware(AuthMiddleware)

    # Mount static files
    static_dir = _PKG_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Register routes
    from .routes import register_routes
    register_routes(app)

    return app
