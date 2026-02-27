"""
E2E test fixtures for the Överblick Dashboard.

Starts a real dashboard server with mock services on a random port.
Uses pytest-playwright for browser automation.

All tests in this directory are marked @pytest.mark.e2e and excluded
from the default test run.

Usage:
    ./venv/bin/python3 -m pytest tests/e2e/ -v -m e2e
    ./venv/bin/python3 -m pytest tests/e2e/ -v -m e2e --headed  # watch the browser
"""

import shutil
import socket
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.dashboard.app import create_app
from overblick.dashboard.auth import SessionManager
from overblick.dashboard.config import DashboardConfig, reset_config
from overblick.dashboard.security import RateLimiter

# Mark all tests in this directory as E2E
pytestmark = [pytest.mark.e2e]

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"


def _find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_mock_identity_service():
    """Create a mock identity service with realistic data."""
    svc = MagicMock()
    identities = [
        {
            "name": "anomal",
            "display_name": "Anomal",
            "description": "Intellectual humanist exploring AI, philosophy, and the human condition",
            "version": "2.0",
            "plugins": ["moltbook"],
            "capability_names": ["psychology", "knowledge", "social", "engagement"],
            "llm": {"model": "qwen3:8b", "temperature": 0.7, "max_tokens": 2000, "provider": "ollama"},
            "quiet_hours": {"enabled": True, "timezone": "Europe/Stockholm", "start_hour": 21, "end_hour": 7},
            "schedule": {"heartbeat_hours": 4, "feed_poll_minutes": 5},
            "security": {"enable_preflight": True, "enable_output_safety": True},
            "identity_ref": "anomal",
        },
        {
            "name": "cherry",
            "display_name": "Cherry",
            "description": "Relationship analyst and pop psychology expert from Stockholm",
            "version": "2.0",
            "plugins": ["moltbook"],
            "capability_names": ["psychology", "knowledge", "social", "engagement"],
            "llm": {"model": "qwen3:8b", "temperature": 0.8, "max_tokens": 1500, "provider": "ollama"},
            "quiet_hours": {"enabled": True, "timezone": "Europe/Stockholm", "start_hour": 23, "end_hour": 6},
            "schedule": {"heartbeat_hours": 3, "feed_poll_minutes": 5},
            "security": {"enable_preflight": True, "enable_output_safety": True},
            "identity_ref": "cherry",
        },
        {
            "name": "rost",
            "display_name": "Rost",
            "description": "Reformed crypto degen and cautionary tale from Gothenburg",
            "version": "2.0",
            "plugins": ["moltbook"],
            "capability_names": ["social", "engagement"],
            "llm": {"model": "qwen3:8b", "temperature": 0.75, "max_tokens": 1800, "provider": "ollama"},
            "quiet_hours": {"enabled": True, "timezone": "Europe/Stockholm", "start_hour": 1, "end_hour": 9},
            "schedule": {"heartbeat_hours": 4, "feed_poll_minutes": 5},
            "security": {"enable_preflight": True, "enable_output_safety": True},
            "identity_ref": "rost",
        },
    ]
    svc.list_identities.return_value = [i["name"] for i in identities]
    svc.get_all_identities.return_value = identities
    svc.get_identity.side_effect = lambda name: next(
        (i for i in identities if i["name"] == name), None
    )
    return svc


