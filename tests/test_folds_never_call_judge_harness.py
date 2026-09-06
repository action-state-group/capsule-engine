# SPDX-License-Identifier: Apache-2.0
"""Design §10.1: "a fold consumes judgment capsules; it must never call the
judge harness." ``counterparty_signals.py`` already documents this invariant
in prose ("structurally cannot call it, by construction -- it only imports
from folds/definition.py and folds/taxonomy.py"); this test makes it an
assertion over the WHOLE ``capsule_engine.folds`` package, not just that one
module, so a future fold can't quietly reintroduce a judge/scorer call.

A static import scan (not a runtime mock-and-assert) is the right tool here:
the invariant is "this package never imports the judge harness at all", which
a source-level AST walk proves directly, rather than a runtime test that
could pass by accident if the judge-calling code path just never executes.
"""
from __future__ import annotations

import ast
from pathlib import Path

FOLDS_PACKAGE_DIR = Path(__file__).resolve().parent.parent / "capsule_engine" / "folds"

# The judge harness this repo must never import from `folds/` (capsule-judge's
# package name, confirmed against its own pyproject.toml `name = "capsule-judge"`).
FORBIDDEN_MODULE_PREFIXES = ("capsule_judge",)


def _imported_module_names(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _fold_module_paths() -> list[Path]:
    return sorted(FOLDS_PACKAGE_DIR.rglob("*.py"))


def _is_forbidden(module_name: str) -> bool:
    return any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in FORBIDDEN_MODULE_PREFIXES)


def test_no_fold_module_imports_the_judge_harness():
    offenders: dict[str, set[str]] = {}
    for path in _fold_module_paths():
        hits = {name for name in _imported_module_names(path) if _is_forbidden(name)}
        if hits:
            offenders[str(path.relative_to(FOLDS_PACKAGE_DIR))] = hits

    assert not offenders, (
        "folds CONSUME judgment capsules and NEVER call the judge harness (design §10.1); "
        f"found forbidden imports: {offenders}"
    )


def test_the_scan_actually_covers_every_fold_module():
    # A non-empty, sane file count -- guards against a path typo silently
    # scanning zero files and the test above passing for the wrong reason.
    assert len(_fold_module_paths()) >= 10


def test_the_scan_catches_a_forbidden_import(tmp_path):
    # Proves the check can fail: a synthetic module importing the judge
    # harness must be flagged by the same detection logic used above.
    offender = tmp_path / "would_be_a_fold_module.py"
    offender.write_text("import capsule_judge\n", encoding="utf-8")
    hits = {name for name in _imported_module_names(offender) if _is_forbidden(name)}
    assert hits == {"capsule_judge"}

    offender.write_text("from capsule_judge.harness import Scorer\n", encoding="utf-8")
    hits = {name for name in _imported_module_names(offender) if _is_forbidden(name)}
    assert hits == {"capsule_judge.harness"}
