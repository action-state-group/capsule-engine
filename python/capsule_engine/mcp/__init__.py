# SPDX-License-Identifier: Apache-2.0
"""MCP advisory server exposing the ledger, folds, and guard checks as
structured tools -- nine read-only, plus `intent_declare`, the only tool
that writes (a guard decision, appended as a signed capsule).

See ``server.py`` for the FastMCP wiring and ``tools.py`` for the actual
logic, which is CLI-independent and directly unit-testable.

``ServerConfig``, ``load_config``, and ``open_backend`` are re-exported here
from ``config.py`` -- this is the backend seam (see ``open_backend``'s own
docstring): an alternate/remote backend implementation should depend on this
package's public surface, not reach into ``capsule_ledger.mcp.config``.
"""
from .config import ServerConfig, load_config, open_backend

__all__ = ["ServerConfig", "load_config", "open_backend"]
