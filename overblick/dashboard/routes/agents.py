"""
Agent detail routes.
"""

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .dashboard import _PLUGIN_ROUTE_MAP

logger = logging.getLogger(__name__)

router = APIRouter()

_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


@router.get("/agent/{name}", response_class=HTMLResponse)
async def agent_detail(request: Request, name: str):
    """Render agent detail page."""
    # Validate name to prevent path traversal (e.g. ../../../etc/passwd)
    if not _SAFE_NAME_RE.match(name):
        logger.warning("Rejected agent detail request with invalid name: %r", name)
        raise HTTPException(status_code=404, detail="Agent not found")

    templates = request.app.state.templates

    identity_svc = request.app.state.identity_service
    personality_svc = request.app.state.personality_service
    supervisor_svc = request.app.state.supervisor_service
    audit_svc = request.app.state.audit_service
    system_svc = request.app.state.system_service

    # Load identity
    identity = identity_svc.get_identity(name)
    if not identity:
        logger.debug("Agent '%s' not found", name)
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    # Load personality (character data)
    personality = personality_svc.get_personality(identity.get("identity_ref", name))

    # Get agent status from supervisor
    agents = await supervisor_svc.get_agents()
    agent_status = next((a for a in agents if a.get("name") == name), {})

    # Get audit entries for this identity
    audit_entries = audit_svc.query(identity=name, limit=30)

    # Get capability bundles for display
    capability_bundles = system_svc.get_capability_bundles()

    supervisor_status = await supervisor_svc.get_status()
    is_running = agent_status.get("state") == "running"

    # Build plugin quick-links from the single source of truth
    plugin_links = [
        {"url": _PLUGIN_ROUTE_MAP[p], "label": p.replace("_", " ").title()}
        for p in identity.get("plugins", [])
        if p in _PLUGIN_ROUTE_MAP
    ]

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
        "plugin_links": plugin_links,
        "plugin_route_map": _PLUGIN_ROUTE_MAP,
    })
