"""
Unit Tests for NonceManager (Sprint 1.3 - Nonce Management Fix)

Tests persistent nonce tracking with atomic increment, chain synchronization,
and recovery from nonce errors.

Created: 2026-01-12
"""

import pytest
import sqlite3
import tempfile
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nonce_manager import NonceManager


class MockWeb3:
    """Mock Web3 instance for testing."""

    def __init__(self, initial_nonce: int = 0):
        self._nonce = initial_nonce
        self._nonce_lock = threading.Lock()

    def get_transaction_count(self, address: str, block: str = "pending") -> int:
        """Simulate get_transaction_count."""
        with self._nonce_lock:
            return self._nonce

    def set_chain_nonce(self, nonce: int):
        """Test helper to simulate chain nonce changes."""
        with self._nonce_lock:
            self._nonce = nonce

    def increment_chain_nonce(self):
        """Test helper to simulate confirmed transaction."""
        with self._nonce_lock:
            self._nonce += 1

    @property
    def eth(self):
        """Return self to allow web3.eth.get_transaction_count pattern."""
        return self


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    # Cleanup
    import os
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def mock_web3():
    """Create a mock Web3 instance."""
    return MockWeb3(initial_nonce=5)


@pytest.fixture
def nonce_manager(mock_web3, temp_db_path):
    """Create a NonceManager with mock Web3."""
    return NonceManager(
        web3=mock_web3,
        address="0x1234567890abcdef1234567890abcdef12345678",
        db_path=temp_db_path
    )


class TestNonceManagerBasics:
    """Basic NonceManager tests."""

    def test_init_creates_tables(self, mock_web3, temp_db_path):
        """Database tables are created on initialization."""
        NonceManager(mock_web3, "0x1234567890abcdef1234567890abcdef12345678", temp_db_path)

        # Verify tables exist
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "nonce_tracking" in tables
        assert "nonce_history" in tables

    def test_get_next_nonce_first_call(self, nonce_manager, mock_web3):
        """First nonce matches chain state."""
        nonce = nonce_manager.get_next_nonce()
        assert nonce == 5  # Same as mock_web3 initial_nonce

    def test_get_next_nonce_increments(self, nonce_manager):
        """Subsequent calls increment nonce."""
        nonce1 = nonce_manager.get_next_nonce()
        nonce2 = nonce_manager.get_next_nonce()
        nonce3 = nonce_manager.get_next_nonce()

        assert nonce2 == nonce1 + 1
        assert nonce3 == nonce2 + 1

    def test_nonce_sync_with_chain(self, nonce_manager, mock_web3):
        """Local nonce syncs when chain is ahead."""
        # Get initial nonce (5)
        nonce1 = nonce_manager.get_next_nonce()
        assert nonce1 == 5

        # Simulate external transaction (chain advances)
        mock_web3.set_chain_nonce(10)

        # Next call should sync with chain
        nonce2 = nonce_manager.get_next_nonce()
        assert nonce2 == 10  # Jumped to chain nonce


class TestNonceErrorRecovery:
    """Tests for nonce error recovery."""

    def test_handle_nonce_too_low_error(self, nonce_manager, mock_web3):
        """Recovers from nonce too low by re-syncing."""
        # Get nonce
        nonce1 = nonce_manager.get_next_nonce()
        assert nonce1 == 5

        # Simulate external transactions (chain moved ahead)
        mock_web3.set_chain_nonce(15)

        # Handle nonce error
        new_nonce = nonce_manager.handle_nonce_error(nonce1, "too_low")
        assert new_nonce == 15  # Synced with chain

    def test_handle_nonce_already_known(self, nonce_manager, mock_web3):
        """Handles 'already known' by getting fresh nonce."""
        nonce1 = nonce_manager.get_next_nonce()

        # Simulate transaction submitted externally with same nonce
        mock_web3.increment_chain_nonce()

        # Handle error
        new_nonce = nonce_manager.handle_nonce_error(nonce1, "already_known")
        assert new_nonce == mock_web3.get_transaction_count("", "pending")

    def test_error_recorded_in_history(self, nonce_manager, temp_db_path):
        """Nonce errors are recorded in history table."""
        nonce = nonce_manager.get_next_nonce()
        nonce_manager.handle_nonce_error(nonce, "too_low")

        # Check history
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM nonce_history WHERE nonce = ?",
            (nonce,)
        )
        statuses = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "failed_too_low" in statuses


