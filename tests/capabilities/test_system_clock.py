"""Tests for SystemClockCapability."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from overblick.capabilities.system.clock import SystemClockCapability
from overblick.core.capability import CapabilityContext


def _make_ctx(timezone: str = "Europe/Stockholm") -> CapabilityContext:
    """Build a minimal CapabilityContext with a timezone."""
    identity = MagicMock()
    identity.quiet_hours.timezone = timezone
    return CapabilityContext(
        identity_name="test",
        data_dir="/tmp/test",
        identity=identity,
    )


class TestSystemClock:
    """Unit tests for system clock capability."""

    @pytest.mark.asyncio
    async def test_setup(self):
        cap = SystemClockCapability(_make_ctx())
        await cap.setup()
        assert cap.enabled is True

    def test_now_returns_datetime(self):
        cap = SystemClockCapability(_make_ctx())
        now = cap.now()
        assert isinstance(now, datetime)
        assert now.tzinfo is not None

    def test_now_respects_timezone(self):
        cap = SystemClockCapability(_make_ctx("US/Eastern"))
        now = cap.now()
        # Should be in US/Eastern timezone
        assert now.tzinfo == ZoneInfo("US/Eastern")

    def test_date_str_format(self):
        cap = SystemClockCapability(_make_ctx())
        date = cap.date_str()
        # Should be YYYY-MM-DD
        assert len(date) == 10
        assert date[4] == "-"
        assert date[7] == "-"

    def test_time_str_format(self):
        cap = SystemClockCapability(_make_ctx())
        time = cap.time_str()
        # Should be HH:MM
        assert len(time) == 5
        assert time[2] == ":"

    def test_weekday_is_english(self):
        cap = SystemClockCapability(_make_ctx())
        day = cap.weekday()
        assert day in [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        ]

    def test_iso_format(self):
        cap = SystemClockCapability(_make_ctx())
        iso = cap.iso()
        # Should parse as ISO 8601
        parsed = datetime.fromisoformat(iso)
        assert parsed.tzinfo is not None

    def test_get_prompt_context(self):
        cap = SystemClockCapability(_make_ctx())
        ctx = cap.get_prompt_context()
        assert "Current time:" in ctx
        # Should contain a day name
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday",
                     "Friday", "Saturday", "Sunday"]:
            if day in ctx:
                break
        else:
            pytest.fail(f"No weekday found in prompt context: {ctx}")

    def test_name(self):
        cap = SystemClockCapability(_make_ctx())
        assert cap.name == "system_clock"

    def test_default_timezone_fallback(self):
        """If identity has no timezone, defaults to Europe/Stockholm."""
        identity = MagicMock()
        identity.quiet_hours.timezone = None
        ctx = CapabilityContext(
            identity_name="test",
            data_dir="/tmp/test",
            identity=identity,
        )
        cap = SystemClockCapability(ctx)
        assert cap._timezone == ZoneInfo("Europe/Stockholm")
