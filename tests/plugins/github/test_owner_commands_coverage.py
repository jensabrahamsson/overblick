"""
Additional coverage tests for owner_commands module.

Covers uncovered lines:
- 151-153: processed_message_ids overflow trimming
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.github.owner_commands import OwnerCommandQueue


class TestProcessedIdsOverflow:
    @pytest.mark.asyncio
    async def test_should_trim_processed_ids_when_overflow(self):
        """Lines 151-153: processed_message_ids set is trimmed at overflow."""
        queue = OwnerCommandQueue()
        queue._max_processed_ids = 5

        # Pre-fill with processed IDs
        queue.processed_message_ids = set(range(10))

        # Now fetch_commands should trigger the trimming
        notifier = AsyncMock()
        update = MagicMock(message_id=20, text="merge owner/repo#1", timestamp="")
        notifier.fetch_updates = AsyncMock(return_value=[update])

        await queue.fetch_commands(notifier)

        # Should have trimmed to keep newer half + the new one
        assert len(queue.processed_message_ids) <= 7  # 5 kept + new ones
        # Newer IDs should be kept
        assert 20 in queue.processed_message_ids
