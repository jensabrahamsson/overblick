"""
Stage route — behavioral scenario results viewer.

Displays scenario test results with pass/fail status, constraint details,
and failure analysis.
"""

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

_PAGE_SIZE = 50

router = APIRouter()


@router.get("/stage", response_class=HTMLResponse)
async def stage_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Stage scenario results page."""
    templates = request.app.state.templates

    try:
        all_results = await asyncio.to_thread(_load_results, request)
        data_errors = []
    except Exception as e:
        logger.error("Failed to load stage data: %s", e, exc_info=True)
        all_results = []
        data_errors = [f"Failed to load stage data: {e}"]

    total = len(all_results)
    results = all_results[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("stage.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "results": results,
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if stage plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("stage")


def _load_results(request: Request) -> list:
    """Load Stage results from data directories."""
    import json
    from pathlib import Path

    from overblick.dashboard.routes._plugin_utils import resolve_data_root
    results = []
    data_root = resolve_data_root(request)
    if not data_root.exists():
        return results

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "stage_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                results.extend(data.get("results", []))
            except Exception as e:
                logger.warning("Failed to load stage state from %s: %s", state_file, e)

    results.sort(key=lambda r: r.get("run_at", 0), reverse=True)
    return results
