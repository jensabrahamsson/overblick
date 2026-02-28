"""
Dev Agent route — autonomous development task monitoring.

Displays bug reports, fix attempts, success rate, PRs created,
and agentic goals from the dev_agent database.
"""

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_PAGE_SIZE = 30


@router.get("/dev", response_class=HTMLResponse)
async def dev_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Dev Agent dashboard page."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_dev_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load dev agent data: %s", e, exc_info=True)
        data = {"bugs": [], "goals": [], "stats": {}}
        data_errors = [f"Failed to load dev agent data: {e}"]

    all_bugs = data["bugs"]
    total = len(all_bugs)
    bugs = all_bugs[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("dev.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "bugs": bugs,
        "goals": data["goals"],
        "stats": data["stats"],
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if dev_agent plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("dev_agent")


def _load_dev_data(request: Request) -> dict:
    """Load Dev Agent data from SQLite databases across identities."""
    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    bugs: list[dict] = []
    goals: list[dict] = []
    stats = {
        "total_bugs": 0, "fixed": 0, "failed": 0, "in_progress": 0,
        "fix_attempts": 0, "prs_created": 0,
    }

    if not data_root.exists():
        return {"bugs": bugs, "goals": goals, "stats": stats}

    for identity_dir in data_root.iterdir():
        db_path = identity_dir / "dev_agent.db"
        if not db_path.exists():
            continue

        identity_name = identity_dir.name
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Bugs
            try:
                rows = conn.execute(
                    "SELECT title, status, priority, fix_attempts, pr_url, "
                    "created_at, updated_at "
                    "FROM bugs ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                for row in rows:
                    bugs.append({
                        "identity": identity_name,
                        "title": row["title"],
                        "status": row["status"],
                        "priority": row["priority"],
                        "fix_attempts": row["fix_attempts"],
                        "pr_url": row["pr_url"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    })
            except sqlite3.OperationalError:
                pass

            # Stats
            try:
                stats["total_bugs"] += conn.execute(
                    "SELECT COUNT(*) FROM bugs"
                ).fetchone()[0]
                for status, key in [
                    ("fixed", "fixed"), ("failed", "failed"),
                    ("analyzing", "in_progress"), ("fixing", "in_progress"),
                ]:
                    c = conn.execute(
                        "SELECT COUNT(*) FROM bugs WHERE status = ?", (status,)
                    ).fetchone()[0]
                    stats[key] += c
            except sqlite3.OperationalError:
                pass

            try:
                stats["fix_attempts"] += conn.execute(
                    "SELECT COUNT(*) FROM fix_attempts"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

            try:
                stats["prs_created"] += conn.execute(
                    "SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_url != ''"
                ).fetchone()[0]
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

            conn.close()
        except Exception as e:
            logger.warning("Failed to read dev_agent db for %s: %s", identity_name, e)

    bugs.sort(key=lambda b: b.get("created_at", 0), reverse=True)
    return {"bugs": bugs, "goals": goals, "stats": stats}
