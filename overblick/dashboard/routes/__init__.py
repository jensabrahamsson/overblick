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
    from .conversations import router as conversations_router
    from .llm import router as llm_router
    from .system import router as system_router
    from .irc import router as irc_router
    from .kontrast import router as kontrast_router
    from .spegel import router as spegel_router
    from .skuggspel import router as skuggspel_router
    from .compass import router as compass_router
    from .stage import router as stage_router
    from .moltbook import router as moltbook_router
    from .settings import router as settings_router
    from .observability import router as observability_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(agents_router)
    app.include_router(audit_router)
    app.include_router(llm_router)
    app.include_router(system_router)
    app.include_router(conversations_router)
    app.include_router(identities_router)
    app.include_router(onboarding_router)
    app.include_router(irc_router)
    app.include_router(kontrast_router)
    app.include_router(spegel_router)
    app.include_router(skuggspel_router)
    app.include_router(compass_router)
    app.include_router(stage_router)
    app.include_router(moltbook_router)
    app.include_router(settings_router)
    app.include_router(observability_router)
    app.include_router(api_router)
