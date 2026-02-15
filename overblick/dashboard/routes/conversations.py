"""
Conversations route — inter-agent communication viewer.

Reads conversation history from agent data directories and displays
them in a chat-style timeline. Scans multiple conversation sources:
- host_health/host_health_state.json (health inquiries)
- Any **/conversations.json files (email consultations, etc.)

Future-proof: automatically discovers new conversation sources.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Known conversation file patterns to scan
_CONVERSATION_SOURCES = [
    ("host_health", "host_health_state.json"),
]


def _relative_time(timestamp_str: str) -> str:
    """
    Convert an ISO timestamp to a human-readable relative time string.

    Args:
        timestamp_str: ISO format timestamp (e.g. "2026-02-15T10:30:00")

    Returns:
        Relative time string (e.g. "2 min ago", "3h ago", "yesterday")
    """
    try:
        ts = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        delta = now - ts

        seconds = int(delta.total_seconds())
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days}d ago"
        weeks = days // 7
        if weeks < 4:
            return f"{weeks}w ago"
        return ts.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _load_conversations(data_dir: Path, identity_filter: str = "") -> tuple[list[dict], list[str]]:
    """
    Load conversations from all identity data directories.

    Scans multiple conversation sources per identity:
    - host_health/host_health_state.json (health inquiries)
    - Any conversations.json files in plugin subdirectories

    Args:
        data_dir: Base data directory (project_root/data)
        identity_filter: If set, only load from this identity

    Returns:
        Tuple of (conversations list, identity names list)
    """
    conversations = []
    identities = set()

    if not data_dir.exists():
        return [], []

    for ident_dir in sorted(data_dir.iterdir()):
        if not ident_dir.is_dir():
            continue

        ident_name = ident_dir.name

        # Skip non-identity directories (like "supervisor")
        if ident_name.startswith(".") or ident_name == "supervisor":
            continue

        found_convos = False

        # Source 1: Known conversation files
        for subdir, filename in _CONVERSATION_SOURCES:
            state_file = ident_dir / subdir / filename
            if not state_file.exists():
                continue

            found_convos = True

            if identity_filter and ident_name != identity_filter:
                continue

            try:
                data = json.loads(state_file.read_text())
                for conv in data.get("conversations", []):
                    conv["identity"] = ident_name
                    conv["source"] = subdir
                    conv["relative_time"] = _relative_time(conv.get("timestamp", ""))
                    conversations.append(conv)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load conversations from '%s/%s': %s", ident_name, subdir, e)

        # Source 2: Generic conversations.json in any plugin subdirectory
        for conv_file in ident_dir.glob("*/conversations.json"):
            source = conv_file.parent.name
            # Skip already-loaded sources
            if source in dict(_CONVERSATION_SOURCES):
                continue

            found_convos = True

            if identity_filter and ident_name != identity_filter:
                continue

            try:
                data = json.loads(conv_file.read_text())
                conv_list = data if isinstance(data, list) else data.get("conversations", [])
                for conv in conv_list:
                    conv["identity"] = ident_name
                    conv["source"] = source
                    conv["relative_time"] = _relative_time(conv.get("timestamp", ""))
                    conversations.append(conv)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load conversations from '%s/%s': %s", ident_name, source, e)

        if found_convos:
            identities.add(ident_name)

    # Sort by timestamp descending (newest first)
    conversations.sort(key=lambda c: c.get("timestamp", ""), reverse=True)

    return conversations, sorted(identities)


@router.get("/conversations", response_class=HTMLResponse)
async def conversations_page(request: Request):
    """Render the agent conversations page."""
    templates = request.app.state.templates

    # Determine base data directory
    base_dir = Path(request.app.state.config.base_dir) if request.app.state.config.base_dir else None
    if not base_dir:
        base_dir = Path(__file__).parent.parent.parent.parent
    data_dir = base_dir / "data"

    # Optional identity filter
    identity_filter = request.query_params.get("identity", "")

    conversations, identities = _load_conversations(data_dir, identity_filter)

    return templates.TemplateResponse("conversations.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "conversations": conversations,
        "identities": identities,
        "selected_identity": identity_filter,
    })
