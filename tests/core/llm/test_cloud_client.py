"""Tests for the CloudLLMClient stub."""

import pytest
from overblick.core.llm.cloud_client import CloudLLMClient


class TestCloudLLMClient:
    """Verify the stub raises NotImplementedError with guidance."""

    def test_init(self):
        client = CloudLLMClient(
            api_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="sk-test",
        )
        assert client.api_url == "https://api.openai.com/v1"
        assert client.model == "gpt-4o"
        assert client.api_key == "sk-test"

    def test_init_defaults(self):
        client = CloudLLMClient(api_url="", model="")
        assert client.temperature == 0.7
        assert client.max_tokens == 2000
        assert client.top_p == 0.9
        assert client.timeout_seconds == 180

    @pytest.mark.asyncio
    async def test_chat_raises(self):
        client = CloudLLMClient(api_url="", model="")
        with pytest.raises(NotImplementedError, match="Cloud LLM client not yet implemented"):
            await client.chat([{"role": "user", "content": "Hello"}])

    @pytest.mark.asyncio
    async def test_health_check_raises(self):
        client = CloudLLMClient(api_url="", model="")
        with pytest.raises(NotImplementedError, match="health_check"):
            await client.health_check()

    @pytest.mark.asyncio
    async def test_close_does_not_raise(self):
        client = CloudLLMClient(api_url="", model="")
        await client.close()  # Should be a no-op
