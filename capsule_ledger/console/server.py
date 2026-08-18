# SPDX-License-Identifier: Apache-2.0
"""`capsule console`'s local HTTP server.

stdlib `http.server` only -- no new dependency (see pyproject.toml).
Single-threaded on purpose: `LedgerStore`'s sqlite3 connection is opened
once at server startup and is not safe to share across request threads, and
a local, single-operator console has no concurrency requirement that would
justify the complication. Binds to localhost by default.

Serves the static console UI -- this directory's own `tokens.css` /
`components.css` (the same component-library files the gallery uses, never
re-invented here) plus `console.css` / `console.js` / `console.html` -- and
a small JSON API over the real `LedgerAPI` (`api.py` in this package).

LOCAL ONLY: no hosted anything, no account system. Every response is
derived live from the ledger on disk; nothing here calls out to a network.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ..ledger.store import LedgerStore
from . import api

__all__ = ["ConsoleServer", "build_server"]

_STATIC_DIR = Path(__file__).resolve().parent
_STATIC_FILES = {
    "/": ("console.html", "text/html; charset=utf-8"),
    "/console.html": ("console.html", "text/html; charset=utf-8"),
    "/console.css": ("console.css", "text/css; charset=utf-8"),
    "/console.js": ("console.js", "application/javascript; charset=utf-8"),
    "/tokens.css": ("tokens.css", "text/css; charset=utf-8"),
    "/components.css": ("components.css", "text/css; charset=utf-8"),
}


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    server_version = "CapsuleConsole/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # the CLI already echoed the one line that matters at startup

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, filename: str, content_type: str) -> None:
        body = (_STATIC_DIR / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        store: LedgerStore = self.server.store

        if path in _STATIC_FILES:
            filename, content_type = _STATIC_FILES[path]
            self._send_static(filename, content_type)
            return

        if path == "/api/checkpoint":
            self._send_json(api.checkpoint_status(store))
            return

        if path == "/api/records":
            filters = {
                "agent": query.get("agent") or None,
                "since": query.get("since") or None,
                "until": query.get("until") or None,
                "counterparty": query.get("counterparty") or None,
                "verdict": query.get("verdict") or None,
                "action_type": query.get("action_type") or None,
                "limit": int(query["limit"]) if query.get("limit") else None,
            }
            self._send_json(api.list_records(store, filters))
            return

        if path.startswith("/api/records/"):
            capsule_id = urllib.parse.unquote(path[len("/api/records/") :])
            detail = api.record_detail(store, capsule_id)
            if detail is None:
                self._send_json({"error": f"no such capsule {capsule_id!r}"}, status=404)
                return
            self._send_json(detail)
            return

        self.send_error(404, "not found")


class ConsoleServer(HTTPServer):
    """Single-threaded (see module docstring). Owns the `LedgerStore` it was
    built with and any throwaway import directory, closing/removing both on
    `server_close()`."""

    def __init__(self, server_address: tuple[str, int], store: LedgerStore, cleanup_dir: Path | None = None):
        super().__init__(server_address, ConsoleRequestHandler)
        self.store = store
        self._cleanup_dir = cleanup_dir

    def server_close(self) -> None:
        super().server_close()
        self.store.close()
        if self._cleanup_dir is not None:
            shutil.rmtree(self._cleanup_dir, ignore_errors=True)


def build_server(ledger_path: str, host: str = "127.0.0.1", port: int = 8420) -> ConsoleServer:
    """Open `ledger_path` (a `LedgerStore` directory, or a JSONL fixture
    imported once into a throwaway store -- the same convenience every
    other ledger-backed verb offers via `cli/ledger_io.py`) and bind a
    server to it. Does not start serving -- call `.serve_forever()`."""
    p = Path(ledger_path)
    if p.is_dir():
        store = LedgerStore(p)
        cleanup_dir = None
    else:
        cleanup_dir = Path(tempfile.mkdtemp(prefix="capsule-ledger-console-"))
        store = LedgerStore(cleanup_dir)
        store.import_jsonl(p)

    return ConsoleServer((host, port), store=store, cleanup_dir=cleanup_dir)
