"""
Tests for Pydantic validation models.
"""

import pytest
from pydantic import ValidationError

from overblick.setup.validators import (
    BackendConfig,
    CommunicationData,
    DeepseekConfig,
    LLMData,
    OpenAIConfig,
    PrincipalData,
    UseCaseSelection,
)


class TestPrincipalData:
    """Tests for Step 2 validation."""

    def test_valid_principal(self):
        data = PrincipalData(
            principal_name="Alice Andersson",
            principal_email="alice@example.com",
            timezone="Europe/Stockholm",
            language_preference="en",
        )
        assert data.principal_name == "Alice Andersson"
        assert data.principal_email == "alice@example.com"

    def test_name_required(self):
        with pytest.raises(ValidationError, match="Principal name is required"):
            PrincipalData(principal_name="")

    def test_name_too_short(self):
        with pytest.raises(ValidationError, match="at least 2 characters"):
            PrincipalData(principal_name="J")

    def test_name_stripped(self):
        data = PrincipalData(principal_name="  Alice  ")
        assert data.principal_name == "Alice"

    def test_email_optional(self):
        data = PrincipalData(principal_name="Alice")
        assert data.principal_email == ""

    def test_invalid_email(self):
        with pytest.raises(ValidationError, match="Invalid email"):
            PrincipalData(principal_name="Alice", principal_email="not-an-email")

    def test_defaults(self):
        data = PrincipalData(principal_name="Alice")
        assert data.timezone == "Europe/Stockholm"
        assert data.language_preference == "en"


class TestBackendConfig:
    """Tests for individual backend configuration."""

    def test_default_backend(self):
        bc = BackendConfig()
        assert not bc.enabled
        assert bc.backend_type == "ollama"
        assert bc.host == "127.0.0.1"
        assert bc.port == 11434
        assert bc.model == "qwen3:8b"

    def test_enabled_backend(self):
        bc = BackendConfig(enabled=True, backend_type="lmstudio", port=1234)
        assert bc.enabled
        assert bc.backend_type == "lmstudio"
        assert bc.port == 1234

    def test_invalid_type(self):
        with pytest.raises(ValidationError, match="ollama"):
            BackendConfig(backend_type="invalid")


class TestLLMData:
    """Tests for Step 3 validation (new backends structure)."""

    def test_defaults(self):
        data = LLMData()
        assert data.gateway_url == "http://127.0.0.1:8200"
        assert data.local.enabled is True
        assert data.cloud.enabled is False
        assert data.openai.enabled is False
        assert data.default_backend == "local"
        assert data.default_temperature == 0.7
        assert data.default_max_tokens == 2000

    def test_local_ollama(self):
        data = LLMData(
            local=BackendConfig(enabled=True, backend_type="ollama", model="qwen3:8b"),
        )
        assert data.local.enabled
        assert data.local.backend_type == "ollama"
        assert data.local.port == 11434

    def test_local_lmstudio(self):
        data = LLMData(
            local=BackendConfig(enabled=True, backend_type="lmstudio", port=1234),
        )
        assert data.local.backend_type == "lmstudio"
        assert data.local.port == 1234

    def test_cloud_backend(self):
        data = LLMData(
            cloud=BackendConfig(enabled=True, host="gpu.example.com", model="qwen3:14b"),
        )
        assert data.cloud.enabled
        assert data.cloud.host == "gpu.example.com"
        assert data.cloud.model == "qwen3:14b"

    def test_openai_backend(self):
        data = LLMData(
            openai=OpenAIConfig(enabled=True, model="gpt-4o"),
            default_backend="openai",
        )
        assert data.openai.enabled
        assert data.openai.model == "gpt-4o"
        assert data.default_backend == "openai"

    def test_deepseek_backend(self):
        data = LLMData(
            deepseek=DeepseekConfig(enabled=True, model="deepseek-chat"),
            default_backend="deepseek",
        )
        assert data.deepseek.enabled
        assert data.deepseek.model == "deepseek-chat"
        assert data.deepseek.api_url == "https://api.deepseek.com/v1"
        assert data.default_backend == "deepseek"

    def test_deepseek_defaults(self):
        data = LLMData()
        assert data.deepseek.enabled is False
        assert data.deepseek.model == "deepseek-chat"

    def test_invalid_default_backend(self):
        with pytest.raises(ValidationError, match="local.*cloud.*deepseek.*openai"):
            LLMData(default_backend="something_else")

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

    def test_gateway_url_custom(self):
        data = LLMData(gateway_url="http://10.0.0.5:8200")
        assert data.gateway_url == "http://10.0.0.5:8200"

    def test_full_config(self):
        """Test a fully configured multi-backend setup."""
        data = LLMData(
            gateway_url="http://127.0.0.1:8200",
            local=BackendConfig(enabled=True, model="qwen3:8b"),
            cloud=BackendConfig(enabled=True, host="gpu.lan", port=11434, model="qwen3:14b"),
            deepseek=DeepseekConfig(enabled=True, model="deepseek-chat"),
            openai=OpenAIConfig(enabled=False),
            default_backend="local",
            default_temperature=0.8,
            default_max_tokens=4000,
        )
        assert data.local.enabled
        assert data.cloud.enabled
        assert data.deepseek.enabled
        assert not data.openai.enabled
        assert data.default_temperature == 0.8
        assert data.default_max_tokens == 4000


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
