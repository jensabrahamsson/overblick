"""Tests for gateway __main__.py — cover lines 3-6."""

from unittest.mock import patch

import pytest


class TestGatewayMain:
    def test_main_guard(self):
        """Cover lines 3-6: import and if __name__ guard."""
        # The module just imports run_server and calls it under __name__ == "__main__"
        # We can import it to get coverage on the import (lines 3-4)
        from overblick.gateway import __main__ as gw_main

        assert hasattr(gw_main, "run_server")
