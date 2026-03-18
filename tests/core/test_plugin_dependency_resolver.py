"""Tests for plugin dependency resolution and topological ordering."""

import pytest

from overblick.core.plugin_dependency_resolver import PluginDependencyResolver


class _FakeRegistry:
    """Registry that returns fake plugin classes with configurable REQUIRES_PLUGINS."""

    def __init__(self, requires_map: dict[str, list[str]]):
        self._requires_map = requires_map

    def get_plugin_class(self, name: str):
        requires = self._requires_map.get(name, [])

        class _Plugin:
            REQUIRES_PLUGINS = requires

        _Plugin.__name__ = f"Plugin_{name}"
        return _Plugin


class TestPluginDependencyResolver:
    def test_dependency_loads_before_dependent(self):
        """A requires B => B loads first."""
        registry = _FakeRegistry({"A": ["B"], "B": []})
        resolver = PluginDependencyResolver(registry)

        result = resolver.resolve(["A", "B"])

        assert result.index("B") < result.index("A")

    def test_transitive_dependency_order(self):
        """A requires B, B requires C => C, B, A."""
        registry = _FakeRegistry({"A": ["B"], "B": ["C"], "C": []})
        resolver = PluginDependencyResolver(registry)

        result = resolver.resolve(["A", "B", "C"])

        assert result.index("C") < result.index("B")
        assert result.index("B") < result.index("A")

    def test_cycle_raises_value_error(self):
        """Circular dependency raises ValueError."""
        registry = _FakeRegistry({"A": ["B"], "B": ["A"]})
        resolver = PluginDependencyResolver(registry)

        with pytest.raises(ValueError, match="Cycle detected"):
            resolver.resolve(["A", "B"])

    def test_unknown_dependency_is_ignored(self):
        """A requires X (not in plugin_names) => X ignored, A still loads."""
        registry = _FakeRegistry({"A": ["X"]})
        resolver = PluginDependencyResolver(registry)

        result = resolver.resolve(["A"])

        assert result == ["A"]

    def test_no_dependencies_preserves_input_order(self):
        """Plugins without dependencies come out in input order."""
        registry = _FakeRegistry({"A": [], "B": [], "C": []})
        resolver = PluginDependencyResolver(registry)

        result = resolver.resolve(["A", "B", "C"])

        assert result == ["A", "B", "C"]

    def test_diamond_dependency(self):
        """A requires B and C, both require D => D first, then B/C, then A."""
        registry = _FakeRegistry({"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []})
        resolver = PluginDependencyResolver(registry)

        result = resolver.resolve(["A", "B", "C", "D"])

        assert result.index("D") < result.index("B")
        assert result.index("D") < result.index("C")
        assert result.index("B") < result.index("A")
        assert result.index("C") < result.index("A")

    def test_plugin_that_fails_to_load_is_skipped(self):
        """If registry.get_plugin_class raises, plugin is included but unordered."""
        registry = _FakeRegistry({"A": []})
        # Override to make B fail to load
        original_load = registry.get_plugin_class

        def failing_load(name):
            if name == "B":
                raise ImportError("no such plugin")
            return original_load(name)

        registry.get_plugin_class = failing_load

        resolver = PluginDependencyResolver(registry)
        result = resolver.resolve(["A", "B"])

        assert set(result) == {"A", "B"}
