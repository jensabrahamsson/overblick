"""
Additional coverage tests for onboarding_chat module.

Covers uncovered lines:
- _call_llm: gateway failure fallback to ollama, provider routing
- _call_gateway: non-200 response, successful response with think tokens
- _call_ollama: non-200 response, successful response, empty choices
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.shared.onboarding_chat import (
    _call_gateway,
    _call_llm,
    _call_ollama,
)


class TestCallLLM:
    @pytest.mark.asyncio
    async def test_should_try_gateway_then_fallback_to_ollama(self):
        """When gateway fails, should try ollama."""
        with (
            patch(
                "overblick.shared.onboarding_chat._call_gateway",
                new_callable=AsyncMock,
                side_effect=ConnectionError("gateway down"),
            ),
            patch(
                "overblick.shared.onboarding_chat._call_ollama",
                new_callable=AsyncMock,
                return_value="ollama response",
            ),
        ):
            result = await _call_llm(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                {"provider": "ollama"},
            )
            assert result == "ollama response"

    @pytest.mark.asyncio
    async def test_should_try_gateway_when_provider_is_gateway(self):
        with (
            patch(
                "overblick.shared.onboarding_chat._call_gateway",
                new_callable=AsyncMock,
                return_value="gateway response",
            ),
        ):
            result = await _call_llm(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                {"provider": "gateway"},
            )
            assert result == "gateway response"

    @pytest.mark.asyncio
    async def test_should_raise_when_ollama_fails(self):
        with (
            patch(
                "overblick.shared.onboarding_chat._call_gateway",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "overblick.shared.onboarding_chat._call_ollama",
                new_callable=AsyncMock,
                side_effect=ConnectionError("ollama down"),
            ),
        ):
            with pytest.raises(ConnectionError):
                await _call_llm(
                    [{"role": "user", "content": "Hi"}],
                    "qwen3:8b",
                    0.7,
                    500,
                    {"provider": "ollama"},
                )

    @pytest.mark.asyncio
    async def test_should_skip_gateway_for_non_matching_provider(self):
        """When provider is not 'gateway' or 'ollama', skip gateway."""
        with (
            patch(
                "overblick.shared.onboarding_chat._call_ollama",
                new_callable=AsyncMock,
                return_value="direct ollama",
            ),
        ):
            result = await _call_llm(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                {"provider": "direct"},
            )
            assert result == "direct ollama"


class TestCallGateway:
    @pytest.mark.asyncio
    async def test_should_return_none_on_non_200(self):
        mock_resp = AsyncMock()
        mock_resp.status = 500

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False),
        )):
            result = await _call_gateway(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                "http://localhost:8200",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_content_on_success(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Hello!"}}],
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with (
            patch("aiohttp.ClientSession", return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )),
            patch("overblick.core.llm.client.LLMClient.strip_think_tokens", return_value="Hello!"),
        ):
            result = await _call_gateway(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                "http://localhost:8200",
            )
        assert result == "Hello!"

    @pytest.mark.asyncio
    async def test_should_return_none_on_empty_choices(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"choices": []})

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False),
        )):
            result = await _call_gateway(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                "http://localhost:8200",
            )
        assert result is None


class TestCallOllama:
    @pytest.mark.asyncio
    async def test_should_raise_on_non_200(self):
        mock_resp = AsyncMock()
        mock_resp.status = 503
        mock_resp.text = AsyncMock(return_value="Service Unavailable")

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False),
        )):
            with pytest.raises(ConnectionError, match="503"):
                await _call_ollama(
                    [{"role": "user", "content": "Hi"}],
                    "qwen3:8b",
                    0.7,
                    500,
                    {},
                )

    @pytest.mark.asyncio
    async def test_should_return_content_on_success(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Ollama says hi!"}}],
        })

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with (
            patch("aiohttp.ClientSession", return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )),
            patch("overblick.core.llm.client.LLMClient.strip_think_tokens", return_value="Ollama says hi!"),
        ):
            result = await _call_ollama(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                {},
            )
        assert result == "Ollama says hi!"

    @pytest.mark.asyncio
    async def test_should_return_none_on_empty_choices(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"choices": []})

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False),
        )):
            result = await _call_ollama(
                [{"role": "user", "content": "Hi"}],
                "qwen3:8b",
                0.7,
                500,
                {},
            )
        assert result is None
