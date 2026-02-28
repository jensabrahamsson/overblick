"""
Kontrast route — multi-perspective content viewer.

Displays Kontrast pieces: multiple identity perspectives on the same topic,
shown side-by-side on the dashboard.
"""

import asyncio
import logging

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


_PAGE_SIZE = 20


@router.get("/kontrast", response_class=HTMLResponse)
async def kontrast_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Kontrast multi-perspective page."""
    templates = request.app.state.templates

    try:
        all_pieces = await asyncio.to_thread(_load_pieces, request)
        data_errors = []
    except Exception as e:
        logger.error("Failed to load kontrast data: %s", e, exc_info=True)
        all_pieces = []
        data_errors = [f"Failed to load kontrast data: {e}"]

    total = len(all_pieces)
    pieces = all_pieces[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("kontrast.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "pieces": pieces,
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if kontrast plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("kontrast")


def _load_pieces(request: Request) -> list:
    """Load Kontrast pieces from the data directory."""
    import json
    from pathlib import Path

    # Try to find kontrast state files across all identity data dirs
    from overblick.dashboard.routes._plugin_utils import resolve_data_root
    pieces = []
    data_root = resolve_data_root(request)
    if not data_root.exists():
        return pieces

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "kontrast" / "kontrast_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                pieces.extend(data.get("pieces", []))
            except Exception as e:
                logger.warning("Failed to load kontrast state from %s: %s", state_file, e)

    # Sort by creation time, newest first
    pieces.sort(key=lambda p: p.get("created_at", 0), reverse=True)
    return pieces
