"""
Tests for the conversations dashboard route.
"""

import json
import time
from pathlib import Path

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


@pytest.mark.asyncio
async def test_conversations_page_unauthenticated(client):
    """Unauthenticated access redirects to login."""
    response = await client.get("/conversations")
    assert response.status_code == 302


@pytest.mark.asyncio
async def test_conversations_page_empty(client, session_cookie, config):
    """Conversations page renders with no data."""
    cookie_value, csrf_token = session_cookie

    response = await client.get(
        "/conversations",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    assert "Agent Conversations" in response.text
    assert "No agent conversations recorded yet" in response.text


@pytest.mark.asyncio
async def test_conversations_page_with_data(client, session_cookie, config):
    """Conversations page renders conversation entries."""
    cookie_value, csrf_token = session_cookie

    # Create conversation data
    data_dir = Path(config.base_dir) / "data" / "natt" / "host_health"
    data_dir.mkdir(parents=True, exist_ok=True)
    state_file = data_dir / "host_health_state.json"
    state_file.write_text(json.dumps({
        "conversations": [
            {
                "timestamp": "2026-02-14T12:00:00",
                "sender": "natt",
                "motivation": "The substrate that holds us — does it ache?",
                "responder": "anomal",
                "response": "The host is doing rather well, actually.",
                "health_grade": "good",
            },
        ],
        "last_inquiry_time": 1000.0,
    }))

    response = await client.get(
        "/conversations",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    assert "substrate" in response.text
    assert "rather well" in response.text
    assert "good" in response.text
    assert "natt" in response.text
    assert "anomal" in response.text


@pytest.mark.asyncio
async def test_conversations_filter_by_identity(client, session_cookie, config):
    """Identity filter limits results to specific agent."""
    cookie_value, csrf_token = session_cookie

    # Create data for two identities
    for ident in ("natt", "cherry"):
        data_dir = Path(config.base_dir) / "data" / ident / "host_health"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "host_health_state.json").write_text(json.dumps({
            "conversations": [{
                "timestamp": "2026-02-14T12:00:00",
                "sender": ident,
                "motivation": f"Motivation from {ident}",
                "responder": "anomal",
                "response": f"Response to {ident}",
                "health_grade": "good",
            }],
        }))

    # Filter by natt
    response = await client.get(
        "/conversations?identity=natt",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    assert "Motivation from natt" in response.text
    assert "Motivation from cherry" not in response.text


@pytest.mark.asyncio
async def test_conversations_shows_multiple_entries(client, session_cookie, config):
    """Multiple conversations render in reverse chronological order."""
    cookie_value, csrf_token = session_cookie

    data_dir = Path(config.base_dir) / "data" / "natt" / "host_health"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "host_health_state.json").write_text(json.dumps({
        "conversations": [
            {
                "timestamp": "2026-02-14T10:00:00",
                "sender": "natt",
                "motivation": "Earlier question",
                "responder": "anomal",
                "response": "Earlier answer",
                "health_grade": "fair",
            },
            {
                "timestamp": "2026-02-14T13:00:00",
                "sender": "natt",
                "motivation": "Later question",
                "responder": "anomal",
                "response": "Later answer",
                "health_grade": "good",
            },
        ],
    }))

    response = await client.get(
        "/conversations",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    # Both should appear
    assert "Earlier question" in response.text
    assert "Later question" in response.text


def _make_ipc_pair(action_received, action_response, plugin, sender, req_details, resp_details):
    """Helper to create a matched audit IPC request/response pair."""
    now = time.time()
    return [
        {
            "id": 1, "timestamp": now - 5.0, "action": action_received,
            "category": "ipc", "identity": "supervisor", "plugin": plugin,
            "details": {"sender": sender, **req_details},
            "success": True, "duration_ms": 50.0, "error": None,
        },
        {
            "id": 2, "timestamp": now - 3.0, "action": action_response,
            "category": "ipc", "identity": "supervisor", "plugin": plugin,
            "details": {"sender": sender, **resp_details},
            "success": True, "duration_ms": 200.0, "error": None,
        },
    ]


@pytest.mark.asyncio
async def test_conversations_shows_audit_email_consultation(
    client, session_cookie, mock_audit_service,
):
    """Audit IPC email consultation entries appear on conversations page."""
    cookie_value, _csrf = session_cookie

    mock_audit_service.query.return_value = _make_ipc_pair(
        "email_consultation_received", "email_consultation_response",
        "email_handler", "stal",
        {"email_from": "alice@example.com", "email_subject": "Meeting tomorrow"},
        {"advised_action": "reply", "reasoning": "Routine scheduling request"},
    )

    response = await client.get(
        "/conversations",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    assert "alice@example.com" in response.text
    assert "Meeting tomorrow" in response.text
    assert "reply" in response.text
    assert "Routine scheduling request" in response.text
    assert "stal" in response.text
    assert "supervisor" in response.text
    assert "email" in response.text.lower()


@pytest.mark.asyncio
async def test_conversations_shows_audit_research(
    client, session_cookie, mock_audit_service,
):
    """Audit IPC research request entries appear on conversations page."""
    cookie_value, _csrf = session_cookie

    mock_audit_service.query.return_value = _make_ipc_pair(
        "research_request_received", "research_response_sent",
        "research_handler", "bjork",
        {"query": "What is quantum entanglement?", "context": "physics discussion"},
        {"summary_preview": "Quantum entanglement is a phenomenon where particles..."},
    )

    response = await client.get(
        "/conversations",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    assert "quantum entanglement" in response.text.lower()
    assert "particles" in response.text
    assert "bjork" in response.text
    assert "research" in response.text.lower()


@pytest.mark.asyncio
async def test_conversations_audit_filter_by_identity(
    client, session_cookie, mock_audit_service,
):
    """Identity filter applies to audit conversations too."""
    cookie_value, _csrf = session_cookie
    now = time.time()

    # Two email consultations from different senders
    mock_audit_service.query.return_value = [
        *_make_ipc_pair(
            "email_consultation_received", "email_consultation_response",
            "email_handler", "stal",
            {"email_from": "bob@example.com", "email_subject": "Invoice"},
            {"advised_action": "forward", "reasoning": "Needs finance review"},
        ),
        {
            "id": 3, "timestamp": now - 60.0,
            "action": "email_consultation_received",
            "category": "ipc", "identity": "supervisor", "plugin": "email_handler",
            "details": {"sender": "natt", "email_from": "eve@example.com", "email_subject": "Hello"},
            "success": True, "duration_ms": 50.0, "error": None,
        },
        {
            "id": 4, "timestamp": now - 58.0,
            "action": "email_consultation_response",
            "category": "ipc", "identity": "supervisor", "plugin": "email_handler",
            "details": {"sender": "natt", "advised_action": "ignore", "reasoning": "Spam"},
            "success": True, "duration_ms": 100.0, "error": None,
        },
    ]

    # Filter to stal only
    response = await client.get(
        "/conversations?identity=stal",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    assert "Invoice" in response.text
    assert "Hello" not in response.text


@pytest.mark.asyncio
async def test_conversations_deduplicates_health(
    client, session_cookie, config, mock_audit_service,
):
    """Health entries from JSON take priority — audit health is deduplicated."""
    cookie_value, _csrf = session_cookie

    # JSON health conversation for natt
    data_dir = Path(config.base_dir) / "data" / "natt" / "host_health"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "host_health_state.json").write_text(json.dumps({
        "conversations": [{
            "timestamp": "2026-02-14T12:00:00",
            "sender": "natt",
            "motivation": "JSON health inquiry",
            "responder": "anomal",
            "response": "All systems nominal",
            "health_grade": "good",
        }],
    }))

    # Audit health conversation for natt (should be deduplicated)
    mock_audit_service.query.return_value = _make_ipc_pair(
        "health_inquiry_received", "health_response_sent",
        "health_handler", "natt",
        {"motivation": "Audit health inquiry"},
        {"response_preview": "Host is fine", "health_grade": "good"},
    )

    response = await client.get(
        "/conversations",
        cookies={SESSION_COOKIE: cookie_value},
    )

    assert response.status_code == 200
    # JSON version should be present
    assert "JSON health inquiry" in response.text
    # Audit version should be deduplicated away
    assert "Audit health inquiry" not in response.text
