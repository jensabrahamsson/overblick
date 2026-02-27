"""
Skuggspel route — shadow-self content viewer.

Displays shadow content: the repressed side of each identity,
marked with shadow metadata on the dashboard.
"""

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

_PAGE_SIZE = 30

router = APIRouter()


@router.get("/skuggspel", response_class=HTMLResponse)
async def skuggspel_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Skuggspel shadow content page."""
    templates = request.app.state.templates

    try:
        all_posts = await asyncio.to_thread(_load_posts, request)
        data_errors = []
    except Exception as e:
        logger.error("Failed to load skuggspel data: %s", e, exc_info=True)
        all_posts = []
        data_errors = [f"Failed to load skuggspel data: {e}"]

    total = len(all_posts)
    posts = all_posts[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("skuggspel.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "posts": posts,
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if skuggspel plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("skuggspel")


def _load_posts(request: Request) -> list:
    """Load Skuggspel posts from data directories."""
    import json
    from pathlib import Path

    from overblick.dashboard.routes._plugin_utils import resolve_data_root
    posts = []
    data_root = resolve_data_root(request)
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
    return posts
