"""Additional tests for VisionCapability — cover lines 100, 221-223.

Uncovered: 100 (session closed, recreate), 221-223 (unexpected exception in analyze_image_base64).
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from overblick.capabilities.vision.analyzer import VisionCapability
from overblick.core.capability import CapabilityContext


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestVisionCoverage:
    @pytest.mark.asyncio
    async def test_ensure_session_creates_new_when_closed(self):
        """Cover line 100: session is closed, create a new one."""
        ctx = make_ctx(config={"api_key": "sk-test"})
        cap = VisionCapability(ctx)
        await cap.setup()

        # Create a mock closed session
        mock_session = MagicMock()
        mock_session.closed = True
        cap._session = mock_session

        session = await cap._ensure_session()
        assert session is not None
        assert session is not mock_session  # New session created
        # Cleanup
        if cap._session and not cap._session.closed:
            await cap._session.close()

    @pytest.mark.asyncio
    async def test_analyze_base64_unexpected_exception(self):
        """Cover lines 221-223: unexpected non-aiohttp exception."""
        ctx = make_ctx(config={"api_key": "sk-test"})
        cap = VisionCapability(ctx)
        cap._api_key = "sk-test"
        cap._enabled = True

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(side_effect=RuntimeError("Unexpected error"))
        cap._session = mock_session

        result = await cap.analyze_image_base64("aGVsbG8=")
        assert result is None
