"""
Pydantic validation models for each wizard step.

Each model validates one step's form data and provides
clean, typed access to the user's input.
"""

from pydantic import BaseModel, field_validator


class PrincipalData(BaseModel):
    """Step 2: Principal identity."""
    principal_name: str
    principal_email: str = ""
    timezone: str = "Europe/Stockholm"
    language_preference: str = "en"

    @field_validator("principal_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Principal name is required")
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v

    @field_validator("principal_email")
    @classmethod
    def email_format(cls, v: str) -> str:
        v = v.strip()
        if v and "@" not in v:
            raise ValueError("Invalid email address")
        return v


class BackendConfig(BaseModel):
    """Configuration for a single LLM backend (local or cloud inference)."""
    enabled: bool = False
    backend_type: str = "ollama"  # "ollama" | "lmstudio"
    host: str = "127.0.0.1"
    port: int = 11434
    model: str = "qwen3:8b"

    @field_validator("backend_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in ("ollama", "lmstudio"):
            raise ValueError("Backend type must be 'ollama' or 'lmstudio'")
        return v


class DeepseekConfig(BaseModel):
    """Configuration for Deepseek API backend."""
    enabled: bool = False
    api_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"


class OpenAIConfig(BaseModel):
    """Configuration for OpenAI backend (coming soon)."""
    enabled: bool = False
    api_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"


class LLMData(BaseModel):
    """Step 3: LLM configuration with gateway-as-router architecture.

    Gateway is always-on infrastructure. Backends (local, cloud, deepseek, openai)
    are the actual inference targets that the gateway routes to.
    """
    gateway_url: str = "http://127.0.0.1:8200"
    local: BackendConfig = BackendConfig(enabled=True)
    cloud: BackendConfig = BackendConfig(enabled=False, host="", port=11434)
    deepseek: DeepseekConfig = DeepseekConfig(enabled=False)
    openai: OpenAIConfig = OpenAIConfig(enabled=False)
    default_backend: str = "local"
    default_temperature: float = 0.7
    default_max_tokens: int = 2000

    @field_validator("default_backend")
    @classmethod
    def valid_default(cls, v: str) -> str:
        if v not in ("local", "cloud", "deepseek", "openai"):
            raise ValueError("Default backend must be 'local', 'cloud', 'deepseek', or 'openai'")
        return v

    @field_validator("default_temperature")
    @classmethod
    def valid_temperature(cls, v: float) -> float:
        if not (0.0 <= v <= 2.0):
            raise ValueError("Temperature must be between 0.0 and 2.0")
        return v

    @field_validator("default_max_tokens")
    @classmethod
    def valid_max_tokens(cls, v: int) -> int:
        if v < 100 or v > 32000:
            raise ValueError("Max tokens must be between 100 and 32000")
        return v


class CommunicationData(BaseModel):
    """Step 4: Communication channels."""
    gmail_enabled: bool = False
    gmail_address: str = ""
    gmail_app_password: str = ""
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @field_validator("gmail_address")
    @classmethod
    def gmail_format(cls, v: str) -> str:
        v = v.strip()
        if v and "@" not in v:
            raise ValueError("Invalid Gmail address")
        return v


class UseCaseSelection(BaseModel):
    """Step 5: Use case selection."""
    selected_use_cases: list[str]

    @field_validator("selected_use_cases")
    @classmethod
    def at_least_one(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Select at least one use case")
        return v


class AgentConfig(BaseModel):
    """Step 6: Per-agent configuration."""
    model_config = {"arbitrary_types_allowed": True}

    agent_configs: dict[str, dict]
