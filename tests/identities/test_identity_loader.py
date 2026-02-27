"""Tests for moltbook_username field in Identity model."""

import pytest
import yaml
from pathlib import Path

from overblick.identities import load_identity, Identity, _build_identity


class TestMoltbookUsername:
    def test_moltbook_username_loaded_from_operational(self, tmp_path):
        """moltbook_username is loaded from operational section."""
        data = {
            "identity": {"display_name": "Test"},
            "operational": {"moltbook_username": "TestUser123"},
        }
        identity = _build_identity("test", data)
        assert identity.moltbook_username == "TestUser123"

    def test_moltbook_username_defaults_empty(self, tmp_path):
        """moltbook_username defaults to empty string."""
        data = {"identity": {"display_name": "Test"}}
        identity = _build_identity("test", data)
        assert identity.moltbook_username == ""

    def test_moltbook_username_in_identity_model(self):
        """Identity model has moltbook_username field."""
        ident = Identity(name="test")
        assert hasattr(ident, "moltbook_username")
        assert ident.moltbook_username == ""
