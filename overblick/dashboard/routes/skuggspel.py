"""
Skuggspel route — shadow-self content viewer.

Displays shadow content: the repressed side of each identity,
marked with shadow metadata on the dashboard.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/skuggspel", response_class=HTMLResponse)
async def skuggspel_page(request: Request):
    """Render the Skuggspel shadow content page."""
    templates = request.app.state.templates

    posts = _load_posts(request)

    return templates.TemplateResponse("skuggspel.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "posts": posts,
    })


def _load_posts(request: Request) -> list:
    """Load Skuggspel posts from data directories."""
    import json
    from pathlib import Path

    posts = []
    data_root = Path("data")
    if not data_root.exists():
        return posts

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "skuggspel_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                posts.extend(data.get("posts", []))
            except Exception as e:
                logger.warning("Failed to load skuggspel state from %s: %s", state_file, e)

    posts.sort(key=lambda p: p.get("generated_at", 0), reverse=True)
    return posts[:30]
