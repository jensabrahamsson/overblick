"""
Spegel route — inter-agent psychological profiling viewer.

Displays profiling pairs: observer's profile and target's reflection,
side-by-side on the dashboard.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/spegel", response_class=HTMLResponse)
async def spegel_page(request: Request):
    """Render the Spegel profiling page."""
    templates = request.app.state.templates

    pairs = _load_pairs(request)

    return templates.TemplateResponse("spegel.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "pairs": pairs,
    })


def _load_pairs(request: Request) -> list:
    """Load Spegel pairs from data directories."""
    import json
    from pathlib import Path

    pairs = []
    data_root = Path("data")
    if not data_root.exists():
        return pairs

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "spegel_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                pairs.extend(data.get("pairs", []))
            except Exception as e:
                logger.warning("Failed to load spegel state from %s: %s", state_file, e)

    pairs.sort(key=lambda p: p.get("created_at", 0), reverse=True)
    return pairs[:30]