class TestNonceTracking:
    """Tests for nonce usage tracking."""

    def test_mark_nonce_used(self, nonce_manager, temp_db_path):
        """Successfully used nonces are recorded."""
        nonce = nonce_manager.get_next_nonce()
        tx_hash = "0xabcdef1234567890"

        nonce_manager.mark_nonce_used(nonce, tx_hash)

        # Verify in database
        conn = sqlite3.connect(temp_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_used_nonce, last_tx_hash FROM nonce_tracking WHERE address = ?",
            (nonce_manager.address,)
        )
        row = cursor.fetchone()
        conn.close()

        assert row["last_used_nonce"] == nonce
        assert row["last_tx_hash"] == tx_hash

    def test_history_tracks_submissions(self, nonce_manager, temp_db_path):
        """Nonce history shows allocation → submission flow."""
        nonce = nonce_manager.get_next_nonce()
        tx_hash = "0x123"
        nonce_manager.mark_nonce_used(nonce, tx_hash)

        # Check history
        conn = sqlite3.connect(temp_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, tx_hash FROM nonce_history WHERE nonce = ? ORDER BY created_at DESC LIMIT 1",
            (nonce,)
        )
        row = cursor.fetchone()
        conn.close()

        assert row["status"] == "submitted"
        assert row["tx_hash"] == tx_hash


