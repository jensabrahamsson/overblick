"""Tests for Moltbook profile filtering — only plugin-configured identities appear."""

import pytest
import yaml

from overblick.dashboard.routes.moltbook import (
    _collect_plugins,
    _get_moltbook_profiles,
    has_data,
)


def _make_identity(
    base: "Path",
    name: str,
    *,
    personality_plugins: list[str] | None = None,
    operational_plugins: list[str] | None = None,
    identity_plugins: list[str] | None = None,
    moltbook_bio: str = "",
    display_name: str | None = None,
    agent_name: str | None = None,
):
    """Helper to create a minimal identity directory structure."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)

    # Build personality.yaml
    personality: dict = {}
    if display_name:
        personality["identity"] = {"display_name": display_name}
    if moltbook_bio:
        personality["moltbook_bio"] = moltbook_bio
    if personality_plugins is not None:
        personality["plugins"] = personality_plugins
    if operational_plugins is not None:
        personality["operational"] = {"plugins": operational_plugins}

    (d / "personality.yaml").write_text(yaml.dump(personality))

    # Build identity.yaml (optional)
    if identity_plugins is not None or agent_name is not None:
        id_data: dict = {}
        if identity_plugins is not None:
            id_data["plugins"] = identity_plugins
        if agent_name is not None:
            id_data["agent_name"] = agent_name
        (d / "identity.yaml").write_text(yaml.dump(id_data))

    return d


class TestCollectPlugins:
    def test_personality_top_level_plugins(self, tmp_path):
        d = _make_identity(tmp_path, "bot", personality_plugins=["moltbook", "irc"])
        assert _collect_plugins(d) == {"moltbook", "irc"}

    def test_personality_operational_plugins(self, tmp_path):
        d = _make_identity(tmp_path, "bot", operational_plugins=["moltbook"])
        assert _collect_plugins(d) == {"moltbook"}

    def test_identity_yaml_plugins(self, tmp_path):
        d = _make_identity(tmp_path, "bot", identity_plugins=["moltbook", "ai_digest"])
        assert _collect_plugins(d) == {"moltbook", "ai_digest"}

    def test_all_three_sources_merged(self, tmp_path):
        d = _make_identity(
            tmp_path,
            "bot",
            personality_plugins=["irc"],
            operational_plugins=["moltbook"],
            identity_plugins=["ai_digest"],
        )
        assert _collect_plugins(d) == {"irc", "moltbook", "ai_digest"}

    def test_no_plugins_anywhere(self, tmp_path):
        d = _make_identity(tmp_path, "bot")
        assert _collect_plugins(d) == set()

    def test_missing_personality_yaml(self, tmp_path):
        d = tmp_path / "empty_bot"
        d.mkdir()
        assert _collect_plugins(d) == set()

    def test_corrupt_personality_yaml(self, tmp_path):
        d = tmp_path / "broken_bot"
        d.mkdir()
        (d / "personality.yaml").write_text(": : : invalid yaml [[[")
        # identity.yaml still works
        (d / "identity.yaml").write_text(yaml.dump({"plugins": ["moltbook"]}))
        assert _collect_plugins(d) == {"moltbook"}

    def test_corrupt_identity_yaml(self, tmp_path):
        d = _make_identity(tmp_path, "bot", personality_plugins=["moltbook"])
        (d / "identity.yaml").write_text(": : : invalid")
        assert _collect_plugins(d) == {"moltbook"}


class TestGetMoltbookProfiles:
    def test_filters_by_plugin_not_bio(self, tmp_path, monkeypatch):
        """Identity with bio but no moltbook plugin should NOT appear."""
        ids = tmp_path / "overblick" / "identities"
        ids.mkdir(parents=True)
        monkeypatch.setattr(
            "overblick.dashboard.routes.moltbook.Path",
            lambda *args: tmp_path / "overblick" / "identities" if args == ("overblick/identities",) else type(tmp_path)(*args),
        )
        # Simpler: monkeypatch the function's internal Path usage
        # Instead, patch at module level
        _make_identity(ids, "hasbiobut_noplugin", moltbook_bio="I'm on Moltbook!", display_name="NoBio")
        _make_identity(ids, "hasplugin", identity_plugins=["moltbook"], moltbook_bio="Active user", display_name="Active", agent_name="ActiveBot")

        # Patch Path("overblick/identities") to point to our tmp dir
        import overblick.dashboard.routes.moltbook as mod
        original_path = mod.Path

        def patched_path(p):
            if p == "overblick/identities":
                return ids
            return original_path(p)

        monkeypatch.setattr(mod, "Path", patched_path)

        profiles = _get_moltbook_profiles()
        assert len(profiles) == 1
        assert profiles[0]["identity"] == "hasplugin"
        assert profiles[0]["display_name"] == "Active"

    def test_url_uses_agent_name(self, tmp_path, monkeypatch):
        """URL should use agent_name from identity.yaml, not display_name."""
        ids = tmp_path / "overblick" / "identities"
        ids.mkdir(parents=True)
        _make_identity(
            ids, "testbot",
            identity_plugins=["moltbook"],
            moltbook_bio="Bio here",
            display_name="TestBot Display",
            agent_name="TestBotUsername",
        )

        import overblick.dashboard.routes.moltbook as mod
        original_path = mod.Path

        def patched_path(p):
            if p == "overblick/identities":
                return ids
            return original_path(p)

        monkeypatch.setattr(mod, "Path", patched_path)

        profiles = _get_moltbook_profiles()
        assert len(profiles) == 1
        assert profiles[0]["url"] == "https://www.moltbook.com/u/TestBotUsername"
        assert profiles[0]["display_name"] == "TestBot Display"

    def test_url_falls_back_to_display_name(self, tmp_path, monkeypatch):
        """Without identity.yaml, URL should use display_name."""
        ids = tmp_path / "overblick" / "identities"
        ids.mkdir(parents=True)
        _make_identity(
            ids, "noident",
            personality_plugins=["moltbook"],
            moltbook_bio="Bio",
            display_name="FallbackName",
        )

        import overblick.dashboard.routes.moltbook as mod
        original_path = mod.Path

        def patched_path(p):
            if p == "overblick/identities":
                return ids
            return original_path(p)

        monkeypatch.setattr(mod, "Path", patched_path)

        profiles = _get_moltbook_profiles()
        assert len(profiles) == 1
        assert profiles[0]["url"] == "https://www.moltbook.com/u/FallbackName"

    def test_empty_dir_returns_empty(self, tmp_path, monkeypatch):
        ids = tmp_path / "overblick" / "identities"
        ids.mkdir(parents=True)

        import overblick.dashboard.routes.moltbook as mod
        monkeypatch.setattr(mod, "Path", lambda p: ids if p == "overblick/identities" else mod.Path(p))

        assert _get_moltbook_profiles() == []

    def test_nonexistent_dir_returns_empty(self, tmp_path, monkeypatch):
        import overblick.dashboard.routes.moltbook as mod
        monkeypatch.setattr(mod, "Path", lambda p: tmp_path / "nonexistent" if p == "overblick/identities" else mod.Path(p))

        assert _get_moltbook_profiles() == []

    def test_multiple_plugin_sources(self, tmp_path, monkeypatch):
        """Identity with moltbook in operational.plugins should appear."""
        ids = tmp_path / "overblick" / "identities"
        ids.mkdir(parents=True)
        _make_identity(
            ids, "opbot",
            operational_plugins=["moltbook"],
            moltbook_bio="Op bio",
            display_name="OpBot",
            agent_name="OpBotUser",
        )

        import overblick.dashboard.routes.moltbook as mod
        original_path = mod.Path

        def patched_path(p):
            if p == "overblick/identities":
                return ids
            return original_path(p)

        monkeypatch.setattr(mod, "Path", patched_path)

        profiles = _get_moltbook_profiles()
        assert len(profiles) == 1
        assert profiles[0]["identity"] == "opbot"


class TestHasData:
    def test_returns_true_when_moltbook_plugin_configured(self, tmp_path, monkeypatch):
        ids = tmp_path / "overblick" / "identities"
        ids.mkdir(parents=True)
        _make_identity(ids, "bot", identity_plugins=["moltbook"])

        import overblick.dashboard.routes.moltbook as mod
        monkeypatch.setattr(mod, "Path", lambda p: ids if p == "overblick/identities" else mod.Path(p))

        assert has_data() is True

    def test_returns_false_with_bio_but_no_plugin(self, tmp_path, monkeypatch):
        ids = tmp_path / "overblick" / "identities"
        ids.mkdir(parents=True)
        _make_identity(ids, "bot", moltbook_bio="I have a bio but no plugin")

        import overblick.dashboard.routes.moltbook as mod
        monkeypatch.setattr(mod, "Path", lambda p: ids if p == "overblick/identities" else mod.Path(p))

        assert has_data() is False

    def test_returns_false_when_no_identities(self, tmp_path, monkeypatch):
        import overblick.dashboard.routes.moltbook as mod
        monkeypatch.setattr(mod, "Path", lambda p: tmp_path / "nonexistent" if p == "overblick/identities" else mod.Path(p))

        assert has_data() is False
