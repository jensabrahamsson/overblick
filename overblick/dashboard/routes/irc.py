"""
IRC route — identity-to-identity conversation viewer.

Displays IRC-style conversations between agent identities on curated topics.
Uses htmx partial updates for live-polling the current conversation feed.

Data is read via IRCService (JSON files), not from live plugin instances.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_irc_service(request: Request):
    """Retrieve the IRC service from app state."""
    return getattr(request.app.state, "irc_service", None)


def _identity_color(name: str) -> str:
    """
    Generate a consistent HSL color for an identity name.

    Uses a simple hash to map identity names to hue values,
    keeping saturation and lightness fixed for readability on dark backgrounds.
    """
    hue = sum(ord(c) for c in name) * 37 % 360
    return f"hsl({hue}, 65%, 60%)"


@router.get("/irc", response_class=HTMLResponse)
async def irc_page(request: Request):
    """Render the IRC conversations page."""
    templates = request.app.state.templates

    irc_service = _get_irc_service(request)

    conversations: list[dict] = []
    current: dict | None = None

    if irc_service:
        conversations = irc_service.get_conversations(limit=20)
        current = irc_service.get_current_conversation()

    # Selected conversation from query param
    selected_id = request.query_params.get("id", "")
    selected: dict | None = None

    if selected_id and irc_service:
        selected = irc_service.get_conversation(selected_id)
    elif current:
        selected = current
        selected_id = current.get("id", "")
    elif conversations:
        selected = conversations[0]
        selected_id = selected.get("id", "")

    # Build color map for participants
    color_map: dict[str, str] = {}
    if selected:
        for name in selected.get("participants", []):
            color_map[name] = _identity_color(name)

    return templates.TemplateResponse("irc.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "conversations": conversations,
        "selected": selected,
        "selected_id": selected_id,
        "current_id": current.get("id", "") if current else "",
        "color_map": color_map,
        "identity_color": _identity_color,
        # Also pass as 'conversation' for the feed partial include
        "conversation": selected,
    })


@router.get("/irc/feed", response_class=HTMLResponse)
async def irc_feed_partial(request: Request):
    """htmx partial: live IRC feed (polled every 3s)."""
    templates = request.app.state.templates

    irc_service = _get_irc_service(request)

    conversation_id = request.query_params.get("id", "")
    conversation: dict | None = None

    if irc_service and conversation_id:
        conversation = irc_service.get_conversation(conversation_id)
    elif irc_service:
        conversation = irc_service.get_current_conversation()

    color_map: dict[str, str] = {}
    if conversation:
        for name in conversation.get("participants", []):
            color_map[name] = _identity_color(name)

    return templates.TemplateResponse("partials/irc_feed.html", {
        "request": request,
        "conversation": conversation,
        "color_map": color_map,
    })
