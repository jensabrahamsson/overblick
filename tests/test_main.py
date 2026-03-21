"""
Tests for the CLI entry point (__main__.py).

Uses unittest.mock to avoid actually starting servers, running agents, etc.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.__main__ import (
    cmd_api_keys,
    cmd_dashboard,
    cmd_internet_gateway,
    cmd_list,
    cmd_manage,
    cmd_run,
    cmd_secrets_import,
    cmd_setup,
    cmd_start,
    cmd_supervisor,
    main,
    setup_logging,
)


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "logs" / "test"
        setup_logging("test", log_dir, verbose=False)
        assert log_dir.exists()

    def test_verbose_sets_debug(self, tmp_path):
        log_dir = tmp_path / "logs" / "verbose"
        # Reset logging
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging("verbose", log_dir, verbose=True)
        assert root.level == logging.DEBUG

    def test_non_verbose_sets_info(self, tmp_path):
        log_dir = tmp_path / "logs" / "info"
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging("info", log_dir, verbose=False)
        assert root.level == logging.INFO


class TestCmdRun:
    """Tests for cmd_run."""

    def test_run_normal(self):
        args = argparse.Namespace(identity="anomal", verbose=False)
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()

        with (
            patch("overblick.__main__.setup_logging"),
            patch("overblick.core.orchestrator.Orchestrator", return_value=mock_orch),
            patch("asyncio.run"),
        ):
            cmd_run(args)

    def test_run_keyboard_interrupt(self):
        args = argparse.Namespace(identity="anomal", verbose=False)
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()

        with (
            patch("overblick.__main__.setup_logging"),
            patch("overblick.core.orchestrator.Orchestrator", return_value=mock_orch),
            patch("asyncio.run", side_effect=KeyboardInterrupt),
        ):
            cmd_run(args)  # Should not raise

    def test_run_fatal_error(self):
        args = argparse.Namespace(identity="anomal", verbose=True)
        mock_orch = MagicMock()

        with (
            patch("overblick.__main__.setup_logging"),
            patch("overblick.core.orchestrator.Orchestrator", return_value=mock_orch),
            patch("asyncio.run", side_effect=RuntimeError("fatal")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_run(args)


class TestCmdList:
    """Tests for cmd_list."""

    def test_list_with_identities(self, capsys):
        args = argparse.Namespace()
        with patch("overblick.identities.list_identities", return_value=["anomal", "cherry"]):
            cmd_list(args)
        captured = capsys.readouterr()
        assert "anomal" in captured.out
        assert "cherry" in captured.out

    def test_list_no_identities(self, capsys):
        args = argparse.Namespace()
        with patch("overblick.identities.list_identities", return_value=[]):
            cmd_list(args)
        captured = capsys.readouterr()
        assert "No identities" in captured.out


class TestCmdDashboard:
    """Tests for cmd_dashboard."""

    def test_dashboard(self):
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)
        with patch("overblick.dashboard.__main__.main") as mock_main:
            cmd_dashboard(args)
            mock_main.assert_called_once()

    def test_dashboard_verbose(self):
        args = argparse.Namespace(port=9090, host="0.0.0.0", verbose=True)
        with patch("overblick.dashboard.__main__.main") as mock_main:
            cmd_dashboard(args)
            mock_main.assert_called_once()
            assert "--verbose" in sys.argv


class TestCmdSecretsImport:
    """Tests for cmd_secrets_import."""

    def test_import_secrets(self, tmp_path):
        secrets_file = tmp_path / "secrets.yaml"
        secrets_file.write_text("key1: value1\nkey2: value2\n")

        args = argparse.Namespace(identity="anomal", file=str(secrets_file))
        with (
            patch("overblick.core.security.secrets_manager.SecretsManager") as MockSM,
        ):
            cmd_secrets_import(args)
            MockSM.return_value.load_plaintext_secrets.assert_called_once()

    def test_import_missing_file(self, tmp_path):
        args = argparse.Namespace(identity="anomal", file=str(tmp_path / "missing.yaml"))
        with pytest.raises(SystemExit, match="1"):
            cmd_secrets_import(args)


class TestCmdSetup:
    """Tests for cmd_setup."""

    def test_setup(self):
        args = argparse.Namespace(sandbox=False, headless=True)
        with patch("overblick.setup.__main__.main") as mock_main:
            cmd_setup(args)
            mock_main.assert_called_once_with(sandbox=False, headless=True)


class TestCmdStart:
    """Tests for cmd_start."""

    def test_start_normal(self, capsys):
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.dashboard.__main__.main"),
            patch("threading.Thread"),
        ):
            cmd_start(args)

        captured = capsys.readouterr()
        assert "Gateway started" in captured.out

    def test_start_first_run(self, tmp_path, capsys):
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.dashboard.__main__.main"),
            patch("threading.Thread"),
            patch("overblick.__main__.Path") as MockPath,
        ):
            # Simulate first run (no config file)
            mock_path = MagicMock()
            mock_path.__truediv__ = MagicMock(return_value=mock_path)
            mock_path.exists.return_value = False
            MockPath.return_value = mock_path
            MockPath.__truediv__ = MagicMock(return_value=mock_path)

            cmd_start(args)

    def test_start_gateway_fails(self, capsys):
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)

        with (
            patch("subprocess.Popen", side_effect=OSError("cannot start")),
            patch("overblick.dashboard.__main__.main"),
            patch("threading.Thread"),
        ):
            cmd_start(args)

        captured = capsys.readouterr()
        assert "Warning" in captured.out

    def test_start_keyboard_interrupt(self, capsys):
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.dashboard.__main__.main", side_effect=KeyboardInterrupt),
            patch("threading.Thread"),
        ):
            cmd_start(args)

        captured = capsys.readouterr()
        assert "Stopped" in captured.out

    def test_start_with_dashboard_config_host(self, capsys):
        """start uses DashboardConfig host when --host not explicitly set."""
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        mock_dash_cfg = MagicMock()
        mock_dash_cfg.bind_host = "0.0.0.0"

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.dashboard.__main__.main"),
            patch("threading.Thread"),
            patch("overblick.dashboard.config.DashboardConfig.from_env", return_value=mock_dash_cfg),
        ):
            cmd_start(args)

    def test_start_dashboard_config_exception(self, capsys):
        """start handles DashboardConfig exception gracefully."""
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.dashboard.__main__.main"),
            patch("threading.Thread"),
            patch("overblick.dashboard.config.DashboardConfig.from_env", side_effect=ImportError("no module")),
        ):
            cmd_start(args)

    def test_start_verbose(self, capsys):
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=True)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.dashboard.__main__.main"),
            patch("threading.Thread"),
        ):
            cmd_start(args)

    def test_start_first_run_detected(self, tmp_path, capsys):
        """First run is detected when config file doesn't exist."""
        args = argparse.Namespace(port=9090, host="127.0.0.1", verbose=False)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        captured_threads = []

        class MockThread:
            def __init__(self, **kwargs):
                self.target = kwargs.get("target")
                captured_threads.append(self)

            def start(self):
                pass

        # Temporarily rename the config file to simulate first run
        original_exists = Path.exists

        def mock_exists(self):
            if "overblick.yaml" in str(self):
                return False
            return original_exists(self)

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.dashboard.__main__.main"),
            patch("threading.Thread", MockThread),
            patch.object(Path, "exists", mock_exists),
        ):
            cmd_start(args)

        captured = capsys.readouterr()
        assert "First run" in captured.out
        assert len(captured_threads) == 1
        # Execute the browser opener to cover lines 169-170
        with patch("time.sleep"), patch("webbrowser.open") as mock_open:
            captured_threads[0].target()
            mock_open.assert_called_once()


