# SPDX-License-Identifier: Apache-2.0
"""A pack may pin the action taxonomy it was authored against (``taxonomy:``
in ``pack.yaml``: its ``taxonomy_version`` and the SHA-256 over its JCS
bytes).

The taxonomy decides what a gated class does on a failure -- its
``approver_role`` makes it ask rather than refuse -- yet a pack's digest did
not cover it: taxonomy 3 changed four classes from refuse to ask under an
unchanged everyday 0.3.2 digest, taxonomy 4 four more, taxonomy 5 one
more, and taxonomy 6 the three seller classes. A pack that pins
the taxonomy moves its digest when the taxonomy moves, and does not load
under any other one.

The pin is optional and everyday 0.3.2 does not carry it: adding it would move
the digest the plugin vendors. everyday 0.3.3 and 0.3.4 carry it
(test_pack_everyday_sale_coverage.py); the pin is exercised here on 0.3.2's
released bytes, kept under tests/fixtures/packs.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards.classes import TAXONOMY_DIGEST, TAXONOMY_VERSION
from capsule_engine.packs import load_pack_dir
from capsule_engine.packs.errors import TAXONOMY_PIN_MISMATCH, PackDefinitionError
from capsule_engine.packs.schema import TaxonomyPin

REPO = Path(__file__).parent.parent
EVERYDAY = REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.2"
EVERYDAY_0_3_2_DIGEST = "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2"


def _pinned_copy(tmp_path: Path, pin: str) -> Path:
    pack_dir = tmp_path / "everyday"
    shutil.copytree(EVERYDAY, pack_dir)
    with (pack_dir / "pack.yaml").open("a") as f:
        f.write(pin)
    return pack_dir


def _pin(version: str, digest: str) -> str:
    return f"\ntaxonomy:\n  taxonomy_version: {json.dumps(version)}\n  digest: {json.dumps(digest)}\n"


def test_the_engine_digest_of_the_taxonomy_is_the_jcs_digest_of_the_packaged_file():
    raw = json.loads((REPO / "capsule_engine" / "guards" / "action_taxonomy.json").read_text())
    assert TAXONOMY_DIGEST == json_digest(raw)
    assert (TAXONOMY_VERSION, TAXONOMY_DIGEST) == (
        "6",
        "fc12eb90bc2b3c85bb49c4d3d7be8f384c6e0b8e189bc3b68a753a4f1af5cd94",
    )


# Each version named the account holder as approver on these classes and
# changed nothing else; undoing exactly that must give the previous version
# back. comms.external is a legacy alias of communication.send, not a class.
TAXONOMY_3_DIGEST = "c826fcf92c18a6463b6a49fbc861e399e9afc423ba534bb69501c234b2c9b631"
TAXONOMY_4_DIGEST = "1ddce1235c3445b4ec92890d903934b08831c993f1b562e7f00082f87ca4c859"
TAXONOMY_4_APPROVERS = {
    "booking.cancel": "account_holder",
    "data.delete": "account_holder",
    "communication.publish": "account_holder",
    "disclosure.personal": "account_holder",
}
TAXONOMY_5_DIGEST = "af3a054cae880e13ce5d8d8bdaa64383e6f3af14fa2bf2ddb88aab226865b13a"
TAXONOMY_5_APPROVERS = {"communication.send": "account_holder"}
TAXONOMY_6_APPROVERS = {
    "marketplace.offer": "account_holder",
    "marketplace.sale": "account_holder",
    "agreement.accept": "account_holder",
}


class _Row(TypedDict):
    name: str
    approver_role: str | None


class _Taxonomy(TypedDict):
    taxonomy_version: str
    actions: list[_Row]


def _undo(raw: _Taxonomy, version: str, approvers: dict[str, str]) -> _Taxonomy:
    rows = {row["name"]: row for row in raw["actions"]}
    assert {name: rows[name]["approver_role"] for name in approvers} == approvers
    raw["taxonomy_version"] = version
    for name in approvers:
        rows[name]["approver_role"] = None
    return raw


def test_taxonomy_6_differs_from_5_only_in_the_version_and_the_three_seller_approvers():
    raw = json.loads((REPO / "capsule_engine" / "guards" / "action_taxonomy.json").read_text())
    assert json_digest(_undo(raw, "5", TAXONOMY_6_APPROVERS)) == TAXONOMY_5_DIGEST


def test_taxonomy_5_differs_from_4_only_in_the_version_and_the_communication_send_approver():
    raw = json.loads((REPO / "capsule_engine" / "guards" / "action_taxonomy.json").read_text())
    raw = _undo(raw, "5", TAXONOMY_6_APPROVERS)
    assert json_digest(_undo(raw, "4", TAXONOMY_5_APPROVERS)) == TAXONOMY_4_DIGEST


def test_taxonomy_4_differs_from_3_only_in_the_version_and_four_approver_roles():
    raw = json.loads((REPO / "capsule_engine" / "guards" / "action_taxonomy.json").read_text())
    raw = _undo(raw, "5", TAXONOMY_6_APPROVERS)
    raw = _undo(raw, "4", TAXONOMY_5_APPROVERS)
    assert json_digest(_undo(raw, "3", TAXONOMY_4_APPROVERS)) == TAXONOMY_3_DIGEST


def test_everyday_0_3_2_carries_no_pin_and_keeps_its_vendored_digest():
    pack = load_pack_dir(EVERYDAY)
    assert pack.taxonomy is None
    assert "taxonomy" not in pack.canonical_dict()
    assert pack.definition_digest() == EVERYDAY_0_3_2_DIGEST


def test_a_pinned_pack_loads_and_its_digest_covers_the_pin(tmp_path):
    pack = load_pack_dir(_pinned_copy(tmp_path, _pin(TAXONOMY_VERSION, TAXONOMY_DIGEST)))
    assert pack.taxonomy == TaxonomyPin(taxonomy_version=TAXONOMY_VERSION, digest=TAXONOMY_DIGEST)
    assert pack.canonical_dict()["taxonomy"] == {"taxonomy_version": TAXONOMY_VERSION, "digest": TAXONOMY_DIGEST}
    assert pack.definition_digest() != EVERYDAY_0_3_2_DIGEST
    moved = replace(pack, taxonomy=TaxonomyPin(taxonomy_version="5", digest="0" * 64))
    assert moved.definition_digest() != pack.definition_digest()


@pytest.mark.parametrize(
    ("version", "digest"),
    [("2", TAXONOMY_DIGEST), (TAXONOMY_VERSION, "0" * 64)],
    ids=["other-version", "same-version-other-contents"],
)
def test_a_pack_pinned_to_another_taxonomy_is_refused(tmp_path, version, digest):
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(_pinned_copy(tmp_path, _pin(version, digest)))
    assert exc.value.reason == TAXONOMY_PIN_MISMATCH
    assert TAXONOMY_DIGEST in str(exc.value)
