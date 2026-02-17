"""
Dashboard test fixtures — mock services, test client.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from overblick.dashboard.config import DashboardConfig, reset_config
from overblick.dashboard.app import create_app
from overblick.dashboard.auth import SessionManager, SESSION_COOKIE
from overblick.dashboard.security import RateLimiter


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config singleton between tests."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
def config(tmp_path):
    """Test dashboard config."""
    return DashboardConfig(
        port=8080,
        password="testpass123",
        secret_key="test-secret-key-for-testing-only-0123456789abcdef",
        session_hours=1,
        base_dir=str(tmp_path),
    )


@pytest.fixture
def config_no_password(tmp_path):
    """Config with no password (auto-login)."""
    return DashboardConfig(
        port=8080,
        password="",
        secret_key="test-secret-key-for-testing-only-0123456789abcdef",
        session_hours=1,
        base_dir=str(tmp_path),
    )


@pytest.fixture
def mock_identity_service():
    """Mock identity service."""
    svc = MagicMock()
    svc.list_identities.return_value = ["anomal", "cherry"]
    svc.get_all_identities.return_value = [
        {
            "name": "anomal",
            "display_name": "Anomal",
            "description": "Intellectual humanist exploring AI",
            "version": "1.0.0",
            "plugins": ["moltbook"],
            "capability_names": ["psychology", "knowledge"],
            "llm": {"model": "qwen3:8b", "temperature": 0.7, "max_tokens": 2000, "provider": "ollama"},
            "quiet_hours": {"enabled": True, "timezone": "Europe/Stockholm", "start_hour": 21, "end_hour": 7},
            "schedule": {"heartbeat_hours": 4, "feed_poll_minutes": 5},
            "security": {"enable_preflight": True, "enable_output_safety": True},
            "identity_ref": "anomal",
        },
        {
            "name": "cherry",
            "display_name": "Cherry",
            "description": "Creative artist and poet",
            "version": "1.0.0",
            "plugins": ["telegram"],
            "capability_names": ["social", "engagement"],
            "llm": {"model": "qwen3:8b", "temperature": 0.9, "max_tokens": 2000, "provider": "ollama"},
            "quiet_hours": {"enabled": False, "timezone": "UTC", "start_hour": 0, "end_hour": 0},
            "schedule": {"heartbeat_hours": 2, "feed_poll_minutes": 3},
            "security": {"enable_preflight": True, "enable_output_safety": True},
            "identity_ref": "cherry",
        },
    ]
    svc.get_identity.side_effect = lambda name: next(
        (i for i in svc.get_all_identities() if i["name"] == name), None
    )
    return svc


@pytest.fixture
def mock_personality_service():
    """Mock personality service."""
    svc = MagicMock()
    svc.list_identities.return_value = ["anomal", "cherry"]
    svc.get_all_personalities.return_value = [
        {
            "name": "anomal",
            "display_name": "Anomal",
            "version": "1.0",
            "traits": {"openness": 0.95, "conscientiousness": 0.7, "agreeableness": 0.6},
            "voice": {"base_tone": "warm intellectual"},
            "identity_info": {},
            "backstory": {},
            "interests": {},
            "vocabulary": {},
            "signature_phrases": {},
            "ethos": {},
            "moltbook_bio": "",
            "raw": {
                "psychological_framework": {
                    "primary": "jungian",
                    "domains": ["archetypes", "shadow_work"],
                    "dream_interpretation": True,
                    "self_reflection_style": "archetypal_analysis",
                    "therapeutic_approach": "depth_psychology",
                    "key_concepts": ["The shadow is not evil."],
                },
            },
        },
    ]
    svc.get_personality.side_effect = lambda name: next(
        (p for p in svc.get_all_personalities() if p["name"] == name), None
    )
    return svc


@pytest.fixture
def mock_audit_service():
    """Mock audit service."""
    svc = MagicMock()
    svc.query.return_value = [
        {
            "id": 1, "timestamp": 1700000000.0, "action": "api_call",
            "category": "moltbook", "identity": "anomal", "plugin": "moltbook",
            "details": {}, "success": True, "duration_ms": 120.0, "error": None,
        },
    ]
    svc.count.return_value = 42
    svc.get_categories.return_value = ["moltbook", "security", "llm"]
    svc.get_actions.return_value = ["api_call", "llm_request", "engagement"]
    svc.close.return_value = None
    return svc


@pytest.fixture
def mock_supervisor_service():
    """Mock supervisor service."""
    svc = AsyncMock()
    svc.get_status.return_value = {
        "state": "running",
        "agents": [
            {"name": "anomal", "state": "running", "pid": 12345, "uptime": 3600, "restart_count": 0},
        ],
    }
    svc.is_running.return_value = True
    svc.get_agents.return_value = [
        {"name": "anomal", "state": "running", "pid": 12345, "uptime": 3600, "restart_count": 0},
    ]
    svc.start_agent.return_value = {"success": True, "identity": "anomal", "action": "start"}
    svc.stop_agent.return_value = {"success": True, "identity": "anomal", "action": "stop"}
    svc.close.return_value = None
    return svc


@pytest.fixture
def mock_system_service():
    """Mock system service."""
    svc = MagicMock()
    svc.get_config.return_value = {}
    svc.get_available_plugins.return_value = ["moltbook", "telegram", "gmail"]
    svc.get_capability_bundles.return_value = {
        "psychology": ["dream_system", "therapy_system", "emotional_state"],
        "knowledge": ["safe_learning", "knowledge_loader"],
        "social": ["openings"],
    }
    svc.get_capability_registry.return_value = [
        "dream_system", "therapy_system", "emotional_state",
        "safe_learning", "knowledge_loader", "openings",
    ]
    svc.get_moltbook_statuses.return_value = []
    return svc


@pytest.fixture
def mock_onboarding_service():
    """Mock onboarding service."""
    svc = MagicMock()
    svc.identity_exists.return_value = False
    svc.create_identity.return_value = {
        "name": "testbot",
        "created_files": ["overblick/identities/testbot/identity.yaml"],
        "identity_dir": "/tmp/test/overblick/identities/testbot",
        "data_dir": "/tmp/test/data/testbot",
    }
    return svc


@pytest.fixture
def app(
    config,
    mock_identity_service,
    mock_personality_service,
    mock_audit_service,
    mock_supervisor_service,
    mock_system_service,
    mock_onboarding_service,
):
    """Create test app with mock services (bypasses lifespan)."""
    test_app = create_app(config)

    # Manually initialize state that lifespan would set
    test_app.state.session_manager = SessionManager(
        secret_key=config.secret_key,
        max_age_hours=config.session_hours,
    )
    test_app.state.rate_limiter = RateLimiter()
    from overblick.dashboard.app import _create_templates
    test_app.state.templates = _create_templates()

    # Inject mock services
    test_app.state.identity_service = mock_identity_service
    test_app.state.personality_service = mock_personality_service
    test_app.state.audit_service = mock_audit_service
    test_app.state.supervisor_service = mock_supervisor_service
    test_app.state.system_service = mock_system_service
    test_app.state.onboarding_service = mock_onboarding_service

    return test_app


@pytest.fixture
def client(app):
    """Create httpx test client."""
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def session_cookie(config):
    """Create a valid session cookie for authenticated requests."""
    sm = SessionManager(config.secret_key, max_age_hours=config.session_hours)
    cookie_value, csrf_token = sm.create_session()
    return cookie_value, csrf_token
