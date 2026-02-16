"""
Reply queue manager — processes queued reply actions.

Coordinates with EngagementDB to process pending reply actions
with retry logic, expiry handling, and anti-spam limits.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ReplyQueueManager:
    """
    Manages the reply action queue.

    Processes pending replies from the engagement database with
    retry handling, expiry cleanup, and rate limiting.
    """

    def __init__(
        self,
        engagement_db,
        max_retries: int = 3,
        max_per_cycle: int = 3,
    ):
        self._db = engagement_db
        self._max_retries = max_retries
        self._max_per_cycle = max_per_cycle

    async def process_queue(self, reply_callback) -> dict:
        """
        Process pending reply actions.

        Args:
            reply_callback: Async function(post_id, comment_id, action, score) -> bool
                Returns True if reply was successfully sent.

        Returns:
            Summary of processing results.
        """
        # Clean up expired items first
        expired = await self._db.cleanup_expired_queue_items()
        if expired:
            logger.info("Cleaned up %d expired queue items", expired)

        stale = await self._db.trim_stale_queue_items(max_age_hours=12)
        if stale:
            logger.info("Trimmed %d stale queue items", stale)

        # Get pending actions
        pending = await self._db.get_pending_reply_actions(limit=self._max_per_cycle)
        if not pending:
            return {"processed": 0, "success": 0, "failed": 0, "expired": expired}

        results = {"processed": 0, "success": 0, "failed": 0, "expired": expired}

        for item in pending:
            queue_id = item["id"]
            comment_id = item["comment_id"]
            post_id = item["post_id"]
            action = item["action"]
            score = item["relevance_score"]
            retry_count = item.get("retry_count", 0)

            if retry_count >= self._max_retries:
                logger.warning("Queue item %d exceeded max retries, removing", queue_id)
                await self._db.mark_reply_processed(comment_id, post_id, f"{action}_max_retries", score)
                await self._db.remove_from_queue(queue_id)
                continue

            results["processed"] += 1

            try:
                success = await reply_callback(post_id, comment_id, action, score)
                if success:
                    await self._db.mark_reply_processed(comment_id, post_id, action, score)
                    await self._db.remove_from_queue(queue_id)
                    results["success"] += 1
                    logger.info("Reply queue: processed %s on post %s", comment_id, post_id)
                else:
                    await self._db.update_queue_retry(queue_id, "Reply callback returned False")
                    results["failed"] += 1
            except Exception as e:
                logger.warning("Reply queue error for %s: %s", comment_id, e, exc_info=True)
                await self._db.update_queue_retry(queue_id, str(e)[:200])
                results["failed"] += 1

        logger.info(
            "Reply queue: %d processed, %d success, %d failed",
            results["processed"], results["success"], results["failed"],
        )
        return results
