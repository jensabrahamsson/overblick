"""
Telegram route — message and notification monitoring.

Displays Telegram bot activity: messages sent/received,
notification history from audit log entries.
"""

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_PAGE_SIZE = 30


@router.get("/telegram", response_class=HTMLResponse)
async def telegram_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Telegram dashboard page."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_telegram_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load telegram data: %s", e, exc_info=True)
        data = {"notifications": [], "stats": {}}
        data_errors = [f"Failed to load telegram data: {e}"]

    all_notifications = data["notifications"]
    total = len(all_notifications)
    notifications = all_notifications[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("telegram.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "notifications": notifications,
        "stats": data["stats"],
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if telegram plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("telegram")


def _load_telegram_data(request: Request) -> dict:
    """Load Telegram notification data from email_agent databases.

    Telegram notifications are tracked via ``notification_tracking`` in
    the email_agent database (since Telegram is used as the notification
    channel for email classification results).  We also scan audit logs
    for any direct telegram plugin entries.
    """
    from pathlib import Path
    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    notifications: list[dict] = []
    stats = {"sent": 0, "feedback_received": 0, "identities": 0}

    if not data_root.exists():
        return {"notifications": notifications, "stats": stats}

    identities_with_data = 0

    for identity_dir in data_root.iterdir():
        # Check email_agent.db for notification_tracking table
        db_path = identity_dir / "email_agent.db"
        if not db_path.exists():
            continue

        identity_name = identity_dir.name
        has_identity_data = False

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            try:
                rows = conn.execute(
                    "SELECT notification_text, feedback_received, feedback_text, "
                    "is_draft_reply, created_at "
                    "FROM notification_tracking ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                for row in rows:
                    notifications.append({
                        "identity": identity_name,
                        "text": row["notification_text"],
                        "feedback": bool(row["feedback_received"]),
                        "feedback_text": row["feedback_text"],
                        "is_draft": bool(row["is_draft_reply"]),
                        "created_at": row["created_at"],
                    })
                    has_identity_data = True
                    stats["sent"] += 1
                    if row["feedback_received"]:
                        stats["feedback_received"] += 1
            except sqlite3.OperationalError:
                pass

            conn.close()
        except Exception as e:
            logger.warning("Failed to read telegram data for %s: %s", identity_name, e)

        if has_identity_data:
            identities_with_data += 1

    stats["identities"] = identities_with_data
    notifications.sort(key=lambda n: n.get("created_at", 0), reverse=True)
    return {"notifications": notifications, "stats": stats}
