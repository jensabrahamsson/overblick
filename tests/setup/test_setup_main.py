"""Tests for setup/__main__.py — cover lines 26-28, 43-56, 67-114, 119-134, 138."""

import socket
from unittest.mock import MagicMock, patch

from overblick.setup.__main__ import _create_sandbox, cli, find_free_port, main


class TestFindFreePort:
    def test_returns_valid_port(self):
        """Cover lines 26-28: find_free_port returns a usable port."""
        port = find_free_port()
        assert 1024 <= port <= 65535

        # Verify port is actually free
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))


class TestCreateSandbox:
    def test_creates_sandbox_directory(self, tmp_path):
        """Cover lines 43-56: sandbox creation with identities and pyproject."""
        # Create fake project structure
        identities_dir = tmp_path / "overblick" / "identities"
        identities_dir.mkdir(parents=True)
        (identities_dir / "anomal").mkdir()
        (identities_dir / "anomal" / "personality.yaml").write_text("name: anomal")

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'overblick'")

        sandbox = _create_sandbox(tmp_path)
        assert sandbox.exists()
        assert (sandbox / "overblick" / "identities" / "anomal" / "personality.yaml").exists()
        assert (sandbox / "pyproject.toml").exists()

    def test_sandbox_without_identities(self, tmp_path):
        """Sandbox creation when identities dir doesn't exist."""
        sandbox = _create_sandbox(tmp_path)
        assert sandbox.exists()
        assert not (sandbox / "overblick" / "identities").exists()


class TestMain:
    def test_main_headless_sandbox(self, tmp_path):
        """Cover lines 67-114: main() with sandbox and headless."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("overblick.setup.__main__.find_free_port", return_value=19999),
            patch("overblick.setup.__main__._create_sandbox", return_value=tmp_path),
            patch("overblick.setup.app.create_setup_app", return_value=mock_app),
            patch("uvicorn.run") as mock_uvicorn,
        ):
            main(sandbox=True, headless=True)
            mock_uvicorn.assert_called_once()

    def test_main_keyboard_interrupt(self, tmp_path):
        """Cover line 107: KeyboardInterrupt during uvicorn.run."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("overblick.setup.app.create_setup_app", return_value=mock_app),
            patch("uvicorn.run", side_effect=KeyboardInterrupt),
        ):
            main(sandbox=False, headless=True)

    def test_main_opens_browser_when_not_headless(self, tmp_path):
        """Cover lines 95-101: browser opening when not headless."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        captured_callback = None

        class MockTimer:
            def __init__(self, interval, callback):
                nonlocal captured_callback
                captured_callback = callback

            def start(self):
                pass

        with (
            patch("overblick.setup.app.create_setup_app", return_value=mock_app),
            patch("uvicorn.run"),
            patch("overblick.setup.__main__.threading.Timer", MockTimer),
            patch("overblick.setup.__main__.webbrowser.open") as mock_open,
        ):
            main(sandbox=False, headless=False)
            assert captured_callback is not None
            # Actually call the callback to cover lines 98-99
            captured_callback()
            mock_open.assert_called_once()


class TestCli:
    def test_cli_no_args(self):
        """Cover lines 119-134: cli entry point."""
        with (
            patch("overblick.setup.__main__.main") as mock_main,
            patch("sys.argv", ["overblick-setup", "--headless"]),
        ):
            cli()
            mock_main.assert_called_once_with(sandbox=False, headless=True)

    def test_cli_sandbox_flag(self):
        """Cover cli with --sandbox flag."""
        with (
            patch("overblick.setup.__main__.main") as mock_main,
            patch("sys.argv", ["overblick-setup", "--sandbox", "--headless"]),
        ):
            cli()
            mock_main.assert_called_once_with(sandbox=True, headless=True)
