# SPDX-License-Identifier: Apache-2.0
"""`capsule console`: serve the local console UI (record stream, filters,
per-record inspector, fold strip) over the real ledger.

LOCAL ONLY -- binds to localhost by default, no hosted anything, no account
system. See `capsule_ledger/console/server.py` for the HTTP wiring and
`capsule_ledger/console/{tokens,components}.css` for the shared component
library this UI is built from (never re-invented here).
"""
from __future__ import annotations

import argparse

from capsule_ledger.cli.ledger_io import require_ledger_path

from .format import build_echo

__all__ = ["add_parser", "run"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "console", help="serve the local console UI (record stream + inspector) over the real ledger"
    )
    p.add_argument("--ledger", help="ledger store directory or a JSONL fixture file (default: $CAPSULE_LEDGER)")
    p.add_argument(
        "--host", default=DEFAULT_HOST, help="bind host -- local only by default (default: %(default)s)"
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port (default: %(default)s)")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    # The console only exists at all in the "full" packaging arm (see
    # ``cli/main.py``) -- it renders capsule vocabulary and verification
    # state throughout, matching log/show/verify/bundle's own M5 marker.
    from ..telemetry.record import record_evidence_touch

    record_evidence_touch("full")

    ledger_path = require_ledger_path("console", args)
    if ledger_path is None:
        return 2

    from ..console.server import build_server

    server = build_server(ledger_path, host=args.host, port=args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"capsule console: serving {ledger_path} at {url} (local only — no hosted anything, no account system)")
    print(
        build_echo(
            "console",
            flags=[
                ("--ledger", args.ledger),
                ("--host", args.host if args.host != DEFAULT_HOST else None),
                ("--port", args.port if args.port != DEFAULT_PORT else None),
            ],
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
