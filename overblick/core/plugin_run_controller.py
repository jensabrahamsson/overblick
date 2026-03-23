from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class PluginRunController:
    def __init__(
        self,
        *,
        control_file: Path | None,
        cache_ttl_seconds: float = 2.0,
    ) -> None:
        self._control_file = control_file
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, Any] = {}
        self._cache_ts: float = 0.0

    @staticmethod
    def _is_stopped_value(value: object) -> bool:
        """Normalize various stopped representations to a boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "stopped", "paused", "1"}
        if isinstance(value, (int, float)):
            return bool(value)
        return False

    async def is_plugin_stopped(self, plugin_name: str) -> bool:
        """Check if a plugin is stopped via control file."""
        if not self._control_file or not self._control_file.exists():
            return False

        now = time.time()
        if self._cache_ts > 0 and now - self._cache_ts < self._cache_ttl_seconds:
            return self._is_stopped_value(self._cache.get(plugin_name, False))

        try:
            with open(self._control_file, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                self._cache = {}
                self._cache_ts = now
                return False

            self._cache = data
            self._cache_ts = now
            return self._is_stopped_value(data.get(plugin_name, False))

        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return False

    def invalidate(self) -> None:
        """Invalidate the cache."""
        self._cache.clear()
        self._cache_ts = 0.0
