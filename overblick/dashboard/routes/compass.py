"""
Compass route — identity drift detector dashboard.

Displays baselines, drift history, and alerts for all monitored identities.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

# Severity classification multipliers for drift score thresholds
_CRITICAL_MULTIPLIER = 2.0  # drift_score > threshold * 2 = critical
_WARNING_MULTIPLIER = 1.0   # drift_score > threshold * 1 = warning

router = APIRouter()


@router.get("/compass", response_class=HTMLResponse)
async def compass_page(request: Request):
    """Render the Compass drift detection page."""
    templates = request.app.state.templates

    try:
        baselines, alerts, drift_history, drift_threshold, identity_status = (
            _load_compass_data(request)
        )
        data_errors = []
    except Exception as e:
        logger.error("Failed to load compass data: %s", e, exc_info=True)
        baselines, alerts, drift_history = {}, [], []
        drift_threshold, identity_status = 2.0, {}
        data_errors = [f"Failed to load compass data: {e}"]

    return templates.TemplateResponse("compass.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "baselines": baselines,
        "alerts": alerts,
        "drift_history": drift_history,
        "drift_threshold": drift_threshold,
        "identity_status": identity_status,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if compass plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("compass")


def _classify_severity(drift_score: float, threshold: float) -> str:
    """Classify drift severity based on score vs threshold.

    Returns 'critical', 'warning', or 'info'.
    """
    if drift_score > _CRITICAL_MULTIPLIER * threshold:
        return "critical"
    if drift_score > _WARNING_MULTIPLIER * threshold:
        return "warning"
    return "info"


def _load_compass_data(request: Request) -> tuple:
    """Load Compass data from data directories.

    Returns (baselines, alerts, drift_history, drift_threshold, identity_status).
    Each alert gets a 'severity' field. identity_status maps identity name to
    latest drift score and severity.
    """
    import json
    from pathlib import Path

    baselines = {}
    alerts = []
    drift_history = []
    drift_threshold = 2.0

    from overblick.dashboard.routes._plugin_utils import resolve_data_root
    data_root = resolve_data_root(request)
    if not data_root.exists():
        return baselines, alerts, drift_history, drift_threshold, {}

    for identity_dir in data_root.iterdir():
        state_file = identity_dir / "compass" / "compass_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                baselines.update(data.get("baselines", {}))
                alerts.extend(data.get("alerts", []))
                drift_history.extend(data.get("drift_history", []))
            except Exception as e:
                logger.warning("Failed to load compass state from %s: %s", state_file, e)

    # Add severity to each alert
    for alert in alerts:
        alert["severity"] = _classify_severity(
            alert.get("drift_score", 0), alert.get("threshold", drift_threshold)
        )

    # Sort alerts: critical first, then warning, then info; within each group by time
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (severity_order.get(a.get("severity"), 9), -a.get("fired_at", 0)))

    drift_history.sort(key=lambda d: d.get("measured_at", 0), reverse=True)

    # Add severity to drift history entries
    for entry in drift_history:
        entry["severity"] = _classify_severity(
            entry.get("drift_score", 0), drift_threshold
        )

    # Build identity status: latest drift per identity
    identity_status = {}
    for entry in drift_history:
        name = entry.get("identity_name", "")
        if name and name not in identity_status:
            identity_status[name] = {
                "drift_score": entry.get("drift_score", 0),
                "severity": entry["severity"],
            }
    # Include baselines with no drift as 'low' severity
    for name in baselines:
        if name not in identity_status:
            identity_status[name] = {"drift_score": 0, "severity": "low"}

    return baselines, alerts[:50], drift_history[:100], drift_threshold, identity_status
