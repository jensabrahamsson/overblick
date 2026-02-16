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
    llm_calls_24h = audit_svc.count(since_hours=24, category="llm")
    failed_24h = audit_svc.count(since_hours=24, success=False)
    error_rate = (failed_24h / audit_count_24h * 100) if audit_count_24h > 0 else 0.0
    categories = audit_svc.get_categories()

    # Build agent status rows (running processes only)
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "agent_rows": agent_rows,
        "supervisor_running": supervisor_status is not None,
        "supervisor_status": supervisor_status or {},
        "entries": recent_audit,
        "audit_count_24h": audit_count_24h,
        "llm_calls_24h": llm_calls_24h,
        "error_rate": error_rate,
        "categories": categories,
        "total_identities": len(identities),
        "total_agents": len(agents),
        "poll_interval": config.poll_interval,
    })


@router.get("/partials/agent-status", response_class=HTMLResponse)
async def agent_status_partial(request: Request):
    """htmx partial: refreshed agent status rows."""
    templates = request.app.state.templates

    identity_svc = request.app.state.identity_service
    supervisor_svc = request.app.state.supervisor_service
    audit_svc = request.app.state.audit_service

    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc)
    supervisor_status = await supervisor_svc.get_status()

    return templates.TemplateResponse("partials/agent_status.html", {
        "request": request,
        "agent_rows": agent_rows,
        "supervisor_running": supervisor_status is not None,
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
    llm_calls = audit_svc.count(since_hours=24, category="llm")
    failed = audit_svc.count(since_hours=24, success=False)
    error_rate = (failed / audit_count * 100) if audit_count > 0 else 0.0

    return templates.TemplateResponse("partials/system_health.html", {
        "request": request,
        "supervisor_running": supervisor_status is not None,
        "total_agents": len(agents),
        "total_identities": len(identity_svc.list_identities()),
        "audit_count_24h": audit_count,
        "llm_calls_24h": llm_calls,
        "error_rate": error_rate,
    })


@router.get("/partials/audit-recent", response_class=HTMLResponse)
async def audit_recent_partial(request: Request):
    """htmx partial: recent audit entries with optional category filter."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service

    category = request.query_params.get("category", "")
    recent_audit = audit_svc.query(limit=20, category=category)

    return templates.TemplateResponse("partials/audit_table.html", {
        "request": request,
        "entries": recent_audit,
    })


@router.post("/agent/{name}/start", response_class=HTMLResponse)
async def agent_start(name: str, request: Request):
    """Start a stopped agent and return updated agent status partial."""
    templates = request.app.state.templates
    supervisor_svc = request.app.state.supervisor_service
    identity_svc = request.app.state.identity_service
    audit_svc = request.app.state.audit_service

    result = await supervisor_svc.start_agent(name)

    # Re-fetch state and return updated partial
    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc)
    supervisor_status = await supervisor_svc.get_status()

    return templates.TemplateResponse("partials/agent_status.html", {
        "request": request,
        "agent_rows": agent_rows,
        "supervisor_running": supervisor_status is not None,
        "action_result": result,
    })


@router.post("/agent/{name}/stop", response_class=HTMLResponse)
async def agent_stop(name: str, request: Request):
    """Stop a running agent and return updated agent status partial."""
    templates = request.app.state.templates
    supervisor_svc = request.app.state.supervisor_service
    identity_svc = request.app.state.identity_service
    audit_svc = request.app.state.audit_service

    result = await supervisor_svc.stop_agent(name)

    # Re-fetch state and return updated partial
    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc)
    supervisor_status = await supervisor_svc.get_status()

    return templates.TemplateResponse("partials/agent_status.html", {
        "request": request,
        "agent_rows": agent_rows,
        "supervisor_running": supervisor_status is not None,
        "action_result": result,
    })


_ACRONYMS = {"ai", "llm", "rss", "api", "ipc"}


def _plugin_display_name(name: str) -> str:
    """Convert plugin snake_case name to display name, preserving acronyms."""
    return " ".join(
        w.upper() if w in _ACRONYMS else w.capitalize()
        for w in name.split("_")
    )


def _build_plugin_cards(
    identities: list[dict], agents: list[dict],
) -> list[dict]:
    """Build plugin cards showing which agents use each plugin."""
    # Build status lookup from supervisor
    agent_status = {a.get("name", ""): a for a in agents}

    # Group agents by plugin
    plugin_map: dict[str, list[dict]] = {}

    for identity in identities:
        name = identity["name"]
        status = agent_status.get(name, {})
        plugins = identity.get("plugins", [])

        # Get Big Five traits for emotion radar chart (if available)
        traits = identity.get("traits", {})
        big_five = {k: v for k, v in traits.items() if k in [
            "openness", "conscientiousness", "extraversion",
            "agreeableness", "neuroticism"
        ]}

        agent_info = {
            "name": name,
            "display_name": identity.get("display_name", name.capitalize()),
            "state": status.get("state", "offline"),
            "identity_ref": identity.get("identity_ref", ""),
            "traits": big_five,
        }

        for plugin in plugins:
            if plugin not in plugin_map:
                plugin_map[plugin] = []
            plugin_map[plugin].append(agent_info)

    # Build plugin cards
    cards = []
    for plugin_name, plugin_agents in sorted(plugin_map.items()):
        running_count = sum(1 for a in plugin_agents if a["state"] == "running")

        cards.append({
            "name": plugin_name,
            "display_name": _plugin_display_name(plugin_name),
            "agent_count": len(plugin_agents),
            "running_count": running_count,
            "agents": plugin_agents,
        })

    return cards


def _build_agent_status_rows(
    identities: list[dict], agents: list[dict], audit_svc=None,
) -> list[dict]:
    """Build operational status rows: one row per identity+plugin.

    Shows identities that have plugins (= runnable agents), including
    stopped ones. Identities without plugins are personality definitions,
    not agents — they belong on the Identities page, not here.
    """
    # Build lookup: identity name -> running process info
    running_lookup = {a.get("name", ""): a for a in agents}
    rows = []

    for identity in identities:
        ident_name = identity.get("name", "")
        plugins = identity.get("plugins", [])

        # Skip identities without plugins — they're not runnable agents
        if not plugins:
            continue

        proc = running_lookup.get(ident_name)
        is_running = proc is not None and proc.get("state") == "running"

        # Get last audit action for this identity
        last_action = None
        if audit_svc:
            recent = audit_svc.query(identity=ident_name, limit=1)
            if recent:
                last_action = {
                    "action": recent[0].get("action", ""),
                    "category": recent[0].get("category", ""),
                    "timestamp": recent[0].get("timestamp", 0),
                    "success": recent[0].get("success", True),
                }

        # One row per plugin = one agent
        for plugin in plugins:
            state = proc.get("state", "offline") if proc else "offline"
            rows.append({
                "agent_name": _plugin_display_name(plugin),
                "plugin": plugin,
                "identity_name": identity.get("display_name", ident_name.capitalize()),
                "identity_ref": ident_name,
                "state": state,
                "pid": proc.get("pid") if proc else None,
                "uptime": proc.get("uptime", proc.get("uptime_seconds", 0)) if proc else 0,
                "restart_count": proc.get("restart_count", 0) if proc else 0,
                "last_action": last_action,
                "can_start": not is_running,
                "can_stop": is_running,
            })

    return rows
