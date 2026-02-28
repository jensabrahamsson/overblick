"""
Email Agent route — email classification and reply monitoring.

Displays email processing history: classified emails, reply queue,
sender reputation, and agent learning stats.
"""

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_PAGE_SIZE = 30


@router.get("/email", response_class=HTMLResponse)
async def email_page(request: Request, page: int = Query(default=1, ge=1)):
    """Render the Email Agent dashboard page."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_email_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load email agent data: %s", e, exc_info=True)
        data = {"emails": [], "stats": {}, "reputation": []}
        data_errors = [f"Failed to load email agent data: {e}"]

    all_emails = data["emails"]
    total = len(all_emails)
    emails = all_emails[:page * _PAGE_SIZE]
    has_more = total > page * _PAGE_SIZE

    return templates.TemplateResponse("email.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "emails": emails,
        "stats": data["stats"],
        "reputation": data["reputation"],
        "page": page,
        "has_more": has_more,
        "data_errors": data_errors,
    })


def has_data() -> bool:
    """Return True if email_agent plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured
    return is_plugin_configured("email_agent")


def _load_email_data(request: Request) -> dict:
    """Load email agent data from SQLite databases across identities."""
    from pathlib import Path
    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    emails: list[dict] = []
    stats = {"processed": 0, "replied": 0, "notified": 0, "ignored": 0}
    reputation: list[dict] = []

    if not data_root.exists():
        return {"emails": emails, "stats": stats, "reputation": reputation}

    for identity_dir in data_root.iterdir():
        db_path = identity_dir / "email_agent.db"
        if not db_path.exists():
            continue

        identity_name = identity_dir.name
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Recent emails
            try:
                rows = conn.execute(
                    "SELECT email_from, email_subject, classified_intent, "
                    "confidence, action_taken, created_at "
                    "FROM email_records ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
                for row in rows:
                    emails.append({
                        "identity": identity_name,
                        "sender": row["email_from"],
                        "subject": row["email_subject"],
                        "intent": row["classified_intent"],
                        "confidence": row["confidence"],
                        "action": row["action_taken"],
                        "created_at": row["created_at"],
                    })
            except sqlite3.OperationalError:
                pass

            # Stats
            try:
                count = conn.execute("SELECT COUNT(*) FROM email_records").fetchone()[0]
                stats["processed"] += count
                for intent in ("reply", "notify", "ignore"):
                    c = conn.execute(
                        "SELECT COUNT(*) FROM email_records WHERE classified_intent = ?",
                        (intent,),
                    ).fetchone()[0]
                    key = "replied" if intent == "reply" else (
                        "notified" if intent == "notify" else "ignored"
                    )
                    stats[key] += c
            except sqlite3.OperationalError:
                pass

            # Sender reputation (top 10 by interaction count)
            try:
                rep_rows = conn.execute(
                    "SELECT sender_domain, ignore_count, notify_count, reply_count, "
                    "auto_ignore FROM sender_reputation "
                    "ORDER BY (ignore_count + notify_count + reply_count) DESC LIMIT 10"
                ).fetchall()
                for row in rep_rows:
                    reputation.append({
                        "identity": identity_name,
                        "domain": row["sender_domain"],
                        "ignore_count": row["ignore_count"],
                        "notify_count": row["notify_count"],
                        "reply_count": row["reply_count"],
                        "auto_ignore": bool(row["auto_ignore"]),
                    })
            except sqlite3.OperationalError:
                pass

            conn.close()
        except Exception as e:
            logger.warning("Failed to read email db for %s: %s", identity_name, e)

    emails.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return {"emails": emails, "stats": stats, "reputation": reputation}
