"""
Tests for the conversations dashboard route.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.dashboard.routes.conversations import (
    _is_ipc_conversation,
    _load_audit_conversations,
    _load_conversations,
    _map_audit_pair,
    _relative_time,
)


@pytest.mark.asyncio
async def test_conversations_page_unauthenticated(client):
    """Unauthenticated access redirects to login."""
    response = await client.get("/conversations")
    assert response.status_code == 302


@pytest.mark.asyncio
async def test_conversations_page_empty(client, session_cookie, config):
    """Conversations page renders with no data."""
    cookie_value, _csrf_token = session_cookie

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
    cookie_value, _csrf_token = session_cookie

    # Create conversation data
    data_dir = Path(config.base_dir) / "data" / "natt" / "host_health"
    data_dir.mkdir(parents=True, exist_ok=True)
    state_file = data_dir / "host_health_state.json"
    state_file.write_text(
        json.dumps(
            {
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
            }
        )
    )

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
    cookie_value, _csrf_token = session_cookie

    # Create data for two identities
    for ident in ("natt", "cherry"):
        data_dir = Path(config.base_dir) / "data" / ident / "host_health"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "host_health_state.json").write_text(
            json.dumps(
                {
                    "conversations": [
                        {
                            "timestamp": "2026-02-14T12:00:00",
                            "sender": ident,
                            "motivation": f"Motivation from {ident}",
                            "responder": "anomal",
                            "response": f"Response to {ident}",
                            "health_grade": "good",
                        }
                    ],
                }
            )
        )

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
    cookie_value, _csrf_token = session_cookie

    data_dir = Path(config.base_dir) / "data" / "natt" / "host_health"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "host_health_state.json").write_text(
        json.dumps(
            {
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
            }
        )
    )

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
            "id": 1,
            "timestamp": now - 5.0,
            "action": action_received,
            "category": "ipc",
            "identity": "supervisor",
            "plugin": plugin,
            "details": {"sender": sender, **req_details},
            "success": True,
            "duration_ms": 50.0,
            "error": None,
        },
        {
            "id": 2,
            "timestamp": now - 3.0,
            "action": action_response,
            "category": "ipc",
            "identity": "supervisor",
            "plugin": plugin,
            "details": {"sender": sender, **resp_details},
            "success": True,
            "duration_ms": 200.0,
            "error": None,
        },
    ]


@pytest.mark.asyncio
async def test_conversations_shows_audit_email_consultation(
    client,
    session_cookie,
    mock_audit_service,
):
    """Audit IPC email consultation entries appear on conversations page."""
    cookie_value, _csrf = session_cookie

    mock_audit_service.query.return_value = _make_ipc_pair(
        "email_consultation_received",
        "email_consultation_response",
        "email_handler",
        "stal",
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
    client,
    session_cookie,
    mock_audit_service,
):
    """Audit IPC research request entries appear on conversations page."""
    cookie_value, _csrf = session_cookie

    mock_audit_service.query.return_value = _make_ipc_pair(
        "research_request_received",
        "research_response_sent",
        "research_handler",
        "bjork",
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
    client,
    session_cookie,
    mock_audit_service,
):
    """Identity filter applies to audit conversations too."""
    cookie_value, _csrf = session_cookie
    now = time.time()

    # Two email consultations from different senders
    mock_audit_service.query.return_value = [
        *_make_ipc_pair(
            "email_consultation_received",
            "email_consultation_response",
            "email_handler",
            "stal",
            {"email_from": "bob@example.com", "email_subject": "Invoice"},
            {"advised_action": "forward", "reasoning": "Needs finance review"},
        ),
        {
            "id": 3,
            "timestamp": now - 60.0,
            "action": "email_consultation_received",
            "category": "ipc",
            "identity": "supervisor",
            "plugin": "email_handler",
            "details": {
                "sender": "natt",
                "email_from": "eve@example.com",
                "email_subject": "Hello",
            },
            "success": True,
            "duration_ms": 50.0,
            "error": None,
        },
        {
            "id": 4,
            "timestamp": now - 58.0,
            "action": "email_consultation_response",
            "category": "ipc",
            "identity": "supervisor",
            "plugin": "email_handler",
            "details": {"sender": "natt", "advised_action": "ignore", "reasoning": "Spam"},
            "success": True,
            "duration_ms": 100.0,
            "error": None,
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
    client,
    session_cookie,
    config,
    mock_audit_service,
):
    """Health entries from JSON take priority — audit health is deduplicated."""
    cookie_value, _csrf = session_cookie

    # JSON health conversation for natt
    data_dir = Path(config.base_dir) / "data" / "natt" / "host_health"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "host_health_state.json").write_text(
        json.dumps(
            {
                "conversations": [
                    {
                        "timestamp": "2026-02-14T12:00:00",
                        "sender": "natt",
                        "motivation": "JSON health inquiry",
                        "responder": "anomal",
                        "response": "All systems nominal",
                        "health_grade": "good",
                    }
                ],
            }
        )
    )

    # Audit health conversation for natt (should be deduplicated)
    mock_audit_service.query.return_value = _make_ipc_pair(
        "health_inquiry_received",
        "health_response_sent",
        "health_handler",
        "natt",
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


# ---- Unit tests for helpers ----


class TestRelativeTime:
    def test_should_return_seconds_ago(self):
        ts = (datetime.now(tz=UTC) - timedelta(seconds=30)).timestamp()
        result = _relative_time(ts)
        assert "s ago" in result

    def test_should_return_just_now_for_future(self):
        ts = (datetime.now(tz=UTC) + timedelta(hours=1)).timestamp()
        result = _relative_time(ts)
        assert result == "just now"

    def test_should_return_minutes_ago(self):
        ts = (datetime.now(tz=UTC) - timedelta(minutes=5)).timestamp()
        result = _relative_time(ts)
        assert "min ago" in result

    def test_should_return_hours_ago(self):
        ts = (datetime.now(tz=UTC) - timedelta(hours=3)).timestamp()
        result = _relative_time(ts)
        assert "h ago" in result

    def test_should_return_yesterday(self):
        ts = (datetime.now(tz=UTC) - timedelta(hours=30)).timestamp()
        result = _relative_time(ts)
        assert result == "yesterday"

    def test_should_return_days_ago(self):
        ts = (datetime.now(tz=UTC) - timedelta(days=3)).timestamp()
        result = _relative_time(ts)
        assert "d ago" in result

    def test_should_return_weeks_ago(self):
        ts = (datetime.now(tz=UTC) - timedelta(weeks=2)).timestamp()
        result = _relative_time(ts)
        assert "w ago" in result

    def test_should_return_date_for_old(self):
        ts = (datetime.now(tz=UTC) - timedelta(days=60)).timestamp()
        result = _relative_time(ts)
        assert "-" in result  # e.g. "2026-01-14"

    def test_should_handle_iso_string(self):
        iso = (datetime.now(tz=UTC) - timedelta(minutes=5)).isoformat()
        result = _relative_time(iso)
        assert "min ago" in result

    def test_should_handle_naive_iso_string(self):
        naive = (datetime.now(tz=UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
        result = _relative_time(naive)
        assert "min ago" in result

    def test_should_return_empty_on_invalid(self):
        assert _relative_time("not-a-date") == ""

    def test_should_return_empty_on_bad_type(self):
        assert _relative_time(None) == ""  # type: ignore[arg-type]


class TestIsIpcConversation:
    def test_should_return_true_for_valid(self):
        conv = {
            "timestamp": "2026-01-01T00:00:00",
            "sender": "natt",
            "motivation": "test",
            "responder": "anomal",
            "response": "ok",
        }
        assert _is_ipc_conversation(conv) is True

    def test_should_return_false_for_missing_fields(self):
        conv = {"timestamp": "2026-01-01T00:00:00", "sender": "natt"}
        assert _is_ipc_conversation(conv) is False


class TestMapAuditPair:
    def test_should_map_email_consultation(self):
        ts = time.time()
        result = _map_audit_pair(
            "email_consultation_received",
            ts,
            "stal",
            {"email_from": "alice@example.com", "email_subject": "Hello"},
            {"advised_action": "reply", "reasoning": "Routine"},
        )
        assert result is not None
        assert result["conversation_type"] == "email"
        assert "alice@example.com" in result["motivation"]
        assert "reply" in result["response"]

    def test_should_map_email_with_only_advised(self):
        ts = time.time()
        result = _map_audit_pair(
            "email_consultation_received",
            ts,
            "stal",
            {"email_from": "alice@example.com", "email_subject": "Hello"},
            {"advised_action": "reply", "reasoning": ""},
        )
        assert result["response"] == "reply"

    def test_should_map_email_with_only_reasoning(self):
        ts = time.time()
        result = _map_audit_pair(
            "email_consultation_received",
            ts,
            "stal",
            {"email_from": "alice@example.com", "email_subject": "Hello"},
            {"advised_action": "", "reasoning": "Just a test"},
        )
        assert result["response"] == "Just a test"

    def test_should_map_health_inquiry(self):
        ts = time.time()
        result = _map_audit_pair(
            "health_inquiry_received",
            ts,
            "natt",
            {"motivation": "How is the host?"},
            {"response_preview": "All good", "health_grade": "good"},
        )
        assert result is not None
        assert result["conversation_type"] == "health"

    def test_should_map_research_request(self):
        ts = time.time()
        result = _map_audit_pair(
            "research_request_received",
            ts,
            "bjork",
            {"query": "Quantum physics"},
            {"summary_preview": "It's about particles"},
        )
        assert result is not None
        assert result["conversation_type"] == "research"

    def test_should_return_none_for_unknown_action(self):
        result = _map_audit_pair("unknown_action", time.time(), "test", {}, {})
        assert result is None


class TestLoadConversations:
    def test_should_return_empty_when_no_data_dir(self, tmp_path):
        convs, ids = _load_conversations(tmp_path / "nonexistent")
        assert convs == []
        assert ids == []

    def test_should_skip_hidden_dirs(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        convs, _ids = _load_conversations(tmp_path)
        assert convs == []

    def test_should_skip_non_dirs(self, tmp_path):
        (tmp_path / "somefile.txt").write_text("hello")
        convs, _ids = _load_conversations(tmp_path)
        assert convs == []

    def test_should_handle_json_decode_error(self, tmp_path):
        data_dir = tmp_path / "natt" / "host_health"
        data_dir.mkdir(parents=True)
        (data_dir / "host_health_state.json").write_text("invalid json")
        convs, ids = _load_conversations(tmp_path)
        assert convs == []
        # natt IS in ids because the file existed (found_convos=True) even though parsing failed
        assert "natt" in ids

    def test_should_load_generic_conversations_json(self, tmp_path):
        plugin_dir = tmp_path / "natt" / "email_handler"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "conversations.json").write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2026-02-14T12:00:00",
                        "sender": "natt",
                        "motivation": "Email question",
                        "responder": "supervisor",
                        "response": "Email answer",
                    },
                ]
            )
        )
        convs, ids = _load_conversations(tmp_path)
        assert len(convs) == 1
        assert convs[0]["source"] == "email_handler"
        assert "natt" in ids

    def test_should_load_generic_conversations_dict_format(self, tmp_path):
        plugin_dir = tmp_path / "natt" / "email_handler"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "conversations.json").write_text(
            json.dumps(
                {
                    "conversations": [
                        {
                            "timestamp": "2026-02-14T12:00:00",
                            "sender": "natt",
                            "motivation": "Email question",
                            "responder": "supervisor",
                            "response": "Email answer",
                        },
                    ]
                }
            )
        )
        convs, _ids = _load_conversations(tmp_path)
        assert len(convs) == 1

    def test_should_skip_non_ipc_conversations_in_generic(self, tmp_path):
        plugin_dir = tmp_path / "natt" / "irc"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "conversations.json").write_text(
            json.dumps(
                [
                    {"timestamp": "2026-02-14T12:00:00", "messages": ["hi", "hello"]},
                ]
            )
        )
        convs, _ids = _load_conversations(tmp_path)
        assert len(convs) == 0

    def test_should_handle_generic_json_decode_error(self, tmp_path):
        plugin_dir = tmp_path / "natt" / "email_handler"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "conversations.json").write_text("bad json")
        convs, _ids = _load_conversations(tmp_path)
        assert convs == []

    def test_should_filter_generic_by_identity(self, tmp_path):
        for name in ("natt", "cherry"):
            plugin_dir = tmp_path / name / "email_handler"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "conversations.json").write_text(
                json.dumps(
                    [
                        {
                            "timestamp": "2026-02-14T12:00:00",
                            "sender": name,
                            "motivation": f"From {name}",
                            "responder": "supervisor",
                            "response": "OK",
                        },
                    ]
                )
            )
        convs, _ids = _load_conversations(tmp_path, identity_filter="natt")
        assert len(convs) == 1
        assert convs[0]["identity"] == "natt"

    def test_should_skip_conversations_json_in_known_source_dirs(self, tmp_path):
        """conversations.json in host_health should be skipped (already loaded as known source)."""
        hh_dir = tmp_path / "natt" / "host_health"
        hh_dir.mkdir(parents=True)
        # Known source file
        (hh_dir / "host_health_state.json").write_text(
            json.dumps(
                {
                    "conversations": [
                        {
                            "timestamp": "2026-02-14T12:00:00",
                            "sender": "natt",
                            "motivation": "From known source",
                            "responder": "anomal",
                            "response": "OK",
                            "health_grade": "good",
                        }
                    ]
                }
            )
        )
        # Also put a conversations.json in the same dir — should be skipped
        (hh_dir / "conversations.json").write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2026-02-14T13:00:00",
                        "sender": "natt",
                        "motivation": "From generic in known dir",
                        "responder": "anomal",
                        "response": "Duplicate",
                    },
                ]
            )
        )
        convs, _ids = _load_conversations(tmp_path)
        # Only the known-source conversation should be loaded
        assert len(convs) == 1
        assert convs[0]["motivation"] == "From known source"

    def test_should_filter_known_sources_by_identity(self, tmp_path):
        for name in ("natt", "cherry"):
            data_dir = tmp_path / name / "host_health"
            data_dir.mkdir(parents=True)
            (data_dir / "host_health_state.json").write_text(
                json.dumps(
                    {
                        "conversations": [
                            {
                                "timestamp": "2026-02-14T12:00:00",
                                "sender": name,
                                "motivation": f"Health from {name}",
                                "responder": "anomal",
                                "response": "Good",
                                "health_grade": "good",
                            }
                        ],
                    }
                )
            )
        convs, ids = _load_conversations(tmp_path, identity_filter="natt")
        assert len(convs) == 1
        assert convs[0]["identity"] == "natt"
        # Both identities should be in ids since both had conversations found
        assert "natt" in ids
        assert "cherry" in ids


class TestLoadAuditConversations:
    def test_should_return_empty_on_exception(self):
        audit_svc = MagicMock()
        audit_svc.query.side_effect = Exception("DB error")
        convs, ids = _load_audit_conversations(audit_svc)
        assert convs == []
        assert ids == set()

    def test_should_return_empty_on_no_entries(self):
        audit_svc = MagicMock()
        audit_svc.query.return_value = []
        convs, ids = _load_audit_conversations(audit_svc)
        assert convs == []
        assert ids == set()

    def test_should_skip_entries_with_non_dict_details(self):
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": "not a dict",
                "timestamp": time.time(),
            },
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        assert convs == []

    def test_should_skip_unmatched_requests(self):
        now = time.time()
        audit_svc = MagicMock()
        # Request with no matching response
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal", "email_from": "a@b.com", "email_subject": "Test"},
                "timestamp": now,
            },
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        assert convs == []

    def test_should_skip_request_outside_pair_window(self):
        now = time.time()
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal"},
                "timestamp": now - 200,
            },
            {
                "action": "email_consultation_response",
                "plugin": "email",
                "details": {"sender": "stal", "advised_action": "reply", "reasoning": "ok"},
                "timestamp": now,
            },
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        assert convs == []

    def test_should_filter_by_identity(self):
        now = time.time()
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal", "email_from": "a@b.com", "email_subject": "Test"},
                "timestamp": now - 5,
            },
            {
                "action": "email_consultation_response",
                "plugin": "email",
                "details": {"sender": "stal", "advised_action": "reply", "reasoning": "ok"},
                "timestamp": now - 3,
            },
        ]
        # Filter for a different identity
        convs, _ids = _load_audit_conversations(audit_svc, identity_filter="natt")
        assert convs == []

    def test_should_handle_none_details(self):
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": None,
                "timestamp": time.time(),
            },
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        assert convs == []

    def test_should_skip_entries_without_action_in_pair_map(self):
        now = time.time()
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "unknown_action",
                "plugin": "email",
                "details": {"sender": "stal"},
                "timestamp": now,
            },
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        assert convs == []

    def test_should_not_reuse_matched_responses(self):
        """When two requests match, only one should get the response."""
        now = time.time()
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal", "email_from": "a@b.com", "email_subject": "First"},
                "timestamp": now - 10,
            },
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal", "email_from": "a@b.com", "email_subject": "Second"},
                "timestamp": now - 5,
            },
            {
                "action": "email_consultation_response",
                "plugin": "email",
                "details": {"sender": "stal", "advised_action": "reply", "reasoning": "ok"},
                "timestamp": now - 3,
            },
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        # Only one should match (the closest in time)
        assert len(convs) == 1

    def test_should_skip_response_with_wrong_action_type(self):
        """When a response has the wrong action type, it should be skipped."""
        now = time.time()
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal", "email_from": "a@b.com", "email_subject": "Test"},
                "timestamp": now - 5,
            },
            # This response is for health, not email — wrong action type
            {
                "action": "health_response_sent",
                "plugin": "email",
                "details": {"sender": "stal", "response_preview": "healthy"},
                "timestamp": now - 3,
            },
            # This is the correct response
            {
                "action": "email_consultation_response",
                "plugin": "email",
                "details": {"sender": "stal", "advised_action": "reply", "reasoning": "correct"},
                "timestamp": now - 3,
            },
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        assert len(convs) == 1
        assert "correct" in convs[0]["response"]

    def test_should_handle_request_with_none_action(self):
        """Defensive: entries without action should be skipped in processing."""
        now = time.time()
        audit_svc = MagicMock()
        # Create an entry that matches _PAIR_MAP filter but also test the defensive check
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal"},
                "timestamp": now - 5,
            },
            # No matching response
        ]
        convs, _ids = _load_audit_conversations(audit_svc)
        assert convs == []

    def test_should_skip_entries_with_empty_action_in_received(self):
        """Defensive check: entries with empty action field after filtering."""

        now = time.time()
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {
                "action": "email_consultation_received",
                "plugin": "email",
                "details": {"sender": "stal"},
                "timestamp": now - 5,
            },
            {
                "action": "email_consultation_response",
                "plugin": "email",
                "details": {"sender": "stal", "advised_action": "reply", "reasoning": "ok"},
                "timestamp": now - 3,
            },
        ]
        # Patch _PAIR_MAP to include an empty string key to trigger the defensive check
        import overblick.dashboard.routes.conversations as conv_mod

        original_map = conv_mod._PAIR_MAP
        try:
            conv_mod._PAIR_MAP = {
                "": "email_consultation_response",
                "email_consultation_received": "email_consultation_response",
                "health_inquiry_received": "health_response_sent",
                "research_request_received": "research_response_sent",
            }
            # Add an entry with empty action that passes filter
            audit_svc.query.return_value = [
                {
                    "action": "",
                    "plugin": "email",
                    "details": {"sender": "stal"},
                    "timestamp": now - 5,
                },
                {
                    "action": "email_consultation_response",
                    "plugin": "email",
                    "details": {"sender": "stal", "advised_action": "reply", "reasoning": "ok"},
                    "timestamp": now - 3,
                },
            ]
            convs, _ids = _load_audit_conversations(audit_svc)
            # Empty action should be skipped by the defensive check
            assert convs == []
        finally:
            conv_mod._PAIR_MAP = original_map


class TestConversationsPageNoAuditService:
    @pytest.mark.asyncio
    async def test_should_render_without_audit_service(self, app, client, session_cookie, config):
        """Page renders even if audit_service is not available."""
        # Remove audit_service from app state
        delattr(app.state, "audit_service")
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/conversations",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        # Restore for other tests
        app.state.audit_service = MagicMock()

    @pytest.mark.asyncio
    async def test_should_handle_no_base_dir_config(self, app, client, session_cookie):
        """When config.base_dir is None, falls back to __file__ based path."""
        original = app.state.config.base_dir
        app.state.config.base_dir = None
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/conversations",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        app.state.config.base_dir = original
