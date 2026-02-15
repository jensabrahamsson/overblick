"""
Tests for the plugin registry — discovery, loading, and security.

Covers:
- Loading known plugins successfully
- Rejecting unknown plugin names
- Handling bad module paths (ImportError)
- Rejecting classes not inheriting PluginBase (TypeError)
- Registering new plugins dynamically
- Getting loaded vs unloaded plugins
- Listing all loaded plugins
- Listing available plugin names (sorted)
- Security: no dynamic imports from arbitrary user input
"""

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.plugin_registry import (
    PluginRegistry,
    _KNOWN_PLUGINS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin_ctx(tmp_path):
    """Minimal PluginContext for testing plugin instantiation."""
    return PluginContext(
        identity_name="test-agent",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def registry():
    """Fresh PluginRegistry instance."""
    return PluginRegistry()


@pytest.fixture
def _cleanup_known_plugins():
    """
    Snapshot _KNOWN_PLUGINS before the test and restore afterward.

    register() mutates the module-level dict, so we must restore it
    to avoid leaking test registrations across tests.
    """
    snapshot = dict(_KNOWN_PLUGINS)
    yield
    _KNOWN_PLUGINS.clear()
    _KNOWN_PLUGINS.update(snapshot)


# ---------------------------------------------------------------------------
# Mock plugin for testing
# ---------------------------------------------------------------------------


class MockPlugin(PluginBase):
    """Concrete plugin used by tests that need a loadable class."""

    async def setup(self) -> None:
        pass

    async def tick(self) -> None:
        pass


class NotAPlugin:
    """A class that does NOT inherit from PluginBase."""

    def __init__(self, ctx):
        self.ctx = ctx


# ---------------------------------------------------------------------------
# Tests: load()
# ---------------------------------------------------------------------------


class TestPluginRegistryLoad:
    """Tests for PluginRegistry.load()."""

    def test_load_known_plugin_success(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """Loading a known plugin should import it, instantiate it, and store it."""
        # Create a fake module containing MockPlugin
        fake_module = types.ModuleType("fake_plugin_module")
        fake_module.MockPlugin = MockPlugin

        registry.register("mock_test", "fake_plugin_module", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            plugin = registry.load("mock_test", plugin_ctx)

        assert isinstance(plugin, PluginBase)
        assert isinstance(plugin, MockPlugin)
        assert plugin.ctx is plugin_ctx
        assert plugin.ctx.identity_name == "test-agent"

    def test_load_stores_plugin_in_loaded(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """After loading, the plugin should be retrievable via get()."""
        fake_module = types.ModuleType("fake_plugin_module")
        fake_module.MockPlugin = MockPlugin

        registry.register("mock_store", "fake_plugin_module", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            plugin = registry.load("mock_store", plugin_ctx)

        assert registry.get("mock_store") is plugin

    def test_load_unknown_plugin_raises_value_error(self, registry, plugin_ctx):
        """Attempting to load an unregistered plugin name must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown plugin"):
            registry.load("nonexistent_plugin_xyz", plugin_ctx)

    def test_load_unknown_plugin_error_lists_available(self, registry, plugin_ctx):
        """The ValueError message should list available plugin names."""
        with pytest.raises(ValueError) as exc_info:
            registry.load("bogus", plugin_ctx)
        # The error message should contain at least one known plugin name
        assert "Available:" in str(exc_info.value)
        assert "telegram" in str(exc_info.value)

    def test_load_bad_module_path_raises_import_error(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """A bad module path should raise ImportError."""
        registry.register(
            "bad_module",
            "overblick.plugins.this_does_not_exist",
            "FakeClass",
        )
        with pytest.raises(ImportError, match="Failed to load plugin 'bad_module'"):
            registry.load("bad_module", plugin_ctx)

    def test_load_bad_class_name_raises_import_error(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """A valid module but missing class name should raise ImportError (AttributeError wrapped)."""
        fake_module = types.ModuleType("fake_module_no_class")
        # Module exists but does not have the expected class attribute

        registry.register("bad_class", "fake_module_no_class", "DoesNotExist")

        with patch("importlib.import_module", return_value=fake_module):
            with pytest.raises(ImportError, match="Failed to load plugin 'bad_class'"):
                registry.load("bad_class", plugin_ctx)

    def test_load_non_plugin_class_raises_type_error(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """A class that does not inherit PluginBase must raise TypeError."""
        fake_module = types.ModuleType("fake_module_bad_class")
        fake_module.NotAPlugin = NotAPlugin

        registry.register("not_plugin", "fake_module_bad_class", "NotAPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            with pytest.raises(TypeError, match="must inherit from PluginBase"):
                registry.load("not_plugin", plugin_ctx)

    def test_load_multiple_plugins(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """Loading multiple distinct plugins should all be tracked."""
        fake_module = types.ModuleType("fake_multi")
        fake_module.MockPlugin = MockPlugin

        registry.register("multi_a", "fake_multi", "MockPlugin")
        registry.register("multi_b", "fake_multi", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            a = registry.load("multi_a", plugin_ctx)
            b = registry.load("multi_b", plugin_ctx)

        assert registry.get("multi_a") is a
        assert registry.get("multi_b") is b
        assert a is not b  # Separate instances

    def test_load_same_plugin_twice_overwrites(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """Loading the same plugin name twice should overwrite the first instance."""
        fake_module = types.ModuleType("fake_overwrite")
        fake_module.MockPlugin = MockPlugin

        registry.register("dup", "fake_overwrite", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            first = registry.load("dup", plugin_ctx)
            second = registry.load("dup", plugin_ctx)

        assert registry.get("dup") is second
        assert first is not second


# ---------------------------------------------------------------------------
# Tests: register()
# ---------------------------------------------------------------------------


class TestPluginRegistryRegister:
    """Tests for PluginRegistry.register()."""

    def test_register_adds_to_known(self, registry, _cleanup_known_plugins):
        """register() should add a new entry to the global _KNOWN_PLUGINS dict."""
        registry.register("custom_test", "my.custom.module", "CustomClass")
        assert "custom_test" in _KNOWN_PLUGINS
        assert _KNOWN_PLUGINS["custom_test"] == ("my.custom.module", "CustomClass")

    def test_register_makes_plugin_loadable(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """A registered plugin should be loadable via load()."""
        fake_module = types.ModuleType("custom_module")
        fake_module.MockPlugin = MockPlugin

        registry.register("custom_loadable", "custom_module", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            plugin = registry.load("custom_loadable", plugin_ctx)

        assert isinstance(plugin, MockPlugin)

    def test_register_appears_in_available_plugins(
        self, registry, _cleanup_known_plugins
    ):
        """A newly registered plugin should appear in available_plugins()."""
        registry.register("zzz_test_plugin", "some.module", "SomeClass")
        available = PluginRegistry.available_plugins()
        assert "zzz_test_plugin" in available

    def test_register_overwrites_existing(self, registry, _cleanup_known_plugins):
        """Registering with the same name should overwrite the previous entry."""
        registry.register("overwrite_me", "old.module", "OldClass")
        registry.register("overwrite_me", "new.module", "NewClass")
        assert _KNOWN_PLUGINS["overwrite_me"] == ("new.module", "NewClass")


# ---------------------------------------------------------------------------
# Tests: get()
# ---------------------------------------------------------------------------


class TestPluginRegistryGet:
    """Tests for PluginRegistry.get()."""

    def test_get_loaded_plugin(self, registry, plugin_ctx, _cleanup_known_plugins):
        """get() should return a loaded plugin instance."""
        fake_module = types.ModuleType("fake_get")
        fake_module.MockPlugin = MockPlugin

        registry.register("get_test", "fake_get", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            loaded = registry.load("get_test", plugin_ctx)

        assert registry.get("get_test") is loaded

    def test_get_unloaded_returns_none(self, registry):
        """get() should return None for plugins that have not been loaded."""
        assert registry.get("telegram") is None
        assert registry.get("nonexistent") is None

    def test_get_does_not_trigger_load(self, registry):
        """get() should never trigger an import or instantiation."""
        # Ensure no side effects — just dict lookup
        result = registry.get("telegram")
        assert result is None
        assert "telegram" not in registry.all_loaded()


# ---------------------------------------------------------------------------
# Tests: all_loaded()
# ---------------------------------------------------------------------------


class TestPluginRegistryAllLoaded:
    """Tests for PluginRegistry.all_loaded()."""

    def test_all_loaded_empty_initially(self, registry):
        """A new registry should have no loaded plugins."""
        assert registry.all_loaded() == {}

    def test_all_loaded_returns_copy(self, registry):
        """all_loaded() should return a copy, not the internal dict."""
        loaded = registry.all_loaded()
        loaded["hacked"] = "injected"
        assert "hacked" not in registry.all_loaded()

    def test_all_loaded_contains_loaded_plugins(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """all_loaded() should contain all plugins loaded so far."""
        fake_module = types.ModuleType("fake_all")
        fake_module.MockPlugin = MockPlugin

        registry.register("all_a", "fake_all", "MockPlugin")
        registry.register("all_b", "fake_all", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            registry.load("all_a", plugin_ctx)
            registry.load("all_b", plugin_ctx)

        loaded = registry.all_loaded()
        assert set(loaded.keys()) == {"all_a", "all_b"}
        assert all(isinstance(p, MockPlugin) for p in loaded.values())


# ---------------------------------------------------------------------------
# Tests: available_plugins()
# ---------------------------------------------------------------------------


class TestPluginRegistryAvailablePlugins:
    """Tests for PluginRegistry.available_plugins()."""

    def test_returns_sorted_list(self):
        """available_plugins() should return plugin names in sorted order."""
        available = PluginRegistry.available_plugins()
        assert available == sorted(available)

    def test_contains_core_plugins(self):
        """The default registry should contain all core plugin names."""
        available = PluginRegistry.available_plugins()
        expected_core = [
            "ai_digest",
            "discord",
            "gmail",
            "matrix",
            "moltbook",
            "rss",
            "telegram",
            "webhook",
            "host_health",
            "email_agent",
        ]
        for name in expected_core:
            assert name in available, f"Core plugin '{name}' missing from available list"

    def test_returns_list_type(self):
        """available_plugins() should return a plain list of strings."""
        available = PluginRegistry.available_plugins()
        assert isinstance(available, list)
        assert all(isinstance(name, str) for name in available)

    def test_is_static_method(self):
        """available_plugins() should be callable without an instance."""
        # Verify it works as a static method (no self required)
        result = PluginRegistry.available_plugins()
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tests: Security
# ---------------------------------------------------------------------------


class TestPluginRegistrySecurity:
    """Security-focused tests for the plugin registry."""

    def test_rejects_arbitrary_plugin_names(self, registry, plugin_ctx):
        """Arbitrary user-supplied names must be rejected (whitelist enforcement)."""
        malicious_names = [
            "../../etc/passwd",
            "<script>alert(1)</script>",
            "'; DROP TABLE plugins; --",
            "random_unknown_plugin",
            "",
        ]
        for name in malicious_names:
            with pytest.raises(ValueError, match="Unknown plugin"):
                registry.load(name, plugin_ctx)

    def test_no_arbitrary_import_paths(self, registry, plugin_ctx):
        """
        The registry should only import from paths in _KNOWN_PLUGINS.

        importlib.import_module should NOT be called for unknown names.
        """
        with patch("importlib.import_module") as mock_import:
            with pytest.raises(ValueError):
                registry.load("not_in_whitelist", plugin_ctx)
            # import_module should NOT have been called at all
            mock_import.assert_not_called()

    def test_subclass_check_blocks_arbitrary_classes(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """Even a successfully imported class must inherit from PluginBase."""
        fake_module = types.ModuleType("evil_module")

        class EvilClass:
            """A class pretending to be a plugin."""
            def __init__(self, ctx):
                pass

        fake_module.EvilClass = EvilClass
        registry.register("evil", "evil_module", "EvilClass")

        with patch("importlib.import_module", return_value=fake_module):
            with pytest.raises(TypeError, match="must inherit from PluginBase"):
                registry.load("evil", plugin_ctx)

    def test_known_plugins_is_static_whitelist(self):
        """_KNOWN_PLUGINS should be a plain dict with no dynamic generation."""
        assert isinstance(_KNOWN_PLUGINS, dict)
        # All values should be (module_path, class_name) tuples
        for name, value in _KNOWN_PLUGINS.items():
            assert isinstance(value, tuple), f"Entry '{name}' is not a tuple"
            assert len(value) == 2, f"Entry '{name}' has wrong length"
            module_path, class_name = value
            assert isinstance(module_path, str)
            assert isinstance(class_name, str)
            # Module paths must start with overblick.plugins
            assert module_path.startswith("overblick.plugins."), (
                f"Plugin '{name}' has unexpected module path: {module_path}"
            )

    def test_load_does_not_use_eval_or_exec(self, registry, plugin_ctx, _cleanup_known_plugins):
        """Verify the load path uses importlib, not eval or exec."""
        # We patch the importlib used inside the registry module and verify
        # that it is called (confirming importlib is the import mechanism)
        fake_module = types.ModuleType("safe_module")
        fake_module.MockPlugin = MockPlugin

        registry.register("safe_test", "safe_module", "MockPlugin")

        with patch("overblick.core.plugin_registry.importlib.import_module",
                    return_value=fake_module) as mock_import:
            plugin = registry.load("safe_test", plugin_ctx)
            mock_import.assert_called_once_with("safe_module")


# ---------------------------------------------------------------------------
# Tests: Instance isolation
# ---------------------------------------------------------------------------


class TestPluginRegistryIsolation:
    """Tests verifying that registry instances are isolated."""

    def test_separate_instances_have_separate_loaded(
        self, plugin_ctx, _cleanup_known_plugins
    ):
        """Two PluginRegistry instances should not share loaded plugins."""
        reg_a = PluginRegistry()
        reg_b = PluginRegistry()

        fake_module = types.ModuleType("fake_isolation")
        fake_module.MockPlugin = MockPlugin

        reg_a.register("iso_test", "fake_isolation", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            reg_a.load("iso_test", plugin_ctx)

        # reg_b should not have the plugin loaded
        assert reg_a.get("iso_test") is not None
        assert reg_b.get("iso_test") is None

    def test_available_plugins_shared_across_instances(self, _cleanup_known_plugins):
        """
        available_plugins() reads from the module-level dict,
        so registrations are visible across all instances.
        """
        reg_a = PluginRegistry()
        reg_b = PluginRegistry()

        reg_a.register("shared_vis", "some.module", "SomeClass")
        # Both instances should see it (static method reads module-level dict)
        assert "shared_vis" in reg_b.available_plugins()
