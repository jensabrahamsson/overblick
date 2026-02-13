"""Tests for VisionCapability — image analysis via Claude API."""

import base64

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.core.capability import CapabilityContext
from overblick.capabilities.vision.analyzer import VisionCapability


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


# ── Basic lifecycle ──────────────────────────────────────────────────


class TestVisionLifecycle:
    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = VisionCapability(ctx)
        assert cap.name == "vision"

    @pytest.mark.asyncio
    async def test_setup_defaults(self):
        ctx = make_ctx(config={"api_key": "sk-test"})
        cap = VisionCapability(ctx)
        await cap.setup()
        assert cap._model == "claude-3-haiku-20240307"
        assert cap._max_tokens == 150
        assert cap._timeout_seconds == 30
        assert cap.enabled

    @pytest.mark.asyncio
    async def test_setup_custom_config(self):
        ctx = make_ctx(config={
            "api_key": "sk-test",
            "model": "claude-3-sonnet-20240229",
            "max_tokens": 300,
            "timeout_seconds": 60,
            "default_prompt": "What is this?",
        })
        cap = VisionCapability(ctx)
        await cap.setup()
        assert cap._model == "claude-3-sonnet-20240229"
        assert cap._max_tokens == 300
        assert cap._timeout_seconds == 60
        assert cap._default_prompt == "What is this?"

    @pytest.mark.asyncio
    async def test_setup_no_api_key_disables(self):
        """Without API key, capability should disable itself."""
        ctx = make_ctx(config={})
        cap = VisionCapability(ctx)
        await cap.setup()
        assert not cap.enabled

    @pytest.mark.asyncio
    async def test_teardown_closes_session(self):
        ctx = make_ctx(config={"api_key": "sk-test"})
        cap = VisionCapability(ctx)
        await cap.setup()

        mock_session = AsyncMock()
        mock_session.closed = False
        cap._session = mock_session

        await cap.teardown()
        mock_session.close.assert_called_once()
        assert cap._session is None

    @pytest.mark.asyncio
    async def test_teardown_no_session(self):
        """Teardown is safe when no session was created."""
        ctx = make_ctx(config={"api_key": "sk-test"})
        cap = VisionCapability(ctx)
        await cap.setup()
        await cap.teardown()  # Should not raise


# ── analyze_image_base64 ────────────────────────────────────────────


class TestAnalyzeBase64:
    def _make_cap(self, api_key: str = "sk-test") -> VisionCapability:
        ctx = make_ctx(config={"api_key": api_key})
        cap = VisionCapability(ctx)
        cap._api_key = api_key
        cap._enabled = True
        return cap

    @pytest.mark.asyncio
    async def test_successful_analysis(self):
        cap = self._make_cap()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "A cat sitting on a mat."}],
            "model": "claude-3-haiku-20240307",
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_resp)
        cap._session = mock_session

        result = await cap.analyze_image_base64("aGVsbG8=", "image/jpeg")
        assert result == "A cat sitting on a mat."

        # Verify correct API payload
        call_kwargs = mock_session.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["model"] == "claude-3-haiku-20240307"
        assert payload["max_tokens"] == 150
        assert payload["messages"][0]["content"][0]["type"] == "image"
        assert payload["messages"][0]["content"][0]["source"]["data"] == "aGVsbG8="

    @pytest.mark.asyncio
    async def test_with_context(self):
        cap = self._make_cap()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "A trading chart."}],
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_resp)
        cap._session = mock_session

        result = await cap.analyze_image_base64(
            "aGVsbG8=", "image/png", context="crypto trading"
        )
        assert result == "A trading chart."

        # Verify context was used in prompt
        payload = mock_session.post.call_args.kwargs["json"]
        text_content = payload["messages"][0]["content"][1]["text"]
        assert "crypto trading" in text_content

    @pytest.mark.asyncio
    async def test_api_error(self):
        cap = self._make_cap()

        mock_resp = AsyncMock()
        mock_resp.status = 429
        mock_resp.text = AsyncMock(return_value="Rate limited")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_resp)
        cap._session = mock_session

        result = await cap.analyze_image_base64("aGVsbG8=")
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_response_format(self):
        cap = self._make_cap()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"content": []})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_resp)
        cap._session = mock_session

        result = await cap.analyze_image_base64("aGVsbG8=")
        assert result is None

    @pytest.mark.asyncio
    async def test_network_error(self):
        import aiohttp

        cap = self._make_cap()

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(
            side_effect=aiohttp.ClientError("Connection failed")
        )
        cap._session = mock_session

        result = await cap.analyze_image_base64("aGVsbG8=")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        ctx = make_ctx(config={})
        cap = VisionCapability(ctx)
        await cap.setup()
        assert not cap.enabled

        result = await cap.analyze_image_base64("aGVsbG8=")
        assert result is None


# ── analyze_image_url ────────────────────────────────────────────────


