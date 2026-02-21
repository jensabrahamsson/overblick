"""
Fixtures for GitHub plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.plugin_base import PluginContext
from overblick.core.llm.pipeline import PipelineResult
from overblick.identities import (
    Identity,
    LLMSettings,
    QuietHoursSettings,
    ScheduleSettings,
    SecuritySettings,
)
from overblick.plugins.github.models import (
    EventType,
    GitHubEvent,
)


@pytest.fixture
def github_identity():
    """Identity configured for GitHub plugin tests."""
    return Identity(
        name="anomal",
        display_name="Anomal",
        description="Security researcher and code analyst",
        engagement_threshold=25,
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=22, end_hour=6),
        schedule=ScheduleSettings(heartbeat_hours=1, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["security", "api", "authentication", "crypto"],
        raw_config={
            "github": {
                "repos": ["moltbook/api"],
                "bot_username": "anomal-bot",
                "default_branch": "main",
                "respond_threshold": 50,
                "notify_threshold": 25,
                "max_responses_per_tick": 2,
                "max_issue_age_hours": 168,
                "tree_refresh_minutes": 60,
                "dry_run": False,
                "triggers": {
                    "respond_to_mentions": True,
                    "respond_to_labels": ["question", "help wanted"],
                },
                "code_context": {
                    "max_files_per_question": 8,
                    "max_file_size_bytes": 50000,
                    "include_patterns": ["*.py", "*.yaml", "*.md"],
                    "exclude_patterns": ["*.lock", "__pycache__/*"],
                },
            },
        },
    )


@pytest.fixture
def mock_github_client():
    """Mock GitHub API client."""
    client = AsyncMock()
    client.rate_limit_remaining = 4500

    # Default: return empty lists
    client.list_issues = AsyncMock(return_value=[])
    client.list_issue_comments = AsyncMock(return_value=[])
    client.create_comment = AsyncMock(return_value={"id": 12345})
    client.get_file_tree = AsyncMock(return_value={
        "sha": "abc123",
        "tree": [
            {"path": "src/main.py", "type": "blob", "sha": "sha1", "size": 500},
            {"path": "src/utils.py", "type": "blob", "sha": "sha2", "size": 300},
            {"path": "README.md", "type": "blob", "sha": "sha3", "size": 200},
            {"path": "tests/test_main.py", "type": "blob", "sha": "sha4", "size": 400},
        ],
    })
    client.get_file_content = AsyncMock(return_value={
        "content": "ZGVmIG1haW4oKToKICAgIHBhc3MK",  # base64: def main():\n    pass\n
        "sha": "content_sha",
    })
    client.get_rate_limit = AsyncMock(return_value={
        "resources": {"core": {"remaining": 4500, "reset": 1700000000}},
    })
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_llm_pipeline_github():
    """Mock LLM pipeline for GitHub plugin tests."""
    pipeline = AsyncMock()

    # Default: return a file selection response
    pipeline.chat = AsyncMock(return_value=PipelineResult(
        content='["src/main.py", "src/utils.py"]',
    ))
    return pipeline


@pytest.fixture
def mock_telegram_notifier_github():
    """Mock Telegram notifier for GitHub plugin tests."""
    notifier = AsyncMock()
    notifier.send_notification = AsyncMock(return_value=True)
    notifier.configured = True
    return notifier


@pytest.fixture
def github_plugin_context(
    github_identity, tmp_path, mock_audit_log,
    mock_llm_pipeline_github, mock_telegram_notifier_github,
):
    """PluginContext configured for GitHub plugin tests."""
    def _mock_secrets(key: str):
        secrets = {"github_token": "ghp_test_token_123"}
        return secrets.get(key)

    ctx = PluginContext(
        identity_name="anomal",
        data_dir=tmp_path / "data" / "anomal",
        log_dir=tmp_path / "logs" / "anomal",
        llm_pipeline=mock_llm_pipeline_github,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=github_identity,
        capabilities={
            "telegram_notifier": mock_telegram_notifier_github,
        },
    )
    ctx._secrets_getter = _mock_secrets
    return ctx


@pytest.fixture
def sample_issue():
    """Sample GitHub issue API response."""
    return {
        "number": 134,
        "title": "API returns 500 on /users endpoint",
        "body": "When calling GET /api/v1/users with a valid token, the server returns 500.",
        "state": "open",
        "user": {"login": "reporter-user"},
        "labels": [{"name": "bug"}, {"name": "help wanted"}],
        "created_at": "2026-02-10T10:00:00Z",
        "updated_at": "2026-02-20T15:00:00Z",
    }


@pytest.fixture
def sample_issue_with_mention():
    """Sample GitHub issue with @mention of the bot."""
    return {
        "number": 135,
        "title": "Question about authentication flow",
        "body": "Hey @anomal-bot, can you explain how the auth middleware works?",
        "state": "open",
        "user": {"login": "curious-dev"},
        "labels": [{"name": "question"}],
        "created_at": "2026-02-20T12:00:00Z",
        "updated_at": "2026-02-20T12:00:00Z",
    }


@pytest.fixture
def sample_event():
    """Sample GitHubEvent for testing."""
    return GitHubEvent(
        event_id="moltbook/api/issues/134",
        event_type=EventType.ISSUE_OPENED,
        repo="moltbook/api",
        issue_number=134,
        issue_title="API returns 500 on /users endpoint",
        body="When calling GET /api/v1/users, the server returns 500.",
        author="reporter-user",
        labels=["bug", "help wanted"],
        created_at="2026-02-10T10:00:00Z",
    )


@pytest.fixture
def sample_mention_event():
    """Sample GitHubEvent with @mention."""
    return GitHubEvent(
        event_id="moltbook/api/issues/135",
        event_type=EventType.ISSUE_OPENED,
        repo="moltbook/api",
        issue_number=135,
        issue_title="Question about authentication flow",
        body="Hey @anomal-bot, can you explain how the auth middleware works?",
        author="curious-dev",
        labels=["question"],
        created_at="2026-02-20T12:00:00Z",
    )
