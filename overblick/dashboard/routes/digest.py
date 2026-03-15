"""
AI Digest route — RSS feed digest monitoring.

Displays configured feeds, last digest date, and digest schedule
for each identity running the ai_digest plugin.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/digest", response_class=HTMLResponse)
async def digest_page(request: Request):
    """Render the AI Digest dashboard page."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_digest_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load digest data: %s", e, exc_info=True)
        data = {"digests": []}
        data_errors = [f"Failed to load digest data: {e}"]

    return templates.TemplateResponse(
        "digest.html",
        {
            "request": request,
            "csrf_token": request.state.session.get("csrf_token", ""),
            "digests": data["digests"],
            "data_errors": data_errors,
        },
    )


def has_data() -> bool:
    """Return True if ai_digest plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured

    return is_plugin_configured("ai_digest")


def _load_digest_data(request: Request) -> dict:
    """Load AI Digest state from data directories."""
    from pathlib import Path

    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    digests: list[dict] = []

    if not data_root.exists():
        return {"digests": digests}

    for identity_dir in sorted(data_root.iterdir()):
        state_file = identity_dir / "ai_digest_state.json"
        if not state_file.exists():
            continue

        identity_name = identity_dir.name
        try:
            state = json.loads(state_file.read_text())

            # Try to load the latest digest content from logs or cache
            latest_digest_content = _load_latest_digest_content(identity_dir)

            digests.append(
                {
                    "identity": identity_name,
                    "last_digest_date": state.get("last_digest_date", "Never"),
                    "feed_count": state.get("feed_count", 0),
                    "article_count": state.get("article_count", 0),
                    "latest_digest_content": latest_digest_content,
                }
            )
        except Exception as e:
            logger.warning("Failed to load digest state for %s: %s", identity_name, e)

    return {"digests": digests}


def _load_latest_digest_content(identity_dir: Path) -> str:
    """Load the latest digest content from logs or cache."""
    # Try to find the most recent digest log entry
    logs_dir = identity_dir / "logs" if identity_dir.exists() else None

    if not logs_dir or not logs_dir.exists():
        return "<em>No digest content available yet</em>"

    try:
        # Look for digest.log files and find most recent one with digest content
        log_files = list(logs_dir.glob("*.log"))
        if not log_files:
            return "<em>No logs found</em>"

        # Sort by modification time (newest first)
        latest_log = max(log_files, key=lambda f: f.stat().st_mtime)

        with open(latest_log, "r") as f:
            content = f.read()

        # Extract digest section from logs
        if "DIGEST_GENERATED" in content or "Digest:" in content:
            lines = content.split("\n")
            in_digest = False
            digest_lines = []

            for line in lines[-100:]:  # Search last 100 lines
                if "Digest:" in line or "DIGEST_GENERATED" in line:
                    in_digest = True

                if in_digest:
                    digest_lines.append(line)

                    # Stop after a reasonable amount of content
                    if len(digest_lines) > 50:
                        break

            return "<pre>" + "\n".join(digest_lines[-30:]) + "</pre>"

        return "<em>Digest content not found in logs</em>"

    except Exception as e:
        logger.warning("Failed to load latest digest from logs: %s", e)
        return "<em>Error loading digest content</em>"
