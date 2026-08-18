# SPDX-License-Identifier: Apache-2.0
"""The backend seam (`ServerConfig`/`load_config`/`open_backend`) must be
importable from the package's public surface, `capsule_ledger.mcp`, not only
from the deeper `capsule_ledger.mcp.config` submodule -- an alternate/remote
backend implementation (e.g. a paid hosted backend) needs to depend on the
public surface, never reach into internals.

Identity (`is`), not equality: this guards against a future re-export that
copies or re-wraps the names instead of pointing at the same objects.
"""
from __future__ import annotations

import capsule_ledger.mcp as mcp_pkg
import capsule_ledger.mcp.config as mcp_config
from capsule_ledger.mcp import ServerConfig, load_config, open_backend


def test_backend_seam_reexported_at_package_surface() -> None:
    assert open_backend is mcp_config.open_backend
    assert load_config is mcp_config.load_config
    assert ServerConfig is mcp_config.ServerConfig


def test_backend_seam_in_all() -> None:
    assert set(mcp_pkg.__all__) == {"ServerConfig", "load_config", "open_backend"}
