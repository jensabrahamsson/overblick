"""Tests for audit log."""

import pytest
from blick.core.security.audit_log import AuditLog


class TestAuditLog:
    def test_log_and_query(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="test_action", details={"key": "value"})

        entries = log.query(action="test_action")
        assert len(entries) >= 1
        assert entries[0]["action"] == "test_action"

    def test_log_multiple(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", details={})
        log.log(action="b", details={})
        log.log(action="a", details={"x": 1})

        a_entries = log.query(action="a")
        assert len(a_entries) == 2

        b_entries = log.query(action="b")
        assert len(b_entries) == 1

    def test_count(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="counted", details={})
        log.log(action="counted", details={})
        log.log(action="other", details={})

        assert log.count(action="counted") == 2
        assert log.count(action="other") == 1

    def test_empty_query(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        assert log.query(action="nonexistent") == []
