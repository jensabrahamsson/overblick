"""
Tests for Pydantic validation models.
"""

import pytest
from pydantic import ValidationError

from overblick.setup.validators import (
    UseCaseSelection,
    CommunicationData,
    LLMData,
    PrincipalData,
)


class TestPrincipalData:
    """Tests for Step 2 validation."""

    def test_valid_principal(self):
        data = PrincipalData(
            principal_name="Jens Abrahamsson",
            principal_email="jens@example.com",
            timezone="Europe/Stockholm",
            language_preference="en",
        )
        assert data.principal_name == "Jens Abrahamsson"
        assert data.principal_email == "jens@example.com"

    def test_name_required(self):
        with pytest.raises(ValidationError, match="Principal name is required"):
            PrincipalData(principal_name="")

    def test_name_too_short(self):
        with pytest.raises(ValidationError, match="at least 2 characters"):
            PrincipalData(principal_name="J")

    def test_name_stripped(self):
        data = PrincipalData(principal_name="  Jens  ")
        assert data.principal_name == "Jens"

    def test_email_optional(self):
        data = PrincipalData(principal_name="Jens")
        assert data.principal_email == ""

    def test_invalid_email(self):
        with pytest.raises(ValidationError, match="Invalid email"):
            PrincipalData(principal_name="Jens", principal_email="not-an-email")

    def test_defaults(self):
        data = PrincipalData(principal_name="Jens")
        assert data.timezone == "Europe/Stockholm"
        assert data.language_preference == "en"


class TestLLMData:
    """Tests for Step 3 validation."""

    def test_valid_ollama(self):
        data = LLMData(llm_provider="ollama", model="qwen3:8b")
        assert data.llm_provider == "ollama"
        assert data.ollama_port == 11434

    def test_valid_gateway(self):
        data = LLMData(llm_provider="gateway")
        assert data.gateway_url == "http://127.0.0.1:8200"

    def test_valid_cloud(self):
        data = LLMData(
            llm_provider="cloud",
            cloud_api_url="https://api.openai.com/v1",
            cloud_model="gpt-4o",
        )
        assert data.llm_provider == "cloud"
        assert data.cloud_api_url == "https://api.openai.com/v1"
        assert data.cloud_model == "gpt-4o"

    def test_invalid_provider(self):
        with pytest.raises(ValidationError, match="ollama.*gateway.*cloud"):
            LLMData(llm_provider="something_else")

    def test_temperature_bounds(self):
        data = LLMData(default_temperature=0.0)
        assert data.default_temperature == 0.0

        data = LLMData(default_temperature=2.0)
        assert data.default_temperature == 2.0

        with pytest.raises(ValidationError, match="Temperature"):
            LLMData(default_temperature=2.5)

        with pytest.raises(ValidationError, match="Temperature"):
            LLMData(default_temperature=-0.1)

    def test_max_tokens_bounds(self):
        with pytest.raises(ValidationError, match="Max tokens"):
            LLMData(default_max_tokens=50)

        with pytest.raises(ValidationError, match="Max tokens"):
            LLMData(default_max_tokens=50000)


class TestCommunicationData:
    """Tests for Step 4 validation."""

    def test_all_disabled(self):
        data = CommunicationData()
        assert not data.gmail_enabled
        assert not data.telegram_enabled

    def test_gmail_enabled(self):
        data = CommunicationData(
            gmail_enabled=True,
            gmail_address="test@gmail.com",
            gmail_app_password="xxxx xxxx xxxx xxxx",
        )
        assert data.gmail_enabled
        assert data.gmail_address == "test@gmail.com"

    def test_invalid_gmail(self):
        with pytest.raises(ValidationError, match="Invalid Gmail"):
            CommunicationData(gmail_address="not-email")

    def test_telegram_enabled(self):
        data = CommunicationData(
            telegram_enabled=True,
            telegram_bot_token="123:ABC",
            telegram_chat_id="456",
        )
        assert data.telegram_enabled


class TestUseCaseSelection:
    """Tests for Step 5 validation."""

    def test_valid_selection(self):
        data = UseCaseSelection(selected_use_cases=["social_media", "email"])
        assert len(data.selected_use_cases) == 2

    def test_empty_selection(self):
        with pytest.raises(ValidationError, match="at least one"):
            UseCaseSelection(selected_use_cases=[])

    def test_single_selection(self):
        data = UseCaseSelection(selected_use_cases=["research"])
        assert data.selected_use_cases == ["research"]
