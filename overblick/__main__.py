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

    # If --host wasn't explicitly set, derive from DashboardConfig (YAML + env)
    host = args.host
    if host == "127.0.0.1":
        try:
            from overblick.dashboard.config import DashboardConfig
            dash_cfg = DashboardConfig.from_env()
            host = dash_cfg.bind_host
        except Exception:
            pass

    # Start gateway in background
    (log_dir / "gateway").mkdir(parents=True, exist_ok=True)
    gateway_log = open(log_dir / "gateway" / "gateway.log", "a")
    gateway_proc = None
    try:
        gateway_proc = subprocess.Popen(
            [sys.executable, "-m", "overblick.gateway"],
            stdout=gateway_log,
            stderr=subprocess.STDOUT,
        )
        print(f"LLM Gateway started (pid {gateway_proc.pid})")
    except OSError as e:
        print(f"Warning: Failed to start LLM Gateway: {e}")
        gateway_log.close()

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
        if gateway_proc is not None:
            gateway_proc.terminate()
            gateway_log.close()
        print("\nStopped.")


def cmd_internet_gateway(args: argparse.Namespace) -> None:
    """Start the Internet Gateway (secure reverse proxy for remote LLM access)."""
    from overblick.gateway.internet_gateway import run_internet_gateway

    base_dir = Path(__file__).parent.parent
    log_dir = base_dir / "logs" / "internet_gateway"

    setup_logging("internet_gateway", log_dir, verbose=args.verbose)

    run_internet_gateway(
        host=args.host,
        port=args.port,
        no_tls=args.no_tls,
    )


def cmd_api_keys(args: argparse.Namespace) -> None:
    """Manage API keys for the Internet Gateway."""
    from overblick.gateway.inet_auth import APIKeyManager
    from overblick.gateway.inet_config import get_inet_config

    config = get_inet_config()
    data_dir = config.resolved_data_dir
    manager = APIKeyManager(data_dir / "api_keys.db")

    try:
        if args.keys_cmd == "create":
            expires = None
            if args.expires:
                raw = args.expires.rstrip("d")
                expires = int(raw)

            models = None
            if args.models:
                models = [m.strip() for m in args.models.split(",")]

            backends = None
            if args.backends:
                backends = [b.strip() for b in args.backends.split(",")]

            raw_key, record = manager.create_key(
                name=args.name,
                expires_days=expires,
                allowed_models=models,
                allowed_backends=backends,
                max_tokens_cap=args.max_tokens,
                requests_per_minute=args.rpm,
            )

            print(f"\nAPI Key created successfully!")
            print(f"  Name:    {record.name}")
            print(f"  ID:      {record.key_id}")
            print(f"  Prefix:  {record.key_prefix}")
            if record.expires_at:
                from datetime import datetime, timezone
                exp = datetime.fromtimestamp(record.expires_at, tz=timezone.utc)
                print(f"  Expires: {exp.strftime('%Y-%m-%d %H:%M UTC')}")
            else:
                print(f"  Expires: never")
            print(f"  RPM:     {record.requests_per_minute}")
            print(f"\n  Full key (shown ONCE — store it securely):")
            print(f"  {raw_key}")
            print(f"\n  WARNING: This key cannot be retrieved again.")

        elif args.keys_cmd == "list":
            keys = manager.list_keys()
            if not keys:
                print("No API keys configured.")
                return

            print(f"\n{'ID':<10} {'Name':<20} {'Prefix':<14} {'RPM':>5} {'Requests':>10} {'Status':<10}")
            print("-" * 75)
            for k in keys:
                status = "revoked" if k.revoked else "active"
                if k.expires_at:
                    import time
                    if time.time() > k.expires_at:
                        status = "expired"
                print(f"{k.key_id:<10} {k.name:<20} {k.key_prefix:<14} {k.requests_per_minute:>5} {k.total_requests:>10} {status:<10}")

        elif args.keys_cmd == "revoke":
            if manager.revoke_key(args.key_id):
                print(f"Key {args.key_id} revoked.")
            else:
                print(f"Key {args.key_id} not found.")
                sys.exit(1)

        elif args.keys_cmd == "rotate":
            result = manager.rotate_key(args.key_id)
            if result:
                raw_key, record = result
                print(f"\nKey {args.key_id} rotated. New key:")
                print(f"  ID:     {record.key_id}")
                print(f"  Prefix: {record.key_prefix}")
                print(f"\n  Full key (shown ONCE — store it securely):")
                print(f"  {raw_key}")
                print(f"\n  WARNING: This key cannot be retrieved again.")
                print(f"  The old key has been revoked.")
            else:
                print(f"Key {args.key_id} not found or already revoked.")
                sys.exit(1)
    finally:
        manager.close()


def cmd_manage(args: argparse.Namespace) -> None:
    """Delegate to the cross-platform service manager CLI."""
    from overblick.manage.__main__ import main as manage_main
    manage_main(args.manage_args)


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

    # internet-gateway
    inet_parser = subparsers.add_parser("internet-gateway", help="Start Internet Gateway (secure LLM proxy)")
    inet_parser.add_argument("--host", default=None, help="Host to bind (default: 0.0.0.0)")
    inet_parser.add_argument("--port", type=int, default=None, help="Port (default: 8201)")
    inet_parser.add_argument("--no-tls", action="store_true", help="Disable TLS (dev mode, forces localhost)")
    inet_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    inet_parser.set_defaults(func=cmd_internet_gateway)

    # api-keys
    keys_parser = subparsers.add_parser("api-keys", help="Manage Internet Gateway API keys")
    keys_sub = keys_parser.add_subparsers(dest="keys_cmd", required=True)

    create_parser = keys_sub.add_parser("create", help="Create a new API key")
    create_parser.add_argument("--name", required=True, help="Key name (e.g. 'my-laptop')")
    create_parser.add_argument("--expires", default=None, help="Expiry (e.g. '90d' for 90 days)")
    create_parser.add_argument("--rpm", type=int, default=30, help="Requests per minute (default: 30)")
    create_parser.add_argument("--models", default=None, help="Comma-separated allowed models")
    create_parser.add_argument("--backends", default=None, help="Comma-separated allowed backends")
    create_parser.add_argument("--max-tokens", type=int, default=4096, help="Max tokens cap (default: 4096)")
    create_parser.set_defaults(func=cmd_api_keys)

    list_keys_parser = keys_sub.add_parser("list", help="List all API keys")
    list_keys_parser.set_defaults(func=cmd_api_keys)

    revoke_parser = keys_sub.add_parser("revoke", help="Revoke an API key")
    revoke_parser.add_argument("key_id", help="Key ID to revoke")
    revoke_parser.set_defaults(func=cmd_api_keys)

    rotate_parser = keys_sub.add_parser("rotate", help="Rotate an API key (create new, revoke old)")
    rotate_parser.add_argument("key_id", help="Key ID to rotate")
    rotate_parser.set_defaults(func=cmd_api_keys)

    # manage (cross-platform service manager — delegates to overblick.manage)
    manage_parser = subparsers.add_parser(
        "manage",
        help="Cross-platform service manager (replaces bash scripts)",
        add_help=False,
    )
    manage_parser.add_argument("manage_args", nargs=argparse.REMAINDER)
    manage_parser.set_defaults(func=cmd_manage)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
