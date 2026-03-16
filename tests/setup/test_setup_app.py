"""Tests for setup/app.py — cover lines 23-25, 40."""

from pathlib import Path
from unittest.mock import patch

import pytest

from overblick.setup.app import create_setup_app


class TestSetupApp:
    def test_create_setup_app_default_base_dir(self):
        """Cover line 40: base_dir defaults when None."""
        app = create_setup_app(base_dir=None)
        assert app is not None
        assert hasattr(app.state, "base_dir")

    def test_create_setup_app_custom_base_dir(self, tmp_path):
        """Cover line 39 (True branch): base_dir provided."""
        app = create_setup_app(base_dir=tmp_path)
        assert app.state.base_dir == tmp_path

    @pytest.mark.asyncio
    async def test_lifespan(self):
        """Cover lines 23-25: lifespan context manager."""
        from overblick.setup.app import lifespan

        app = create_setup_app()
        async with lifespan(app):
            pass  # Should not raise
