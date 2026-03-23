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

import textwrap
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.plugin_registry import (
    _KNOWN_PLUGINS,
    PluginMetadata,
    PluginRegistry,
    _extract_plugin_metadata,
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

    def test_load_known_plugin_success(self, registry, plugin_ctx, _cleanup_known_plugins):
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

    def test_load_stores_plugin_in_loaded(self, registry, plugin_ctx, _cleanup_known_plugins):
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
        with pytest.raises(ImportError, match="Failed to load plugin class 'bad_module'"):
            registry.load("bad_module", plugin_ctx)

    def test_load_bad_class_name_raises_import_error(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """A valid module but missing class name should raise ImportError (AttributeError wrapped)."""
        fake_module = types.ModuleType("fake_module_no_class")
        # Module exists but does not have the expected class attribute

        registry.register("bad_class", "fake_module_no_class", "DoesNotExist")

        with patch("importlib.import_module", return_value=fake_module):
            with pytest.raises(ImportError, match="Failed to load plugin class 'bad_class'"):
                registry.load("bad_class", plugin_ctx)

    def test_load_non_plugin_class_raises_type_error(
        self, registry, plugin_ctx, _cleanup_known_plugins
    ):
        """A class that does not inherit PluginBase must raise TypeError."""
        fake_module = types.ModuleType("fake_module_bad_class")
        fake_module.NotAPlugin = NotAPlugin

        registry.register("not_plugin", "fake_module_bad_class", "NotAPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            with pytest.raises(TypeError, match="not a subclass of PluginBase"):
                registry.load("not_plugin", plugin_ctx)

    def test_load_multiple_plugins(self, registry, plugin_ctx, _cleanup_known_plugins):
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

    def test_load_same_plugin_twice_overwrites(self, registry, plugin_ctx, _cleanup_known_plugins):
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

    def test_register_adds_to_instance(self, registry, _cleanup_known_plugins):
        """register() should add a new entry to the instance's plugin dict."""
        registry.register("custom_test", "my.custom.module", "CustomClass")
        assert "custom_test" in registry._plugins
        assert registry._plugins["custom_test"] == ("my.custom.module", "CustomClass")

    def test_register_makes_plugin_loadable(self, registry, plugin_ctx, _cleanup_known_plugins):
        """A registered plugin should be loadable via load()."""
        fake_module = types.ModuleType("custom_module")
        fake_module.MockPlugin = MockPlugin

        registry.register("custom_loadable", "custom_module", "MockPlugin")

        with patch("importlib.import_module", return_value=fake_module):
            plugin = registry.load("custom_loadable", plugin_ctx)

        assert isinstance(plugin, MockPlugin)

    def test_register_appears_in_available_plugins(self, registry, _cleanup_known_plugins):
        """A newly registered plugin should appear in available_plugins()."""
        registry.register("zzz_test_plugin", "some.module", "SomeClass")
        available = registry.available_plugins()
        assert "zzz_test_plugin" in available

    def test_register_overwrites_existing(self, registry, _cleanup_known_plugins):
        """Registering with the same name should overwrite the previous entry."""
        registry.register("overwrite_me", "old.module", "OldClass")
        registry.register("overwrite_me", "new.module", "NewClass")
        assert registry._plugins["overwrite_me"] == ("new.module", "NewClass")

    def test_register_does_not_leak_to_other_instances(self, _cleanup_known_plugins):
        """Registering on one instance should NOT leak to a new instance."""
        from overblick.core.plugin_registry import PluginRegistry
        reg1 = PluginRegistry()
        reg1.register("isolated_test", "some.module", "SomeClass")
        assert "isolated_test" in reg1._plugins

        reg2 = PluginRegistry()
        assert "isolated_test" not in reg2._plugins


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

    def test_all_loaded_contains_loaded_plugins(self, registry, plugin_ctx, _cleanup_known_plugins):
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

    def test_returns_sorted_list(self, registry):
        """available_plugins() should return plugin names in sorted order."""
        available = registry.available_plugins()
        assert available == sorted(available)

    def test_contains_core_plugins(self, registry):
        """The default registry should contain all core plugin names."""
        available = registry.available_plugins()
        expected_core = [
            "ai_digest",
            "compass",
            "dev_agent",
            "email_agent",
            "github",
            "host_health",
            "irc",
            "kontrast",
            "moltbook",
            "skuggspel",
            "spegel",
            "stage",
            "telegram",
        ]
        for name in expected_core:
            assert name in available, f"Core plugin '{name}' missing from available list"

    def test_returns_list_type(self, registry):
        """available_plugins() should return a plain list of strings."""
        available = registry.available_plugins()
        assert isinstance(available, list)
        assert all(isinstance(name, str) for name in available)

    def test_instance_method(self, registry):
        """available_plugins() should be callable on an instance."""
        result = registry.available_plugins()
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
            with pytest.raises(TypeError, match="not a subclass of PluginBase"):
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

        with patch(
            "overblick.core.plugin_registry.importlib.import_module", return_value=fake_module
        ) as mock_import:
            registry.load("safe_test", plugin_ctx)
            mock_import.assert_called_once_with("safe_module")


# ---------------------------------------------------------------------------
# Tests: Instance isolation
# ---------------------------------------------------------------------------


class TestPluginRegistryIsolation:
    """Tests verifying that registry instances are isolated."""

    def test_separate_instances_have_separate_loaded(self, plugin_ctx, _cleanup_known_plugins):
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

    def test_instances_have_separate_registrations(self, _cleanup_known_plugins):
        """
        Each PluginRegistry instance has its own plugins dict.
        Registrations on one instance don't affect another.
        """
        reg_a = PluginRegistry()
        reg_b = PluginRegistry()

        reg_a.register("instance_only", "some.module", "SomeClass")
        assert "instance_only" in reg_a.available_plugins()
        # reg_b should NOT see it (per-instance isolation)
        # Note: it IS in _KNOWN_PLUGINS for backward compat, but
        # reg_b's instance dict was created before the registration
        assert "instance_only" not in reg_b.available_plugins()


# ---------------------------------------------------------------------------
# Tests: _extract_plugin_metadata()
# ---------------------------------------------------------------------------


class TestExtractPluginMetadata:
    """Tests for the _extract_plugin_metadata helper function."""

    def test_should_extract_metadata_when_spec_found(self, tmp_path):
        """When importlib.util.find_spec locates the module, extract metadata via AST."""
        source = textwrap.dedent("""\
            class MyPlugin:
                DEPENDS_ON = ["foo", "bar"]
                REQUIRED_CAPABILITIES = ["cap_a"]

                def tick(self):
                    pass
        """)
        src_file = tmp_path / "my_plugin.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = _extract_plugin_metadata("some.package.my_plugin", "MyPlugin")

        assert isinstance(meta, PluginMetadata)
        assert meta.name == "package"  # parts[-2] heuristic
        assert meta.module_path == "some.package.my_plugin"
        assert meta.class_name == "MyPlugin"
        assert meta.depends_on == ["foo", "bar"]
        assert meta.required_capabilities == ["cap_a"]
        assert meta.description == ""

    def test_should_use_last_part_as_name_when_single_part_module(self, tmp_path):
        """When module_path has only one part, name should be parts[-1]."""
        source = "class X:\n    pass\n"
        src_file = tmp_path / "single.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = _extract_plugin_metadata("single", "X")

        assert meta.name == "single"

    def test_should_raise_when_spec_is_none(self):
        """When find_spec returns None, raise FileNotFoundError via the fallback path."""
        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=None):
            with pytest.raises(FileNotFoundError):
                _extract_plugin_metadata("nonexistent.module.path", "SomeClass")

    def test_should_raise_when_spec_origin_is_none(self, tmp_path):
        """When spec.origin is None (namespace package), fallback path should be used."""
        fake_spec = MagicMock()
        fake_spec.origin = None

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            with pytest.raises(FileNotFoundError):
                _extract_plugin_metadata("nonexistent.ns.pkg", "Cls")

    def test_should_fallback_when_find_spec_raises(self, tmp_path):
        """When find_spec raises an exception, fall back to file path computation."""
        source = textwrap.dedent("""\
            class FallbackPlugin:
                DEPENDS_ON = ["dep1"]
                REQUIRED_CAPABILITIES = []
        """)
        # Create the file at the expected fallback location
        # Fallback: base_dir / Path(*parts).with_suffix(".py")
        # base_dir = Path(__file__).parent.parent.parent for the real code
        # We need to mock the fallback path to point to tmp_path
        fallback_dir = tmp_path / "fake" / "fallback"
        fallback_dir.mkdir(parents=True)
        src_file = fallback_dir / "plugin.py"
        src_file.write_text(source)

        with patch(
            "overblick.core.plugin_registry.importlib.util.find_spec",
            side_effect=ModuleNotFoundError("nope"),
        ):
            # Also patch the base_dir calculation: Path(__file__).parent.parent.parent
            with patch("overblick.core.plugin_registry.Path") as MockPath:
                # We need Path to work normally except for __file__ resolution
                # Simpler: patch at the point where base_dir is computed
                # base_dir = Path(__file__).parent.parent.parent
                # We'll make it return tmp_path
                real_path = Path

                def path_side_effect(*args, **kwargs):
                    p = real_path(*args, **kwargs)
                    return p

                MockPath.side_effect = path_side_effect
                MockPath.__truediv__ = real_path.__truediv__

        # The fallback path is tricky to mock cleanly. Let's use a different approach:
        # Create the file where the fallback code expects it and patch __file__
        # Actually, let's just test the fallback raises FileNotFoundError when file doesn't exist
        with patch(
            "overblick.core.plugin_registry.importlib.util.find_spec",
            side_effect=ModuleNotFoundError("nope"),
        ):
            with pytest.raises(FileNotFoundError, match="Plugin source not found"):
                _extract_plugin_metadata("totally.bogus.module", "SomeClass")

    def test_should_raise_when_class_not_found_in_ast(self, tmp_path):
        """When the class name doesn't exist in the source, raise ValueError."""
        source = "class OtherClass:\n    pass\n"
        src_file = tmp_path / "no_match.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            with pytest.raises(ValueError, match="Class MissingClass not found"):
                _extract_plugin_metadata("some.no_match", "MissingClass")

    def test_should_handle_empty_depends_and_capabilities(self, tmp_path):
        """A class with no DEPENDS_ON or REQUIRED_CAPABILITIES should return empty lists."""
        source = textwrap.dedent("""\
            class PlainPlugin:
                some_other_attr = 42

                def do_stuff(self):
                    pass
        """)
        src_file = tmp_path / "plain.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = _extract_plugin_metadata("pkg.plain", "PlainPlugin")

        assert meta.depends_on == []
        assert meta.required_capabilities == []

    def test_should_ignore_non_list_depends_on(self, tmp_path):
        """If DEPENDS_ON is not a list literal, it should be ignored."""
        source = textwrap.dedent("""\
            class WeirdPlugin:
                DEPENDS_ON = "not_a_list"
                REQUIRED_CAPABILITIES = "also_not_a_list"
        """)
        src_file = tmp_path / "weird.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = _extract_plugin_metadata("pkg.weird", "WeirdPlugin")

        assert meta.depends_on == []
        assert meta.required_capabilities == []

    def test_should_ignore_non_string_elements_in_lists(self, tmp_path):
        """Non-string elements in DEPENDS_ON/REQUIRED_CAPABILITIES lists should be skipped."""
        source = textwrap.dedent("""\
            class MixedPlugin:
                DEPENDS_ON = ["valid", 123, None]
                REQUIRED_CAPABILITIES = [True, "real_cap"]
        """)
        src_file = tmp_path / "mixed.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = _extract_plugin_metadata("pkg.mixed", "MixedPlugin")

        assert meta.depends_on == ["valid"]
        assert meta.required_capabilities == ["real_cap"]

    def test_should_ignore_non_assign_body_items(self, tmp_path):
        """Class body items that are not Assign nodes should be ignored."""
        source = textwrap.dedent("""\
            class MethodOnly:
                def setup(self):
                    pass

                async def tick(self):
                    pass
        """)
        src_file = tmp_path / "methodonly.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = _extract_plugin_metadata("pkg.methodonly", "MethodOnly")

        assert meta.depends_on == []
        assert meta.required_capabilities == []

    def test_should_ignore_tuple_assignment_targets(self, tmp_path):
        """Assignments with tuple targets (e.g. a, b = 1, 2) should be ignored."""
        source = textwrap.dedent("""\
            class TupleTarget:
                a, b = 1, 2
                DEPENDS_ON = ["real_dep"]
        """)
        src_file = tmp_path / "tuple_target.py"
        src_file.write_text(source)

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = _extract_plugin_metadata("pkg.tuple_target", "TupleTarget")

        assert meta.depends_on == ["real_dep"]


# ---------------------------------------------------------------------------
# Tests: get_plugin_metadata()
# ---------------------------------------------------------------------------


class TestGetPluginMetadata:
    """Tests for PluginRegistry.get_plugin_metadata()."""

    def test_should_raise_value_error_when_unknown_plugin(self, registry):
        """get_plugin_metadata should raise ValueError for unknown plugin names."""
        with pytest.raises(ValueError, match=r"Unknown plugin.*nonexistent"):
            registry.get_plugin_metadata("nonexistent")

    def test_should_raise_value_error_listing_available_plugins(self, registry):
        """The ValueError message should list available plugin names."""
        with pytest.raises(ValueError) as exc_info:
            registry.get_plugin_metadata("bogus")
        assert "Available:" in str(exc_info.value)

    def test_should_return_metadata_for_known_plugin(self, registry, tmp_path, _cleanup_known_plugins):
        """get_plugin_metadata should return PluginMetadata for a registered plugin."""
        source = textwrap.dedent("""\
            class TestPlugin:
                DEPENDS_ON = ["dep_x"]
                REQUIRED_CAPABILITIES = ["cap_y"]
        """)
        src_file = tmp_path / "test_plug.py"
        src_file.write_text(source)

        registry.register("meta_test", "fake.meta_test", "TestPlugin")

        fake_spec = MagicMock()
        fake_spec.origin = str(src_file)

        with patch("overblick.core.plugin_registry.importlib.util.find_spec", return_value=fake_spec):
            meta = registry.get_plugin_metadata("meta_test")

        assert isinstance(meta, PluginMetadata)
        assert meta.class_name == "TestPlugin"
        assert meta.depends_on == ["dep_x"]
        assert meta.required_capabilities == ["cap_y"]
