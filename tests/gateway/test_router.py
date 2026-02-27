"""
Tests for the RequestRouter.

Verifies all routing rules:
1. Explicit backend override always wins
2. complexity=high → cloud > deepseek > local
3. complexity=low → local
4. priority=high + cloud → cloud (backward compat)
5. Default fallback
"""

import pytest
from unittest.mock import MagicMock

from overblick.gateway.router import RequestRouter


def _make_registry(backends: list[str], default: str = "local"):
    """Create a mock BackendRegistry with given available backends."""
    registry = MagicMock()
    registry.available_backends = backends
    registry.default_backend = default
    return registry


class TestExplicitBackend:
    """Rule 1: Explicit backend override always wins."""

    def test_explicit_backend_exists(self):
        router = RequestRouter(_make_registry(["local", "cloud"]))
        assert router.resolve_backend(explicit_backend="cloud") == "cloud"

    def test_explicit_backend_local(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router.resolve_backend(explicit_backend="local") == "local"

    def test_explicit_backend_deepseek(self):
        router = RequestRouter(_make_registry(["local", "deepseek"]))
        assert router.resolve_backend(explicit_backend="deepseek") == "deepseek"

    def test_explicit_backend_not_available_falls_back(self):
        """If explicit backend doesn't exist, fall back to default."""
        router = RequestRouter(_make_registry(["local"], default="local"))
        assert router.resolve_backend(explicit_backend="cloud") == "local"

    def test_explicit_backend_overrides_complexity(self):
        """Explicit backend wins even when complexity is specified."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        result = router.resolve_backend(
            complexity="high",
            explicit_backend="local",
        )
        assert result == "local"


class TestComplexityRouting:
    """Rules 2-3: Complexity-based routing."""

    def test_high_complexity_prefers_cloud(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router.resolve_backend(complexity="high") == "cloud"

    def test_high_complexity_falls_to_deepseek_when_no_cloud(self):
        router = RequestRouter(_make_registry(["local", "deepseek"]))
        assert router.resolve_backend(complexity="high") == "deepseek"

    def test_high_complexity_falls_to_default_when_only_local(self):
        router = RequestRouter(_make_registry(["local"], default="local"))
        assert router.resolve_backend(complexity="high") == "local"

    def test_low_complexity_prefers_local(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router.resolve_backend(complexity="low") == "local"

    def test_low_complexity_falls_to_default_when_no_local(self):
        router = RequestRouter(_make_registry(["cloud", "deepseek"], default="cloud"))
        assert router.resolve_backend(complexity="low") == "cloud"


class TestPriorityRouting:
    """Rule 4: Priority-based routing (backward compatible)."""

    def test_high_priority_with_cloud_routes_to_cloud(self):
        router = RequestRouter(_make_registry(["local", "cloud"]))
        assert router.resolve_backend(priority="high") == "cloud"

    def test_high_priority_without_cloud_uses_default(self):
        router = RequestRouter(_make_registry(["local"], default="local"))
        assert router.resolve_backend(priority="high") == "local"

    def test_low_priority_uses_default(self):
        router = RequestRouter(_make_registry(["local", "cloud"]))
        assert router.resolve_backend(priority="low") == "local"

    def test_no_priority_uses_default(self):
        router = RequestRouter(_make_registry(["local", "cloud"]))
        assert router.resolve_backend() == "local"


class TestDefaultFallback:
    """Rule 5: Default fallback."""

    def test_no_params_returns_default(self):
        router = RequestRouter(_make_registry(["local", "cloud"], default="local"))
        assert router.resolve_backend() == "local"

    def test_custom_default_backend(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"], default="deepseek"))
        assert router.resolve_backend() == "deepseek"


class TestEdgeCases:
    """Edge cases and combined scenarios."""

    def test_complexity_none_priority_none(self):
        """Both None → default."""
        router = RequestRouter(_make_registry(["local", "cloud"]))
        assert router.resolve_backend(priority=None, complexity=None) == "local"

    def test_high_complexity_high_priority(self):
        """Complexity takes precedence over priority when both specified."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        # complexity=high should route to cloud, not be influenced by priority
        result = router.resolve_backend(priority="high", complexity="high")
        assert result == "cloud"

    def test_low_complexity_high_priority(self):
        """complexity=low overrides priority=high."""
        router = RequestRouter(_make_registry(["local", "cloud"]))
        result = router.resolve_backend(priority="high", complexity="low")
        assert result == "local"

    def test_single_backend_always_returns_it(self):
        """With only one backend, everything routes there."""
        router = RequestRouter(_make_registry(["deepseek"], default="deepseek"))
        assert router.resolve_backend(complexity="low") == "deepseek"
        assert router.resolve_backend(complexity="high") == "deepseek"
        assert router.resolve_backend(complexity="ultra") == "deepseek"
        assert router.resolve_backend(complexity="einstein") == "deepseek"
        assert router.resolve_backend(priority="high") == "deepseek"


class TestUltraComplexityRouting:
    """Rule 2a: Ultra complexity prefers deepseek over cloud."""

    def test_ultra_prefers_deepseek(self):
        """With all backends available, ultra routes to deepseek."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router.resolve_backend(complexity="ultra") == "deepseek"

    def test_ultra_falls_to_cloud_when_no_deepseek(self):
        """Without deepseek, ultra falls back to cloud."""
        router = RequestRouter(_make_registry(["local", "cloud"]))
        assert router.resolve_backend(complexity="ultra") == "cloud"

    def test_ultra_falls_to_default_when_only_local(self):
        """Without deepseek or cloud, ultra falls back to default."""
        router = RequestRouter(_make_registry(["local"], default="local"))
        assert router.resolve_backend(complexity="ultra") == "local"

    def test_ultra_vs_high_different_preference(self):
        """Ultra and high have inverted deepseek/cloud preference."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router.resolve_backend(complexity="ultra") == "deepseek"
        assert router.resolve_backend(complexity="high") == "cloud"


class TestEinsteinComplexityRouting:
    """Rule 2a-i: Einstein complexity routes to deepseek only (reasoning model)."""

    def test_einstein_routes_to_deepseek(self):
        """Einstein always routes to deepseek for reasoning."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router.resolve_backend(complexity="einstein") == "deepseek"

    def test_einstein_without_deepseek_falls_to_default(self):
        """Without deepseek, einstein falls to default (no cloud fallback)."""
        router = RequestRouter(_make_registry(["local", "cloud"], default="local"))
        assert router.resolve_backend(complexity="einstein") == "local"

    def test_einstein_only_deepseek_available(self):
        """Einstein works with only deepseek available."""
        router = RequestRouter(_make_registry(["deepseek"], default="deepseek"))
        assert router.resolve_backend(complexity="einstein") == "deepseek"

    def test_einstein_vs_ultra_same_deepseek_preference(self):
        """Both einstein and ultra prefer deepseek, but einstein has no cloud fallback."""
        router_full = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router_full.resolve_backend(complexity="einstein") == "deepseek"
        assert router_full.resolve_backend(complexity="ultra") == "deepseek"

        # Difference: without deepseek, ultra falls to cloud, einstein to default
        router_no_ds = RequestRouter(_make_registry(["local", "cloud"], default="local"))
        assert router_no_ds.resolve_backend(complexity="einstein") == "local"
        assert router_no_ds.resolve_backend(complexity="ultra") == "cloud"

    def test_einstein_with_exclude(self):
        """Einstein with deepseek excluded falls to default."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        result = router.resolve_backend(complexity="einstein", exclude={"deepseek"})
        assert result == "local"

    def test_einstein_overrides_priority(self):
        """Einstein complexity takes precedence over priority."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        result = router.resolve_backend(priority="low", complexity="einstein")
        assert result == "deepseek"


class TestRouterExclude:
    """Test exclude parameter for backend fallback."""

    def test_ultra_excludes_deepseek_falls_to_cloud(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        result = router.resolve_backend(complexity="ultra", exclude={"deepseek"})
        assert result == "cloud"

    def test_ultra_excludes_both_falls_to_default(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        result = router.resolve_backend(complexity="ultra", exclude={"deepseek", "cloud"})
        assert result == "local"

    def test_high_excludes_cloud_falls_to_deepseek(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        result = router.resolve_backend(complexity="high", exclude={"cloud"})
        assert result == "deepseek"

    def test_exclude_does_not_affect_explicit(self):
        """Explicit backend override ignores exclude."""
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        result = router.resolve_backend(explicit_backend="cloud", exclude={"cloud"})
        # Explicit wins — but cloud was excluded from available, so it falls to default
        assert result == "local"

    def test_exclude_none_same_as_default(self):
        router = RequestRouter(_make_registry(["local", "cloud", "deepseek"]))
        assert router.resolve_backend(complexity="ultra", exclude=None) == "deepseek"
        assert router.resolve_backend(complexity="ultra") == "deepseek"
