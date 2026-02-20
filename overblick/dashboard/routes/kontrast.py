"""
Kontrast route — multi-perspective content viewer.

Displays Kontrast pieces: multiple identity perspectives on the same topic,
shown side-by-side on the dashboard.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/kontrast", response_class=HTMLResponse)
async def kontrast_page(request: Request):
    """Render the Kontrast multi-perspective page."""
    templates = request.app.state.templates

    # Get stored pieces from plugin state (read-only)
    pieces = _load_pieces(request)

    return templates.TemplateResponse("kontrast.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "pieces": pieces,
    })


def _load_pieces(request: Request) -> list:
    """Load Kontrast pieces from the data directory."""
    import json
    from pathlib import Path

    # Try to find kontrast state files across all identity data dirs
    pieces = []
    data_root = Path("data")
    if not data_root.exists():
        return pieces

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "kontrast_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                pieces.extend(data.get("pieces", []))
            except Exception as e:
                logger.warning("Failed to load kontrast state from %s: %s", state_file, e)

    # Sort by creation time, newest first
    pieces.sort(key=lambda p: p.get("created_at", 0), reverse=True)
    return pieces[:20]
