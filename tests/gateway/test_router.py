"""
Tests for the RequestRouter.

Verifies all routing rules:
1. Explicit backend override always wins
2. Complexity-based routing (einstein, ultra, high, low)
3. Priority-based routing (high → first non-local in preference)
4. Default → first available in backend_preference chain
5. Fallback to registry.default_backend when no preference matches
"""

from unittest.mock import MagicMock

import pytest

from overblick.gateway.router import RequestRouter


def _make_registry(
    backends: list[str],
    default: str = "local",
    preference: list[str] | None = None,
):
    """Create a mock BackendRegistry with given available backends."""
    registry = MagicMock()
    registry.available_backends = backends
    registry.default_backend = default
    registry.backend_preference = preference if preference is not None else []
    return registry


class TestExplicitBackend:
    """Rule 1: Explicit backend override always wins."""

    def test_explicit_backend_exists(self):
        router = RequestRouter(_make_registry(["local", "remote"]))
        assert router.resolve_backend(explicit_backend="remote") == "remote"

    def test_explicit_backend_local(self):
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router.resolve_backend(explicit_backend="local") == "local"

    def test_explicit_backend_deepseek(self):
        router = RequestRouter(_make_registry(["local", "deepseek"]))
        assert router.resolve_backend(explicit_backend="deepseek") == "deepseek"

    def test_explicit_backend_not_configured_raises(self):
        """If explicit backend doesn't exist, raise ValueError."""
        router = RequestRouter(_make_registry(["local"], default="local"))
        with pytest.raises(ValueError, match="Backend 'remote' not configured"):
            router.resolve_backend(explicit_backend="remote")

    def test_explicit_backend_excluded_falls_back(self):
        """If explicit backend exists but is excluded (unhealthy), fall back."""
        router = RequestRouter(_make_registry(["local", "remote"], default="local"))
        assert router.resolve_backend(explicit_backend="remote", exclude={"remote"}) == "local"

    def test_explicit_backend_overrides_complexity(self):
        """Explicit backend wins even when complexity is specified."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        result = router.resolve_backend(
            complexity="high",
            explicit_backend="local",
        )
        assert result == "local"


class TestComplexityRouting:
    """Rules 2-3: Complexity-based routing."""

    def test_high_complexity_prefers_remote(self):
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router.resolve_backend(complexity="high") == "remote"

    def test_high_complexity_falls_to_deepseek_when_no_remote(self):
        router = RequestRouter(_make_registry(["local", "deepseek"]))
        assert router.resolve_backend(complexity="high") == "deepseek"

    def test_high_complexity_falls_to_default_when_only_local(self):
        router = RequestRouter(_make_registry(["local"], default="local"))
        assert router.resolve_backend(complexity="high") == "local"

    def test_low_complexity_prefers_local(self):
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router.resolve_backend(complexity="low") == "local"

    def test_low_complexity_falls_to_default_when_no_local(self):
        router = RequestRouter(_make_registry(["remote", "deepseek"], default="remote"))
        assert router.resolve_backend(complexity="low") == "remote"


class TestPriorityRouting:
    """Rule 3: Priority-based routing via backend_preference."""

    def test_high_priority_routes_to_first_non_local_in_preference(self):
        router = RequestRouter(
            _make_registry(
                ["local", "remote", "deepseek"],
                preference=["remote", "deepseek", "local"],
            )
        )
        assert router.resolve_backend(priority="high") == "remote"

    def test_high_priority_skips_local_in_preference(self):
        router = RequestRouter(
            _make_registry(
                ["local", "deepseek"],
                preference=["local", "deepseek"],
            )
        )
        assert router.resolve_backend(priority="high") == "deepseek"

    def test_high_priority_only_local_available_falls_to_default(self):
        """If only local is available and preference has no non-local match, fall to default chain."""
        router = RequestRouter(
            _make_registry(
                ["local"],
                default="local",
                preference=["remote", "local"],
            )
        )
        # priority=high skips local, no non-local available → falls to default chain
        # default chain finds "remote" in preference but not available, then "local" → returns local
        assert router.resolve_backend(priority="high") == "local"

    def test_high_priority_no_preference_falls_to_default(self):
        """Without preference list, high priority falls to default chain."""
        router = RequestRouter(_make_registry(["local", "remote"], default="local"))
        assert router.resolve_backend(priority="high") == "local"

    def test_low_priority_uses_preference_chain(self):
        router = RequestRouter(
            _make_registry(
                ["local", "remote"],
                preference=["remote", "local"],
            )
        )
        assert router.resolve_backend(priority="low") == "remote"

    def test_no_priority_uses_preference_chain(self):
        router = RequestRouter(
            _make_registry(
                ["local", "remote"],
                preference=["remote", "local"],
            )
        )
        assert router.resolve_backend() == "remote"


class TestDefaultFallback:
    """Rule 4-5: Default fallback via preference chain."""

    def test_no_params_returns_first_in_preference(self):
        router = RequestRouter(
            _make_registry(
                ["local", "remote"],
                default="local",
                preference=["remote", "local"],
            )
        )
        assert router.resolve_backend() == "remote"

    def test_no_preference_returns_registry_default(self):
        router = RequestRouter(_make_registry(["local", "remote"], default="local"))
        assert router.resolve_backend() == "local"

    def test_preference_skips_unavailable(self):
        router = RequestRouter(
            _make_registry(
                ["local", "deepseek"],
                default="local",
                preference=["remote", "local", "deepseek"],
            )
        )
        # remote not available → local is first available
        assert router.resolve_backend() == "local"

    def test_custom_default_backend_with_no_preference(self):
        router = RequestRouter(
            _make_registry(["local", "remote", "deepseek"], default="deepseek")
        )
        assert router.resolve_backend() == "deepseek"


class TestEdgeCases:
    """Edge cases and combined scenarios."""

    def test_complexity_none_priority_none_with_preference(self):
        """Both None → first in preference chain."""
        router = RequestRouter(
            _make_registry(
                ["local", "remote"],
                preference=["remote", "local"],
            )
        )
        assert router.resolve_backend(priority=None, complexity=None) == "remote"

    def test_high_complexity_high_priority(self):
        """Complexity takes precedence over priority when both specified."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        # complexity=high should route to remote, not be influenced by priority
        result = router.resolve_backend(priority="high", complexity="high")
        assert result == "remote"

    def test_low_complexity_high_priority(self):
        """complexity=low overrides priority=high."""
        router = RequestRouter(
            _make_registry(
                ["local", "remote"],
                preference=["remote", "local"],
            )
        )
        result = router.resolve_backend(priority="high", complexity="low")
        assert result == "local"

    def test_single_backend_always_returns_it(self):
        """With only one backend, everything routes there."""
        router = RequestRouter(
            _make_registry(["deepseek"], default="deepseek", preference=["deepseek"])
        )
        assert router.resolve_backend(complexity="low") == "deepseek"
        assert router.resolve_backend(complexity="high") == "deepseek"
        assert router.resolve_backend(complexity="ultra") == "deepseek"
        assert router.resolve_backend(complexity="einstein") == "deepseek"
        assert router.resolve_backend(priority="high") == "deepseek"


