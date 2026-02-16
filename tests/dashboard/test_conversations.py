"""
Tests for the conversations dashboard route.
"""

import json
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
