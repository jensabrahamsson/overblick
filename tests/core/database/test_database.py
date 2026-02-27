"""Tests for the database abstraction layer."""

import pytest
from pathlib import Path

from overblick.core.database.base import (
    DatabaseConfig,
    DatabaseRow,
    Migration,
    MigrationManager,
)
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.database.factory import create_backend
from overblick.core.database.migrations import MIGRATIONS


# ---------------------------------------------------------------------------
# DatabaseConfig
# ---------------------------------------------------------------------------

class TestDatabaseConfig:
    def test_defaults(self):
        config = DatabaseConfig()
        assert config.backend == "sqlite"
        assert "{identity}" in config.sqlite_path
        assert config.pg_host == "localhost"
        assert config.pg_port == 5432

    def test_from_dict_sqlite(self):
        data = {
            "backend": "sqlite",
            "sqlite": {"path": "data/test.db"},
        }
        config = DatabaseConfig.from_dict(data)
        assert config.backend == "sqlite"
        assert config.sqlite_path == "data/test.db"

    def test_from_dict_postgresql(self):
        data = {
            "backend": "postgresql",
            "postgresql": {
                "host": "db.example.com",
                "port": 5433,
                "database": "mydb",
                "user": "myuser",
                "password": "secret",
            },
        }
        config = DatabaseConfig.from_dict(data)
        assert config.backend == "postgresql"
        assert config.pg_host == "db.example.com"
        assert config.pg_port == 5433
        assert config.pg_database == "mydb"

    def test_from_dict_empty(self):
        config = DatabaseConfig.from_dict({})
        assert config.backend == "sqlite"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_create_sqlite(self):
        db = create_backend({"backend": "sqlite", "sqlite": {"path": "/tmp/test.db"}})
        assert isinstance(db, SQLiteBackend)

    def test_create_default(self):
        db = create_backend()
        assert isinstance(db, SQLiteBackend)

    def test_create_from_config(self):
        config = DatabaseConfig(backend="sqlite", sqlite_path="/tmp/test.db")
        db = create_backend(config)
        assert isinstance(db, SQLiteBackend)

    def test_create_unknown_backend(self):
        with pytest.raises(ValueError, match="Unknown database backend"):
            create_backend({"backend": "mongodb"})

    def test_create_postgresql_no_asyncpg(self):
        """PostgreSQL backend raises ImportError if asyncpg not installed."""
        try:
            import asyncpg
            pytest.skip("asyncpg is installed")
        except ImportError:
            with pytest.raises(ImportError, match="asyncpg"):
                create_backend({"backend": "postgresql"})


# ---------------------------------------------------------------------------
# SQLiteBackend
# ---------------------------------------------------------------------------

