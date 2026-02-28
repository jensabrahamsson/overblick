"""
Observability Command Center — agent-level operational monitoring.

Provides a unified view of LLM Gateway performance, agent fleet status,
audit activity, message routing, and error feeds. Each section is an
independent htmx partial that polls every 5 seconds.

This complements the /system page (host-level metrics) with agent-level
and service-level operational data.
"""

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_GATEWAY_HEALTH_URL = "http://127.0.0.1:8200/health"
_GATEWAY_STATS_URL = "http://127.0.0.1:8200/stats"
_GATEWAY_TIMEOUT = 3.0

# Cache for local plugin map (loaded once from config/overblick.yaml)
_local_plugin_cache: dict[str, list[str]] | None = None


def _load_local_plugin_map() -> dict[str, list[str]]:
    """Load identity→local_plugins mapping from config/overblick.yaml."""
    global _local_plugin_cache
    if _local_plugin_cache is not None:
        return _local_plugin_cache

    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "overblick.yaml"
    if not config_path.exists():
        _local_plugin_cache = {}
        return _local_plugin_cache

    try:
        import yaml
        data = yaml.safe_load(config_path.read_text()) or {}
        lp = data.get("local_plugins", {})
        _local_plugin_cache = {k: list(v) for k, v in lp.items()} if lp else {}
    except Exception as e:
        logger.debug("Failed to load local_plugins from config: %s", e)
        _local_plugin_cache = {}

    return _local_plugin_cache


async def _fetch_gateway(url: str) -> dict[str, Any] | None:
    """Fetch a Gateway endpoint. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.debug("Gateway unavailable at %s: %s", url, exc)
    return None


def _format_uptime(seconds: float) -> str:
    """Format seconds into Xh Ym string."""
    if seconds <= 0:
        return "—"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _agent_health_color(agent: dict, error_rate: float) -> str:
    """Determine health color for an agent dot.

    Green: running + error rate <10%
    Amber: running + error rate >=10% OR restart_count > 0
    Red: crashed/stopped/error rate >=25%
    """
    state = agent.get("state", "offline")
    restarts = agent.get("restart_count", 0)

    if state not in ("running",):
        return "red"
    if error_rate >= 25:
        return "red"
    if error_rate >= 10 or restarts > 0:
        return "amber"
    return "green"


# ---- Main page ----

@router.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request):
    """Render the Monitor Command Center page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("observability.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
    })


# ---- Section A: Agent Health Strip ----

@router.get("/monitor/agents-strip", response_class=HTMLResponse)
async def agents_strip_partial(request: Request):
    """htmx partial: agent health status dots."""
    templates = request.app.state.templates
    supervisor_svc = request.app.state.supervisor_service
    audit_svc = request.app.state.audit_service

    agents = await supervisor_svc.get_agents()
    supervisor_status = await supervisor_svc.get_status()

    # Calculate per-agent error rate for health color (single query per agent)
    agent_dots = []
    for agent in agents:
        name = agent.get("name", "")
        total, failures = audit_svc.count_with_failures(identity=name, since_hours=1)
        error_rate = (failures / total * 100) if total > 0 else 0.0
        color = _agent_health_color(agent, error_rate)

        agent_dots.append({
            "name": name,
            "state": agent.get("state", "offline"),
            "color": color,
            "error_rate": round(error_rate, 1),
        })

    return templates.TemplateResponse("partials/obs_agents_strip.html", {
        "request": request,
        "agent_dots": agent_dots,
        "supervisor_running": supervisor_status is not None,
    })


# ---- Section B: LLM Gateway ----

