"""
Nonce Manager for Whallet

Provides persistent nonce tracking with atomic increment and chain synchronization.
Solves race conditions and nonce desync issues when multiple components submit transactions.

Key features:
- Local nonce tracking in SQLite for persistence across restarts
- Atomic increment with threading.Lock for concurrent access
- Auto-sync with chain when local nonce falls behind
- Recovery from nonce errors (too low, already known, replacement underpriced)

Created: 2026-01-12 (Sprint 1.3 - Critical Fixes)
"""

import logging
import sqlite3
import threading
import time
from typing import Callable, Optional

from web3 import Web3

logger = logging.getLogger(__name__)


class NonceManager:
    """
    Manages transaction nonces with persistence and recovery.

    Uses SQLite for persistence (survives restarts) and threading.Lock
    for atomic operations (protects against concurrent access).

    Usage:
        nonce_mgr = NonceManager(web3, wallet_address, db_path)
        nonce = nonce_mgr.get_next_nonce()  # Atomic increment
        # ... build and send transaction ...
        nonce_mgr.mark_nonce_used(nonce, tx_hash)  # Record usage

        # If nonce error:
        new_nonce = nonce_mgr.handle_nonce_error(failed_nonce)
    """

    # Table name for nonce tracking
    TABLE_NAME = "nonce_tracking"

    def __init__(
        self,
        web3,
        address: str,
        db_path: str,
        chain_nonce_fn: Optional[Callable[[str], int]] = None,
    ):
        """
        Initialize NonceManager.

        Args:
            web3: Web3 instance for chain queries
            address: Wallet address to track nonces for
            db_path: Path to SQLite database file
        """
        self.web3 = web3
        self._chain_nonce_fn = chain_nonce_fn
        # Store checksummed address for chain queries (web3.py requires checksum)
        # Use lowercase only for database key normalization
        self._checksum_address = Web3.to_checksum_address(address)
        self.address = address.lower()  # For database key only
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_database()

        logger.info(f"NonceManager initialized for {self._checksum_address[:10]}... (db: {db_path})")

    def _init_database(self) -> None:
        """Initialize SQLite database with nonce tracking table."""
        # CRITICAL: Use same WAL settings as WhalletDatabase to avoid "disk I/O error"
        # All processes accessing the same SQLite db must use consistent PRAGMA settings
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")

        # Create nonce tracking table
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                address TEXT PRIMARY KEY NOT NULL,
                local_nonce INTEGER NOT NULL DEFAULT 0,
                last_used_nonce INTEGER,
                last_tx_hash TEXT,
                last_sync_time REAL,
                last_updated REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)

        # Create nonce history table for auditing
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nonce_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                nonce INTEGER NOT NULL,
                tx_hash TEXT,
                status TEXT NOT NULL DEFAULT 'allocated',
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nonce_history_address
            ON nonce_history(address)
        """)

        conn.commit()
        conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # CRITICAL: Must set busy_timeout on EVERY connection, not just init
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _get_local_nonce(self) -> int:
        """Get the current local nonce from database."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT local_nonce FROM {self.TABLE_NAME} WHERE address = ?",
                (self.address,)
            )
            row = cursor.fetchone()
            return row["local_nonce"] if row else 0
        finally:
            conn.close()

    def _set_local_nonce(self, nonce: int) -> None:
        """Set the local nonce in database."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            now = time.time()

            cursor.execute(f"""
                INSERT INTO {self.TABLE_NAME} (address, local_nonce, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    local_nonce = excluded.local_nonce,
                    last_updated = excluded.last_updated
            """, (self.address, nonce, now))

            conn.commit()
        finally:
            conn.close()

    def _get_chain_nonce(self) -> int:
        """Get current nonce from blockchain (pending transactions included)."""
        try:
            if self._chain_nonce_fn:
                return self._chain_nonce_fn(self._checksum_address)
            return self.web3.eth.get_transaction_count(self._checksum_address, "pending")
        except Exception as e:
            logger.error(f"Failed to get chain nonce: {e}")
            raise

    def get_next_nonce(self) -> int:
        """
        Get the next nonce to use for a transaction.

        This is the main entry point. It:
        1. Gets both chain and local nonce
        2. Takes the maximum (handles desync)
        3. Increments and persists the new value
        4. Returns the nonce to use

        Thread-safe via threading.Lock.

        Returns:
            Next nonce to use for transaction
        """
        with self._lock:
            # Get both chain and local nonce
            chain_nonce = self._get_chain_nonce()
            local_nonce = self._get_local_nonce()

            # Take maximum to handle desync
            # If chain is ahead (external tx), use chain value
            # If local is ahead (pending tx), use local value
            next_nonce = max(chain_nonce, local_nonce)

            # Record allocation in history
            self._record_nonce_allocation(next_nonce)

            # Increment local nonce for next call
            self._set_local_nonce(next_nonce + 1)

            # Update sync time
            self._update_sync_time()

            logger.debug(
                f"Nonce allocated: {next_nonce} "
                f"(chain={chain_nonce}, local={local_nonce})"
            )

            return next_nonce

    def _record_nonce_allocation(self, nonce: int) -> None:
        """Record nonce allocation in history table."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO nonce_history (address, nonce, status)
                VALUES (?, ?, 'allocated')
            """, (self.address, nonce))

            conn.commit()
        finally:
            conn.close()

    def _update_sync_time(self) -> None:
        """Update last sync time in database."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            now = time.time()

            cursor.execute(f"""
                UPDATE {self.TABLE_NAME}
                SET last_sync_time = ?
                WHERE address = ?
            """, (now, self.address))

            conn.commit()
        finally:
            conn.close()

    def mark_nonce_used(self, nonce: int, tx_hash: str) -> None:
        """
        Mark a nonce as successfully used (transaction submitted).

        Args:
            nonce: The nonce that was used
            tx_hash: Transaction hash for auditing
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = time.time()

                # Update main tracking table
                cursor.execute(f"""
                    UPDATE {self.TABLE_NAME}
                    SET last_used_nonce = ?,
                        last_tx_hash = ?,
                        last_updated = ?
                    WHERE address = ?
                """, (nonce, tx_hash, now, self.address))

                # Update history record (using subquery since SQLite doesn't support ORDER BY in UPDATE)
                cursor.execute("""
                    UPDATE nonce_history
                    SET tx_hash = ?, status = 'submitted'
                    WHERE rowid = (
                        SELECT rowid FROM nonce_history
                        WHERE address = ? AND nonce = ? AND status = 'allocated'
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                """, (tx_hash, self.address, nonce))

                conn.commit()
            finally:
                conn.close()

            logger.debug(f"Nonce {nonce} marked as used (tx: {tx_hash[:16]}...)")

    def release_nonce(self, nonce: int) -> bool:
        """
        Release a reserved nonce when a transaction fails before submission.

        This only rolls back if the nonce is the most recently allocated
        (local_nonce == nonce + 1). This avoids corrupting concurrent allocations.

        Args:
            nonce: The nonce to release

        Returns:
            True if released, False if rollback was unsafe
        """
        with self._lock:
            local_nonce = self._get_local_nonce()
            if local_nonce != nonce + 1:
                logger.debug(
                    f"Nonce release skipped: local_nonce={local_nonce}, nonce={nonce}"
                )
                return False

            # Roll back local nonce to the released value
            self._set_local_nonce(nonce)

            # Update history record (best-effort)
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE nonce_history
                    SET status = 'released'
                    WHERE rowid = (
                        SELECT rowid FROM nonce_history
                        WHERE address = ? AND nonce = ? AND status = 'allocated'
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                """, (self.address, nonce))
                conn.commit()
            finally:
                conn.close()

            logger.info(f"Nonce {nonce} released (pre-send failure)")
            return True

    def handle_nonce_error(self, failed_nonce: int, error_type: str = "unknown") -> int:
        """
        Handle a nonce error and return a fresh nonce to retry with.

        This is called when a transaction fails with nonce-related errors:
        - "nonce too low" - chain is ahead of our local tracking
        - "already known" - transaction with this nonce already submitted
        - "replacement transaction underpriced" - need higher gas for same nonce

        Args:
            failed_nonce: The nonce that failed
            error_type: Type of error for logging ("too_low", "already_known", etc.)

        Returns:
            New nonce to retry with
        """
        with self._lock:
            # Get fresh chain nonce
            chain_nonce = self._get_chain_nonce()

            # Mark the failed nonce in history
            conn = self._get_connection()
            try:
                cursor = conn.cursor()

                # Using subquery since SQLite doesn't support ORDER BY in UPDATE
                cursor.execute("""
                    UPDATE nonce_history
                    SET status = ?
                    WHERE rowid = (
                        SELECT rowid FROM nonce_history
                        WHERE address = ? AND nonce = ? AND status IN ('allocated', 'submitted')
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                """, (f"failed_{error_type}", self.address, failed_nonce))

                conn.commit()
            finally:
                conn.close()

            # Update local nonce to chain value (resync)
            self._set_local_nonce(chain_nonce + 1)

            # Record new allocation
            self._record_nonce_allocation(chain_nonce)

            logger.warning(
                f"Nonce error recovery: failed={failed_nonce}, "
                f"new={chain_nonce}, error={error_type}"
            )

            return chain_nonce

    def sync_with_chain(self) -> int:
        """
        Force sync local nonce with chain state.

        Useful after suspected desync or on startup.

        Returns:
            Current chain nonce
        """
        with self._lock:
            chain_nonce = self._get_chain_nonce()
            self._set_local_nonce(chain_nonce)
            self._update_sync_time()

            logger.info(f"Nonce synced with chain: {chain_nonce}")
            return chain_nonce

    def get_nonce_gap(self) -> int:
        """
        Check for nonce gaps (difference between local and chain).

        Positive: Local is ahead (pending transactions)
        Negative: Chain is ahead (external transactions)
        Zero: In sync

        Returns:
            Difference between local and chain nonce
        """
        with self._lock:
            local = self._get_local_nonce()
            chain = self._get_chain_nonce()
            return local - chain

    def get_stats(self) -> dict:
        """
        Get nonce tracking statistics for monitoring.

        Returns:
            Dict with nonce stats and recent history
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()

                # Get main tracking data
                cursor.execute(
                    f"SELECT * FROM {self.TABLE_NAME} WHERE address = ?",
                    (self.address,)
                )
                row = cursor.fetchone()

                # Get recent history
                cursor.execute("""
                    SELECT nonce, status, tx_hash, created_at
                    FROM nonce_history
                    WHERE address = ?
                    ORDER BY created_at DESC
                    LIMIT 10
                """, (self.address,))
                history = [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()

            chain_nonce = self._get_chain_nonce()
            local_nonce = row["local_nonce"] if row else 0

            return {
                "address": self.address,
                "chain_nonce": chain_nonce,
                "local_nonce": local_nonce,
                "gap": local_nonce - chain_nonce,
                "last_used_nonce": row["last_used_nonce"] if row else None,
                "last_tx_hash": row["last_tx_hash"] if row else None,
                "last_sync_time": row["last_sync_time"] if row else None,
                "recent_history": history,
            }


__all__ = ['NonceManager']
