# SPDX-License-Identifier: Apache-2.0
"""Validator entry point for ``schemas/evidence-contract-v0.json``
([batch1-evidence-contract-jsonschema-v0]).

This module is deliberately independent of the pack loader (``loader.py``):
it validates a *file on disk* against the Evidence Contract JSON Schema, the
same file the neutral lane and any future ``capsulectl contract validate``
consume -- there is no private compiler/planner dependency here, only the
schema plus ``jsonschema``.

``validate_requirement`` is what cross-checks
``EvidenceContract.canonical_dict()`` against the schema
(``tests/test_evidence_contract_schema.py``): a single requirement is not a
full Evidence Contract document, so it validates against the ``requirement``
union directly rather than wrapping it in a synthetic root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "evidence-contract-v0.json"

__all__ = ["SCHEMA_PATH", "load_schema", "validate_evidence_contract", "validate_requirement", "main"]


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def _validator_for(pointer: str) -> jsonschema.Draft202012Validator:
    schema = load_schema()
    return jsonschema.Draft202012Validator({**schema, "$ref": pointer})


def validate_evidence_contract(doc: dict[str, Any]) -> None:
    """Validate a full Evidence Contract root document. Raises
    ``jsonschema.exceptions.ValidationError`` on the first violation."""
    _validator_for("#/$defs/evidenceContract").validate(doc)


def validate_requirement(doc: dict[str, Any]) -> None:
    """Validate a single requirement object -- e.g. one entry of
    ``EvidenceContract.canonical_dict()`` -- against the ``requirement``
    union. Raises ``jsonschema.exceptions.ValidationError`` on the first
    violation."""
    _validator_for("#/$defs/requirement").validate(doc)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m capsule_engine.packs.contract_validate <evidence-contract.json> [...]", file=sys.stderr)
        return 2
    exit_code = 0
    for path_str in argv:
        path = Path(path_str)
        doc = json.loads(path.read_text())
        try:
            validate_evidence_contract(doc)
        except jsonschema.exceptions.ValidationError as exc:
            exit_code = 1
            at = "/".join(str(p) for p in exc.absolute_path) or "<root>"
            print(f"{path}: INVALID at {at} -- {exc.message}", file=sys.stderr)
        else:
            print(f"{path}: valid")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
