"""
CLI entry point: python -m overblick <command>

Usage:
    python -m overblick start            # Gateway + dashboard + browser (recommended)
    python -m overblick run anomal       # Run as Anomal
    python -m overblick run cherry       # Run as Cherry
    python -m overblick list             # List available identities
    python -m overblick dashboard        # Start web dashboard only
    python -m overblick setup            # First-time onboarding wizard
    python -m overblick secrets import anomal config/plaintext.yaml
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def setup_logging(identity: str, log_dir: Path, verbose: bool = False) -> None:
    """Configure logging for the identity."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{identity}.log"

    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def cmd_run(args: argparse.Namespace) -> None:
    """Run an agent with the specified identity."""
    from overblick.core.orchestrator import Orchestrator

    base_dir = Path(__file__).parent.parent
    log_dir = base_dir / "logs" / args.identity

    setup_logging(args.identity, log_dir, verbose=args.verbose)

    logger = logging.getLogger("overblick")
    logger.info(f"Starting Överblick with identity: {args.identity}")

    orch = Orchestrator(
        identity_name=args.identity,
        base_dir=base_dir,
    )

    try:
        asyncio.run(orch.run())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def cmd_list(args: argparse.Namespace) -> None:
    """List available identities."""
    from overblick.identities import list_identities

    identities = list_identities()
    if not identities:
        print("No identities found. Create one in overblick/identities/<name>/personality.yaml")
        return

    print("Available identities:")
    for name in identities:
        print(f"  - {name}")


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Start the web dashboard."""
    from overblick.dashboard.__main__ import main as dashboard_main
    # Override sys.argv so dashboard's argparse picks up our args
    import sys
    sys.argv = ["overblick-dashboard", "--port", str(args.port), "--host", args.host]
    if args.verbose:
        sys.argv.append("--verbose")
    dashboard_main()


def cmd_secrets_import(args: argparse.Namespace) -> None:
    """Import plaintext secrets for an identity."""
    import yaml
    from overblick.core.security.secrets_manager import SecretsManager

    base_dir = Path(__file__).parent.parent
    sm = SecretsManager(base_dir / "config" / "secrets")

    plaintext_path = Path(args.file)
    if not plaintext_path.exists():
        print(f"File not found: {plaintext_path}")
        sys.exit(1)

    with open(plaintext_path) as f:
        data = yaml.safe_load(f) or {}

    sm.load_plaintext_secrets(args.identity, data)
    print(f"Imported {len(data)} secrets for '{args.identity}'")


def cmd_setup(args: argparse.Namespace) -> None:
    """Launch the first-time onboarding setup wizard."""
    from overblick.setup.__main__ import main as setup_main
    setup_main(sandbox=args.sandbox, headless=args.headless)


def cmd_start(args: argparse.Namespace) -> None:
    """Start gateway + dashboard in one command. Opens browser on first run."""
    import subprocess
    import time
    import webbrowser

    base_dir = Path(__file__).parent.parent
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    port = args.port
    host = args.host

    # Start gateway in background
    gateway_log = open(log_dir / "gateway" / "gateway.log", "a")
    (log_dir / "gateway").mkdir(parents=True, exist_ok=True)
    gateway_proc = subprocess.Popen(
        [sys.executable, "-m", "overblick.gateway"],
        stdout=gateway_log,
        stderr=subprocess.STDOUT,
    )
    print(f"LLM Gateway started (pid {gateway_proc.pid})")

    # Check if this is first run (no config = wizard will auto-launch)
    cfg_file = base_dir / "config" / "overblick.yaml"
    first_run = not cfg_file.exists()

    url = f"http://{host}:{port}"
    if first_run:
        print(f"First run detected — setup wizard will launch at {url}")
    else:
        print(f"Dashboard starting at {url}")

    # Open browser after a short delay
    def _open_browser():
        time.sleep(2)
        webbrowser.open(url)

    import threading
    threading.Thread(target=_open_browser, daemon=True).start()

    # Start dashboard in foreground (blocks until Ctrl+C)
    try:
        from overblick.dashboard.__main__ import main as dashboard_main
        sys.argv = ["overblick-dashboard", "--port", str(port), "--host", host]
        if args.verbose:
            sys.argv.append("--verbose")
        dashboard_main()
    except KeyboardInterrupt:
        pass
    finally:
        gateway_proc.terminate()
        gateway_log.close()
        print("\nStopped.")


def cmd_supervisor(args: argparse.Namespace) -> None:
    """Start the supervisor (boss agent) managing multiple identities."""
    from overblick.supervisor.supervisor import Supervisor

    base_dir = Path(__file__).parent.parent
    log_dir = base_dir / "logs" / "supervisor"

    setup_logging("supervisor", log_dir, verbose=args.verbose)

    logger = logging.getLogger("overblick.supervisor")
    logger.info(f"Starting Överblick Supervisor with identities: {', '.join(args.identities)}")

    supervisor = Supervisor(
        identities=args.identities,
        socket_dir=base_dir / "data" / "ipc",
        auto_restart=not args.no_restart,
    )

    async def run_supervisor():
        """Start and run the supervisor."""
        await supervisor.start()
        await supervisor.run()

    try:
        asyncio.run(run_supervisor())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="overblick",
        description="Överblick — Security-focused multi-identity agent framework",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = subparsers.add_parser("run", help="Run agent with identity")
    run_parser.add_argument("identity", help="Identity name (e.g. anomal, cherry)")
    run_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    run_parser.set_defaults(func=cmd_run)

    # list
    list_parser = subparsers.add_parser("list", help="List available identities")
    list_parser.set_defaults(func=cmd_list)

    # dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Start web dashboard")
    dash_parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    dash_parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    dash_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    dash_parser.set_defaults(func=cmd_dashboard)

    # secrets import
    secrets_parser = subparsers.add_parser("secrets", help="Manage secrets")
    secrets_sub = secrets_parser.add_subparsers(dest="secrets_cmd", required=True)
    import_parser = secrets_sub.add_parser("import", help="Import plaintext secrets")
    import_parser.add_argument("identity", help="Identity name")
    import_parser.add_argument("file", help="Path to plaintext YAML file")
    import_parser.set_defaults(func=cmd_secrets_import)

    # setup
    setup_parser = subparsers.add_parser("setup", help="First-time onboarding wizard")
    setup_parser.add_argument("--sandbox", action="store_true", help="Sandbox mode (temp dir)")
    setup_parser.add_argument("--headless", action="store_true", help="Skip browser open")
    setup_parser.set_defaults(func=cmd_setup)

    # start (gateway + dashboard in one command)
    start_parser = subparsers.add_parser("start", help="Start gateway + dashboard (opens browser)")
    start_parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    start_parser.add_argument("--port", type=int, default=8080, help="Dashboard port (default: 8080)")
    start_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    start_parser.set_defaults(func=cmd_start)

    # supervisor
    sup_parser = subparsers.add_parser("supervisor", help="Start supervisor (boss agent)")
    sup_parser.add_argument("identities", nargs="+", help="Identity names (e.g. anomal cherry)")
    sup_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sup_parser.add_argument("--no-restart", action="store_true", help="Disable auto-restart")
    sup_parser.set_defaults(func=cmd_supervisor)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
