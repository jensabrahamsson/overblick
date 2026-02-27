"""
System health route — host metrics, LLM gateway status, resource gauges.

Polls HostInspectionCapability for system data and Gateway /health for LLM metrics.
Uses htmx partial updates for live polling without full page reloads.
"""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from overblick.capabilities.monitoring.inspector import HostInspectionCapability
from overblick.capabilities.monitoring.models import HostHealth

logger = logging.getLogger(__name__)

router = APIRouter()

# Gateway health endpoint
_GATEWAY_URL = "http://127.0.0.1:8200/health"
_GATEWAY_TIMEOUT = 3.0


async def _fetch_gateway_health() -> dict[str, Any] | None:
    """Fetch LLM Gateway health data. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT) as client:
            resp = await client.get(_GATEWAY_URL)
            if resp.status_code == 200:
                return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.debug("Gateway health unavailable: %s", exc)
    return None


async def _collect_host_health() -> HostHealth:
    """Collect host health via HostInspectionCapability. Returns defaults on failure."""
    try:
        inspector = HostInspectionCapability()
        return await inspector.inspect()
    except Exception as exc:
        logger.warning("Host inspection failed: %s", exc)
        return HostHealth()


def _normalize_backends(gateway: dict[str, Any]) -> dict[str, Any]:
    """Normalize backends to rich dict format.

    Handles both old format (str values like "connected") and new format
    (dict values with status/type/model/default) so the dashboard works
    regardless of which gateway version is running.
    """
    backends = gateway.get("backends", {})
    default_name = gateway.get("default_backend", "")
    normalized = {}
    for name, info in backends.items():
        if isinstance(info, str):
            # Old format: "connected" / "disconnected"
            normalized[name] = {
                "status": info,
                "type": "unknown",
                "model": "unknown",
                "default": name == default_name,
            }
        else:
            normalized[name] = info
    gateway["backends"] = normalized
    return gateway


def _build_metrics_context(
    health: HostHealth,
    gateway: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build template context from health data and gateway response."""
    # CPU load normalized to percentage of core count
    cpu_percent = 0.0
    if health.cpu.core_count > 0:
        cpu_percent = min(100.0, (health.cpu.load_1m / health.cpu.core_count) * 100)

    if gateway is not None:
        gateway = _normalize_backends(gateway)

    return {
        "health": health,
        "health_grade": health.health_grade,
        "cpu_percent": round(cpu_percent, 1),
        "gateway": gateway,
        "gateway_available": gateway is not None,
    }


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    """Render System Health page."""
    templates = request.app.state.templates
    health = await _collect_host_health()
    gateway = await _fetch_gateway_health()
    ctx = _build_metrics_context(health, gateway)

    return templates.TemplateResponse("system.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        **ctx,
    })


@router.get("/system/metrics", response_class=HTMLResponse)
async def system_metrics_partial(request: Request):
    """htmx partial: live system metrics (polled every 5s)."""
    templates = request.app.state.templates
    health = await _collect_host_health()
    gateway = await _fetch_gateway_health()
    ctx = _build_metrics_context(health, gateway)

    return templates.TemplateResponse("partials/system_metrics.html", {
        "request": request,
        **ctx,
    })