class TestUltraComplexityRouting:
    """Rule 2a: Ultra complexity prefers deepseek over remote."""

    def test_ultra_prefers_deepseek(self):
        """With all backends available, ultra routes to deepseek."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router.resolve_backend(complexity="ultra") == "deepseek"

    def test_ultra_falls_to_remote_when_no_deepseek(self):
        """Without deepseek, ultra falls back to remote."""
        router = RequestRouter(_make_registry(["local", "remote"]))
        assert router.resolve_backend(complexity="ultra") == "remote"

    def test_ultra_falls_to_default_when_only_local(self):
        """Without deepseek or remote, ultra falls back to default."""
        router = RequestRouter(_make_registry(["local"], default="local"))
        assert router.resolve_backend(complexity="ultra") == "local"

    def test_ultra_vs_high_different_preference(self):
        """Ultra and high have inverted deepseek/remote preference."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router.resolve_backend(complexity="ultra") == "deepseek"
        assert router.resolve_backend(complexity="high") == "remote"


class TestEinsteinComplexityRouting:
    """Rule 2a-i: Einstein complexity routes to deepseek only (reasoning model)."""

    def test_einstein_routes_to_deepseek(self):
        """Einstein always routes to deepseek for reasoning."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router.resolve_backend(complexity="einstein") == "deepseek"

    def test_einstein_without_deepseek_falls_to_default(self):
        """Without deepseek, einstein falls to default (no remote fallback)."""
        router = RequestRouter(_make_registry(["local", "remote"], default="local"))
        assert router.resolve_backend(complexity="einstein") == "local"

    def test_einstein_only_deepseek_available(self):
        """Einstein works with only deepseek available."""
        router = RequestRouter(_make_registry(["deepseek"], default="deepseek"))
        assert router.resolve_backend(complexity="einstein") == "deepseek"

    def test_einstein_vs_ultra_same_deepseek_preference(self):
        """Both einstein and ultra prefer deepseek, but einstein has no remote fallback."""
        router_full = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router_full.resolve_backend(complexity="einstein") == "deepseek"
        assert router_full.resolve_backend(complexity="ultra") == "deepseek"

        # Difference: without deepseek, ultra falls to remote, einstein to default
        router_no_ds = RequestRouter(_make_registry(["local", "remote"], default="local"))
        assert router_no_ds.resolve_backend(complexity="einstein") == "local"
        assert router_no_ds.resolve_backend(complexity="ultra") == "remote"

    def test_einstein_with_exclude(self):
        """Einstein with deepseek excluded falls to default."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        result = router.resolve_backend(complexity="einstein", exclude={"deepseek"})
        assert result == "local"

    def test_einstein_overrides_priority(self):
        """Einstein complexity takes precedence over priority."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        result = router.resolve_backend(priority="low", complexity="einstein")
        assert result == "deepseek"


class TestRouterExclude:
    """Test exclude parameter for backend fallback."""

    def test_ultra_excludes_deepseek_falls_to_remote(self):
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        result = router.resolve_backend(complexity="ultra", exclude={"deepseek"})
        assert result == "remote"

    def test_ultra_excludes_both_falls_to_default(self):
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        result = router.resolve_backend(complexity="ultra", exclude={"deepseek", "remote"})
        assert result == "local"

    def test_high_excludes_remote_falls_to_deepseek(self):
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        result = router.resolve_backend(complexity="high", exclude={"remote"})
        assert result == "deepseek"

    def test_exclude_does_not_affect_explicit(self):
        """Explicit backend override ignores exclude."""
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        result = router.resolve_backend(explicit_backend="remote", exclude={"remote"})
        # Explicit wins — but remote was excluded from available, so it falls to default
        assert result == "local"

    def test_exclude_none_same_as_default(self):
        router = RequestRouter(_make_registry(["local", "remote", "deepseek"]))
        assert router.resolve_backend(complexity="ultra", exclude=None) == "deepseek"
        assert router.resolve_backend(complexity="ultra") == "deepseek"


class TestBackendPreferenceChain:
    """Tests for the backend_preference fallback chain."""

    def test_full_preference_chain(self):
        """With all backends available, first in preference wins."""
        router = RequestRouter(
            _make_registry(
                ["local", "remote", "deepseek"],
                default="local",
                preference=["remote", "local", "deepseek"],
            )
        )
        assert router.resolve_backend() == "remote"

    def test_preference_chain_skips_unavailable(self):
        """If first in preference is down, falls to second."""
        router = RequestRouter(
            _make_registry(
                ["local", "deepseek"],
                default="local",
                preference=["remote", "local", "deepseek"],
            )
        )
        # remote not available → local
        assert router.resolve_backend() == "local"

    def test_preference_chain_all_down_except_last(self):
        """If all preferred are down except deepseek, routes to deepseek."""
        router = RequestRouter(
            _make_registry(
                ["deepseek"],
                default="deepseek",
                preference=["remote", "local", "deepseek"],
            )
        )
        assert router.resolve_backend() == "deepseek"

    def test_empty_preference_uses_registry_default(self):
        """With empty preference list, falls to registry.default_backend."""
        router = RequestRouter(
            _make_registry(
                ["local", "remote"],
                default="local",
                preference=[],
            )
        )
        assert router.resolve_backend() == "local"

    def test_priority_high_with_preference_skips_local(self):
        """Priority=high uses preference but skips local."""
        router = RequestRouter(
            _make_registry(
                ["local", "remote", "deepseek"],
                default="local",
                preference=["remote", "local", "deepseek"],
            )
        )
        assert router.resolve_backend(priority="high") == "remote"

    def test_priority_high_with_only_local_falls_through(self):
        """Priority=high with only local available falls to default chain."""
        router = RequestRouter(
            _make_registry(
                ["local"],
                default="local",
                preference=["remote", "local", "deepseek"],
            )
        )
        # priority=high skips local → no match → falls to default chain → local
        assert router.resolve_backend(priority="high") == "local"
