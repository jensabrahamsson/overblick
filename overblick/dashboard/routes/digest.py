"""
AI Digest route — RSS feed digest monitoring.

Displays configured feeds, last digest date, and digest schedule
for each identity running the ai_digest plugin.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/digest", response_class=HTMLResponse)
async def digest_page(request: Request):
    """Render the AI Digest dashboard page."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_digest_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load digest data: %s", e, exc_info=True)
        data = {"digests": []}
        data_errors = [f"Failed to load digest data: {e}"]

    return templates.TemplateResponse("digest.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "digests": data["digests"],
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if ai_digest plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("ai_digest")


def _load_digest_data(request: Request) -> dict:
    """Load AI Digest state from data directories."""
    from pathlib import Path
    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    digests: list[dict] = []

    if not data_root.exists():
        return {"digests": digests}

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "ai_digest_state.json"
        if not state_file.exists():
            continue

        identity_name = identity_dir.name
        try:
            state = json.loads(state_file.read_text())
            digests.append({
                "identity": identity_name,
                "last_digest_date": state.get("last_digest_date", "Never"),
                "feed_count": state.get("feed_count", 0),
                "article_count": state.get("article_count", 0),
            })
        except Exception as e:
            logger.warning("Failed to load digest state for %s: %s", identity_name, e)

    return {"digests": digests}
