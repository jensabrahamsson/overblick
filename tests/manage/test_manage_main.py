"""Tests for manage/__main__.py — cover lines 23-86, 90."""

from unittest.mock import MagicMock, patch

import pytest

from overblick.manage.__main__ import main


class TestManageMain:
    @pytest.fixture
    def mock_mgr(self):
        with patch("overblick.manage.__main__.ServiceManager") as cls:
            mgr = MagicMock()
            cls.return_value = mgr
            yield mgr

    def test_up_no_identities(self, mock_mgr):
        main(["up"])
        mock_mgr.up.assert_called_once_with(identities=None, port=8080)

    def test_up_with_identities(self, mock_mgr):
        main(["up", "anomal", "cherry"])
        mock_mgr.up.assert_called_once_with(identities=["anomal", "cherry"], port=8080)

    def test_up_with_port(self, mock_mgr):
        main(["up", "--port", "9090"])
        mock_mgr.up.assert_called_once_with(identities=None, port=9090)

    def test_down(self, mock_mgr):
        main(["down"])
        mock_mgr.down.assert_called_once()

    def test_status(self, mock_mgr):
        main(["status"])
        mock_mgr.status.assert_called_once_with(port=8080)

    def test_status_with_port(self, mock_mgr):
        main(["status", "--port", "9090"])
        mock_mgr.status.assert_called_once_with(port=9090)

    def test_gateway_start(self, mock_mgr):
        main(["gateway", "start"])
        mock_mgr.start_gateway.assert_called_once()

    def test_gateway_stop(self, mock_mgr):
        main(["gateway", "stop"])
        mock_mgr.stop_gateway.assert_called_once()

    def test_gateway_status(self, mock_mgr):
        main(["gateway", "status"])
        mock_mgr.status_gateway.assert_called_once()

    def test_dashboard_start(self, mock_mgr):
        main(["dashboard", "start"])
        mock_mgr.start_dashboard.assert_called_once_with(port=8080)

    def test_dashboard_stop(self, mock_mgr):
        main(["dashboard", "stop"])
        mock_mgr.stop_dashboard.assert_called_once()

    def test_dashboard_status(self, mock_mgr):
        main(["dashboard", "status"])
        mock_mgr.status_dashboard.assert_called_once_with(port=8080)

    def test_supervisor_start(self, mock_mgr):
        main(["supervisor", "start"])
        mock_mgr.start_supervisor.assert_called_once_with(identities=None)

    def test_supervisor_start_with_identities(self, mock_mgr):
        main(["supervisor", "start", "anomal"])
        mock_mgr.start_supervisor.assert_called_once_with(identities=["anomal"])

    def test_supervisor_stop(self, mock_mgr):
        main(["supervisor", "stop"])
        mock_mgr.stop_supervisor.assert_called_once()

    def test_supervisor_status(self, mock_mgr):
        main(["supervisor", "status"])
        mock_mgr.status_supervisor.assert_called_once()
