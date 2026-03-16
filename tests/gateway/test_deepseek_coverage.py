"""Additional tests for deepseek_client — cover lines 113-115."""

from unittest.mock import AsyncMock

import pytest

from overblick.gateway.deepseek_client import DeepseekClient


class TestDeepseekCoverage:
    @pytest.mark.asyncio
    async def test_list_models_generic_exception(self):
        """Cover lines 113-115: generic exception in list_models returns []."""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=RuntimeError("Unexpected"))

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        models = await client.list_models()
        assert models == []
