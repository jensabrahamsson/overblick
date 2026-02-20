"""
Compass route — identity drift detector dashboard.

Displays baselines, drift history, and alerts for all monitored identities.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/compass", response_class=HTMLResponse)
async def compass_page(request: Request):
    """Render the Compass drift detection page."""
    templates = request.app.state.templates

    baselines, alerts, drift_history, drift_threshold = _load_compass_data(request)

    return templates.TemplateResponse("compass.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "baselines": baselines,
        "alerts": alerts,
        "drift_history": drift_history,
        "drift_threshold": drift_threshold,
    })


def _load_compass_data(request: Request) -> tuple:
    """Load Compass data from data directories."""
    import json
    from pathlib import Path

    baselines = {}
    alerts = []
    drift_history = []
    drift_threshold = 2.0

    data_root = Path("data")
    if not data_root.exists():
        return baselines, alerts, drift_history, drift_threshold

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "compass_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                baselines.update(data.get("baselines", {}))
                alerts.extend(data.get("alerts", []))
                drift_history.extend(data.get("drift_history", []))
            except Exception as e:
                logger.warning("Failed to load compass state from %s: %s", state_file, e)

    alerts.sort(key=lambda a: a.get("fired_at", 0), reverse=True)
    drift_history.sort(key=lambda d: d.get("measured_at", 0), reverse=True)
    return baselines, alerts[:50], drift_history[:100], drift_threshold
