from __future__ import annotations

from typing import Any

from overblick.core.plugin_registry import PluginRegistry


class PluginDependencyResolver:
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def resolve(self, plugin_names: list[str]) -> list[str]:
        """
        Resolve plugin dependencies and return topologically sorted list.

        Plugins that declare `REQUIRES_PLUGINS` are ordered after their dependencies.
        Cycles raise ValueError.
        """
        # Build dependency-to-dependent graph (edges: dep → name)
        graph: dict[str, list[str]] = {name: [] for name in plugin_names}
        all_plugins = set(plugin_names)
        in_degree = {name: 0 for name in plugin_names}

        for name in plugin_names:
            try:
                plugin_cls = self._registry.load_plugin(name)
                requires = getattr(plugin_cls, "REQUIRES_PLUGINS", [])
                for dep in requires:
                    if dep in all_plugins:
                        graph[dep].append(name)
                        in_degree[name] += 1
            except Exception:
                # Let PluginLoader handle actual load failures later
                continue

        queue = [name for name in plugin_names if in_degree[name] == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            for neighbor in graph.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(plugin_names):
            raise ValueError("Cycle detected in plugin dependencies")

        return result
