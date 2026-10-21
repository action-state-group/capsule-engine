# SPDX-License-Identifier: Apache-2.0
"""capsule-ledger is archived (#170) and no longer a dependency: ledger
storage comes from checkpointed-local-log's ``cll.ledger``, and nothing here
imports ``capsule_ledger``."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = "capsule_ledger"


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


def _import_module_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Local names bound to ``importlib.import_module`` (plus ``__import__``),
    and local names bound to the ``importlib`` module itself."""
    functions, modules = {"__import__"}, {"importlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            functions.update(a.asname or a.name for a in node.names if a.name == "import_module")
        elif isinstance(node, ast.Import):
            modules.update(a.asname for a in node.names if a.name == "importlib" and a.asname)
    return functions, modules


def _is_loader(func: ast.expr, loaders: tuple[set[str], set[str]]) -> bool:
    functions, modules = loaders
    if isinstance(func, ast.Name):
        return func.id in functions
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


def _offenders() -> list[str]:
    found = []
    for directory in ("capsule_engine", "tests", "scripts", "examples"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            hits = [m for m in _imported_modules(path) if m == FORBIDDEN or m.startswith(FORBIDDEN + ".")]
            if hits:
                found.append(f"{path.relative_to(REPO_ROOT)}: {hits[0]}")
    return found


def test_no_capsule_ledger_imports():
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
    (package / "f.py").write_text(
        "import importlib as il\nil.import_module('capsule_ledger.ledger')\n", encoding="utf-8"
    )
    (package / "g.py").write_text("from capsule_ledger.payload_store import PayloadStore\n", encoding="utf-8")
    (package / "fine.py").write_text(
        "from cll.ledger.api import LedgerAPI\nfrom capsule_engine import registry\n"
        "import importlib\nimportlib.import_module('cll.ledger')\n"
        "plugins.import_module('capsule_ledger.ledger')\nimport capsule_ledgers\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    assert _offenders() == [
        "capsule_engine/a.py: capsule_ledger.ledger.api",
        "capsule_engine/b.py: capsule_ledger",
        "capsule_engine/c.py: capsule_ledger.ledger.store",
        "capsule_engine/d.py: capsule_ledger.ledger",
        "capsule_engine/e.py: capsule_ledger.ledger.records",
        "capsule_engine/f.py: capsule_ledger.ledger",
        "capsule_engine/g.py: capsule_ledger.payload_store",
    ]
