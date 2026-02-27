"""
Moltbook route — profile links and account status for Moltbook agents.
"""

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

MOLTBOOK_BASE_URL = "https://www.moltbook.com/u"


@router.get("/moltbook", response_class=HTMLResponse)
async def moltbook_page(request: Request):
    """Render the Moltbook profiles page."""
    templates = request.app.state.templates
    system_svc = request.app.state.system_service

    profiles = _get_moltbook_profiles()
    statuses = system_svc.get_moltbook_statuses()

    # Merge status info into profiles
    status_map = {s["identity"]: s for s in statuses}
    for profile in profiles:
        s = status_map.get(profile["identity"])
        if s:
            profile["status"] = s.get("status", "unknown")
            profile["detail"] = s.get("detail", "")
            profile["updated_at"] = s.get("updated_at", "")

    return templates.TemplateResponse("moltbook.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "profiles": profiles,
    })


def has_data() -> bool:
    """Return True if any identity has the moltbook plugin configured."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("moltbook")


# Re-export shared helpers for use within this module
def _safe_load_yaml(path: Path) -> dict:
    """Load a YAML file safely, returning {} on any error."""
    from overblick.dashboard.routes._plugin_utils import safe_load_yaml
    return safe_load_yaml(path)


def _collect_plugins(identity_dir: Path) -> set[str]:
    """Collect plugins from all config sources for an identity."""
    from overblick.dashboard.routes._plugin_utils import collect_plugins
    return collect_plugins(identity_dir)


def _get_moltbook_profiles() -> list[dict]:
    """Gather Moltbook profile data for identities with the moltbook plugin."""
    identities_dir = Path("overblick/identities")
    if not identities_dir.exists():
        return []

    profiles = []
    for d in sorted(identities_dir.iterdir()):
        if not d.is_dir():
            continue

        # Load both files once
        personality_data = _safe_load_yaml(d / "personality.yaml")
        identity_data = _safe_load_yaml(d / "identity.yaml")

        # Collect plugins from all sources
        plugins: set[str] = set()
        top = personality_data.get("plugins", [])
        if isinstance(top, list):
            plugins.update(top)
        op = personality_data.get("operational", {})
        if isinstance(op, dict):
            op_plugins = op.get("plugins", [])
            if isinstance(op_plugins, list):
                plugins.update(op_plugins)
        id_plugins = identity_data.get("plugins", [])
        if isinstance(id_plugins, list):
            plugins.update(id_plugins)

        if "moltbook" not in plugins:
            continue

        # Skip if personality.yaml was empty/missing (no data to show)
        if not personality_data:
            continue

        identity_section = personality_data.get("identity", {})
        display_name = identity_section.get("display_name", d.name.capitalize())
        bio = personality_data.get("moltbook_bio", "").strip()
        agent_name = identity_data.get("agent_name", display_name)

        profiles.append({
            "identity": d.name,
            "display_name": display_name,
            "bio": bio,
            "url": f"{MOLTBOOK_BASE_URL}/{agent_name}",
            "status": None,
            "detail": "",
            "updated_at": "",
        })

    return profiles