class TestAnalyzeURL:
    def _make_cap(self) -> VisionCapability:
        ctx = make_ctx(config={"api_key": "sk-test"})
        cap = VisionCapability(ctx)
        cap._api_key = "sk-test"
        cap._enabled = True
        return cap

    @pytest.mark.asyncio
    async def test_download_and_analyze(self):
        cap = self._make_cap()

        # Mock download response
        image_bytes = b"\x89PNG\r\n\x1a\n"  # PNG header
        mock_dl_resp = AsyncMock()
        mock_dl_resp.status = 200
        mock_dl_resp.read = AsyncMock(return_value=image_bytes)
        mock_dl_resp.__aenter__ = AsyncMock(return_value=mock_dl_resp)
        mock_dl_resp.__aexit__ = AsyncMock(return_value=False)

        # Mock Claude API response
        mock_api_resp = AsyncMock()
        mock_api_resp.status = 200
        mock_api_resp.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "A logo image."}],
        })
        mock_api_resp.__aenter__ = AsyncMock(return_value=mock_api_resp)
        mock_api_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        # get() for download, post() for API call
        mock_session.get = MagicMock(return_value=mock_dl_resp)
        mock_session.post = MagicMock(return_value=mock_api_resp)
        cap._session = mock_session

        result = await cap.analyze_image_url("https://example.com/logo.png")
        assert result == "A logo image."

        # Verify download was made
        mock_session.get.assert_called_once_with("https://example.com/logo.png")

        # Verify correct media type was detected
        payload = mock_session.post.call_args.kwargs["json"]
        assert payload["messages"][0]["content"][0]["source"]["media_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_media_type_detection_jpeg(self):
        cap = self._make_cap()

        mock_dl_resp = AsyncMock()
        mock_dl_resp.status = 200
        mock_dl_resp.read = AsyncMock(return_value=b"\xff\xd8\xff")
        mock_dl_resp.__aenter__ = AsyncMock(return_value=mock_dl_resp)
        mock_dl_resp.__aexit__ = AsyncMock(return_value=False)

        mock_api_resp = AsyncMock()
        mock_api_resp.status = 200
        mock_api_resp.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "A photo."}],
        })
        mock_api_resp.__aenter__ = AsyncMock(return_value=mock_api_resp)
        mock_api_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=mock_dl_resp)
        mock_session.post = MagicMock(return_value=mock_api_resp)
        cap._session = mock_session

        await cap.analyze_image_url("https://example.com/photo.jpg")

        payload = mock_session.post.call_args.kwargs["json"]
        assert payload["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_media_type_default_for_unknown(self):
        cap = self._make_cap()

        mock_dl_resp = AsyncMock()
        mock_dl_resp.status = 200
        mock_dl_resp.read = AsyncMock(return_value=b"\x00\x01")
        mock_dl_resp.__aenter__ = AsyncMock(return_value=mock_dl_resp)
        mock_dl_resp.__aexit__ = AsyncMock(return_value=False)

        mock_api_resp = AsyncMock()
        mock_api_resp.status = 200
        mock_api_resp.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "An image."}],
        })
        mock_api_resp.__aenter__ = AsyncMock(return_value=mock_api_resp)
        mock_api_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=mock_dl_resp)
        mock_session.post = MagicMock(return_value=mock_api_resp)
        cap._session = mock_session

        await cap.analyze_image_url("https://example.com/image?id=123")

        payload = mock_session.post.call_args.kwargs["json"]
        # Unknown extension defaults to jpeg
        assert payload["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_download_failure(self):
        cap = self._make_cap()

        mock_dl_resp = AsyncMock()
        mock_dl_resp.status = 404
        mock_dl_resp.__aenter__ = AsyncMock(return_value=mock_dl_resp)
        mock_dl_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=mock_dl_resp)
        cap._session = mock_session

        result = await cap.analyze_image_url("https://example.com/missing.jpg")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_network_error(self):
        import aiohttp

        cap = self._make_cap()

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(
            side_effect=aiohttp.ClientError("DNS failed")
        )
        cap._session = mock_session

        result = await cap.analyze_image_url("https://example.com/image.jpg")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        ctx = make_ctx(config={})
        cap = VisionCapability(ctx)
        await cap.setup()
        assert not cap.enabled

        result = await cap.analyze_image_url("https://example.com/image.jpg")
        assert result is None


# ── Registry integration ─────────────────────────────────────────────


class TestVisionRegistry:
    def test_registered_in_capability_registry(self):
        from overblick.capabilities import CAPABILITY_REGISTRY
        assert "vision" in CAPABILITY_REGISTRY
        assert CAPABILITY_REGISTRY["vision"] is VisionCapability

    def test_vision_bundle(self):
        from overblick.capabilities import CAPABILITY_BUNDLES
        assert "vision" in CAPABILITY_BUNDLES
        assert "vision" in CAPABILITY_BUNDLES["vision"]

    def test_resolve_vision_bundle(self):
        from overblick.capabilities import resolve_capabilities
        resolved = resolve_capabilities(["vision"])
        assert "vision" in resolved
