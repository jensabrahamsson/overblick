"""
Connectors — thin I/O adapters (alias for plugins).

The 'connectors' package is the new naming convention for what was
previously 'plugins'. All imports are re-exported from overblick.plugins.

Usage:
    from overblick.connectors.moltbook.plugin import MoltbookPlugin
    from overblick.connectors.telegram.plugin import TelegramPlugin
"""

# Re-export everything from overblick.plugins
from overblick.plugins import *  # noqa: F401,F403
