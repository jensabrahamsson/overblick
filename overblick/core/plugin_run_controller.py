from __future__ import annotations

import json
import time
from pathlib import Path


class PluginRunController:
    def __init__(
        self,
        *,
        control_file: Path | None,
        cache_ttl_seconds: float = 2.0,
    ) -> None:
        self._control_file = control_file
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, str] = {}
        self._cache_ts: float = 0.0

    async def is_plugin_stopped(self, plugin_name: str) -> bool:
        """Check if a plugin is stopped via control file."""
        if not self._control_file or not self._control_file.exists():
            return False

        # Use cache for performance
        now = time.time()
        if self._cache_ts > 0 and now - self._cache_ts < self._cache_ttl_seconds:
            return self._cache.get(plugin_name, "false") == "true"

        try:
            with open(self._control_file, "r") as f:
                data = json.load(f)
                value = data.get(plugin_name, "false")
                self._cache[plugin_name] = value
                self._cache_ts = now
                return value == "true"
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return False

    def invalidate(self) -> None:
        """Invalidate the cache."""
        self._cache.clear()
        self._cache_ts = 0.0
