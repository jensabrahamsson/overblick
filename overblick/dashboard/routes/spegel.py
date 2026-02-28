"""
Spegel route — inter-agent psychological profiling viewer.

Displays profiling pairs: observer's profile and target's reflection,
side-by-side on the dashboard.
"""

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

_PAGE_SIZE = 30

router = APIRouter()


@router.get("/spegel", response_class=HTMLResponse)
async def spegel_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Spegel profiling page."""
    templates = request.app.state.templates

    try:
        all_pairs = await asyncio.to_thread(_load_pairs, request)
        data_errors = []
    except Exception as e:
        logger.error("Failed to load spegel data: %s", e, exc_info=True)
        all_pairs = []
        data_errors = [f"Failed to load spegel data: {e}"]

    total = len(all_pairs)
    pairs = all_pairs[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("spegel.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "pairs": pairs,
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if spegel plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("spegel")


def _load_pairs(request: Request) -> list:
    """Load Spegel pairs from data directories."""
    import json
    from pathlib import Path

    from overblick.dashboard.routes._plugin_utils import resolve_data_root
    pairs = []
    data_root = resolve_data_root(request)
    if not data_root.exists():
        return pairs

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "spegel" / "spegel_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                pairs.extend(data.get("pairs", []))
            except Exception as e:
                logger.warning("Failed to load spegel state from %s: %s", state_file, e)

    pairs.sort(key=lambda p: p.get("created_at", 0), reverse=True)
    return pairs
