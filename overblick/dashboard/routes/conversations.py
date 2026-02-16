"""
Conversations route — inter-agent communication viewer.

Reads conversation history from agent data directories AND from audit
databases, then merges them into a unified chat-style timeline.

Sources:
- host_health/host_health_state.json (health inquiries from JSON files)
- Any **/conversations.json files (plugin conversations)
- Supervisor audit.db IPC entries (email consultations, health, research)

Future-proof: automatically discovers new conversation sources.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Known conversation file patterns to scan
_CONVERSATION_SOURCES = [
    ("host_health", "host_health_state.json"),
]


def _relative_time(timestamp_val: str | float | int) -> str:
    """
    Convert a timestamp to a human-readable relative time string.

    Args:
        timestamp_val: ISO format string (e.g. "2026-02-15T10:30:00")
                       or epoch float/int (e.g. 1739500000.0)

    Returns:
        Relative time string (e.g. "2 min ago", "3h ago", "yesterday")
    """
    try:
        if isinstance(timestamp_val, (int, float)):
            ts = datetime.fromtimestamp(timestamp_val)
        else:
            ts = datetime.fromisoformat(timestamp_val)
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

        # Skip hidden directories
        if ident_name.startswith("."):
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


# Maps received→response action names for IPC audit pairing
_PAIR_MAP = {
    "email_consultation_received": "email_consultation_response",
    "health_inquiry_received": "health_response_sent",
    "research_request_received": "research_response_sent",
}

# Maximum time gap (seconds) between request and response to consider them paired
_PAIR_WINDOW_SECONDS = 120


def _load_audit_conversations(
    audit_service: Any,
    identity_filter: str = "",
) -> tuple[list[dict], set[str]]:
    """
    Load conversations from supervisor audit database IPC entries.

    Queries the audit service for IPC-category entries, pairs request/response
    events by (plugin, sender) within a time window, and maps them to the
    conversation dict format expected by the template.

    Args:
        audit_service: The AuditService instance from app state.
        identity_filter: If set, only include conversations involving this identity.

    Returns:
        Tuple of (conversations list, identity names set)
    """
    conversations: list[dict] = []
    identities: set[str] = set()

    try:
        entries = audit_service.query(
            identity="supervisor",
            category="ipc",
            since_hours=168,  # 1 week
            limit=200,
        )
    except Exception as e:
        logger.warning("Failed to query audit service for IPC entries: %s", e)
        return [], set()

    if not entries:
        return [], set()

    # Group entries by (plugin, sender) for pairing
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        details = entry.get("details") or {}
        if not isinstance(details, dict):
            continue
        sender = details.get("sender", "")
        plugin = entry.get("plugin", "") or ""
        key = (plugin, sender)
        groups.setdefault(key, []).append(entry)

    # Within each group, pair received→response by timestamp proximity
    for (_plugin, _sender), group_entries in groups.items():
        received_entries = [
            e for e in group_entries if e.get("action") in _PAIR_MAP
        ]
        response_actions = set(_PAIR_MAP.values())
        resp_entries = [
            e for e in group_entries if e.get("action") in response_actions
        ]

        used_response_ids: set[int] = set()

        for req in received_entries:
            req_ts = req.get("timestamp", 0)
            req_action = req.get("action")
            if not req_action or req_action not in _PAIR_MAP:
                continue
            expected_resp_action = _PAIR_MAP[req_action]

            # Find closest response within the time window
            best_resp = None
            best_gap = _PAIR_WINDOW_SECONDS + 1
            for resp in resp_entries:
                if id(resp) in used_response_ids:
                    continue
                if resp.get("action") != expected_resp_action:
                    continue
                gap = abs(resp.get("timestamp", 0) - req_ts)
                if gap < best_gap:
                    best_gap = gap
                    best_resp = resp

            if best_resp is None:
                continue

            # Mark matched response as used so it's not reused
            used_response_ids.add(id(best_resp))

            req_details = req.get("details") or {}
            resp_details = best_resp.get("details") or {}
            sender = req_details.get("sender", "unknown")

            if identity_filter and sender != identity_filter:
                continue

            identities.add(sender)

            # Map to conversation format based on action type
            conv = _map_audit_pair(req["action"], req_ts, sender, req_details, resp_details)
            if conv:
                conversations.append(conv)

    return conversations, identities


def _map_audit_pair(
    action: str,
    timestamp: float,
    sender: str,
    req_details: dict,
    resp_details: dict,
) -> dict | None:
    """
    Map a paired audit request/response to a conversation dict.

    Returns None if the action type is unrecognized.
    """
    iso_ts = datetime.fromtimestamp(timestamp).isoformat()

    if action == "email_consultation_received":
        email_from = req_details.get("email_from", "unknown")
        email_subject = req_details.get("email_subject", "")
        advised = resp_details.get("advised_action", "")
        reasoning = resp_details.get("reasoning", "")
        response_text = f"{advised}: {reasoning}" if advised and reasoning else advised or reasoning
        return {
            "timestamp": iso_ts,
            "sender": sender,
            "motivation": f"Email from {email_from}: '{email_subject}'",
            "responder": "supervisor",
            "response": response_text,
            "conversation_type": "email",
            "identity": sender,
            "source": "audit",
            "relative_time": _relative_time(timestamp),
        }

    if action == "health_inquiry_received":
        return {
            "timestamp": iso_ts,
            "sender": sender,
            "motivation": req_details.get("motivation", ""),
            "responder": "supervisor",
            "response": resp_details.get("response_preview", ""),
            "health_grade": resp_details.get("health_grade"),
            "conversation_type": "health",
            "identity": sender,
            "source": "audit",
            "relative_time": _relative_time(timestamp),
        }

    if action == "research_request_received":
        return {
            "timestamp": iso_ts,
            "sender": sender,
            "motivation": req_details.get("query", ""),
            "responder": "supervisor",
            "response": resp_details.get("summary_preview", ""),
            "conversation_type": "research",
            "identity": sender,
            "source": "audit",
            "relative_time": _relative_time(timestamp),
        }

    return None


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

    json_conversations, json_identities = _load_conversations(data_dir, identity_filter)

    # Load audit-based conversations
    audit_service = getattr(request.app.state, "audit_service", None)
    audit_conversations: list[dict] = []
    audit_identities: set[str] = set()
    if audit_service:
        audit_conversations, audit_identities = _load_audit_conversations(
            audit_service, identity_filter
        )

    # Deduplicate: skip audit health entries if JSON health exists for same identity
    json_health_identities = {
        c["identity"] for c in json_conversations
        if c.get("source") == "host_health"
    }
    deduped_audit = [
        c for c in audit_conversations
        if not (c.get("conversation_type") == "health" and c["identity"] in json_health_identities)
    ]

    # Merge and sort by timestamp descending
    all_conversations = json_conversations + deduped_audit
    all_conversations.sort(key=lambda c: c.get("timestamp", ""), reverse=True)

    all_identities = sorted(set(json_identities) | audit_identities)

    return templates.TemplateResponse("conversations.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "conversations": all_conversations,
        "identities": all_identities,
        "selected_identity": identity_filter,
    })
