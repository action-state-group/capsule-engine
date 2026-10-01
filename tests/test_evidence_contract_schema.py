# SPDX-License-Identifier: Apache-2.0
"""``schemas/evidence-contract-v0.json`` + its validator entry point
(``capsule_engine.packs.contract_validate``).

Three groups: (1) the schema itself is valid JSON Schema and the three
positive example contracts in ``examples/contracts/`` validate against it;
(2) every fixture in ``examples/contracts/negative/`` fails validation, one
failure mode each; (3) the cross-repo parity that keeps
``EPISTEMIC_TYPE_VALUES`` and the schema's ``epistemicType`` enum in sync
with the vendored copy of EvidenceBook's owning definition, plus the
cross-check that ``EvidenceContract.canonical_dict()`` -- both a plain
outcome and an obligation-profile entry, including every real catalog
pack's outcomes -- validates against the schema's ``requirement`` union.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from capsule_engine.packs.contract_validate import (
    load_schema,
    validate_evidence_contract,
    validate_requirement,
)
from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.packs.schema import EPISTEMIC_TYPE_VALUES, ClauseSpec, EvidenceContract

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples" / "contracts"
NEGATIVE_DIR = EXAMPLES_DIR / "negative"
VENDORED_EPISTEMIC_TYPES = REPO_ROOT / "schemas" / "vendor" / "epistemic-types.json"

CATALOG_DIR = REPO_ROOT / "capsule_engine" / "packs" / "catalog"


def test_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(load_schema())


POSITIVE_FIXTURES = sorted(EXAMPLES_DIR.glob("*.json"))


def test_positive_fixtures_exist():
    assert len(POSITIVE_FIXTURES) == 3, f"expected 3 positive example contracts, found {POSITIVE_FIXTURES}"


@pytest.mark.parametrize("path", POSITIVE_FIXTURES, ids=lambda p: p.name)
def test_positive_example_contracts_validate(path):
    doc = json.loads(path.read_text())
    validate_evidence_contract(doc)  # raises on failure


NEGATIVE_FIXTURES = sorted(NEGATIVE_DIR.glob("*.json"))


def test_negative_fixtures_meet_the_floor():
    assert len(NEGATIVE_FIXTURES) >= 6, f"expected >=6 negative fixtures, found {len(NEGATIVE_FIXTURES)}"


@pytest.mark.parametrize("path", NEGATIVE_FIXTURES, ids=lambda p: p.name)
def test_negative_fixtures_fail_validation(path):
    doc = json.loads(path.read_text())
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validate_evidence_contract(doc)


def test_negative_fixture_near_miss_corrections_validate():
    """R4: a negative fixture that never had a passing near-miss counterpart
    proves nothing was actually being discriminated. Two of the nine are
    checked both ways here; the rest are single-purpose shape failures
    already covered by the positive fixtures above (e.g. the obligation
    branch's clause requirement is exercised GREEN by
    dogfood-change-control.json's req-obligation-1)."""
    unknown_epistemic_type = json.loads((NEGATIVE_DIR / "unknown-epistemic-type.json").read_text())
    corrected = json.loads(json.dumps(unknown_epistemic_type))
    corrected["requirements"][0]["evidence_requirements"]["accepted_epistemic_types"] = ["SYSTEM_OF_RECORD_FACT"]
    validate_evidence_contract(corrected)

    self_declared = json.loads((NEGATIVE_DIR / "requirement-self-declares-satisfied.json").read_text())
    corrected = json.loads(json.dumps(self_declared))
    del corrected["requirements"][0]["status"]
    validate_evidence_contract(corrected)


def test_epistemic_type_parity_schema_vendor_and_implementation():
    """The one owner is EvidenceBook's record header (its epistemic_type field);
    schemas/vendor/epistemic-types.json is the vendored transcription of it (created
    here -- none existed yet); this test is the parity gate: packs/schema.py's EPISTEMIC_TYPE_VALUES and this schema's own enum must both equal
    the vendored set, never independently re-derived."""
    vendored = set(json.loads(VENDORED_EPISTEMIC_TYPES.read_text())["values"])
    assert vendored == EPISTEMIC_TYPE_VALUES, (
        f"schemas/vendor/epistemic-types.json drifted from capsule_engine.packs.schema."
        f"EPISTEMIC_TYPE_VALUES: vendored-only={vendored - EPISTEMIC_TYPE_VALUES}, "
        f"impl-only={EPISTEMIC_TYPE_VALUES - vendored}"
    )
    schema_enum = set(load_schema()["$defs"]["epistemicType"]["enum"])
    assert schema_enum == vendored, (
        f"schemas/evidence-contract-v0.json's epistemicType enum drifted from the vendored "
        f"copy: schema-only={schema_enum - vendored}, vendor-only={vendored - schema_enum}"
    )


def test_canonical_dict_of_a_plain_outcome_validates():
    outcome = EvidenceContract(
        id="C1",
        statement="The remediation was confirmed.",
        evidence_rule="fulfill capsule chained to intent, effect_attestation=counterparty_confirmed",
        forward_verdict="DETERMINISTIC",
        backward_verdict="DETERMINISTIC",
    )
    validate_requirement(outcome.canonical_dict())


def test_canonical_dict_of_an_obligation_requires_clause_to_validate():
    obligation = EvidenceContract(
        id="O1",
        statement="a required retention control was evidenced",
        evidence_rule="retention record chained to clause",
        forward_verdict="DETERMINISTIC",
        backward_verdict="DETERMINISTIC",
        profile="obligation",
        clause=ClauseSpec(instrument="Regulation (EU) 2024/1689", article="Article 26", paragraph="6"),
    )
    validate_requirement(obligation.canonical_dict())

    # R4: the same obligation with its clause dropped is EXACTLY the
    # loader's missing_obligation_clause rule at load time, but here it must also fail schema validation --
    # a schema that let this through would silently accept a register row
    # with no clause anchor at all.
    from dataclasses import replace

    no_clause = replace(obligation, clause=None)
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validate_requirement(no_clause.canonical_dict())


@pytest.mark.parametrize("pack_dir", sorted(p for p in CATALOG_DIR.iterdir() if p.is_dir()), ids=lambda p: p.name)
def test_every_real_catalog_packs_outcomes_validate(pack_dir):
    pack = load_pack_dir(pack_dir)
    for outcome in pack.outcomes:
        validate_requirement(outcome.canonical_dict())


def test_at_least_one_real_catalog_pack_has_outcomes_to_cross_check():
    packs = [load_pack_dir(p) for p in CATALOG_DIR.iterdir() if p.is_dir()]
    assert any(pack.outcomes for pack in packs), "no catalog pack declares outcomes[] -- the cross-check above is vacuous"
