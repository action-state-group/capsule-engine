# SPDX-License-Identifier: Apache-2.0
"""A pack's AI-BOOTSTRAP.md is followed literally by an agent, so it must not name
the archived ``capsule_ledger`` package (#177); the importable names it gives must
exist under ``capsule_engine``."""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog"
BOOTSTRAPS = sorted(CATALOG.glob("*/AI-BOOTSTRAP.md"))
_IMPORT = re.compile(r"^\s*from\s+(capsule_engine[\w.]*)\s+import\s+(.+)$", re.MULTILINE)


def test_the_bootstrap_files_are_found():
    assert len(BOOTSTRAPS) >= 3


@pytest.mark.parametrize("path", BOOTSTRAPS, ids=lambda p: p.parent.name)
def test_bootstrap_names_no_capsule_ledger_path(path):
    text = path.read_text(encoding="utf-8")
    assert "capsule_ledger" not in text
    assert "capsule-ledger" not in text


@pytest.mark.parametrize("path", BOOTSTRAPS, ids=lambda p: p.parent.name)
def test_bootstrap_imports_resolve(path):
    for module, names in _IMPORT.findall(path.read_text(encoding="utf-8")):
        mod = importlib.import_module(module)
        for name in (n.strip() for n in names.split(",")):
            assert hasattr(mod, name), f"{path.parent.name}: {module}.{name}"


def test_mcp_server_registers_as_capsule_engine():
    pytest.importorskip("mcp")
    from capsule_engine.mcp import server

    assert server.mcp.name == "capsule-engine"
