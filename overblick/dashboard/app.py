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

from starlette.responses import Response

from .auth import AuthMiddleware, SessionManager
from .config import DashboardConfig, get_config
from .security import RateLimiter

logger = logging.getLogger(__name__)

# Package directory (for templates and static files)
_PKG_DIR = Path(__file__).parent


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses (defense-in-depth)."""

    async def dispatch(self, request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'"
        )
        return response


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


def _format_irc_time(value: int | float) -> str:
    """Format Unix timestamp to IRC-style [HH:MM] format."""
    from datetime import datetime

    try:
        dt = datetime.fromtimestamp(float(value))
        return dt.strftime("[%H:%M]")
    except (ValueError, TypeError, OSError):
        return "[??:??]"


def _is_operational_cap(name: str) -> bool:
    """Check if a capability is operational (I/O, communication, monitoring).

    Used in templates to assign a different badge color to operational
    capabilities vs personality-behavioral ones (social, engagement, etc.).
    """
    _OPERATIONAL = {
        # Bundle names
        "communication", "monitoring", "system",
        # Individual capability names
        "boss_request", "email", "gmail", "telegram_notifier",
        "host_inspection", "email_agent", "system_clock",
    }
    return name in _OPERATIONAL


def _create_templates() -> Jinja2Templates:
    """Create Jinja2 templates with autoescape enabled and global functions."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(_PKG_DIR / "templates")),
        autoescape=True,
    )
    # Register global template functions
    from overblick.setup.wizard import plugin_name
    env.globals["plugin_name"] = plugin_name
    env.globals["_format_uptime"] = _format_uptime
    env.globals["_is_operational_cap"] = _is_operational_cap
    # Default nav globals — overridden in lifespan once services are initialized
    env.globals["irc_enabled"] = lambda: False
    env.globals["kontrast_enabled"] = lambda: False
    env.globals["spegel_enabled"] = lambda: False
    env.globals["skuggspel_enabled"] = lambda: False
    env.globals["compass_enabled"] = lambda: False
    env.globals["stage_enabled"] = lambda: False
    env.globals["settings_enabled"] = lambda: True

    # Register filters
    env.filters["epoch_to_datetime"] = _format_epoch
    env.filters["irc_time"] = _format_irc_time

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

    # Register nav context globals (must be after services are initialized)
    def _check_irc_enabled() -> bool:
        irc_svc = getattr(app.state, "irc_service", None)
        return irc_svc.has_data() if irc_svc else False

    from .routes.kontrast import has_data as _kontrast_has_data
    from .routes.spegel import has_data as _spegel_has_data
    from .routes.skuggspel import has_data as _skuggspel_has_data
    from .routes.compass import has_data as _compass_has_data
    from .routes.stage import has_data as _stage_has_data

    app.state.templates.env.globals["irc_enabled"] = _check_irc_enabled
    app.state.templates.env.globals["kontrast_enabled"] = _kontrast_has_data
    app.state.templates.env.globals["spegel_enabled"] = _spegel_has_data
    app.state.templates.env.globals["skuggspel_enabled"] = _skuggspel_has_data
    app.state.templates.env.globals["compass_enabled"] = _compass_has_data
    app.state.templates.env.globals["stage_enabled"] = _stage_has_data
    app.state.templates.env.globals["settings_enabled"] = lambda: True

    # First-run detection: redirect to /settings/ if no config exists
    if config.test_mode:
        app.state.setup_needed = False
    else:
        cfg_file = _PKG_DIR.parent.parent / "config" / "overblick.yaml"
        # Also check base_dir from config if set
        if config.base_dir:
            cfg_file = Path(config.base_dir) / "config" / "overblick.yaml"
        app.state.setup_needed = not cfg_file.exists()

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

    # Security headers middleware (outermost — runs on every response)
    app.add_middleware(SecurityHeadersMiddleware)

    # Auth middleware (must be added before routes)
    app.add_middleware(AuthMiddleware)

    # Mount static files
    static_dir = _PKG_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount setup static files (CSS, JS, images from the original onboarding wizard)
    setup_static = _PKG_DIR.parent / "setup" / "static"
    if setup_static.exists():
        app.mount("/setup-static", StaticFiles(directory=str(setup_static)), name="setup-static")

    # Register routes
    from .routes import register_routes
    register_routes(app)

    return app
