"""
GitHub Agent route — autonomous GitHub issue/PR management.

Displays events observed, actions taken, PR tracking, goals,
and reflection history from the agentic database.
"""

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_PAGE_SIZE = 30


@router.get("/github", response_class=HTMLResponse)
async def github_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the GitHub Agent dashboard page."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_github_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load github agent data: %s", e, exc_info=True)
        data = {"actions": [], "goals": [], "stats": {}, "prs": []}
        data_errors = [f"Failed to load github agent data: {e}"]

    all_actions = data["actions"]
    total = len(all_actions)
    actions = all_actions[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("github.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "actions": actions,
        "goals": data["goals"],
        "stats": data["stats"],
        "prs": data["prs"],
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if github plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("github")


def _load_github_data(request: Request) -> dict:
    """Load GitHub agent data from SQLite databases across identities."""
    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    actions: list[dict] = []
    goals: list[dict] = []
    prs: list[dict] = []
    stats = {"events": 0, "actions_taken": 0, "comments_posted": 0, "prs_tracked": 0}

    if not data_root.exists():
        return {"actions": actions, "goals": goals, "stats": stats, "prs": prs}

    for identity_dir in data_root.iterdir():
        db_path = identity_dir / "github.db"
        if not db_path.exists():
            continue

        identity_name = identity_dir.name
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Action log
            try:
                rows = conn.execute(
                    "SELECT action_type, target, repo, reasoning, success, "
                    "result, duration_ms, created_at "
                    "FROM action_log ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                for row in rows:
                    actions.append({
                        "identity": identity_name,
                        "action_type": row["action_type"],
                        "target": row["target"],
                        "repo": row["repo"],
                        "reasoning": row["reasoning"],
                        "success": bool(row["success"]),
                        "result": row["result"],
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

            # PR tracking
            try:
                rows = conn.execute(
                    "SELECT repo, pr_number, title, author, is_dependabot, "
                    "ci_status, merged, auto_merged, first_seen "
                    "FROM pr_tracking ORDER BY first_seen DESC LIMIT 20"
                ).fetchall()
                for row in rows:
                    prs.append({
                        "identity": identity_name,
                        "repo": row["repo"],
                        "pr_number": row["pr_number"],
                        "title": row["title"],
                        "author": row["author"],
                        "is_dependabot": bool(row["is_dependabot"]),
                        "ci_status": row["ci_status"],
                        "merged": bool(row["merged"]),
                        "auto_merged": bool(row["auto_merged"]),
                        "first_seen": row["first_seen"],
                    })
            except sqlite3.OperationalError:
                pass

            # Stats
            try:
                stats["events"] += conn.execute(
                    "SELECT COUNT(*) FROM events_seen"
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
                stats["comments_posted"] += conn.execute(
                    "SELECT COUNT(*) FROM comments_posted"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            try:
                stats["prs_tracked"] += conn.execute(
                    "SELECT COUNT(*) FROM pr_tracking"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

            conn.close()
        except Exception as e:
            logger.warning("Failed to read github db for %s: %s", identity_name, e)

    actions.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return {"actions": actions, "goals": goals, "stats": stats, "prs": prs}
