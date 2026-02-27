"""Tests for Monitor (ex-Observability) dashboard routes."""

import pytest

from overblick.dashboard.routes.observability import router


class TestMonitorEndpoints:
    """Verify all /monitor endpoints are registered."""

    def test_monitor_main_endpoint(self):
        """Main /monitor page endpoint exists."""
        paths = [r.path for r in router.routes]
        assert "/monitor" in paths

    def test_monitor_agents_strip_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/agents-strip" in paths

    def test_monitor_gateway_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/gateway" in paths

    def test_monitor_fleet_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/fleet" in paths

    def test_monitor_audit_activity_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/audit-activity" in paths

    def test_monitor_routing_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/routing" in paths

    def test_monitor_errors_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/errors" in paths

    def test_old_observability_not_registered(self):
        """Old /observability path should NOT exist."""
        paths = [r.path for r in router.routes]
        assert "/observability" not in paths