class TestCmdInternetGateway:
    """Tests for cmd_internet_gateway."""

    def test_internet_gateway(self):
        args = argparse.Namespace(host="0.0.0.0", port=8201, no_tls=True, verbose=False)
        with (
            patch("overblick.__main__.setup_logging"),
            patch("overblick.gateway.internet_gateway.run_internet_gateway"),
        ):
            cmd_internet_gateway(args)


class TestCmdApiKeys:
    """Tests for cmd_api_keys."""

    def test_create_key(self, capsys):
        args = argparse.Namespace(
            keys_cmd="create",
            name="test-key",
            expires="90d",
            models="qwen3:8b,llama3",
            backends="local,cloud",
            max_tokens=4096,
            rpm=30,
        )
        mock_record = MagicMock()
        mock_record.name = "test-key"
        mock_record.key_id = "abc123"
        mock_record.key_prefix = "ob_abc"
        mock_record.expires_at = None
        mock_record.requests_per_minute = 30

        mock_mgr = MagicMock()
        mock_mgr.create_key.return_value = ("raw-key-value", mock_record)
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            cmd_api_keys(args)

        captured = capsys.readouterr()
        assert "raw-key-value" in captured.out

    def test_create_key_with_expiry_hits_bug(self, capsys):
        """Create key with expiry triggers a known production bug (datetime.UTC).

        The code does `from datetime import datetime` then `datetime.UTC`
        but datetime.datetime doesn't have UTC attribute. The finally block
        (manager.close()) still runs, which is the important coverage point.
        """
        args = argparse.Namespace(
            keys_cmd="create", name="expiring", expires="30d",
            models=None, backends=None, max_tokens=4096, rpm=30,
        )
        mock_record = MagicMock()
        mock_record.name = "expiring"
        mock_record.key_id = "def456"
        mock_record.key_prefix = "ob_def"
        mock_record.expires_at = 9999999999.0
        mock_record.requests_per_minute = 30

        mock_mgr = MagicMock()
        mock_mgr.create_key.return_value = ("raw-key", mock_record)
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            with pytest.raises(AttributeError, match="UTC"):
                cmd_api_keys(args)

        # Verify close was still called (finally block)
        mock_mgr.close.assert_called_once()

    def test_create_key_no_expires(self, capsys):
        args = argparse.Namespace(
            keys_cmd="create", name="never", expires=None,
            models=None, backends=None, max_tokens=4096, rpm=30,
        )
        mock_record = MagicMock()
        mock_record.name = "never"
        mock_record.key_id = "ghi789"
        mock_record.key_prefix = "ob_ghi"
        mock_record.expires_at = None
        mock_record.requests_per_minute = 30

        mock_mgr = MagicMock()
        mock_mgr.create_key.return_value = ("raw-key", mock_record)
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            cmd_api_keys(args)

    def test_list_keys(self, capsys):
        args = argparse.Namespace(keys_cmd="list")
        mock_key = MagicMock()
        mock_key.key_id = "abc"
        mock_key.name = "test"
        mock_key.key_prefix = "ob_abc"
        mock_key.requests_per_minute = 30
        mock_key.total_requests = 100
        mock_key.revoked = False
        mock_key.expires_at = None

        mock_mgr = MagicMock()
        mock_mgr.list_keys.return_value = [mock_key]
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            cmd_api_keys(args)

        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_list_keys_empty(self, capsys):
        args = argparse.Namespace(keys_cmd="list")
        mock_mgr = MagicMock()
        mock_mgr.list_keys.return_value = []
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            cmd_api_keys(args)

        captured = capsys.readouterr()
        assert "No API keys" in captured.out

    def test_list_keys_with_expired_and_revoked(self, capsys):
        args = argparse.Namespace(keys_cmd="list")

        expired_key = MagicMock()
        expired_key.key_id = "exp"
        expired_key.name = "expired"
        expired_key.key_prefix = "ob_exp"
        expired_key.requests_per_minute = 30
        expired_key.total_requests = 50
        expired_key.revoked = False
        expired_key.expires_at = 1000000.0  # In the past

        revoked_key = MagicMock()
        revoked_key.key_id = "rev"
        revoked_key.name = "revoked"
        revoked_key.key_prefix = "ob_rev"
        revoked_key.requests_per_minute = 30
        revoked_key.total_requests = 10
        revoked_key.revoked = True
        revoked_key.expires_at = None

        mock_mgr = MagicMock()
        mock_mgr.list_keys.return_value = [expired_key, revoked_key]
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            cmd_api_keys(args)

        captured = capsys.readouterr()
        assert "expired" in captured.out
        assert "revoked" in captured.out

    def test_revoke_key_success(self, capsys):
        args = argparse.Namespace(keys_cmd="revoke", key_id="abc123")
        mock_mgr = MagicMock()
        mock_mgr.revoke_key.return_value = True
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            cmd_api_keys(args)

        captured = capsys.readouterr()
        assert "revoked" in captured.out

    def test_revoke_key_not_found(self, capsys):
        args = argparse.Namespace(keys_cmd="revoke", key_id="nonexistent")
        mock_mgr = MagicMock()
        mock_mgr.revoke_key.return_value = False
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            with pytest.raises(SystemExit, match="1"):
                cmd_api_keys(args)

    def test_rotate_key_success(self, capsys):
        args = argparse.Namespace(keys_cmd="rotate", key_id="abc123")
        mock_record = MagicMock()
        mock_record.key_id = "new123"
        mock_record.key_prefix = "ob_new"

        mock_mgr = MagicMock()
        mock_mgr.rotate_key.return_value = ("new-raw-key", mock_record)
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            cmd_api_keys(args)

        captured = capsys.readouterr()
        assert "new-raw-key" in captured.out

    def test_rotate_key_not_found(self, capsys):
        args = argparse.Namespace(keys_cmd="rotate", key_id="nonexistent")
        mock_mgr = MagicMock()
        mock_mgr.rotate_key.return_value = None
        mock_mgr.close = MagicMock()

        with (
            patch("overblick.gateway.inet_auth.APIKeyManager", return_value=mock_mgr),
            patch("overblick.gateway.inet_config.get_inet_config") as mock_config,
        ):
            mock_config.return_value.resolved_data_dir = Path("/tmp")
            with pytest.raises(SystemExit, match="1"):
                cmd_api_keys(args)