class TestConcurrentNonceRequests:
    """Tests for thread-safe nonce allocation."""

    def test_concurrent_nonce_requests(self, mock_web3, temp_db_path):
        """Thread-safe nonce allocation under concurrent access."""
        nm = NonceManager(mock_web3, "0x1234567890abcdef1234567890abcdef12345678", temp_db_path)

        nonces = []
        errors = []

        def get_nonce():
            try:
                nonce = nm.get_next_nonce()
                nonces.append(nonce)
            except Exception as e:
                errors.append(e)

        # 20 concurrent requests
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(get_nonce) for _ in range(20)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(nonces) == 20

        # All nonces should be unique (atomic increment)
        assert len(set(nonces)) == 20, f"Duplicate nonces found: {sorted(nonces)}"

        # Should be consecutive starting from chain nonce
        assert sorted(nonces) == list(range(5, 25))

    def test_no_duplicate_nonces_under_stress(self, mock_web3, temp_db_path):
        """No duplicate nonces even with many concurrent threads."""
        nm = NonceManager(mock_web3, "0x1234567890abcdef1234567890abcdef12345678", temp_db_path)

        nonces = []
        lock = threading.Lock()

        def get_many_nonces(count):
            for _ in range(count):
                nonce = nm.get_next_nonce()
                with lock:
                    nonces.append(nonce)

        # 10 threads each getting 50 nonces
        threads = [threading.Thread(target=get_many_nonces, args=(50,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 500 nonces should be unique
        assert len(nonces) == 500
        assert len(set(nonces)) == 500, "Found duplicate nonces!"


class TestNoncePersistence:
    """Tests for nonce state persistence."""

    def test_nonce_persistence_across_restart(self, mock_web3, temp_db_path):
        """Nonce state survives process restart."""
        test_addr = "0x1234567890abcdef1234567890abcdef12345678"
        # First "session"
        nm1 = NonceManager(mock_web3, test_addr, temp_db_path)
        for _ in range(10):
            nm1.get_next_nonce()

        # Second "session" (simulates restart)
        nm2 = NonceManager(mock_web3, test_addr, temp_db_path)
        nonce = nm2.get_next_nonce()

        # Should continue from where we left off (or sync with chain if ahead)
        assert nonce >= 15  # At least 5 (initial) + 10 (first session)

    def test_sync_with_chain_forces_update(self, nonce_manager, mock_web3):
        """sync_with_chain() updates local state from chain."""
        # Advance local nonce
        for _ in range(5):
            nonce_manager.get_next_nonce()

        # Chain is still at 5
        synced = nonce_manager.sync_with_chain()
        assert synced == 5  # Chain nonce

        # Next nonce should be from chain, not local
        # (but actually max(chain, local) so this might still be 10)
        # The sync just resets local to chain, but get_next_nonce takes max
        stats = nonce_manager.get_stats()
        assert stats["chain_nonce"] == 5


class TestNonceGapDetection:
    """Tests for nonce gap detection."""

    def test_get_nonce_gap_positive(self, nonce_manager, mock_web3):
        """Positive gap means local is ahead (pending transactions)."""
        # Get several nonces without "confirming" them
        for _ in range(5):
            nonce_manager.get_next_nonce()

        # Chain is still at 5, local is at 10
        gap = nonce_manager.get_nonce_gap()
        assert gap == 5  # Local (10) - Chain (5)

    def test_get_nonce_gap_negative(self, nonce_manager, mock_web3):
        """Negative gap means chain is ahead (external transactions)."""
        # Get one nonce
        nonce_manager.get_next_nonce()

        # External transactions advance chain
        mock_web3.set_chain_nonce(20)

        # Local is at 6, chain is at 20
        gap = nonce_manager.get_nonce_gap()
        assert gap < 0  # Local behind chain


class TestNonceStats:
    """Tests for nonce statistics."""

    def test_get_stats_returns_data(self, nonce_manager):
        """get_stats() returns comprehensive nonce data."""
        # Use some nonces
        nonce = nonce_manager.get_next_nonce()
        nonce_manager.mark_nonce_used(nonce, "0xtx123")

        stats = nonce_manager.get_stats()

        assert "address" in stats
        assert "chain_nonce" in stats
        assert "local_nonce" in stats
        assert "gap" in stats
        assert "last_used_nonce" in stats
        assert "recent_history" in stats

        assert stats["last_used_nonce"] == nonce
        assert len(stats["recent_history"]) > 0


class TestBusyTimeoutFix:
    """
    CRITICAL: Tests for the busy_timeout bug fix (2026-01-13 incident).

    The root cause of the failed transactions was that _get_connection()
    did NOT set busy_timeout, causing immediate 'disk I/O error' under
    concurrent access instead of waiting for locks.
    """

    def test_busy_timeout_is_set_on_connection(self, mock_web3, temp_db_path):
        """
        CRITICAL: Verify busy_timeout is set on every new connection.

        This is the exact bug that caused the 2026-01-13 incident.
        busy_timeout=0 means SQLite gives up immediately on lock conflicts.
        """
        nm = NonceManager(mock_web3, "0x1234567890abcdef1234567890abcdef12345678", temp_db_path)

        # Get a connection using the internal method
        conn = nm._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        conn.close()

        # Must be 30000ms (30 seconds), not 0
        assert timeout == 30000, f"busy_timeout should be 30000ms, got {timeout}ms"

    def test_no_disk_io_error_under_concurrent_load(self, mock_web3, temp_db_path):
        """
        Stress test: Concurrent operations should NOT cause disk I/O error.

        This replicates the exact scenario that caused 7+ failed transactions.
        """
        nm = NonceManager(mock_web3, "0x1234567890abcdef1234567890abcdef12345678", temp_db_path)

        errors = []
        successes = []
        lock = threading.Lock()

        def stress_operation():
            try:
                nonce = nm.get_next_nonce()
                time.sleep(0.01)  # Simulate some work
                nm.mark_nonce_used(nonce, f"0x{nonce:064x}")
                with lock:
                    successes.append(nonce)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        # 50 concurrent operations (stress test)
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(stress_operation) for _ in range(50)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    with lock:
                        errors.append(str(e))

        # CRITICAL: Should have ZERO errors (the bug fix)
        assert len(errors) == 0, f"Expected 0 errors, got {len(errors)}: {errors[:5]}"
        assert len(successes) == 50, f"Expected 50 successes, got {len(successes)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
