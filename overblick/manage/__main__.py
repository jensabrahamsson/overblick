"""
CLI entry point: python -m overblick.manage

Cross-platform service management for Överblick.
Replaces the Unix-only bash scripts with Python equivalents.

Usage:
    python -m overblick.manage up [identity ...]
    python -m overblick.manage down
    python -m overblick.manage status
    python -m overblick.manage gateway start|stop|status
    python -m overblick.manage dashboard start|stop|status
    python -m overblick.manage supervisor start|stop|status [identity ...]
"""

import argparse
import sys

from overblick.manage.manager import ServiceManager


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="overblick manage",
        description="Överblick — Cross-platform service manager",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # up
    up_p = sub.add_parser("up", help="Start all services")
    up_p.add_argument("identities", nargs="*", help="Identity names (default: all)")
    up_p.add_argument("--port", type=int, default=8080, help="Dashboard port")

    # down
    sub.add_parser("down", help="Stop all services")

    # status
    status_p = sub.add_parser("status", help="Show service status")
    status_p.add_argument("--port", type=int, default=8080, help="Dashboard port")

    # gateway
    gw_p = sub.add_parser("gateway", help="Manage LLM Gateway")
    gw_p.add_argument("action", choices=["start", "stop", "status"])

    # dashboard
    dash_p = sub.add_parser("dashboard", help="Manage web dashboard")
    dash_p.add_argument("action", choices=["start", "stop", "status"])
    dash_p.add_argument("--port", type=int, default=8080, help="Dashboard port")

    # supervisor
    sup_p = sub.add_parser("supervisor", help="Manage supervisor")
    sup_p.add_argument("action", choices=["start", "stop", "status"])
    sup_p.add_argument("identities", nargs="*", help="Identity names")

    args = parser.parse_args(argv)
    mgr = ServiceManager()

    if args.command == "up":
        ids = args.identities or None
        mgr.up(identities=ids, port=args.port)
    elif args.command == "down":
        mgr.down()
    elif args.command == "status":
        mgr.status(port=args.port)
    elif args.command == "gateway":
        if args.action == "start":
            mgr.start_gateway()
        elif args.action == "stop":
            mgr.stop_gateway()
        elif args.action == "status":
            mgr.status_gateway()
    elif args.command == "dashboard":
        if args.action == "start":
            mgr.start_dashboard(port=args.port)
        elif args.action == "stop":
            mgr.stop_dashboard()
        elif args.action == "status":
            mgr.status_dashboard(port=args.port)
    elif args.command == "supervisor":
        if args.action == "start":
            ids = args.identities or None
            mgr.start_supervisor(identities=ids)
        elif args.action == "stop":
            mgr.stop_supervisor()
        elif args.action == "status":
            mgr.status_supervisor()


if __name__ == "__main__":
    main()
