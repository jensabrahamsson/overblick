"""
Identities routes — view all identities and personalities.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


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

    # Merge personality data into identities for display
    personality_map = {p["name"]: p for p in personalities}
    for identity in identities:
        pref = identity.get("identity_ref", identity["name"])
        identity["personality_data"] = personality_map.get(pref)

    return templates.TemplateResponse("identities.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "identities": identities,
        "personalities": personalities,
        "capability_bundles": capability_bundles,
    })
