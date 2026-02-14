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

    # Build plugin cards (showing which agents use each plugin)
    plugin_cards = _build_plugin_cards(identities, agents)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "plugin_cards": plugin_cards,
        "supervisor_running": supervisor_status is not None,
        "supervisor_status": supervisor_status or {},
        "recent_audit": recent_audit,
        "audit_count_24h": audit_count_24h,
        "total_identities": len(identities),
        "total_agents": len(agents),
        "poll_interval": config.poll_interval,
    })


@router.get("/partials/plugin-cards", response_class=HTMLResponse)
async def plugin_cards_partial(request: Request):
    """htmx partial: refreshed plugin cards."""
    templates = request.app.state.templates

    identity_svc = request.app.state.identity_service
    supervisor_svc = request.app.state.supervisor_service

    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()
    plugin_cards = _build_plugin_cards(identities, agents)

    return templates.TemplateResponse("partials/plugin_cards.html", {
        "request": request,
        "plugin_cards": plugin_cards,
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


def _build_plugin_cards(
    identities: list[dict], agents: list[dict],
) -> list[dict]:
    """Build plugin cards showing which agents use each plugin."""
    # Build status lookup from supervisor
    agent_status = {a.get("name", ""): a for a in agents}

    # Group agents by plugin (connector)
    plugin_map: dict[str, list[dict]] = {}

    for identity in identities:
        name = identity["name"]
        status = agent_status.get(name, {})
        connectors = identity.get("connectors", [])

        # Get Big Five traits for emotion radar chart (if available)
        traits = identity.get("traits", {})
        big_five = {k: v for k, v in traits.items() if k in [
            "openness", "conscientiousness", "extraversion",
            "agreeableness", "neuroticism"
        ]}

        agent_info = {
            "name": name,
            "display_name": identity.get("display_name", name.capitalize()),
            "state": status.get("state", "stopped"),
            "personality_ref": identity.get("personality_ref", ""),
            "traits": big_five,
        }

        for connector in connectors:
            if connector not in plugin_map:
                plugin_map[connector] = []
            plugin_map[connector].append(agent_info)

    # Build plugin cards
    cards = []
    for plugin_name, plugin_agents in sorted(plugin_map.items()):
        running_count = sum(1 for a in plugin_agents if a["state"] == "running")

        cards.append({
            "name": plugin_name,
            "display_name": plugin_name.replace("_", " ").title(),
            "agent_count": len(plugin_agents),
            "running_count": running_count,
            "agents": plugin_agents,
        })

    return cards


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
