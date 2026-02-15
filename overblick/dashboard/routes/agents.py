"""
Agent detail routes.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/agent/{name}", response_class=HTMLResponse)
async def agent_detail(request: Request, name: str):
    """Render agent detail page."""
    templates = request.app.state.templates

    identity_svc = request.app.state.identity_service
    personality_svc = request.app.state.personality_service
    supervisor_svc = request.app.state.supervisor_service
    audit_svc = request.app.state.audit_service
    system_svc = request.app.state.system_service

    # Load identity
    identity = identity_svc.get_identity(name)
    if not identity:
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "csrf_token": request.state.session.get("csrf_token", ""),
            "error": f"Agent '{name}' not found.",
            "agent_rows": [],
            "entries": [],
            "supervisor_running": False,
            "supervisor_status": {},
            "audit_count_24h": 0,
            "llm_calls_24h": 0,
            "error_rate": 0.0,
            "categories": [],
            "total_identities": 0,
            "total_agents": 0,
            "poll_interval": 5,
        }, status_code=404)

    # Load personality
    personality = personality_svc.get_personality(identity.get("personality_ref", name))

    # Get agent status from supervisor
    agents = await supervisor_svc.get_agents()
    agent_status = next((a for a in agents if a.get("name") == name), {})

    # Get audit entries for this identity
    audit_entries = audit_svc.query(identity=name, limit=30)

    # Get capability bundles for display
    capability_bundles = system_svc.get_capability_bundles()

    supervisor_status = await supervisor_svc.get_status()
    is_running = agent_status.get("state") == "running"

    return templates.TemplateResponse("agent_detail.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "identity": identity,
        "personality": personality,
        "agent_status": agent_status,
        "audit_entries": audit_entries,
        "capability_bundles": capability_bundles,
        "supervisor_running": supervisor_status is not None,
        "can_start": not is_running,
        "can_stop": is_running,
        "poll_interval": request.app.state.config.poll_interval,
    })
