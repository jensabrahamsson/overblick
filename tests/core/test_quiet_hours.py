"""Tests for quiet hours checker."""

from datetime import datetime
from unittest.mock import patch

import pytest
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.identities import QuietHoursSettings


class TestQuietHoursChecker:
    def test_disabled(self):
        settings = QuietHoursSettings(enabled=False)
        checker = QuietHoursChecker(settings)
        assert not checker.is_quiet_hours()

    def test_during_quiet_hours(self):
        settings = QuietHoursSettings(enabled=True, start_hour=22, end_hour=7)
        checker = QuietHoursChecker(settings)

        with patch("overblick.core.quiet_hours.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert checker.is_quiet_hours()

    def test_outside_quiet_hours(self):
        settings = QuietHoursSettings(enabled=True, start_hour=22, end_hour=7)
        checker = QuietHoursChecker(settings)

        with patch("overblick.core.quiet_hours.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert not checker.is_quiet_hours()

    def test_daytime_quiet_hours(self):
        """Quiet hours during daytime (start < end, e.g. 06:00-08:00)."""
        settings = QuietHoursSettings(enabled=True, start_hour=6, end_hour=8)
        checker = QuietHoursChecker(settings)

        # 07:00 should be quiet
        assert checker.is_quiet_hours(datetime(2026, 1, 1, 7, 0))
        # 05:00 should not be quiet
        assert not checker.is_quiet_hours(datetime(2026, 1, 1, 5, 0))
        # 09:00 should not be quiet
        assert not checker.is_quiet_hours(datetime(2026, 1, 1, 9, 0))

    def test_time_until_active_daytime(self):
        """time_until_active with daytime window should return correct value."""
        settings = QuietHoursSettings(enabled=True, start_hour=6, end_hour=8)
        checker = QuietHoursChecker(settings)

        # At 07:00, should be about 1 hour until 08:00
        with patch("overblick.core.quiet_hours.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 7, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            seconds = checker.time_until_active()
            assert seconds is not None
            assert 3500 <= seconds <= 3600  # ~1 hour

    def test_time_until_active_overnight_after_start(self):
        """time_until_active overnight window when past start hour."""
        settings = QuietHoursSettings(enabled=True, start_hour=22, end_hour=7)
        checker = QuietHoursChecker(settings)

        # At 23:00, should be about 8 hours until 07:00
        with patch("overblick.core.quiet_hours.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            seconds = checker.time_until_active()
            assert seconds is not None
            assert 28000 <= seconds <= 28800  # ~8 hours

    def test_time_until_active_overnight_before_end(self):
        """time_until_active overnight window when before end hour."""
        settings = QuietHoursSettings(enabled=True, start_hour=22, end_hour=7)
        checker = QuietHoursChecker(settings)

        # At 05:00, should be about 2 hours until 07:00
        with patch("overblick.core.quiet_hours.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 5, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            seconds = checker.time_until_active()
            assert seconds is not None
            assert 7000 <= seconds <= 7200  # ~2 hours

    def test_time_until_active_not_quiet(self):
        """time_until_active returns None when not in quiet hours."""
        settings = QuietHoursSettings(enabled=True, start_hour=22, end_hour=7)
        checker = QuietHoursChecker(settings)

        with patch("overblick.core.quiet_hours.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert checker.time_until_active() is None
