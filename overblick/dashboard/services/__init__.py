"""
Dashboard service layer — read-only access to framework data.

Services are initialized during app lifespan and stored on app.state.
All services except OnboardingService are strictly read-only.
"""

import logging
from pathlib import Path

from fastapi import FastAPI

from ..config import DashboardConfig

logger = logging.getLogger(__name__)


async def init_services(app: FastAPI, config: DashboardConfig) -> None:
    """Initialize all dashboard services."""
    from .identity import IdentityService
    from .personality import PersonalityService
    from .audit import AuditService
    from .supervisor import SupervisorService
    from .system import SystemService
    from .onboarding import OnboardingService

    base_dir = Path(config.base_dir) if config.base_dir else Path(__file__).parent.parent.parent

    app.state.identity_service = IdentityService(base_dir)
    app.state.personality_service = PersonalityService()
    app.state.audit_service = AuditService(base_dir)
    app.state.supervisor_service = SupervisorService()
    app.state.system_service = SystemService(base_dir)
    app.state.onboarding_service = OnboardingService(base_dir)

    logger.info("Dashboard services initialized (base_dir=%s)", base_dir)


async def cleanup_services(app: FastAPI) -> None:
    """Cleanup services on shutdown."""
    if hasattr(app.state, "audit_service"):
        app.state.audit_service.close()
    if hasattr(app.state, "supervisor_service"):
        await app.state.supervisor_service.close()
    logger.info("Dashboard services cleaned up")
