# SPDX-License-Identifier: Apache-2.0
"""The vendored Result schema admits ``x-`` members on the document and on a
claim, and nothing else (agent-action-capsule ``spec/evidence-result-v0.md``
section 8a). The vectors under ``tests/fixtures/evidence-result/aac-vectors/``
are byte-for-byte copies of agent-action-capsule
``vectors/evidence-result/`` at main ``627dd8c16f7eb0dd00169f199ba6b938fb179913``
(identical at ``b109a9e4`` and ``7662a003``); their
sha256 is pinned below. They sit in a subdirectory so the positive-corpus
floor in ``test_report_result_unknown_fixtures.py`` does not count them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from capsule_engine.report.result import SCHEMA_PATH, load_schema, validate_against_schema

ROOT = Path(__file__).resolve().parent.parent
VECTOR_DIR = ROOT / "tests" / "fixtures" / "evidence-result" / "aac-vectors"

# The vendored schema: agent-action-capsule main 627dd8c:schemas/evidence-result-v0.json.
VENDORED_SCHEMA_SHA256 = "cf46d273e0220a6a6e9cfa4e41b331b6a6a155d053ca81cc33f3c004dfa734a9"

VECTOR_SHA256 = {
    "pos-example-org-extended-result.json": "f194bcb3baa4c3db25e6b13b7d8996d4e00ef4fcc6f7dac187a059e68af4ebb7",
    "neg-unprefixed-document-member.json": "2fc906a842032082ad0b1388cbac47717cb3f6da5f57bbebae6d93b1181878c4",
    "neg-unprefixed-claim-member.json": "3eed4863a2c9f75e00d8b9f9bd5411ee3145b6a887dd7fae73398252923866d4",
    "neg-judge-producer-unprefixed-result.json": "2d8dda27935cf67e5b631465e08283d929b30f42223e28fcccee5194d7e6aeab",
}

# Each negative is rejected for exactly these bare members (the vectors'
# README, agent-action-capsule vectors/evidence-result/README.md).
NEGATIVE_BARE_MEMBERS = {
    "neg-unprefixed-document-member.json": {"judged_selection"},
    "neg-unprefixed-claim-member.json": {"rule_id"},
    "neg-judge-producer-unprefixed-result.json": {"rule_id", "judge_pin", "judge_pin_digest", "rationale"},
}


def _load(name: str) -> dict:
    return json.loads((VECTOR_DIR / name).read_text())


def _rejected_members(doc: dict) -> set[str]:
    """The member names the schema rejects as unexpected, and nothing else:
    any other kind of error fails the caller's comparison."""
    names: set[str] = set()
    for error in jsonschema.Draft202012Validator(load_schema()).iter_errors(doc):
        assert error.validator == "additionalProperties", error.message
        names.update(k for k in error.instance if k not in error.schema.get("properties", {}))
    return names


def test_vendored_schema_bytes_are_pinned():
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() == VENDORED_SCHEMA_SHA256


@pytest.mark.parametrize("name", list(VECTOR_SHA256))
def test_vector_bytes_are_pinned(name: str):
    assert hashlib.sha256((VECTOR_DIR / name).read_bytes()).hexdigest() == VECTOR_SHA256[name]


def test_x_members_at_document_and_claim_level_validate():
    doc = _load("pos-example-org-extended-result.json")
    assert any(k.startswith("x-") for k in doc)
    assert any(k.startswith("x-") for claim in doc["claims"] for k in claim)
    validate_against_schema(doc)


@pytest.mark.parametrize("name", list(NEGATIVE_BARE_MEMBERS))
def test_unprefixed_extra_member_is_rejected(name: str):
    doc = _load(name)
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validate_against_schema(doc)
    assert _rejected_members(doc) == NEGATIVE_BARE_MEMBERS[name]


@pytest.mark.parametrize("name", list(NEGATIVE_BARE_MEMBERS))
def test_negative_validates_once_its_bare_members_are_x_prefixed(name: str):
    """The negatives fail only on the prefix: renamed to ``x-``, each passes."""
    doc = _load(name)
    bare = NEGATIVE_BARE_MEMBERS[name]
    for obj in [doc, *doc["claims"]]:
        for key in [k for k in obj if k in bare]:
            obj[f"x-{key}"] = obj.pop(key)
    validate_against_schema(doc)
