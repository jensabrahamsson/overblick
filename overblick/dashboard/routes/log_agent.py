"""
Log Agent route — autonomous log analysis and anomaly detection.

Displays scan history, anomalies detected, alerts sent, and
agentic goals from the log_agent database.
"""

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_PAGE_SIZE = 30


@router.get("/logs", response_class=HTMLResponse)
async def log_agent_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Log Agent dashboard page."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_log_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load log agent data: %s", e, exc_info=True)
        data = {"actions": [], "goals": [], "stats": {}, "ticks": []}
        data_errors = [f"Failed to load log agent data: {e}"]

    all_actions = data["actions"]
    total = len(all_actions)
    actions = all_actions[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("log_agent.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "actions": actions,
        "goals": data["goals"],
        "stats": data["stats"],
        "ticks": data["ticks"],
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if log_agent plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("log_agent")


def _load_log_data(request: Request) -> dict:
    """Load Log Agent data from SQLite databases across identities."""
    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    actions: list[dict] = []
    goals: list[dict] = []
    ticks: list[dict] = []
    stats = {"total_ticks": 0, "actions_taken": 0, "alerts_sent": 0, "learnings": 0}

    if not data_root.exists():
        return {"actions": actions, "goals": goals, "stats": stats, "ticks": ticks}

    for identity_dir in data_root.iterdir():
        db_path = identity_dir / "log_agent.db"
        if not db_path.exists():
            continue

        identity_name = identity_dir.name
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Action log
            try:
                rows = conn.execute(
                    "SELECT action_type, target, reasoning, success, "
                    "result, error, duration_ms, created_at "
                    "FROM action_log ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                for row in rows:
                    actions.append({
                        "identity": identity_name,
                        "action_type": row["action_type"],
                        "target": row["target"],
                        "reasoning": row["reasoning"],
                        "success": bool(row["success"]),
                        "result": row["result"],
                        "error": row["error"],
                        "duration_ms": row["duration_ms"],
                        "created_at": row["created_at"],
                    })
            except sqlite3.OperationalError:
                pass

            # Goals
            try:
                rows = conn.execute(
                    "SELECT name, description, priority, status, progress "
                    "FROM agent_goals ORDER BY priority DESC"
                ).fetchall()
                for row in rows:
                    goals.append({
                        "identity": identity_name,
                        "name": row["name"],
                        "description": row["description"],
                        "priority": row["priority"],
                        "status": row["status"],
                        "progress": row["progress"],
                    })
            except sqlite3.OperationalError:
                pass

            # Tick log (recent scan cycles)
            try:
                rows = conn.execute(
                    "SELECT tick_number, observations_count, actions_planned, "
                    "actions_executed, actions_succeeded, reasoning_summary, "
                    "duration_ms, completed_at "
                    "FROM tick_log ORDER BY tick_number DESC LIMIT 20"
                ).fetchall()
                for row in rows:
                    ticks.append({
                        "identity": identity_name,
                        "tick": row["tick_number"],
                        "observations": row["observations_count"],
                        "planned": row["actions_planned"],
                        "executed": row["actions_executed"],
                        "succeeded": row["actions_succeeded"],
                        "summary": row["reasoning_summary"],
                        "duration_ms": row["duration_ms"],
                        "completed_at": row["completed_at"],
                    })
            except sqlite3.OperationalError:
                pass

            # Stats
            try:
                stats["total_ticks"] += conn.execute(
                    "SELECT COUNT(*) FROM tick_log"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            try:
                stats["actions_taken"] += conn.execute(
                    "SELECT COUNT(*) FROM action_log"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            try:
                stats["alerts_sent"] += conn.execute(
                    "SELECT COUNT(*) FROM action_log WHERE action_type = 'send_alert'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            try:
                stats["learnings"] += conn.execute(
                    "SELECT COUNT(*) FROM agent_learnings"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

            conn.close()
        except Exception as e:
            logger.warning("Failed to read log_agent db for %s: %s", identity_name, e)

    actions.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return {"actions": actions, "goals": goals, "stats": stats, "ticks": ticks}
