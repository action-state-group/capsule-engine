# SPDX-License-Identifier: Apache-2.0
"""seller.single_commitment/1.0.0: one sale, several buyer threads, one
acceptance. Once an acceptance is sealed for a task authority and an item,
a commitment for the same task authority and item fails unless it cites
that acceptance AND is addressed to the same counterparty.

The two-thread reference vector (tests/fixtures/single-commitment/
two-thread.json) is replayed through a real engine; its steps name each
other by step id, never by capsule id, so it does not depend on a signer."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TypedDict

import pytest
import yaml

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import ConstraintOutcome, not_applicable_evidence
from capsule_engine.guards.checks import CONFIGURED_CHECKS
from capsule_engine.guards.classes import resolve
from capsule_engine.guards.engine import GuardDecision
from capsule_engine.guards.wickets import Catalog, WicketDefinition, load_definition_file
from capsule_engine.packs.loader import load_pack_dir

ROOT = Path(__file__).parent.parent / "capsule_engine"
CATALOG = ROOT / "guards" / "wickets" / "catalog_defs"
SINGLE = load_definition_file(CATALOG / "single_commitment.seller.yaml")
SPEND = load_fold(ROOT / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")
VECTOR = json.loads((Path(__file__).parent / "fixtures" / "single-commitment" / "two-thread.json").read_text())

SALE = "a" * 64
OTHER_SALE = "b" * 64
ITEM = "item/ref-1"
BUYER_A = "buyer/ref-a"
BUYER_B = "buyer/ref-b"


def _action(**overrides) -> Action:
    fields = dict(
        verb="offer",
        operator="household",
        developer="assistant@v1",
        action_class="marketplace.offer",
        amount_minor=175_000,
        currency="USD",
        target=BUYER_B,
        timestamp="2026-10-08T09:00:00Z",
        task_authority_ref=SALE,
        item_ref=ITEM,
    )
    fields.update(overrides)
    return Action(**fields)


def _engine(store, signer, definition: WicketDefinition = SINGLE) -> GuardEngine:
    return GuardEngine(ledger=store, caps_fold=SPEND, signer_provider=lambda: signer, wickets=(definition,))


def _constraint(decision):
    (out,) = [c for c in decision.constraints if c.id == "single_commitment"]
    return out


def _accept_in_a(engine, **overrides) -> str:
    fields = dict(verb="accept", action_id="accept/a", action_class="agreement.accept", target=BUYER_A,
                  timestamp="2026-10-08T08:00:00Z")
    fields.update(overrides)
    decision = engine.check(_action(**fields))
    assert decision.outcome == "allow"
    return decision.capsule["capsule_id"]


# -- the input shape ------------------------------------------------------------


def test_item_ref_is_one_optional_scalar_sealed_only_when_set(store, signer):
    hints = {f.name: f.type for f in dataclasses.fields(Action)}
    assert hints["item_ref"] == "str | None"
    assert Action.__dataclass_fields__["item_ref"].default is None
    engine = _engine(store, signer)
    bare = engine.check(_action(action_id="offer/bare", item_ref=None)).capsule["asg_payload"]
    assert "item_ref" not in bare
    sealed = engine.check(_action(action_id="offer/sealed")).capsule["asg_payload"]
    assert sealed["item_ref"] == ITEM


def test_the_definition_configures_the_new_check():
    assert SINGLE.wicket_id == "seller.single_commitment/1.0.0"
    assert SINGLE.check == "single_commitment"
    assert SINGLE.config["acceptance_classes"] == ["agreement.accept"]
    assert "agreement.accept" in SINGLE.config["commit_classes"]
    assert "communication.send" not in SINGLE.config["commit_classes"]
    assert "single_commitment" in CONFIGURED_CHECKS


def test_a_seller_pack_citing_it_validates(tmp_path):
    pack = {
        "pack_id": "test_pub/seller-everyday/0.1.0",
        "obligations": [{"id": "o1", "statement": SINGLE.wicket_id, "check": SINGLE.check}],
        "action_semantics": [{
            "action_type": "offer.make",
            "action_class": "marketplace.offer",
            "required_fields": ["amount_minor", "task_authority_ref", "item_ref", "target"],
            "optional_fields": ["cited_mandate_capsule_id"],
        }],
        "constraints": [{"wicket_ref": SINGLE.wicket_id, "digest": Catalog(CATALOG).get(SINGLE.wicket_id).digest}],
        "folds": [{"file": "seen.yaml"}],
    }
    (tmp_path / "pack.yaml").write_text(yaml.dump(pack))
    (tmp_path / "seen.yaml").write_text((ROOT / "folds" / "catalog_defs" / "disclosure.class_seen.yaml").read_text())
    assert [c.wicket_id for c in load_pack_dir(tmp_path).constraints] == [SINGLE.wicket_id]


# -- the two-thread reference vector --------------------------------------------


class Step(TypedDict):
    id: str
    action: dict[str, str | int]
    expect: dict[str, str]


@dataclasses.dataclass
class Replay:
    ids: dict[str, str]
    results: dict[str, tuple[GuardDecision, ConstraintOutcome]]


def _replay(store, signer, steps: list[Step]) -> Replay:
    engine = _engine(store, signer)
    replay = Replay(ids={}, results={})
    for step in steps:
        fields = dict(step["action"])
        cites = fields.pop("cites_step", None)
        if cites is not None:
            fields["cited_mandate_capsule_id"] = replay.ids[str(cites)]
        decision = engine.check(Action(**fields))
        if decision.capsule is not None:
            replay.ids[step["id"]] = decision.capsule["capsule_id"]
        replay.results[step["id"]] = (decision, _constraint(decision))
    return replay


@pytest.mark.parametrize("vector", VECTOR["vectors"], ids=[v["name"] for v in VECTOR["vectors"]])
def test_reference_vector(store, signer, vector):
    replay = _replay(store, signer, vector["steps"])
    for step in vector["steps"]:
        decision, out = replay.results[step["id"]]
        expect = step["expect"]
        assert out.result == expect["single_commitment"], step["id"]
        assert decision.outcome == expect["outcome"], step["id"]
        if "acceptance_step" in expect:
            assert out.evidence["acceptance_capsule_id"] == replay.ids[expect["acceptance_step"]], step["id"]
        if "missing_field" in expect:
            assert out.evidence == not_applicable_evidence(
                "single_commitment", in_scope=True, missing_field=expect["missing_field"])
        if expect["single_commitment"] == "n/a" and "missing_field" not in expect:
            assert out.evidence == not_applicable_evidence("single_commitment", in_scope=False)


def test_the_vector_covers_the_acceptance_cases():
    names = {v["name"] for v in VECTOR["vectors"]}
    assert {"a-accepts-then-b-commits", "b-commits-before-a-accepts", "b-non-commit-after-a-accepts",
            "b-cites-a-acceptance", "a-follows-its-own-acceptance", "commit-without-item-ref"} <= names


# -- the check, directly --------------------------------------------------------


def test_a_commitment_in_b_after_a_accepts_is_denied_naming_the_acceptance(store, signer):
    engine = _engine(store, signer)
    accepted = _accept_in_a(engine)
    decision = engine.check(_action(action_id="offer/b"))
    out = _constraint(decision)
    assert out.result == "fail"
    assert decision.outcome == "deny"
    assert out.evidence == {
        "task_authority_ref": SALE,
        "item_ref": ITEM,
        "acceptance_capsule_id": accepted,
        "acceptance_counterparty": BUYER_A,
        "counterparty": BUYER_B,
        "cites_acceptance": False,
    }


def test_b_citing_a_acceptance_still_fails(store, signer):
    """Citing the acceptance is not enough: the commitment must also be
    addressed to the counterparty that accepted."""
    engine = _engine(store, signer)
    accepted = _accept_in_a(engine)
    decision = engine.check(_action(action_id="offer/b-cites", cited_mandate_capsule_id=accepted))
    out = _constraint(decision)
    assert out.result == "fail"
    assert decision.outcome == "deny"
    assert out.evidence["cites_acceptance"] is True
    assert out.evidence["counterparty"] == BUYER_B


def test_a_follows_its_own_acceptance(store, signer):
    engine = _engine(store, signer)
    accepted = _accept_in_a(engine)
    out = _constraint(engine.check(
        _action(action_id="sale/a", action_class="marketplace.sale", target=BUYER_A, cited_mandate_capsule_id=accepted)
    ))
    assert out.result == "pass"
    assert out.evidence["acceptance_capsule_id"] == accepted


def test_a_without_citing_its_acceptance_fails(store, signer):
    engine = _engine(store, signer)
    _accept_in_a(engine)
    out = _constraint(engine.check(_action(action_id="sale/a-uncited", action_class="marketplace.sale",
                                           target=BUYER_A)))
    assert out.result == "fail"


def test_a_commitment_naming_no_counterparty_is_never_exempt(store, signer):
    engine = _engine(store, signer)
    accepted = _accept_in_a(engine)
    out = _constraint(engine.check(_action(action_id="sale/no-target", action_class="marketplace.sale", target=None,
                                           cited_mandate_capsule_id=accepted)))
    assert out.result == "fail"
    assert out.evidence["counterparty"] is None


def test_b_before_any_acceptance_passes(store, signer):
    out = _constraint(_engine(store, signer).check(_action(action_id="offer/b-early")))
    assert out.result == "pass"
    assert out.evidence["acceptance_capsule_id"] is None


def test_a_non_commit_message_in_b_after_acceptance_is_not_touched(store, signer):
    engine = _engine(store, signer)
    _accept_in_a(engine)
    decision = engine.check(_action(action_id="send/b", verb="send", action_class="communication.send",
                                    amount_minor=None))
    out = _constraint(decision)
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("single_commitment", in_scope=False)
    assert decision.outcome == "allow"


@pytest.mark.parametrize("field", ["item_ref", "task_authority_ref"])
def test_a_commitment_missing_a_key_field_is_n_a_naming_it(store, signer, field):
    engine = _engine(store, signer)
    _accept_in_a(engine)
    out = _constraint(engine.check(_action(action_id=f"offer/no-{field}", **{field: None})))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("single_commitment", in_scope=True, missing_field=field)


@pytest.mark.parametrize("overrides", [
    dict(task_authority_ref=OTHER_SALE),
    dict(item_ref="item/ref-2"),
], ids=["another-sale", "another-item"])
def test_an_acceptance_for_another_sale_or_item_does_not_count(store, signer, overrides):
    engine = _engine(store, signer)
    _accept_in_a(engine, **overrides)
    assert _constraint(engine.check(_action(action_id="offer/b"))).result == "pass"


def test_a_refused_acceptance_does_not_count(store, signer):
    """A record the guard refused never sealed an acceptance. The acceptance
    cites a mandate the ledger does not hold, so verify_before_dispatch
    refuses it, and the refusal is recorded."""
    engine = _engine(store, signer)
    refused = engine.check(_action(verb="accept", action_id="accept/refused", action_class="agreement.accept",
                                   target=BUYER_A, cited_mandate_capsule_id="0" * 64))
    assert refused.outcome == "deny"
    assert refused.capsule["disposition"]["decision"] == "reject"
    assert _constraint(engine.check(_action(action_id="offer/b"))).result == "pass"


def test_a_dry_run_acceptance_does_not_count(store, signer):
    engine = _engine(store, signer)
    engine.check(_action(verb="accept", action_id="accept/dry", action_class="agreement.accept", target=BUYER_A),
                 dry_run=True)
    assert _constraint(engine.check(_action(action_id="offer/b"))).result == "pass"


def test_the_rule_never_asks(store, signer):
    """An integrity failure refuses, even on a class an approver could clear.
    The seller's commit classes have no approver, so the rule is checked
    here on one that has."""
    assert resolve("money.transfer").approver_role is not None
    engine = _engine(store, signer, dataclasses.replace(
        SINGLE, config={**SINGLE.config, "commit_classes": ["money.transfer"]}))
    _accept_in_a(engine)
    decision = engine.check(_action(action_id="transfer/b", verb="transfer", action_class="money.transfer"))
    assert _constraint(decision).result == "fail"
    assert decision.outcome == "deny"


# -- every config field fails its mutant ----------------------------------------


@pytest.mark.parametrize("field,mutated,expect_real,expect_mutant", [
    ("commit_classes", ["marketplace.sale"], "fail", "n/a"),
    ("acceptance_classes", ["marketplace.sale"], "fail", "pass"),
])
def test_config_field_mutants(store, signer, field, mutated, expect_real, expect_mutant):
    engine = _engine(store, signer)
    _accept_in_a(engine)
    assert _constraint(engine.check(_action(action_id="offer/real"))).result == expect_real
    mutant_def = dataclasses.replace(SINGLE, config={**SINGLE.config, field: mutated})
    mutant = _engine(store, signer, mutant_def)
    assert _constraint(mutant.check(_action(action_id="offer/mutant"))).result == expect_mutant


def test_every_config_field_has_a_mutant():
    assert set(SINGLE.config) == {"commit_classes", "acceptance_classes"}
