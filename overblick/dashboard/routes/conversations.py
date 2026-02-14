"""
Conversations route — inter-agent communication viewer.

Reads conversation history from agent data directories and displays
them in a chat-style timeline. Future-proof: scans all identity
directories for conversation state files, not just specific agents.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_conversations(data_dir: Path, identity_filter: str = "") -> tuple[list[dict], list[str]]:
    """
    Load conversations from all identity data directories.

    Scans data/<identity>/host_health/host_health_state.json for each
    identity directory found.

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

        # Check for host_health state file
        state_file = ident_dir / "host_health" / "host_health_state.json"
        if not state_file.exists():
            continue

        ident_name = ident_dir.name
        identities.add(ident_name)

        if identity_filter and ident_name != identity_filter:
            continue

        try:
            data = json.loads(state_file.read_text())
            for conv in data.get("conversations", []):
                conv["identity"] = ident_name
                conversations.append(conv)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load conversations for '%s': %s", ident_name, e)

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
