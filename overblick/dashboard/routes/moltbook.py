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
    """Return True if any identity has a moltbook_bio defined."""
    identities_dir = Path("overblick/identities")
    if not identities_dir.exists():
        return False
    for d in identities_dir.iterdir():
        if not d.is_dir():
            continue
        personality = d / "personality.yaml"
        if personality.exists():
            try:
                data = yaml.safe_load(personality.read_text()) or {}
                if data.get("moltbook_bio"):
                    return True
            except Exception:
                pass
    return False


def _get_moltbook_profiles() -> list[dict]:
    """Gather Moltbook profile data from identity personality files."""
    identities_dir = Path("overblick/identities")
    if not identities_dir.exists():
        return []

    profiles = []
    for d in sorted(identities_dir.iterdir()):
        if not d.is_dir():
            continue
        personality = d / "personality.yaml"
        if not personality.exists():
            continue
        try:
            data = yaml.safe_load(personality.read_text()) or {}
        except Exception:
            continue

        bio = data.get("moltbook_bio", "").strip()
        if not bio:
            continue

        display_name = data.get("display_name", d.name.capitalize())
        profiles.append({
            "identity": d.name,
            "display_name": display_name,
            "bio": bio,
            "url": f"{MOLTBOOK_BASE_URL}/{display_name}",
            "status": None,
            "detail": "",
            "updated_at": "",
        })

    return profiles
