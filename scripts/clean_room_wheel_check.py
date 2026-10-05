# SPDX-License-Identifier: Apache-2.0
"""Clean-room check of an INSTALLED capsule-engine wheel.

Run by ``scripts/clean_room_wheel.sh`` with a fresh venv's interpreter, from
a working directory outside the source checkout. Every other test in this
repo runs against the editable install, where the checkout is on disk
whatever the wheel declares -- so a runtime file read that resolves outside
the package (or a file missing from package-data) passes there and fails
for anyone who ``pip install``s the wheel. This script exercises the
schema-backed calls end to end on what the wheel actually ships:
``build_result`` -> ``verify_result`` -> ``validate_against_schema`` on a
real Result, and ``validate_evidence_contract`` on a real contract.

Usage: python clean_room_wheel_check.py <examples/contracts dir>
(the contract fixtures are read as input data; no repo code is imported).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import jsonschema

import capsule_engine
from capsule_engine.packs.contract_validate import validate_evidence_contract
from capsule_engine.report.result import (
    Claim,
    DigestRef,
    DisclosureCarrier,
    EvidenceResult,
    ProofRef,
    build_result,
    validate_against_schema,
    verify_result,
)


def _assert_not_from_checkout(contracts_dir: Path) -> None:
    """The point of the clean room: the package must come from the venv,
    never from the source checkout the fixtures live in."""
    package_dir = Path(capsule_engine.__file__).resolve().parent
    checkout = contracts_dir.resolve().parents[1]
    if package_dir.is_relative_to(checkout):
        sys.exit(f"capsule_engine imported from the checkout ({package_dir}), not the installed wheel")
    if "site-packages" not in package_dir.parts:
        sys.exit(f"capsule_engine imported from {package_dir}, which is not a site-packages install")


def _result() -> EvidenceResult:
    claim = Claim(
        id="claim-1",
        contract_ref="ec:example-org-test:2026-09-22@1",
        requirement_ref="req-claim-1",
        tier="recomputed",
        grade="self-attested",
        sufficiency="SATISFIED",
        verdict="met",
        evidence=(DigestRef(digest="a" * 64),),
        proofs=(ProofRef(kind="inclusion_proof", digest="c" * 64),),
        presentation=DisclosureCarrier(status="SATISFIED", evidence=(DigestRef(digest="a" * 64),)),
    )
    return build_result([claim], generated_at="2026-10-05T00:00:00Z")


def _expect_invalid(validate_bad_document: Callable[[], None], what: str) -> None:
    """A validator that accepts a known-bad document never loaded its schema."""
    try:
        validate_bad_document()
    except jsonschema.ValidationError:
        # Rejection is the expected outcome: the schema was loaded and applied.
        return
    sys.exit(f"{what}: a schema-invalid document validated -- the schema check is not running")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit("usage: clean_room_wheel_check.py <examples/contracts dir>")
    contracts_dir = Path(argv[1])
    _assert_not_from_checkout(contracts_dir)

    doc = _result().to_dict()
    verify_result(doc)
    validate_against_schema(doc)
    _expect_invalid(lambda: validate_against_schema({**doc, "claims": "not-a-list"}), "Evidence Result")

    contracts = sorted(contracts_dir.glob("*.json"))
    if not contracts:
        sys.exit(f"no example contracts found in {contracts_dir}")
    for path in contracts:
        validate_evidence_contract(json.loads(path.read_text(encoding="utf-8")))
    _expect_invalid(lambda: validate_evidence_contract({}), "Evidence Contract")

    print(f"clean-room OK: capsule_engine from {Path(capsule_engine.__file__).resolve().parent}")
    print(f"  Result built, verified and schema-validated; {len(contracts)} example contracts validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
