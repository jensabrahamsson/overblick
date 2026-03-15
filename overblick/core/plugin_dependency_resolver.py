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
        # Build dependency graph
        graph: dict[str, list[str]] = {}
        all_plugins = set(plugin_names)

        for name in plugin_names:
            graph[name] = []
            try:
                plugin_cls = self._registry.load_plugin(name)
                requires = getattr(plugin_cls, "REQUIRES_PLUGINS", [])
                for dep in requires:
                    if dep in all_plugins:
                        graph[name].append(dep)
            except Exception as e:
                # Skip plugins that can't be loaded (e.g. during setup)
                pass

        # Simple topological sort (Kahn's algorithm)
        in_degree = {name: 0 for name in plugin_names}
        for deps in graph.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

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
