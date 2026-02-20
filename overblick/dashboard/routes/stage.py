"""
Stage route — behavioral scenario results viewer.

Displays scenario test results with pass/fail status, constraint details,
and failure analysis.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stage", response_class=HTMLResponse)
async def stage_page(request: Request):
    """Render the Stage scenario results page."""
    templates = request.app.state.templates

    results = _load_results(request)

    return templates.TemplateResponse("stage.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "results": results,
    })


def _load_results(request: Request) -> list:
    """Load Stage results from data directories."""
    import json
    from pathlib import Path

    results = []
    data_root = Path("data")
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
    return results[:50]
