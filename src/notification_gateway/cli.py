"""Small operational CLI for private/local v0.1 deployments."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import time
from collections.abc import Sequence
from wsgiref.simple_server import make_server

from .gateway import NotificationGateway
from .http import GatewayWSGIApp
from .providers import WeComWebhookProvider
from .store import SQLiteStore
from .worker import DeliveryWorker, RetryPolicy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notification-gateway")
    parser.add_argument(
        "--db",
        default=os.environ.get("NOTIFICATION_GATEWAY_DB", "runtime-data/notifications.sqlite3"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the private/local HTTP intake service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--allow-non-loopback", action="store_true")

    work = subparsers.add_parser("work-once", help="deliver at most one due notification")
    work.add_argument("--max-attempts", type=int, default=5)
    work.add_argument("--base-delay", type=float, default=5.0)
    work.add_argument("--max-delay", type=float, default=300.0)
    work.add_argument("--lease", type=float, default=60.0)

    purge = subparsers.add_parser("purge", help="delete expired terminal notification records")
    purge.add_argument("--delivered-retention", type=float, default=604_800.0)
    purge.add_argument("--dead-retention", type=float, default=2_592_000.0)
    return parser


def _gateway(db_path: str) -> NotificationGateway:
    webhook = os.environ.get("WECOM_WEBHOOK_URL")
    providers = [WeComWebhookProvider(webhook)] if webhook else []
    return NotificationGateway(SQLiteStore(db_path), providers)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "purge":
        count = SQLiteStore(args.db).purge_terminal(
            now=time.time(),
            delivered_retention_seconds=args.delivered_retention,
            dead_retention_seconds=args.dead_retention,
        )
        print(count)
        return 0
    gateway = _gateway(args.db)
    if args.command == "serve":
        try:
            loopback = ipaddress.ip_address(args.host).is_loopback
        except ValueError:
            loopback = args.host.lower() == "localhost"
        token = os.environ.get("NOTIFICATION_GATEWAY_AUTH_TOKEN")
        if not loopback and not args.allow_non_loopback:
            print("refusing non-loopback bind without --allow-non-loopback", file=sys.stderr)
            return 2
        if not loopback and not token:
            print("non-loopback bind requires NOTIFICATION_GATEWAY_AUTH_TOKEN", file=sys.stderr)
            return 2
        app = GatewayWSGIApp(gateway, auth_token=token)
        with make_server(args.host, args.port, app) as server:
            server.serve_forever()
        return 0
    if args.command == "work-once":
        if not gateway.providers:
            print("WECOM_WEBHOOK_URL is required to deliver notifications", file=sys.stderr)
            return 2
        worker = DeliveryWorker(
            gateway,
            RetryPolicy(
                max_attempts=args.max_attempts,
                base_delay_seconds=args.base_delay,
                max_delay_seconds=args.max_delay,
                lease_seconds=args.lease,
            ),
        )
        return 0 if worker.run_once() else 3
    return 2
