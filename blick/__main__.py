"""
CLI entry point: python -m blick run <identity>

Usage:
    python -m blick run anomal       # Run as Anomal
    python -m blick run cherry       # Run as Cherry
    python -m blick list             # List available identities
    python -m blick secrets import anomal config/plaintext.yaml
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
    from blick.core.orchestrator import Orchestrator

    base_dir = Path(__file__).parent.parent
    log_dir = base_dir / "logs" / args.identity

    setup_logging(args.identity, log_dir, verbose=args.verbose)

    logger = logging.getLogger("blick")
    logger.info(f"Starting Blick with identity: {args.identity}")

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
    from blick.core.identity import list_identities

    identities = list_identities()
    if not identities:
        print("No identities found. Create one in blick/identities/<name>/identity.yaml")
        return

    print("Available identities:")
    for name in identities:
        print(f"  - {name}")


def cmd_secrets_import(args: argparse.Namespace) -> None:
    """Import plaintext secrets for an identity."""
    import yaml
    from blick.core.security.secrets_manager import SecretsManager

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="blick",
        description="Blick — Security-focused multi-identity agent framework",
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

    # secrets import
    secrets_parser = subparsers.add_parser("secrets", help="Manage secrets")
    secrets_sub = secrets_parser.add_subparsers(dest="secrets_cmd", required=True)
    import_parser = secrets_sub.add_parser("import", help="Import plaintext secrets")
    import_parser.add_argument("identity", help="Identity name")
    import_parser.add_argument("file", help="Path to plaintext YAML file")
    import_parser.set_defaults(func=cmd_secrets_import)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