class TestSQLiteBackend:
    @pytest.fixture
    async def db(self, tmp_path):
        """Create and connect a test SQLite database."""
        config = DatabaseConfig(sqlite_path=str(tmp_path / "test.db"))
        backend = SQLiteBackend(config)
        await backend.connect()
        yield backend
        await backend.close()

    @pytest.mark.asyncio
    async def test_connect_creates_file(self, tmp_path):
        config = DatabaseConfig(sqlite_path=str(tmp_path / "new.db"))
        backend = SQLiteBackend(config)
        await backend.connect()
        assert backend.connected
        assert (tmp_path / "new.db").exists()
        await backend.close()
        assert not backend.connected

    @pytest.mark.asyncio
    async def test_placeholder(self, db):
        assert db.ph(1) == "?"
        assert db.ph(2) == "?"

    @pytest.mark.asyncio
    async def test_backend_name(self, db):
        assert db.backend_name == "sqlite"

    @pytest.mark.asyncio
    async def test_execute_script(self, db):
        await db.execute_script("""
            CREATE TABLE test_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                value INTEGER
            );
        """)
        assert await db.table_exists("test_items")

    @pytest.mark.asyncio
    async def test_execute(self, db):
        await db.execute_script("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT);")
        rows = await db.execute("INSERT INTO t (name) VALUES (?)", ("hello",))
        assert rows == 1

    @pytest.mark.asyncio
    async def test_execute_returning_id(self, db):
        await db.execute_script("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);")
        row_id = await db.execute_returning_id("INSERT INTO t (name) VALUES (?)", ("first",))
        assert row_id == 1
        row_id2 = await db.execute_returning_id("INSERT INTO t (name) VALUES (?)", ("second",))
        assert row_id2 == 2

    @pytest.mark.asyncio
    async def test_fetch_one(self, db):
        await db.execute_script("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT);")
        await db.execute("INSERT INTO t (id, name) VALUES (?, ?)", (1, "hello"))
        row = await db.fetch_one("SELECT * FROM t WHERE id = ?", (1,))
        assert row is not None
        assert row["name"] == "hello"

    @pytest.mark.asyncio
    async def test_fetch_one_empty(self, db):
        await db.execute_script("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT);")
        row = await db.fetch_one("SELECT * FROM t WHERE id = ?", (999,))
        assert row is None

    @pytest.mark.asyncio
    async def test_fetch_all(self, db):
        await db.execute_script("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT);")
        await db.execute("INSERT INTO t (id, name) VALUES (?, ?)", (1, "a"))
        await db.execute("INSERT INTO t (id, name) VALUES (?, ?)", (2, "b"))
        rows = await db.fetch_all("SELECT * FROM t ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["name"] == "a"
        assert rows[1]["name"] == "b"

    @pytest.mark.asyncio
    async def test_fetch_scalar(self, db):
        await db.execute_script("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT);")
        await db.execute("INSERT INTO t (id, name) VALUES (?, ?)", (1, "x"))
        count = await db.fetch_scalar("SELECT COUNT(*) FROM t")
        assert count == 1

    @pytest.mark.asyncio
    async def test_table_exists(self, db):
        assert not await db.table_exists("nonexistent")
        await db.execute_script("CREATE TABLE exists_test (id INTEGER);")
        assert await db.table_exists("exists_test")

    @pytest.mark.asyncio
    async def test_not_connected_error(self, tmp_path):
        config = DatabaseConfig(sqlite_path=str(tmp_path / "nc.db"))
        backend = SQLiteBackend(config)
        with pytest.raises(RuntimeError, match="not connected"):
            await backend.execute("SELECT 1")

    @pytest.mark.asyncio
    async def test_identity_path_template(self, tmp_path):
        config = DatabaseConfig(sqlite_path=str(tmp_path / "{identity}" / "overblick.db"))
        backend = SQLiteBackend(config, identity="anomal")
        await backend.connect()
        assert backend.db_path == tmp_path / "anomal" / "overblick.db"
        assert backend.db_path.exists()
        await backend.close()

    @pytest.mark.asyncio
    async def test_execute_many(self, db):
        """Batch insert via execute_many (Pass 4, fix 4.4)."""
        await db.execute_script("CREATE TABLE batch (id INTEGER PRIMARY KEY, val TEXT);")
        params = [(1, "a"), (2, "b"), (3, "c")]
        count = await db.execute_many(
            "INSERT INTO batch (id, val) VALUES (?, ?)", params
        )
        assert count == 3
        rows = await db.fetch_all("SELECT * FROM batch ORDER BY id")
        assert len(rows) == 3
        assert rows[0]["val"] == "a"
        assert rows[2]["val"] == "c"

    @pytest.mark.asyncio
    async def test_execute_many_empty(self, db):
        """execute_many with empty list returns 0."""
        await db.execute_script("CREATE TABLE empty_batch (id INTEGER PRIMARY KEY);")
        count = await db.execute_many("INSERT INTO empty_batch (id) VALUES (?)", [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_read_executor_exists(self, db):
        """SQLite backend has a separate read executor (Pass 4, fix 4.3)."""
        assert hasattr(db, "_read_executor")
        assert db._read_executor._max_workers == 3

    @pytest.mark.asyncio
    async def test_reads_use_read_executor(self, db):
        """Read operations use the dedicated read executor."""
        await db.execute_script("CREATE TABLE cr (id INTEGER PRIMARY KEY, val TEXT);")
        await db.execute("INSERT INTO cr (id, val) VALUES (?, ?)", (1, "hello"))

        # Verify sequential reads work through the read executor
        row = await db.fetch_one("SELECT * FROM cr WHERE id = ?", (1,))
        assert row["val"] == "hello"

        rows = await db.fetch_all("SELECT * FROM cr")
        assert len(rows) == 1

        count = await db.fetch_scalar("SELECT COUNT(*) FROM cr")
        assert count == 1


# ---------------------------------------------------------------------------
# MigrationManager
# ---------------------------------------------------------------------------

class TestMigrationManager:
    @pytest.fixture
    async def db(self, tmp_path):
        config = DatabaseConfig(sqlite_path=str(tmp_path / "migrate.db"))
        backend = SQLiteBackend(config)
        await backend.connect()
        yield backend
        await backend.close()

    @pytest.mark.asyncio
    async def test_initial_version(self, db):
        mgr = MigrationManager(db)
        assert await mgr.current_version() == 0

    @pytest.mark.asyncio
    async def test_apply_single_migration(self, db):
        mgr = MigrationManager(db)
        migrations = [
            Migration(
                version=1,
                name="create_test_table",
                up_sql="CREATE TABLE test_m (id INTEGER PRIMARY KEY, val TEXT);",
            )
        ]
        applied = await mgr.apply(migrations)
        assert applied == 1
        assert await mgr.current_version() == 1
        assert await db.table_exists("test_m")

    @pytest.mark.asyncio
    async def test_apply_multiple_migrations(self, db):
        mgr = MigrationManager(db)
        migrations = [
            Migration(version=1, name="first",
                      up_sql="CREATE TABLE m1 (id INTEGER PRIMARY KEY);"),
            Migration(version=2, name="second",
                      up_sql="CREATE TABLE m2 (id INTEGER PRIMARY KEY);"),
            Migration(version=3, name="third",
                      up_sql="CREATE TABLE m3 (id INTEGER PRIMARY KEY);"),
        ]
        applied = await mgr.apply(migrations)
        assert applied == 3
        assert await mgr.current_version() == 3

    @pytest.mark.asyncio
    async def test_skip_applied_migrations(self, db):
        mgr = MigrationManager(db)
        migrations = [
            Migration(version=1, name="first",
                      up_sql="CREATE TABLE skip1 (id INTEGER PRIMARY KEY);"),
            Migration(version=2, name="second",
                      up_sql="CREATE TABLE skip2 (id INTEGER PRIMARY KEY);"),
        ]
        await mgr.apply(migrations)

        # Apply again — should skip both
        applied = await mgr.apply(migrations)
        assert applied == 0
        assert await mgr.current_version() == 2

    @pytest.mark.asyncio
    async def test_incremental_migration(self, db):
        mgr = MigrationManager(db)

        # Apply first batch
        batch1 = [
            Migration(version=1, name="first",
                      up_sql="CREATE TABLE inc1 (id INTEGER PRIMARY KEY);"),
        ]
        await mgr.apply(batch1)
        assert await mgr.current_version() == 1

        # Apply second batch (includes old + new)
        batch2 = batch1 + [
            Migration(version=2, name="second",
                      up_sql="CREATE TABLE inc2 (id INTEGER PRIMARY KEY);"),
        ]
        applied = await mgr.apply(batch2)
        assert applied == 1  # Only the new one
        assert await mgr.current_version() == 2

    @pytest.mark.asyncio
    async def test_real_migrations(self, db):
        """Apply the actual Överblick migrations."""
        mgr = MigrationManager(db)
        applied = await mgr.apply(MIGRATIONS)
        assert applied == len(MIGRATIONS)
        assert await mgr.current_version() == MIGRATIONS[-1].version

        # Verify key tables exist
        for table in ["engagements", "audit_log", "dreams", "therapy_sessions",
                       "learnings", "emotional_snapshots", "seen_posts",
                       "agent_audits", "prompt_tweaks"]:
            assert await db.table_exists(table), f"Table {table} should exist"

    @pytest.mark.asyncio
    async def test_real_migrations_idempotent(self, db):
        """Applying real migrations twice is safe."""
        mgr = MigrationManager(db)
        await mgr.apply(MIGRATIONS)
        applied = await mgr.apply(MIGRATIONS)
        assert applied == 0
