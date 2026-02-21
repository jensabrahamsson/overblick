"""
Entry point: python -m overblick.dashboard

Starts the dashboard server on localhost.
"""

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="overblick-dashboard",
        description="Överblick Dashboard — Agent monitoring and onboarding",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1, use 0.0.0.0 for LAN access)",
    )
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument(
        "--test", action="store_true",
        help="Test mode: disable auth, deterministic secret key, skip first-run redirect",
    )

    args = parser.parse_args()

    # Security: warn about non-localhost binding
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: Binding to {args.host} — dashboard will be accessible on the network.")
        print("Ensure password auth is configured (OVERBLICK_DASH_PASSWORD).")

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from .config import get_config
    config = get_config()
    config.port = args.port

    if args.test:
        config.test_mode = True
        config.password = ""
        config.secret_key = "test-mode-deterministic-key-do-not-use-in-production"
        print("TEST MODE ACTIVE")

    from .app import create_app
    app = create_app(config)

    config.host = args.host

    import uvicorn
    uvicorn.run(
        app,
        host=args.host,
        port=config.port,
        log_level="debug" if args.verbose else "info",
    )


if __name__ == "__main__":
    main()
