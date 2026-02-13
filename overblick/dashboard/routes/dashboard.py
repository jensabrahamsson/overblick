"""
Dashboard routes — main page with agent cards and system health.
"""

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Render the main dashboard page."""
    templates = request.app.state.templates
    config = request.app.state.config

    identity_svc = request.app.state.identity_service
    supervisor_svc = request.app.state.supervisor_service
    audit_svc = request.app.state.audit_service

    # Gather data
    identities = identity_svc.get_all_identities()
    supervisor_status = await supervisor_svc.get_status()
    agents = await supervisor_svc.get_agents()
    recent_audit = audit_svc.query(limit=20)
    audit_count_24h = audit_svc.count(since_hours=24)

    # Build agent cards with status info
    agent_cards = _build_agent_cards(identities, agents)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "agent_cards": agent_cards,
        "supervisor_running": supervisor_status is not None,
        "supervisor_status": supervisor_status or {},
        "recent_audit": recent_audit,
        "audit_count_24h": audit_count_24h,
        "total_identities": len(identities),
        "total_agents": len(agents),
        "poll_interval": config.poll_interval,
    })


@router.get("/partials/agent-cards", response_class=HTMLResponse)
async def agent_cards_partial(request: Request):
    """htmx partial: refreshed agent cards."""
    templates = request.app.state.templates

    identity_svc = request.app.state.identity_service
    supervisor_svc = request.app.state.supervisor_service

    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()
    agent_cards = _build_agent_cards(identities, agents)

    return templates.TemplateResponse("partials/agent_cards.html", {
        "request": request,
        "agent_cards": agent_cards,
    })


@router.get("/partials/system-health", response_class=HTMLResponse)
async def system_health_partial(request: Request):
    """htmx partial: system health summary."""
    templates = request.app.state.templates

    supervisor_svc = request.app.state.supervisor_service
    audit_svc = request.app.state.audit_service
    identity_svc = request.app.state.identity_service

    supervisor_status = await supervisor_svc.get_status()
    agents = await supervisor_svc.get_agents()
    audit_count = audit_svc.count(since_hours=24)

    return templates.TemplateResponse("partials/system_health.html", {
        "request": request,
        "supervisor_running": supervisor_status is not None,
        "total_agents": len(agents),
        "total_identities": len(identity_svc.list_identities()),
        "audit_count_24h": audit_count,
    })


@router.get("/partials/audit-recent", response_class=HTMLResponse)
async def audit_recent_partial(request: Request):
    """htmx partial: recent audit entries."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service

    recent_audit = audit_svc.query(limit=20)

    return templates.TemplateResponse("partials/audit_table.html", {
        "request": request,
        "entries": recent_audit,
    })


def _build_agent_cards(
    identities: list[dict], agents: list[dict],
) -> list[dict]:
    """Merge identity config with live agent status."""
    # Build status lookup from supervisor
    agent_status = {a.get("name", ""): a for a in agents}

    cards = []
    for identity in identities:
        name = identity["name"]
        status = agent_status.get(name, {})

        cards.append({
            "name": name,
            "display_name": identity.get("display_name", name.capitalize()),
            "description": identity.get("description", ""),
            "personality_ref": identity.get("personality_ref", ""),
            "connectors": identity.get("connectors", []),
            "capabilities": identity.get("capability_names", []),
            "llm_model": identity.get("llm", {}).get("model", "unknown"),
            # Status from supervisor (or defaults if not running)
            "state": status.get("state", "stopped"),
            "pid": status.get("pid"),
            "uptime": status.get("uptime", 0),
            "restart_count": status.get("restart_count", 0),
        })

    return cards
