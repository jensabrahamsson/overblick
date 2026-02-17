"""
IRC service — read-only access to IRC conversation data via JSON files.

The IRC plugin writes conversations to data/<identity>/conversations.json.
This service reads those files for dashboard display without requiring
a live plugin instance.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class IRCService:
    """Read-only access to IRC conversation data via JSON files."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _find_conversations_file(self) -> Path | None:
        """Find the IRC conversations.json in any identity's data dir."""
        data_dir = self._base_dir / "data"
        if not data_dir.exists():
            return None
        for identity_dir in sorted(data_dir.iterdir()):
            if not identity_dir.is_dir():
                continue
            f = identity_dir / "conversations.json"
            if f.exists():
                return f
        return None

    def get_conversations(self, limit: int = 20) -> list[dict]:
        """Get recent conversations sorted by updated_at."""
        f = self._find_conversations_file()
        if not f:
            return []
        try:
            data = json.loads(f.read_text())
            if not isinstance(data, list):
                return []
            data.sort(key=lambda c: c.get("updated_at", 0), reverse=True)
            return data[:limit]
        except Exception as e:
            logger.warning("Failed to read IRC conversations: %s", e)
            return []

    def get_conversation(self, conversation_id: str) -> dict | None:
        """Get a specific conversation by ID."""
        for conv in self.get_conversations(limit=100):
            if conv.get("id") == conversation_id:
                return conv
        return None

    def get_current_conversation(self) -> dict | None:
        """Get the most recent active conversation."""
        for conv in self.get_conversations():
            if conv.get("state") == "active":
                return conv
        # Fallback to most recent conversation
        convs = self.get_conversations(limit=1)
        return convs[0] if convs else None
