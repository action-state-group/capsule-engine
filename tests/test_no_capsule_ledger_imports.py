# SPDX-License-Identifier: Apache-2.0
"""capsule-ledger is archived (#170). Ledger storage comes from
checkpointed-local-log's ``cll.ledger``; nothing here imports the archived
``capsule_ledger.ledger`` aliases of it."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = "capsule_ledger.ledger"


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    loaders = _import_module_names(tree)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            # `from capsule_ledger import ledger` names the submodule only here.
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and _is_loader(node.func, loaders):
            target = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "name"), None)
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                names.append(target.value)
    return names


def _import_module_names(tree: ast.Module) -> set[str]:
    """Local names bound to ``importlib.import_module`` in this file, plus ``__import__``."""
    names = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            names.update(alias.asname or alias.name for alias in node.names if alias.name == "import_module")
    return names


def _is_loader(func: ast.expr, loaders: set[str]) -> bool:
    if isinstance(func, ast.Name):
        return func.id in loaders
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
    )


def _offenders() -> list[str]:
    found = []
    for directory in ("capsule_engine", "tests", "scripts", "examples"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            hits = [m for m in _imported_modules(path) if m == FORBIDDEN or m.startswith(FORBIDDEN + ".")]
            if hits:
                found.append(f"{path.relative_to(REPO_ROOT)}: {hits[0]}")
    return found


def test_no_capsule_ledger_ledger_imports():
    assert _offenders() == []


def test_detector_flags_an_aliased_import(tmp_path, monkeypatch):
    package = tmp_path / "capsule_engine"
    package.mkdir()
    (package / "a.py").write_text("from capsule_ledger.ledger.api import LedgerAPI\n", encoding="utf-8")
    (package / "b.py").write_text("from capsule_ledger import ledger as old\n", encoding="utf-8")
    (package / "c.py").write_text(
        "import importlib\nimportlib.import_module('capsule_ledger.ledger.store')\n", encoding="utf-8"
    )
    (package / "d.py").write_text("import capsule_ledger.ledger\n", encoding="utf-8")
    (package / "e.py").write_text(
        "from importlib import import_module as load\nload(name='capsule_ledger.ledger.records')\n",
        encoding="utf-8",
    )
    (package / "fine.py").write_text(
        "from cll.ledger.api import LedgerAPI\nfrom capsule_ledger import registry\n"
        "import importlib\nimportlib.import_module('cll.ledger')\n"
        "plugins.import_module('capsule_ledger.ledger')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    assert _offenders() == [
        "capsule_engine/a.py: capsule_ledger.ledger.api",
        "capsule_engine/b.py: capsule_ledger.ledger",
        "capsule_engine/c.py: capsule_ledger.ledger.store",
        "capsule_engine/d.py: capsule_ledger.ledger",
        "capsule_engine/e.py: capsule_ledger.ledger.records",
    ]
