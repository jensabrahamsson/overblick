"""
Entry point: python -m overblick.setup

Launches the setup wizard as an ephemeral localhost web server,
opens the browser, and shuts down on completion.

Sandbox mode (--sandbox):
  Creates a temporary directory with copied personality files,
  so provisioning writes to a throwaway location instead of the
  real project config. Useful for automated testing and iteration.
"""

import argparse
import logging
import shutil
import socket
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path


def find_free_port() -> int:
    """Find a random available port by briefly binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_sandbox(real_base: Path) -> Path:
    """
    Create a sandbox directory for safe provisioning.

    Copies personality YAML files and pyproject.toml into a temp
    directory so the wizard can load personality data, but all
    provisioned config/secrets/directories go to the sandbox.

    Returns:
        Path to the sandbox directory (caller should NOT delete it —
        tempfile.mkdtemp persists for inspection after the run).
    """
    sandbox = Path(tempfile.mkdtemp(prefix="overblick-sandbox-"))

    # Copy personalities (needed for character select)
    real_personalities = real_base / "overblick" / "personalities"
    if real_personalities.exists():
        sandbox_personalities = sandbox / "overblick" / "personalities"
        shutil.copytree(real_personalities, sandbox_personalities)

    # Copy pyproject.toml (needed for version detection)
    real_toml = real_base / "pyproject.toml"
    if real_toml.exists():
        shutil.copy2(real_toml, sandbox / "pyproject.toml")

    return sandbox


def main(sandbox: bool = False, headless: bool = False) -> None:
    """
    Launch the setup wizard.

    Args:
        sandbox: If True, provision to a temp directory instead of real config.
        headless: If True, skip opening the browser (for automated testing).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("overblick.setup")

    port = find_free_port()
    real_base = Path(__file__).parent.parent.parent

    if sandbox:
        base_dir = _create_sandbox(real_base)
        logger.info("SANDBOX MODE — provisioning to: %s", base_dir)
    else:
        base_dir = real_base

    url = f"http://127.0.0.1:{port}"

    logger.info("Starting Överblick Setup Wizard on %s", url)
    logger.info("Base directory: %s", base_dir)

    from .app import create_setup_app
    app = create_setup_app(base_dir=base_dir)

    # Store port and url on app state (useful for Playwright tests)
    app.state.port = port
    app.state.url = url

    if not headless:
        def _open_browser():
            logger.info("Opening browser at %s", url)
            webbrowser.open(url)
        threading.Timer(1.2, _open_browser).start()

    try:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        logger.info("Setup wizard interrupted by user")
    except ImportError:
        print("ERROR: uvicorn is required. Install with: pip install 'overblick[dashboard]'")
        sys.exit(1)

    if sandbox:
        logger.info("Sandbox results at: %s", base_dir)


def cli() -> None:
    """CLI entry point with argparse."""
    parser = argparse.ArgumentParser(
        prog="overblick-setup",
        description="Överblick first-time onboarding wizard",
    )
    parser.add_argument(
        "--sandbox", action="store_true",
        help="Run in sandbox mode (temp directory, no real config changes)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Skip opening the browser (for automated testing)",
    )
    args = parser.parse_args()
    main(sandbox=args.sandbox, headless=args.headless)


if __name__ == "__main__":
    cli()
