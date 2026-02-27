"""
Identities routes — view all identities and personalities.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Identity icons — keyed by identity name (lowercase)
_IDENTITY_ICONS: dict[str, str] = {
    "anomal": "\U0001f9ea",     # 🧪
    "bjork": "\U0001f332",      # 🌲
    "blixt": "\u26a1",          # ⚡
    "cherry": "\U0001f352",     # 🍒
    "natt": "\U0001f319",       # 🌙
    "prisma": "\U0001f308",     # 🌈
    "rost": "\u2693",           # ⚓
    "smed": "\U0001f528",       # 🔨
    "stal": "\U0001f4e7",       # 📧
    "supervisor": "\U0001f441", # 👁
    "vakt": "\U0001f6e1",       # 🛡
}


@router.get("/identities", response_class=HTMLResponse)
async def identities_page(request: Request):
    """Render identities overview page."""
    templates = request.app.state.templates

    identity_svc = request.app.state.identity_service
    personality_svc = request.app.state.personality_service
    system_svc = request.app.state.system_service

    identities = identity_svc.get_all_identities()
    personalities = personality_svc.get_all_personalities()
    capability_bundles = system_svc.get_capability_bundles()

    # Merge personality data and icon into identities for display
    personality_map = {p["name"]: p for p in personalities}
    for identity in identities:
        pref = identity.get("identity_ref", identity["name"])
        identity["personality_data"] = personality_map.get(pref)
        identity["icon"] = _IDENTITY_ICONS.get(identity["name"].lower(), "")

    return templates.TemplateResponse("identities.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "identities": identities,
        "personalities": personalities,
        "capability_bundles": capability_bundles,
    })
