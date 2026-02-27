"""
Dashboard routes — main page with agent cards and system health.
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from overblick.dashboard.routes._plugin_utils import (
    IDENTITY_NAME_RE as _IDENTITY_NAME_RE,
    PLUGIN_NAME_RE as _PLUGIN_NAME_RE,
    resolve_base_dir as _resolve_base_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Render the main dashboard page."""
    # First-run: redirect to settings wizard if no config exists
    if getattr(request.app.state, "setup_needed", False):
        return RedirectResponse("/settings/", status_code=302)

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

    # Build agent status rows
    base_dir = _resolve_base_dir(request)
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc, base_dir)

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
    base_dir = _resolve_base_dir(request)
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc, base_dir)
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


@router.get("/partials/moltbook-status", response_class=HTMLResponse)
async def moltbook_status_partial(request: Request):
    """htmx partial: Moltbook account status badges."""
    templates = request.app.state.templates
    system_service = request.app.state.system_service
    statuses = system_service.get_moltbook_statuses()
    return templates.TemplateResponse("partials/moltbook_status.html", {
        "request": request,
        "statuses": statuses,
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


@router.post("/agent/{identity}/{plugin}/start", response_class=HTMLResponse)
async def agent_start(identity: str, plugin: str, request: Request):
    """Start a specific agent (plugin within an identity)."""
    if not _IDENTITY_NAME_RE.match(identity) or not _PLUGIN_NAME_RE.match(plugin):
        return Response("Invalid agent", status_code=400)
    templates = request.app.state.templates
    supervisor_svc = request.app.state.supervisor_service
    identity_svc = request.app.state.identity_service
    audit_svc = request.app.state.audit_service

    # If the identity process is offline, start it via supervisor
    agents = await supervisor_svc.get_agents()
    proc = next((a for a in agents if a.get("name") == identity), None)
    if not proc or proc.get("state") != "running":
        await supervisor_svc.start_agent(identity)

    # Mark this plugin as running
    base_dir = _resolve_base_dir(request)
    _write_plugin_state(base_dir, identity, plugin, "running")

    # Re-fetch state and return updated partial
    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc, base_dir)
    supervisor_status = await supervisor_svc.get_status()

    return templates.TemplateResponse("partials/agent_status.html", {
        "request": request,
        "agent_rows": agent_rows,
        "supervisor_running": supervisor_status is not None,
    })


@router.post("/agent/{identity}/{plugin}/stop", response_class=HTMLResponse)
async def agent_stop(identity: str, plugin: str, request: Request):
    """Stop a specific agent (plugin within an identity)."""
    if not _IDENTITY_NAME_RE.match(identity) or not _PLUGIN_NAME_RE.match(plugin):
        return Response("Invalid agent", status_code=400)
    templates = request.app.state.templates
    supervisor_svc = request.app.state.supervisor_service
    identity_svc = request.app.state.identity_service
    audit_svc = request.app.state.audit_service

    # Mark this plugin as stopped (orchestrator reads the control file)
    base_dir = _resolve_base_dir(request)
    _write_plugin_state(base_dir, identity, plugin, "stopped")

    # Re-fetch state and return updated partial
    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()
    agent_rows = _build_agent_status_rows(identities, agents, audit_svc, base_dir)
    supervisor_status = await supervisor_svc.get_status()

    return templates.TemplateResponse("partials/agent_status.html", {
        "request": request,
        "agent_rows": agent_rows,
        "supervisor_running": supervisor_status is not None,
    })


# Identity-level stop/start (used by agent detail page — affects all plugins)

@router.post("/agent/{name}/start", response_class=HTMLResponse)
async def identity_start(name: str, request: Request):
    """Start all agents for an identity (process-level)."""
    if not _IDENTITY_NAME_RE.match(name):
        return Response("Invalid identity name", status_code=400)
    supervisor_svc = request.app.state.supervisor_service
    identity_svc = request.app.state.identity_service

    await supervisor_svc.start_agent(name)

    # Clear all plugin stops for this identity
    base_dir = _resolve_base_dir(request)
    ident = next((i for i in identity_svc.get_all_identities() if i["name"] == name), None)
    if ident:
        for plugin in ident.get("plugins", []):
            _write_plugin_state(base_dir, name, plugin, "running")

    return Response(status_code=204)


@router.post("/agent/{name}/stop", response_class=HTMLResponse)
async def identity_stop(name: str, request: Request):
    """Stop all agents for an identity (process-level)."""
    if not _IDENTITY_NAME_RE.match(name):
        return Response("Invalid identity name", status_code=400)
    supervisor_svc = request.app.state.supervisor_service
    await supervisor_svc.stop_agent(name)
    return Response(status_code=204)


_ACRONYMS = {"ai", "llm", "rss", "api", "ipc"}


# -- Plugin control file helpers (per-agent stop/start) ----------------------

def _read_plugin_states(base_dir: Path, identity: str) -> dict[str, str]:
    """Read per-plugin states from the control file."""
    path = base_dir / "data" / identity / "plugin_control.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _write_plugin_state(base_dir: Path, identity: str, plugin: str, state: str) -> None:
    """Write a single plugin's state to the control file.

    Uses atomic write (temp file + os.rename) to prevent corruption
    if the process crashes mid-write or concurrent readers see partial data.
    """
    data = _read_plugin_states(base_dir, identity)
    data[plugin] = state
    path = base_dir / "data" / identity / "plugin_control.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.rename(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
    base_dir: Path | None = None,
) -> list[dict]:
    """Build operational status rows: one row per agent (plugin + identity).

    Each agent is independently stoppable via a control file that the
    orchestrator reads before each tick. The identity process stays alive.
    """
    if not base_dir:
        base_dir = Path(__file__).parent.parent.parent.parent
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
        process_running = proc is not None and proc.get("state") == "running"

        # Per-plugin control state
        plugin_states = _read_plugin_states(base_dir, ident_name)

        for plugin in plugins:
            plugin_stopped = plugin_states.get(plugin) == "stopped"

            if process_running and not plugin_stopped:
                state = "running"
            elif process_running and plugin_stopped:
                state = "stopped"
            else:
                state = proc.get("state", "offline") if proc else "offline"

            # Get last audit action for this specific plugin
            last_action = None
            if audit_svc:
                recent = audit_svc.query(identity=ident_name, plugin=plugin, limit=1)
                if recent:
                    last_action = {
                        "action": recent[0].get("action", ""),
                        "category": recent[0].get("category", ""),
                        "timestamp": recent[0].get("timestamp", 0),
                        "success": recent[0].get("success", True),
                    }

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
                "can_start": not process_running or plugin_stopped,
                "can_stop": process_running and not plugin_stopped,
            })

    return rows
