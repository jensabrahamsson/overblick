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
