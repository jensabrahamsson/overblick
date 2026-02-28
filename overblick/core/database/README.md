# Database Layer

## Overview

Abstract database backend with pluggable implementations. Supports SQLite (default, zero-config) and PostgreSQL (production scale). Includes a migration system for schema evolution.

## Architecture

```
DatabaseBackend (abstract)
    ├── SQLiteBackend    — File-based, WAL mode, per-call read connections
    └── PostgresBackend  — asyncpg-based, connection pooling
```

## Components

### DatabaseBackend (`base.py`)

Abstract base class defining the interface: `execute()`, `fetch_one()`, `fetch_all()`, `table_exists()`, `connect()`, `close()`.

### SQLiteBackend (`sqlite_backend.py`)

Thread-safe SQLite implementation using WAL mode. Write operations go through a single `sqlite3.Connection`, while read operations open per-call connections for safe concurrent access from the async executor. The executor uses `ThreadPoolExecutor` to avoid blocking the event loop.

### PostgresBackend (`pg_backend.py`)

PostgreSQL backend using `asyncpg` with connection pooling. Optional dependency — gracefully absent if asyncpg is not installed.

### Factory (`factory.py`)

Creates the appropriate backend based on configuration:
```python
backend = create_backend(config)  # Returns SQLiteBackend or PostgresBackend
```

### Migrations (`migrations.py`)

Ordered list of SQL migrations applied idempotently on startup. Each migration runs inside a transaction. No rollback support (forward-only by design).

## Thread Safety

The SQLite backend was designed for concurrent access:
- **Writes**: Single connection via `ThreadPoolExecutor(max_workers=1)`
- **Reads**: Per-call connections opened in executor threads (WAL mode allows concurrent readers)
- **Shutdown**: `shutdown(wait=True)` ensures pending writes complete before closing

## Configuration

```yaml
database:
  backend: sqlite  # or postgresql
  path: data/overblick.db  # SQLite path
  # PostgreSQL:
  # host: localhost
  # port: 5432
  # name: overblick
```
