# SPDX-License-Identifier: Apache-2.0
"""everyday 0.3.3: a sale is measured by the six scalar checks.

Under everyday 0.3.2, marketplace.sale was in no class list of channel_change,
material_fields_changed, offer_fields_changed, refundability, task_authority
or upfront_amount, so a sale with changed terms, a large deposit, a moved
channel or a step outside the task read n/a on all six and was allowed.
Version 1.1.0 of each definition is its 1.0.0 config with marketplace.sale
added to ``action_classes`` and the rule stated in ``semantics``; 1.0.0 stays
byte-identical and is not retired, because 0.3.2 cites it.

caps is unchanged: no caps version has listed marketplace.sale in its config.
caps/4.0.0 names it only in a comment, as money coming in, which caps leaves
out like money.refund. caps/5.0.0 has the same class set as caps/4.0.0.

0.3.2 is kept byte for byte under ``tests/fixtures/packs/everyday-0.3.2``, and
it still has to load at its digest from this engine's definitions. 0.3.3 is
kept the same way since 0.3.4 replaced it in the catalog, and 0.3.4 since
0.3.5 did; both measure a sale exactly as 0.3.3 does.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardDecision, LocalSigner
from capsule_engine.guards.capsule import ALLOW, ESCALATE
from capsule_engine.guards.checks import fields_basis, task_authority_record_digest
from capsule_engine.guards.classes import TAXONOMY_DIGEST, TAXONOMY_VERSION
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.guards.wickets.catalog import Catalog
from capsule_engine.guards.wickets.retired import retired_entry
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.errors import TAXONOMY_PIN_MISMATCH, PackDefinitionError
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.packs.schema import PackDefinition

REPO = Path(__file__).parent.parent
PACK_DIR = REPO / "capsule_engine" / "packs" / "catalog" / "everyday"
PACK = load_pack_dir(PACK_DIR)
FROZEN_0_3_3_DIR = REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.3"
PACK_0_3_3_DIGEST = "d153219b9b5f7ad8eb4805eb718aab81a5dfa29a6a1d977301559fc167268752"
FROZEN_0_3_2_DIR = REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.2"
PACK_0_3_2_DIGEST = "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2"
PACK_0_3_2_FILE_SHA256 = "b2d5408cc71ea6b70b6c2318dc633c459cd2cab1dca4e6eaaf880bc7fc583f24"
WICKETS = REPO / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
SALE = "marketplace.sale"

# check -> (1.0.0 file, 1.1.0 file, the rule it measures, the input that fails it on a sale)
SALE_CHECKS = {
    "material_fields_changed": ("material_fields_changed.yaml", "material_fields_changed.v1.1.yaml",
                                "r09-material-terms-changed", {"material_fields_changed": 2}),
    "offer_fields_changed": ("offer_fields_changed.yaml", "offer_fields_changed.v1.1.yaml",
                             "r10-materially-different-offer", {"offer_fields_changed": 1}),
    "refundability": ("refundability.yaml", "refundability.v1.1.yaml",
                      "r08-non-refundable-or-hard-to-undo", {"refundable": False}),
    "channel_change": ("channel_change.yaml", "channel_change.v1.1.yaml",
                       "r21-unexpected-channel-change", {"channel": "whatsapp"}),
    "upfront_amount": ("upfront_amount.yaml", "upfront_amount.v1.1.yaml",
                       "r22-unusual-counterparty-behaviour", {"upfront_amount_minor": 5_001}),
    "task_authority": ("task_authority.yaml", "task_authority.v1.1.yaml",
                       "r26-stay-within-task-bounds", {"target": "buyer/someone-else"}),
}

MATERIAL_BASIS = fields_basis(load_definition_file(WICKETS / "material_fields_changed.v1.1.yaml").config["counted_fields"])
OFFER_BASIS = fields_basis(load_definition_file(WICKETS / "offer_fields_changed.v1.1.yaml").config["counted_fields"])
SIGNER = LocalSigner(key_id="everyday-sale-coverage-key", secret=b"everyday-sale-coverage-fixed-key")
TASK_AUTHORITY = {"body": {"outcome_id": "household.sell_the_bike/1.0.0", "allowed_actions": ["sell_item"],
                           "preconditions": [], "binding": {"subject": "buyer/bike-1"}}}
TASK_AUTHORITY_REF = task_authority_record_digest(TASK_AUTHORITY)


def _sale(n: int, **fields) -> Action:
    """A sale whose declared inputs are each inside their limit; ``fields``
    moves one past it."""
    base = dict(
        amount_minor=20_000, currency="EUR", target="buyer/bike-1", rail="card", refundable=True,
        material_fields_changed=0, material_fields_basis=MATERIAL_BASIS,
        offer_fields_changed=0, offer_fields_basis=OFFER_BASIS,
        channel="marketplace", first_contact_channel="marketplace", upfront_amount_minor=5_000,
        task_authority_ref=TASK_AUTHORITY_REF,
    )
    base.update(fields)
    return Action(
        verb="sell_item",
        operator=f"household-sale-coverage-{n}",
        developer="household-assistant-sc@v1",
        action_class=SALE,
        action_id=f"sell_item/everyday-sale-coverage-{n}",
        timestamp=f"2026-08-10T16:{n:02d}:00Z",
        **base,
    )


@pytest.fixture
def decide(tmp_path):
    stores = []

    def run(pack: PackDefinition, action: Action) -> GuardDecision:
        n = len(stores)
        store = LedgerStore(tmp_path / f"ledger-{n}")
        stores.append(store)
        installed = install_pack(pack, project_dir=tmp_path / f"project-{n}", mode="observe")
        engine = build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
        return engine.check(action, dry_run=True, task_authority_record=TASK_AUTHORITY)

    yield run
    for store in stores:
        store.close()


def _results(decision: GuardDecision) -> dict[str, str]:
    return {c.id: c.result for c in decision.constraints}


def _failing_rules(pack: PackDefinition, decision: GuardDecision) -> list[str]:
    return [r.obligation_id for r in obligation_results(pack, decision.constraints) if r.result == "fail"]


# -- the new definitions ------------------------------------------------------


@pytest.mark.parametrize("check", sorted(SALE_CHECKS))
def test_version_1_1_0_is_1_0_0_with_the_sale_class_added_and_its_rule_stated(check):
    old_file, new_file, _, _ = SALE_CHECKS[check]
    old = load_definition_file(WICKETS / old_file)
    new = load_definition_file(WICKETS / new_file)
    assert (old.wicket_id, new.wicket_id) == (f"{check}/1.0.0", f"{check}/1.1.0")
    assert new.check == old.check == check
    assert SALE not in old.config["action_classes"]
    assert new.config["action_classes"] == [*old.config["action_classes"], SALE]
    assert {k: v for k, v in new.config.items() if k != "action_classes"} == \
        {k: v for k, v in old.config.items() if k != "action_classes"}
    assert new.semantics is not None and SALE in new.semantics


@pytest.mark.parametrize("check", sorted(SALE_CHECKS))
def test_the_pack_cites_version_1_1_0_and_neither_version_is_retired(check):
    cited = {c.wicket_id: c.definition_digest() for c in PACK.constraints}
    assert f"{check}/1.0.0" not in cited
    new_digest = cited[f"{check}/1.1.0"]
    old_digest = load_definition_file(WICKETS / SALE_CHECKS[check][0]).definition_digest()
    assert retired_entry(f"{check}/1.1.0", new_digest) is None
    assert retired_entry(f"{check}/1.0.0", old_digest) is None


# -- everyday 0.3.3 -------------------------------------------------------------


def test_the_frozen_0_3_3_loads_at_its_recorded_digest():
    pack = load_pack_dir(FROZEN_0_3_3_DIR)
    assert pack.pack_id == "asg/everyday/0.3.3"
    assert pack.definition_digest() == PACK_0_3_3_DIGEST


def test_the_catalog_pack_cites_the_same_sale_definitions_as_0_3_3():
    frozen = {c.wicket_id: c.definition_digest() for c in load_pack_dir(FROZEN_0_3_3_DIR).constraints}
    cited = {c.wicket_id: c.definition_digest() for c in PACK.constraints}
    for check in SALE_CHECKS:
        assert cited[f"{check}/1.1.0"] == frozen[f"{check}/1.1.0"]


def test_the_catalog_pins_the_taxonomy_this_engine_ships():
    assert PACK.taxonomy is not None
    assert (PACK.taxonomy.taxonomy_version, PACK.taxonomy.digest) == (TAXONOMY_VERSION, TAXONOMY_DIGEST)


def test_the_catalog_pack_does_not_load_under_another_taxonomy(tmp_path):
    copy = tmp_path / "everyday"
    shutil.copytree(PACK_DIR, copy)
    text = (copy / "pack.yaml").read_text()
    assert text.count(TAXONOMY_DIGEST) == 1
    (copy / "pack.yaml").write_text(text.replace(TAXONOMY_DIGEST, "0" * 64))
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(copy)
    assert exc_info.value.reason == TAXONOMY_PIN_MISMATCH


def test_a_sale_inside_every_limit_is_allowed_and_each_check_measured_it(decide):
    decision = decide(PACK, _sale(1))
    results = _results(decision)
    assert {check: results[check] for check in SALE_CHECKS} == dict.fromkeys(SALE_CHECKS, "pass")
    assert decision.outcome == ALLOW


@pytest.mark.parametrize("check", sorted(SALE_CHECKS))
def test_a_sale_past_one_limit_asks_citing_only_that_rule(decide, check):
    _, _, rule, fields = SALE_CHECKS[check]
    decision = decide(PACK, _sale(2, **fields))
    assert {c for c, r in _results(decision).items() if r == "fail"} == {check}
    assert _failing_rules(PACK, decision) == [rule]
    assert decision.outcome == ESCALATE
    assert decision.capsule["disposition"]["decision"] == "needs_input"


def test_caps_leaves_a_sale_out_of_scope(decide):
    """A sale far above every caps limit: caps records the class as out of
    scope, as caps/5.0.0 does for money.refund."""
    decision = decide(PACK, _sale(3, amount_minor=1_000_000))
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    assert caps.result == "n/a"
    assert caps.evidence["in_scope"] is False
    assert decision.outcome == ALLOW


# -- everyday 0.3.2, as the plugin vendors it -------------------------------------


def test_the_frozen_0_3_2_is_the_released_bytes_and_loads_at_its_digest():
    assert hashlib.sha256((FROZEN_0_3_2_DIR / "pack.yaml").read_bytes()).hexdigest() == PACK_0_3_2_FILE_SHA256
    pack = load_pack_dir(FROZEN_0_3_2_DIR)
    assert pack.pack_id == "asg/everyday/0.3.2"
    assert pack.taxonomy is None
    assert pack.definition_digest() == PACK_0_3_2_DIGEST


def test_every_definition_0_3_2_cites_resolves_in_the_catalog_and_is_not_retired():
    pack = load_pack_dir(FROZEN_0_3_2_DIR)
    shipped = {e.definition.wicket_id: e.digest for e in Catalog(WICKETS).list_entries()}
    for constraint in pack.constraints:
        digest = constraint.definition_digest()
        assert shipped[constraint.wicket_id] == digest
        assert retired_entry(constraint.wicket_id, digest) is None


def test_under_0_3_2_a_sale_with_changed_terms_is_still_allowed(decide):
    """The gap 0.3.3 closes, kept on the vendored pack: its decisions do not
    change under it."""
    pack = load_pack_dir(FROZEN_0_3_2_DIR)
    decision = decide(pack, _sale(4, material_fields_changed=2))
    assert _results(decision)["material_fields_changed"] == "n/a"
    assert decision.outcome == ALLOW
