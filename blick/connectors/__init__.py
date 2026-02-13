"""
Connectors — thin I/O adapters (alias for plugins).

The 'connectors' package is the new naming convention for what was
previously 'plugins'. All imports are re-exported from blick.plugins.

Usage:
    from blick.connectors.moltbook.plugin import MoltbookPlugin
    from blick.connectors.telegram.plugin import TelegramPlugin
"""

# Re-export everything from blick.plugins
from blick.plugins import *  # noqa: F401,F403