@router.get("/monitor/gateway", response_class=HTMLResponse)
async def gateway_partial(request: Request):
    """htmx partial: LLM Gateway metrics."""
    templates = request.app.state.templates
    health = await _fetch_gateway(_GATEWAY_HEALTH_URL)
    stats = await _fetch_gateway(_GATEWAY_STATS_URL)

    gateway_available = health is not None
    combined: dict[str, Any] = {}

    if health:
        combined.update(health)
        combined["gateway_status"] = health.get("status", "unknown")
        # Normalize backends
        backends = health.get("backends", {})
        default_name = health.get("default_backend", "")
        normalized = {}
        for name, info in backends.items():
            if isinstance(info, str):
                normalized[name] = {
                    "status": info,
                    "type": "unknown",
                    "model": "unknown",
                    "default": name == default_name,
                }
            else:
                normalized[name] = info
        combined["backends"] = normalized

    if stats:
        combined["requests_processed"] = stats.get("requests_processed", 0)
        combined["high_priority"] = stats.get("requests_high_priority", 0)
        combined["low_priority"] = stats.get("requests_low_priority", 0)
        combined["uptime"] = _format_uptime(stats.get("uptime_seconds", 0))

    return templates.TemplateResponse("partials/obs_gateway.html", {
        "request": request,
        "gateway": combined,
        "gateway_available": gateway_available,
    })


# ---- Section C: Agent Fleet ----

@router.get("/monitor/fleet", response_class=HTMLResponse)
async def fleet_partial(request: Request):
    """htmx partial: agent fleet table."""
    templates = request.app.state.templates
    supervisor_svc = request.app.state.supervisor_service

    agents = await supervisor_svc.get_agents()
    supervisor_status = await supervisor_svc.get_status()

    # Load local plugins from config to supplement plugin lists
    local_plugins = _load_local_plugin_map()

    fleet_rows = []
    for agent in agents:
        uptime_sec = agent.get("uptime", agent.get("uptime_seconds", 0))
        name = agent.get("name", "")
        plugins = list(agent.get("plugins", []))
        # Merge local plugins that aren't already listed
        for lp in local_plugins.get(name, []):
            if lp not in plugins:
                plugins.append(lp)
        fleet_rows.append({
            "name": name,
            "state": agent.get("state", "offline"),
            "pid": agent.get("pid"),
            "uptime": _format_uptime(uptime_sec),
            "restart_count": agent.get("restart_count", 0),
            "plugins": plugins,
        })

    return templates.TemplateResponse("partials/obs_fleet.html", {
        "request": request,
        "fleet_rows": fleet_rows,
        "supervisor_running": supervisor_status is not None,
    })


# ---- Section D: Audit Activity ----

@router.get("/monitor/audit-activity", response_class=HTMLResponse)
async def audit_activity_partial(request: Request):
    """htmx partial: audit activity sparkline and category breakdown."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service

    hourly = audit_svc.count_by_hour(hours=12)
    categories = audit_svc.count_by_category(since_hours=24)
    total_24h = audit_svc.count(since_hours=24)
    failures_24h = audit_svc.count(since_hours=24, success=False)
    llm_24h = audit_svc.count(since_hours=24, category="llm")
    error_rate = (failures_24h / total_24h * 100) if total_24h > 0 else 0.0
    events_per_hour = round(total_24h / 24, 1) if total_24h > 0 else 0.0

    # Find max category count for proportional bars
    max_cat_count = max(categories.values()) if categories else 1

    return templates.TemplateResponse("partials/obs_audit_activity.html", {
        "request": request,
        "hourly": hourly,
        "categories": categories,
        "total_24h": total_24h,
        "events_per_hour": events_per_hour,
        "error_rate": round(error_rate, 1),
        "llm_24h": llm_24h,
        "max_cat_count": max_cat_count,
    })


# ---- Section E: Message Routing ----

@router.get("/monitor/routing", response_class=HTMLResponse)
async def routing_partial(request: Request):
    """htmx partial: message routing stats."""
    templates = request.app.state.templates
    supervisor_svc = request.app.state.supervisor_service

    status = await supervisor_svc.get_status()
    routing: dict[str, Any] = {}

    if status:
        routing = status.get("routing", {})

    return templates.TemplateResponse("partials/obs_routing.html", {
        "request": request,
        "routing": routing,
        "supervisor_running": status is not None,
    })


# ---- Section F: Error Feed ----

@router.get("/monitor/errors", response_class=HTMLResponse)
async def errors_partial(request: Request):
    """htmx partial: recent error entries from audit log."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service

    # Query failed audit entries from the last 6 hours
    errors = audit_svc.query(since_hours=6, limit=20)
    # Filter to only failures
    errors = [e for e in errors if not e.get("success", True)]

    return templates.TemplateResponse("partials/obs_errors.html", {
        "request": request,
        "errors": errors,
    })
