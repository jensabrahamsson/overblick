"""Tests for base.py — DatabaseConfig.from_dict edge cases and execute_many default."""

from collections.abc import Sequence
from typing import Any

import pytest

from overblick.core.database.base import DatabaseBackend, DatabaseConfig, DatabaseRow


class TestDatabaseConfigFromDict:
    """Cover from_dict branches for pool, echo_sql, and pg_schema."""

    def test_should_parse_pool_settings_when_pool_dict_provided(self):
        data = {
            "backend": "postgresql",
            "pool": {"min_size": 3, "max_size": 20},
        }
        config = DatabaseConfig.from_dict(data)
        assert config.pool_min_size == 3
        assert config.pool_max_size == 20

    def test_should_parse_echo_sql_when_provided(self):
        data = {"echo_sql": True}
        config = DatabaseConfig.from_dict(data)
        assert config.echo_sql is True

    def test_should_parse_pg_schema_when_provided(self):
        data = {
            "backend": "postgresql",
            "postgresql": {"schema": "my_schema"},
        }
        config = DatabaseConfig.from_dict(data)
        assert config.pg_schema == "my_schema"


class _StubBackend(DatabaseBackend):
    """Minimal concrete backend for testing the default execute_many."""

    def __init__(self) -> None:
        super().__init__(DatabaseConfig(), "")
        self.execute_calls: list[tuple[str, Sequence[Any]]] = []

    def ph(self, position: int) -> str:
        return "?"

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        self.execute_calls.append((sql, params))
        return 1

    async def execute_returning_id(self, sql: str, params: Sequence[Any] = ()) -> int | None:
        return None

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> DatabaseRow | None:
        return None

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[DatabaseRow]:
        return []

    async def fetch_scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        return None

    async def execute_script(self, sql: str) -> None:
        pass

    async def table_exists(self, table_name: str) -> bool:
        return False


class TestDatabaseBackendExecuteMany:
    """Cover the default execute_many loop (lines 155-158)."""

    @pytest.mark.asyncio
    async def test_should_call_execute_for_each_param_set_when_execute_many_default(self):
        backend = _StubBackend()
        params_list: list[Sequence[Any]] = [(1, "a"), (2, "b"), (3, "c")]
        total = await backend.execute_many("INSERT INTO t VALUES (?, ?)", params_list)
        assert total == 3
        assert len(backend.execute_calls) == 3

    @pytest.mark.asyncio
    async def test_should_return_zero_when_execute_many_with_empty_list(self):
        backend = _StubBackend()
        total = await backend.execute_many("INSERT INTO t VALUES (?)", [])
        assert total == 0
        assert len(backend.execute_calls) == 0
