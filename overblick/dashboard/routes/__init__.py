"""
Dashboard route registration.

All routes are registered via register_routes() during app creation.
"""

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    """Register all dashboard routes."""
    from .auth import router as auth_router
    from .dashboard import router as dashboard_router
    from .agents import router as agents_router
    from .audit import router as audit_router
    from .identities import router as identities_router
    from .onboarding import router as onboarding_router
    from .api import router as api_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(agents_router)
    app.include_router(audit_router)
    app.include_router(identities_router)
    app.include_router(onboarding_router)
    app.include_router(api_router)