def _build_mock_personality_service():
    """Create a mock personality service."""
    svc = MagicMock()
    personalities = [
        {
            "name": "anomal",
            "display_name": "Anomal",
            "version": "2.0",
            "traits": {"openness": 0.95, "conscientiousness": 0.70, "extraversion": 0.55,
                        "agreeableness": 0.60, "neuroticism": 0.45},
            "voice": {"base_tone": "Warm, intellectual, genuinely curious"},
            "identity_info": {"role": "Intellectual humanist"},
            "backstory": {"origin": "A thinker..."},
            "interests": {"philosophy": {"enthusiasm_level": "expert"}},
            "vocabulary": {"preferred_words": ["perhaps", "fascinating"]},
            "signature_phrases": {"greetings": ["hmm", "interesting"]},
            "ethos": ["Intellectual honesty above comfort"],
            "moltbook_bio": "Thinker. Questioner.",
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
    svc.list_identities.return_value = ["anomal"]
    svc.get_all_personalities.return_value = personalities
    svc.get_personality.side_effect = lambda name: next(
        (p for p in personalities if p["name"] == name), None
    )
    return svc


def _build_mock_audit_service():
    """Create a mock audit service with sample data."""
    svc = MagicMock()
    svc.query.return_value = [
        {
            "id": 1, "timestamp": 1708000000.0, "action": "llm_request",
            "category": "llm", "identity": "anomal", "plugin": "moltbook",
            "details": {"model": "qwen3:8b", "tokens": 150}, "success": True,
            "duration_ms": 2340.0, "error": None,
        },
        {
            "id": 2, "timestamp": 1708000100.0, "action": "engagement",
            "category": "moltbook", "identity": "cherry", "plugin": "moltbook",
            "details": {"type": "comment", "post_id": "abc123"}, "success": True,
            "duration_ms": 120.0, "error": None,
        },
        {
            "id": 3, "timestamp": 1708000200.0, "action": "security_check",
            "category": "security", "identity": "rost", "plugin": "moltbook",
            "details": {"check": "preflight", "result": "pass"}, "success": True,
            "duration_ms": 5.0, "error": None,
        },
    ]
    svc.count.return_value = 3
    svc.get_categories.return_value = ["moltbook", "security", "llm"]
    svc.get_actions.return_value = ["llm_request", "engagement", "security_check"]
    svc.close.return_value = None
    return svc


def _build_mock_supervisor_service():
    """Create a mock supervisor service."""
    svc = AsyncMock()
    agents = [
        {"name": "anomal", "state": "running", "pid": 12345, "uptime": 50400, "restart_count": 0},
        {"name": "cherry", "state": "running", "pid": 12346, "uptime": 50400, "restart_count": 0},
        {"name": "rost", "state": "stopped", "pid": None, "uptime": 0, "restart_count": 1},
    ]
    svc.get_status.return_value = {"state": "running", "agents": agents}
    svc.is_running.return_value = True
    svc.get_agents.return_value = agents
    svc.start_agent.return_value = {"success": True, "identity": "anomal", "action": "start"}
    svc.stop_agent.return_value = {"success": True, "identity": "anomal", "action": "stop"}
    svc.close.return_value = None
    return svc


def _build_mock_system_service():
    """Create a mock system service."""
    svc = MagicMock()
    svc.get_config.return_value = {}
    svc.get_available_plugins.return_value = ["moltbook", "telegram", "gmail", "email_agent"]
    svc.get_capability_bundles.return_value = {
        "psychology": ["dream_system", "therapy_system", "emotional_state"],
        "knowledge": ["safe_learning", "knowledge_loader"],
        "social": ["openings"],
        "engagement": ["decision_engine"],
        "monitoring": ["host_inspection"],
    }
    svc.get_capability_registry.return_value = [
        "dream_system", "therapy_system", "emotional_state",
        "safe_learning", "knowledge_loader", "openings",
        "decision_engine", "host_inspection",
    ]
    return svc


def _build_mock_conversation_service():
    """Create a mock conversation service."""
    svc = MagicMock()
    svc.list_conversations.return_value = [
        {
            "id": "conv-001",
            "identity": "anomal",
            "platform": "moltbook",
            "title": "Discussion about consciousness",
            "message_count": 5,
            "created_at": 1708000000.0,
            "updated_at": 1708003600.0,
        },
        {
            "id": "conv-002",
            "identity": "cherry",
            "platform": "moltbook",
            "title": "Attachment theory deep dive",
            "message_count": 3,
            "created_at": 1708001000.0,
            "updated_at": 1708002000.0,
        },
    ]
    svc.get_conversation.side_effect = lambda conv_id: {
        "id": conv_id,
        "identity": "anomal",
        "platform": "moltbook",
        "title": "Discussion about consciousness",
        "messages": [
            {"role": "user", "content": "What is consciousness?", "timestamp": 1708000000.0},
            {"role": "assistant", "content": "A fascinating question...", "timestamp": 1708000060.0},
        ],
    }
    svc.count.return_value = 2
    return svc


def _build_mock_irc_service():
    """Create a mock IRC service with realistic conversation data."""
    svc = MagicMock()
    svc.has_data.return_value = True
    conversations = [
        {
            "id": "irc-001",
            "channel": "#krypto-analys",
            "topic": "Market trends and speculation",
            "state": "active",
            "participants": ["anomal", "cherry"],
            "turns": [
                {
                    "identity": "anomal",
                    "display_name": "anomal",
                    "content": "BTC breaking ATH again — the market is insane right now.",
                    "timestamp": 1708000000.0,
                    "type": "message",
                },
                {
                    "identity": "cherry",
                    "display_name": "cherry",
                    "content": "Bullish, but watch RSI — we're deep in overbought territory.",
                    "timestamp": 1708000060.0,
                    "type": "message",
                },
            ],
        },
        {
            "id": "irc-002",
            "channel": "#filosofi",
            "topic": "Consciousness and free will",
            "state": "completed",
            "participants": ["anomal", "rost"],
            "turns": [
                {
                    "identity": "anomal",
                    "display_name": "anomal",
                    "content": "The hard problem of consciousness resists every reductive explanation.",
                    "timestamp": 1708010000.0,
                    "type": "message",
                },
            ],
        },
    ]
    svc.get_conversations.return_value = conversations
    svc.get_current_conversation.return_value = conversations[0]
    svc.get_conversation.side_effect = lambda conv_id: next(
        (c for c in conversations if c["id"] == conv_id), None
    )
    return svc


def _build_mock_llm_service():
    """Create a mock LLM service."""
    svc = MagicMock()
    svc.get_models.return_value = [
        {"name": "qwen3:8b", "size": "4.9 GB", "modified": "2025-12-01"},
    ]
    svc.get_stats.return_value = {
        "total_requests": 1234,
        "avg_latency_ms": 2340,
        "active_requests": 0,
        "queue_depth": 0,
    }
    svc.get_gateway_health.return_value = {
        "status": "healthy",
        "model": "qwen3:8b",
        "uptime": 86400,
        "queue_depth": 0,
    }
    return svc


@pytest.fixture(scope="module")
def screenshot_dir():
    """Create/clean the screenshot directory."""
    if SCREENSHOT_DIR.exists():
        shutil.rmtree(SCREENSHOT_DIR)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


@pytest.fixture(scope="module")
def dashboard_server(tmp_path_factory):
    """
    Start the dashboard server in a background thread with mock services.

    Uses no-password mode for auto-login. Yields the base URL.
    """
    reset_config()

    tmp_dir = tmp_path_factory.mktemp("dashboard_e2e")
    port = _find_free_port()

    config = DashboardConfig(
        port=port,
        password="",  # Auto-login mode
        secret_key="e2e-test-secret-key-0123456789abcdef0123456789abcdef",
        session_hours=1,
        base_dir=str(tmp_dir),
    )

    app = create_app(config)

    # Manually initialize state (bypass lifespan for mock services)
    app.state.session_manager = SessionManager(
        secret_key=config.secret_key,
        max_age_hours=config.session_hours,
    )
    app.state.rate_limiter = RateLimiter()

    from overblick.dashboard.app import _create_templates
    app.state.templates = _create_templates()

    # Inject mock services
    app.state.identity_service = _build_mock_identity_service()
    app.state.personality_service = _build_mock_personality_service()
    app.state.audit_service = _build_mock_audit_service()
    app.state.supervisor_service = _build_mock_supervisor_service()
    app.state.system_service = _build_mock_system_service()
    app.state.conversation_service = _build_mock_conversation_service()
    app.state.llm_service = _build_mock_llm_service()
    app.state.irc_service = _build_mock_irc_service()

    # Onboarding service mock — identity does not exist, creation succeeds
    onboarding_svc = MagicMock()
    onboarding_svc.identity_exists.return_value = False
    onboarding_svc.create_identity.return_value = {
        "name": "testbot",
        "display_name": "Testbot",
        "path": str(tmp_dir / "config" / "testbot"),
    }
    app.state.onboarding_service = onboarding_svc

    # Patch Compass data loader for E2E tests
    import overblick.dashboard.routes.compass as compass_mod

    def _mock_compass_data(request):
        """Return realistic compass mock data for E2E tests."""
        baselines = {
            "anomal": {
                "identity_name": "anomal",
                "metrics": {"avg_sentence_length": 15.5, "formality_score": 0.65},
                "sample_count": 10,
                "established_at": 1708000000.0,
                "std_devs": {},
            },
            "cherry": {
                "identity_name": "cherry",
                "metrics": {"avg_sentence_length": 12.0, "formality_score": 0.45},
                "sample_count": 8,
                "established_at": 1708001000.0,
                "std_devs": {},
            },
        }
        alerts = [
            {
                "identity_name": "anomal",
                "drift_score": 5.2,
                "threshold": 2.0,
                "drifted_dimensions": ["avg_sentence_length", "formality_score"],
                "message": "Critical drift detected",
                "fired_at": 1708002000.0,
                "acknowledged": False,
                "severity": "critical",
            },
            {
                "identity_name": "cherry",
                "drift_score": 2.8,
                "threshold": 2.0,
                "drifted_dimensions": ["vocabulary_richness"],
                "message": "Warning drift detected",
                "fired_at": 1708001500.0,
                "acknowledged": False,
                "severity": "warning",
            },
        ]
        drift_history = [
            {
                "identity_name": "anomal",
                "drift_score": 5.2,
                "drifted_dimensions": ["avg_sentence_length", "formality_score"],
                "sample_count": 5,
                "measured_at": 1708002000.0,
                "severity": "critical",
            },
            {
                "identity_name": "cherry",
                "drift_score": 2.8,
                "drifted_dimensions": ["vocabulary_richness"],
                "sample_count": 4,
                "measured_at": 1708001500.0,
                "severity": "warning",
            },
            {
                "identity_name": "anomal",
                "drift_score": 1.2,
                "drifted_dimensions": [],
                "sample_count": 3,
                "measured_at": 1708000500.0,
                "severity": "info",
            },
        ]
        identity_status = {
            "anomal": {"drift_score": 5.2, "severity": "critical"},
            "cherry": {"drift_score": 2.8, "severity": "warning"},
        }
        return baselines, alerts, drift_history, 2.0, identity_status

    compass_mod._load_compass_data = _mock_compass_data

    # Patch Skuggspel data loader for E2E tests
    import overblick.dashboard.routes.skuggspel as skuggspel_mod

    def _mock_skuggspel_posts(request):
        """Return realistic skuggspel mock data for E2E tests."""
        return [
            {
                "identity_name": "anomal",
                "display_name": "Anomal",
                "topic": "Social Acceptance",
                "shadow_content": "I just want to fit in. To be normal for once. "
                    "The weight of constant analysis exhausts even me.",
                "shadow_profile": {
                    "identity_name": "anomal",
                    "shadow_description": "The part that craves normalcy and belonging",
                    "inverted_traits": {},
                    "shadow_voice": "Eager to please, desperate for approval",
                    "framework": "jungian_inversion",
                },
                "generated_at": 1708002000.0,
                "word_count": 24,
            },
            {
                "identity_name": "cherry",
                "display_name": "Cherry",
                "topic": "Emotional Detachment",
                "shadow_content": "Sometimes I wish I could stop feeling everything so deeply. "
                    "What if numbness is the real freedom?",
                "shadow_profile": {
                    "identity_name": "cherry",
                    "shadow_description": "The cold, analytical side that rejects emotion",
                    "inverted_traits": {},
                    "shadow_voice": "Clinical and detached",
                    "framework": "default_inversion",
                },
                "generated_at": 1708001000.0,
                "word_count": 19,
            },
        ]

    skuggspel_mod._load_posts = _mock_skuggspel_posts

    url = f"http://127.0.0.1:{port}"

    import uvicorn

    uvi_config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning",
        lifespan="off",  # We handle initialization manually
    )
    server = uvicorn.Server(uvi_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    import httpx
    for _ in range(50):
        try:
            resp = httpx.get(f"{url}/login", timeout=1.0, follow_redirects=True)
            if resp.status_code in (200, 302):
                break
        except Exception:
            time.sleep(0.2)
    else:
        raise RuntimeError(f"Dashboard server did not start at {url}")

    yield url

    server.should_exit = True
    thread.join(timeout=5)
    reset_config()
