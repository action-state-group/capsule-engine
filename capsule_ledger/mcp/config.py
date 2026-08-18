# SPDX-License-Identifier: Apache-2.0
"""Server configuration and the backend seam.

``backend`` selects how the server obtains its :class:`~capsule_ledger.ledger.api.LedgerAPI`
binding. ``"local"`` -- the only value implemented in v0 -- opens a
:class:`~capsule_ledger.ledger.store.LedgerStore` rooted at ``ledger_path`` (or imports a
JSONL fixture into one, reusing the CLI's own ``open_ledger`` convenience so a
fixture under ``tests/fixtures/`` works here exactly as it does for `capsule log`) and
keeps it open for the life of the process.

Any other value is the seam for a future remote/paid backend: same tools, same
``LedgerAPI`` Protocol (see ``ledger/api.py``'s own docstring for why every method
on it is already wire-shaped), a different binding putting requests over a
network hop instead of touching a local directory -- no server-shape change, no
tool-schema change. Nothing is built for that yet, deliberately: this module
only keeps v0 from being painted into a local-only corner. ``open_backend``
raises rather than silently falling back to ``"local"``, so a misconfigured
deployment fails loud at startup, not quietly mid-session.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..cli.ledger_io import open_ledger
from ..envcompat import env_get
from ..ledger.api import LedgerAPI

__all__ = ["ServerConfig", "load_config", "open_backend", "DEFAULT_CATALOG_DIR"]

DEFAULT_CATALOG_DIR = Path(__file__).resolve().parent.parent / "folds" / "catalog_defs"

_DEFAULT_SIGNING_KEY_ID = "capsule-mcp-server"
_DEFAULT_SIGNING_SECRET = b"capsule-mcp-server-dev-key"


@dataclass(frozen=True)
class ServerConfig:
    """Everything the server needs to wire up its tools. Every field has an
    env-var source (below) so a harness config (Claude Code hook, Goose
    extension entry) can point this at a real deployment with no code change."""

    backend: str = "local"
    ledger_path: str | None = None
    fold_catalog_dir: Path = field(default_factory=lambda: DEFAULT_CATALOG_DIR)
    caps_minor: dict[str, int] = field(default_factory=dict)
    signing_key_id: str = _DEFAULT_SIGNING_KEY_ID
    signing_secret: bytes = _DEFAULT_SIGNING_SECRET


def load_config() -> ServerConfig:
    """Read config from the environment (the same variables the CLI honors,
    plus MCP-only ones for the guard signer and per-class caps).

    ``$CAPSULE_LEDGER`` / ``$CAPSULE_FOLD_DIR`` -- shared with the CLI, see AGENTS.md.
    ``$CAPSULE_MCP_BACKEND`` -- backend selector (default ``"local"``).
    ``$CAPSULE_MCP_CAPS_MINOR`` -- JSON object, ``{"money.transfer": 10000000}``;
        an action class absent here has no cap configured, matching the CLI's
        own default (`guard dry-run --cap` omitted = never triggers the caps
        guard for that class).
    ``$CAPSULE_MCP_SIGNING_KEY_ID`` / ``$CAPSULE_MCP_SIGNING_SECRET`` -- the local
        HMAC signer `intent.declare` uses to seal decision capsules (v0 has
        no COSE/asymmetric signer anywhere in this package yet -- capsules
        stay ``attestation_mode: self_attested``, see `guards/signing.py`).
        Defaults to a fixed dev key so the server runs out of the box; set
        both explicitly for anything beyond local experimentation.

    are still honored as a fallback when the ``CAPSULE_*`` name isn't set.
    """
    caps_minor: dict[str, int] = {}
    raw_caps = env_get("CAPSULE_MCP_CAPS_MINOR")
    if raw_caps:
        caps_minor = {k: int(v) for k, v in json.loads(raw_caps).items()}

    fold_dir_env = env_get("CAPSULE_FOLD_DIR")
    fold_catalog_dir = Path(fold_dir_env) if fold_dir_env else DEFAULT_CATALOG_DIR

    return ServerConfig(
        backend=env_get("CAPSULE_MCP_BACKEND", "local"),
        ledger_path=env_get("CAPSULE_LEDGER"),
        fold_catalog_dir=fold_catalog_dir,
        caps_minor=caps_minor,
        signing_key_id=env_get("CAPSULE_MCP_SIGNING_KEY_ID", _DEFAULT_SIGNING_KEY_ID),
        signing_secret=(env_get("CAPSULE_MCP_SIGNING_SECRET", "") or "").encode("utf-8")
        or _DEFAULT_SIGNING_SECRET,
    )


def open_backend(config: ServerConfig) -> tuple[LedgerAPI, Callable[[], None]]:
    """Resolve ``config.backend`` into a live ``LedgerAPI`` binding.

    Returns ``(ledger, close)`` -- the caller owns the lifetime and must call
    ``close()`` on server shutdown (there is no ``atexit`` registration here;
    that's a server-process concern, not a config concern).
    """
    if config.backend != "local":
        raise NotImplementedError(
            f"backend={config.backend!r} is not implemented -- only 'local' is wired up in v0. "
            "This is the seam for a future remote/paid backend (same tools, same LedgerAPI "
            "Protocol, a different wire binding); no transport or auth exists yet."
        )
    if not config.ledger_path:
        raise RuntimeError(
            "CAPSULE_LEDGER is required for backend='local' -- point it at a LedgerStore "
            "directory or a JSONL fixture file, same as the CLI's --ledger."
        )

    cm = open_ledger(config.ledger_path)
    ledger = cm.__enter__()

    def _close() -> None:
        cm.__exit__(None, None, None)

    return ledger, _close
