"""Tests for PluginRunController — control file parsing and caching."""

import json

import pytest

from overblick.core.plugin_run_controller import PluginRunController


class TestPluginRunController:
    @pytest.mark.asyncio
    async def test_no_control_file_returns_false(self):
        controller = PluginRunController(control_file=None)
        assert await controller.is_plugin_stopped("any") is False

    @pytest.mark.asyncio
    async def test_missing_file_returns_false(self, tmp_path):
        controller = PluginRunController(
            control_file=tmp_path / "nonexistent.json", cache_ttl_seconds=0
        )
        assert await controller.is_plugin_stopped("any") is False

    @pytest.mark.asyncio
    async def test_accepts_boolean_true(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": True}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is True

    @pytest.mark.asyncio
    async def test_accepts_boolean_false(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": False}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is False

    @pytest.mark.asyncio
    async def test_accepts_string_true(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": "true"}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is True

    @pytest.mark.asyncio
    async def test_accepts_string_false(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": "false"}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is False

    @pytest.mark.asyncio
    async def test_accepts_legacy_stopped(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": "stopped"}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is True

    @pytest.mark.asyncio
    async def test_accepts_legacy_paused(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": "paused"}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is True

    @pytest.mark.asyncio
    async def test_unknown_plugin_returns_false(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": True}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("calendar") is False

    @pytest.mark.asyncio
    async def test_invalid_json_returns_false(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text("{not-json")
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is False

    @pytest.mark.asyncio
    async def test_cache_is_used_until_invalidated(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps({"mail": True}))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=60)

        assert await controller.is_plugin_stopped("mail") is True

        # Change file — but cache should still return old value
        f.write_text(json.dumps({"mail": False}))
        assert await controller.is_plugin_stopped("mail") is True

        # Invalidate cache — should read new value
        controller.invalidate()
        assert await controller.is_plugin_stopped("mail") is False

    @pytest.mark.asyncio
    async def test_non_dict_json_returns_false(self, tmp_path):
        f = tmp_path / "ctrl.json"
        f.write_text(json.dumps([1, 2, 3]))
        controller = PluginRunController(control_file=f, cache_ttl_seconds=0)
        assert await controller.is_plugin_stopped("mail") is False