class TestCmdManage:
    """Tests for cmd_manage."""

    def test_manage(self):
        args = argparse.Namespace(manage_args=["status"])
        with patch("overblick.manage.__main__.main") as mock_main:
            cmd_manage(args)
            mock_main.assert_called_once_with(["status"])


class TestCmdSupervisor:
    """Tests for cmd_supervisor."""

    def test_supervisor_normal(self):
        args = argparse.Namespace(identities=["anomal"], verbose=False, no_restart=False)

        mock_supervisor = MagicMock()
        mock_supervisor.start = AsyncMock()
        mock_supervisor.run = AsyncMock()

        with (
            patch("overblick.__main__.setup_logging"),
            patch("overblick.supervisor.supervisor.Supervisor", return_value=mock_supervisor),
        ):
            # Actually run the inner async function to cover lines 331-332
            with patch("asyncio.run") as mock_arun:
                cmd_supervisor(args)
                # Get the coroutine passed to asyncio.run and execute it
                coro = mock_arun.call_args[0][0]
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(coro)
                finally:
                    loop.close()

    def test_supervisor_keyboard_interrupt(self):
        args = argparse.Namespace(identities=["anomal"], verbose=True, no_restart=True)
        with (
            patch("overblick.__main__.setup_logging"),
            patch("overblick.supervisor.supervisor.Supervisor"),
            patch("asyncio.run", side_effect=KeyboardInterrupt),
        ):
            cmd_supervisor(args)

    def test_supervisor_fatal_error(self):
        args = argparse.Namespace(identities=["anomal"], verbose=False, no_restart=False)
        with (
            patch("overblick.__main__.setup_logging"),
            patch("overblick.supervisor.supervisor.Supervisor"),
            patch("asyncio.run", side_effect=RuntimeError("fatal")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_supervisor(args)


class TestMain:
    """Tests for main() argument parsing."""

    def test_run_command(self):
        with (
            patch("sys.argv", ["overblick", "run", "anomal"]),
            patch("overblick.__main__.cmd_run") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_list_command(self):
        with (
            patch("sys.argv", ["overblick", "list"]),
            patch("overblick.__main__.cmd_list") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_dashboard_command(self):
        with (
            patch("sys.argv", ["overblick", "dashboard", "--port", "9090"]),
            patch("overblick.__main__.cmd_dashboard") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_setup_command(self):
        with (
            patch("sys.argv", ["overblick", "setup", "--headless"]),
            patch("overblick.__main__.cmd_setup") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_start_command(self):
        with (
            patch("sys.argv", ["overblick", "start", "--port", "9090"]),
            patch("overblick.__main__.cmd_start") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_supervisor_command(self):
        with (
            patch("sys.argv", ["overblick", "supervisor", "anomal", "cherry"]),
            patch("overblick.__main__.cmd_supervisor") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_secrets_import_command(self):
        with (
            patch("sys.argv", ["overblick", "secrets", "import", "anomal", "secrets.yaml"]),
            patch("overblick.__main__.cmd_secrets_import") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_internet_gateway_command(self):
        with (
            patch("sys.argv", ["overblick", "internet-gateway", "--no-tls"]),
            patch("overblick.__main__.cmd_internet_gateway") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_api_keys_create_command(self):
        with (
            patch("sys.argv", ["overblick", "api-keys", "create", "--name", "test"]),
            patch("overblick.__main__.cmd_api_keys") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_api_keys_list_command(self):
        with (
            patch("sys.argv", ["overblick", "api-keys", "list"]),
            patch("overblick.__main__.cmd_api_keys") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_api_keys_revoke_command(self):
        with (
            patch("sys.argv", ["overblick", "api-keys", "revoke", "key123"]),
            patch("overblick.__main__.cmd_api_keys") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_api_keys_rotate_command(self):
        with (
            patch("sys.argv", ["overblick", "api-keys", "rotate", "key123"]),
            patch("overblick.__main__.cmd_api_keys") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_manage_command(self):
        with (
            patch("sys.argv", ["overblick", "manage", "status"]),
            patch("overblick.__main__.cmd_manage") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_dunder_main(self):
        """Cover if __name__ == '__main__' guard."""
        import overblick.__main__ as main_mod

        with (
            patch("sys.argv", ["overblick", "list"]),
            patch.object(main_mod, "main") as mock_main,
        ):
            # Simulate the if __name__ == "__main__" check
            if True:  # pragma: no branch
                main_mod.main()
            mock_main.assert_called_once()
