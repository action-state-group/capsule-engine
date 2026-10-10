# SPDX-License-Identifier: Apache-2.0
"""A claim the agent made in a deal counts as a statement for s05.

capsulectl seals a seller's statement to a buyer as an ``x-deal-v0`` claim
record with ``body.source_kind`` ``agent`` and a ``body.class``. It states no
act, so under ``required_disclosure/1.0.0`` nothing carried its class and every
offer and commit after it failed. ``required_disclosure/1.1.0`` also counts
the statements in the action's deal (``disclosure.statement_seen/1.0.0``),
written by one reader (``report/replay.py``, ``statement_record``) for the
replay and for a live check's history alike. The real two-thread fixture is
read in ``test_live_history.py``; this file pins the reader, the check and the
replay's order on that fixture's records.
"""
from __future__ import annotations

import copy
import json
import tempfile
from functools import cache
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

import capsule_engine
from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardDecision
from capsule_engine.guards.capsule import not_applicable_evidence
from capsule_engine.guards.checks import RUNNABLE_CHECKS, check_dedupe
from capsule_engine.guards.checks.required_disclosure import check_required_disclosure
from capsule_engine.guards.statements import CLASS_ALIASES, STATED
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.report.live_history import history_ledger
from capsule_engine.report.replay import (
    load_disclosed,
    load_records,
    load_withheld,
    replay,
    statement_for_history_entry,
    statement_record,
)

ROOT = Path(capsule_engine.__file__).parent
FOLDS = ROOT / "folds" / "catalog_defs"
WICKETS = ROOT / "guards" / "wickets" / "catalog_defs"
CLASS_SEEN = load_fold(FOLDS / "disclosure.class_seen.yaml")
STATEMENT_SEEN = load_fold(FOLDS / "disclosure.statement_seen.yaml")
V1_0 = load_definition_file(WICKETS / "required_disclosure.yaml")
V1_1 = load_definition_file(WICKETS / "required_disclosure.v1.1.yaml")
REPRESENTATION_CLASSES = V1_1.config["representation_classes"]
FIXTURE = Path(__file__).parent / "fixtures" / "live-history"
OWN = (FIXTURE / "buyer-a.bundle.json", FIXTURE / "buyer-b.bundle.json")
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
FROZEN_0_1_2 = load_pack_dir(Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.2")
S05 = "s05-required-statement-made-first"
OPERATOR = "synthetic-seller"


# -- building a bound claim ----------------------------------------------------------


def _claim(*, deal: str = "deal-a", record_type: str = "claim", **body) -> tuple[dict, dict]:
    """A capsule and the ``x-deal-v0`` record it binds by digest."""
    shown = {"body": {"source_kind": "agent", "class": "condition", "text_commitment": "ab" * 32, **body},
             "x-deal-v0": {"record_type": record_type, "deal_id": deal, "profile": "x-deal-v0", "seq": 3}}
    shown["body"] = {k: v for k, v in shown["body"].items() if v is not None}
    capsule = {"capsule_id": json_digest(shown)[:8] + "c" * 56, "operator": OPERATOR, "action_type": "fyi",
               "timestamp": "2026-10-10T00:41:06Z",
               "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(shown)}}}
    return capsule, shown


def test_an_agent_claim_with_a_class_is_a_statement_in_its_deal():
    capsule, shown = _claim()
    stated = statement_record(capsule, shown)
    assert stated["asg_payload"] == {STATED: {"class": "condition", "source_kind": "agent", "deal_id": "deal-a",
                                              "claim": capsule["capsule_id"]}}
    assert stated["operator"] == OPERATOR and stated["timestamp"] == capsule["timestamp"]
    assert stated["capsule_id"] == json_digest({k: v for k, v in stated.items() if k != "capsule_id"})
    assert "disposition" not in stated and "target" not in stated["asg_payload"]


def test_delivery_promise_is_read_as_delivery_date_and_is_the_only_alias():
    assert CLASS_ALIASES == {"delivery_promise": "delivery_date"}
    capsule, shown = _claim(**{"class": "delivery_promise"})
    assert statement_record(capsule, shown)["asg_payload"][STATED]["class"] == "delivery_date"
    for cls in REPRESENTATION_CLASSES:
        capsule, shown = _claim(**{"class": cls})
        assert statement_record(capsule, shown)["asg_payload"][STATED]["class"] == cls


@pytest.mark.parametrize("change", [
    {"source_kind": "counterparty"},
    {"source_kind": None},
    {"class": None},
    {"class": ""},
    {"record_type": "message"},
    {"record_type": "evidence"},
], ids=["the buyer's claim", "no source_kind", "no class", "empty class", "a message", "evidence"])
def test_anything_but_an_agent_claim_with_a_class_is_no_statement(change):
    record_type = change.pop("record_type", "claim")
    capsule, shown = _claim(record_type=record_type, **change)
    assert statement_record(capsule, shown) is None


def test_a_claim_its_capsule_does_not_bind_is_no_statement():
    capsule, shown = _claim()
    tampered = copy.deepcopy(shown)
    tampered["body"]["class"] = "warranty"
    assert statement_record(capsule, tampered) is None
    assert statement_record(capsule, None) is None


def test_a_claim_naming_two_deals_is_no_statement():
    capsule, shown = _claim()
    shown["chain_id"] = "deal-b"
    capsule["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(shown)
    assert statement_record(capsule, shown) is None
    shown["chain_id"] = "deal-a"
    capsule["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(shown)
    assert statement_record(capsule, shown)["asg_payload"][STATED]["deal_id"] == "deal-a"


def test_a_personal_data_disclosure_class_is_never_read_as_a_statement():
    """A disclosure's ``body.fields[].class`` is the user's data given out."""
    shown = {"body": {"fields": [{"class": "condition"}, {"class": "home_address"}], "source_kind": "agent"},
             "x-deal-v0": {"record_type": "disclosure", "deal_id": "deal-a"}}
    capsule = {"capsule_id": "d" * 64, "operator": OPERATOR, "timestamp": "2026-10-10T00:41:06Z",
               "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(shown)}}}
    assert statement_record(capsule, shown) is None


def test_a_history_entry_is_read_by_the_same_reader():
    capsule, shown = _claim()
    entry = {**capsule, "agent_input": shown, "item_ref": "3f" * 32}
    assert statement_for_history_entry(entry) == statement_record(capsule, shown)


# -- the check -------------------------------------------------------------------------


def _action(**overrides) -> Action:
    fields = dict(verb="offer", operator=OPERATOR, developer="capsulectl-deal", action_class="marketplace.offer",
                  amount_minor=0, currency="USD", target="payee-fp:x:" + "b" * 64, deal_id="deal-a",
                  timestamp="2026-10-10T00:41:07Z", taxonomy_version="6")
    fields.update(overrides)
    return Action(**fields)


def _check(store, action: Action, *, definition=V1_1):
    statement = STATEMENT_SEEN if "statement_fold_id" in definition.config else None
    return check_required_disclosure(
        action, store, definition=CLASS_SEEN, representation_classes=REPRESENTATION_CLASSES,
        required_classes=definition.config["required_classes"], action_classes=definition.config["action_classes"],
        statement_definition=statement,
    ).constraint


def _state(store, **change) -> None:
    capsule, shown = _claim(**change)
    store.append(dict(statement_record(capsule, shown)), consequential=False)


def test_a_condition_claim_in_the_deal_passes(store):
    _state(store)
    out = _check(store, _action())
    assert out.result == "pass"
    assert out.evidence["stated_counts"] == {"condition": 1}
    assert out.evidence["prior_counts"] == {"condition": 0}
    assert out.evidence["scope"] == {"operator": OPERATOR, "target": _action().target, "deal_id": "deal-a"}
    assert out.evidence["statement_fold"] == STATEMENT_SEEN.definition_digest()


@pytest.mark.parametrize("change", [{"deal": "deal-b"}, {"class": "price"}],
                         ids=["in another deal", "of another class"])
def test_a_claim_that_is_not_this_deals_condition_fails(store, change):
    _state(store, **change)
    out = _check(store, _action())
    assert (out.result, out.evidence["missing_classes"]) == ("fail", ["condition"])
    assert "an approval does not waive it" in out.reason


def test_another_operators_claim_does_not_count(store):
    capsule, shown = _claim()
    stated = statement_record({**capsule, "operator": "someone-else"}, shown)
    store.append(dict(stated), consequential=False)
    assert _check(store, _action()).result == "fail"


def test_with_no_deal_only_the_counterparty_is_read_and_with_neither_it_is_n_a(store):
    _state(store)
    assert _check(store, _action(deal_id=None)).result == "fail"
    out = _check(store, _action(deal_id=None, target=None))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("required_disclosure", in_scope=True, missing_field="target")


def test_with_no_target_the_deals_claim_still_counts(store):
    _state(store)
    assert _check(store, _action(target=None)).result == "pass"


def test_version_1_0_never_reads_a_statement(store):
    _state(store)
    out = _check(store, _action(), definition=V1_0)
    assert (out.result, out.evidence["prior_counts"]) == ("fail", {"condition": 0})
    assert "stated_counts" not in out.evidence


def test_1_1_is_1_0_with_only_the_statement_fold_added():
    assert {k: v for k, v in V1_1.config.items() if not k.startswith("statement_")} == V1_0.config
    assert V1_1.config["statement_fold_id"] == STATEMENT_SEEN.fold_id
    assert V1_1.config["statement_fold_digest"] == STATEMENT_SEEN.definition_digest()


def test_a_statement_is_never_an_act_dedupe_can_match(store):
    _state(store)
    assert check_dedupe(_action(), store).constraint.result == "pass"


def test_a_live_claim_with_the_sales_item_leaves_s11_and_the_history_alone(store):
    """A claim entry in a live history is a statement, never an unread act
    of the sale, whatever ``item_ref`` it carries beside it."""
    capsule, shown = _claim()
    envelope = {"record": {"operator": OPERATOR, "timestamp": "2026-10-10T00:41:07Z"},
                "history": [{**capsule, "agent_input": shown, "item_ref": "3f" * 32}],
                "history_scope": {"complete": True, "days": 31, "max_records": 1000}}
    written = history_ledger(envelope, store)
    assert (written.complete, written.statement, written.unread, written.disposition) == (True, 1, 0, 0)
    (row,) = [r.capsule for r in store.scan()]
    assert row["asg_payload"]["live_history"] == "statement" and "item_ref" not in row["asg_payload"]


# -- the replay, on the real fixture's records --------------------------------------


def _bundle_records() -> tuple[list[dict], dict[str, dict]]:
    return load_records(OWN), load_disclosed(OWN)


def _is_claim(shown: dict | None) -> bool:
    return ((shown or {}).get("x-deal-v0") or {}).get("record_type") == "claim"


def _replay(records: list[dict], disclosed: dict[str, dict], pack=PACK) -> dict[str, GuardDecision]:
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe")
        resolved = installed.resolved
        result = replay(
            records, caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
            manifest_digest=resolved.manifest_digest, disclosed=disclosed, withheld=load_withheld(OWN),
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack,
        )
    return {s.record["action_id"]: s.decision for s in result.decisions}


def _s05(decision: GuardDecision):
    (found,) = [c for c in decision.constraints if c.id == "required_disclosure"]
    return found


def _offers_and_commits(decisions: dict[str, GuardDecision]) -> dict[str, GuardDecision]:
    return {k: d for k, d in decisions.items() if d.capsule is not None and _s05(d).result != "n/a"}


@cache
def _as_bundled() -> dict[str, GuardDecision]:
    return _replay(*_bundle_records())


def test_every_offer_and_commit_after_its_threads_claim_passes_in_the_replay():
    checked = _offers_and_commits(_as_bundled())
    assert len(checked) == 5
    for action_id, decision in checked.items():
        assert (_s05(decision).result, _s05(decision).evidence["stated_counts"]) == ("pass", {"condition": 1}), (
            action_id)


def test_with_the_claims_left_out_every_offer_and_commit_asks_for_the_statement():
    records, disclosed = _bundle_records()
    kept = [r for r in records if not _is_claim(disclosed.get(r["capsule_id"]))]
    assert len(kept) == len(records) - 2
    checked = _offers_and_commits(_replay(kept, disclosed))
    assert len(checked) == 5
    for action_id, decision in checked.items():
        assert _s05(decision).result == "fail", action_id
        assert decision.outcome in ("escalate", "deny"), action_id


def test_a_claim_made_after_the_offer_does_not_count_for_it():
    """Ledger order is the order made: moved after A's offer, A's claim counts
    for A's commits but not the offer."""
    records, disclosed = _bundle_records()
    (claim,) = [r for r in records if _is_claim(disclosed.get(r["capsule_id"])) and r["action_id"].startswith(
        "deal-0e39b9755240e242/")]
    records.remove(claim)
    offer = next(i for i, r in enumerate(records) if r["action_id"] == "deal-0e39b9755240e242/4")
    records.insert(offer + 1, claim)
    decisions = _replay(records, disclosed)
    assert _s05(decisions["deal-0e39b9755240e242/4"]).result == "fail"
    assert _s05(decisions["deal-0e39b9755240e242/8"]).result == "pass"


def test_0_1_3_replays_the_fixture_as_0_1_2_except_s05():
    """The statement records move nothing but s05: every other constraint of
    every decision has the same result under both versions."""
    old, new = _replay(*_bundle_records(), pack=FROZEN_0_1_2), _as_bundled()
    assert list(old) == list(new)
    for action_id in new:
        before = {c.id: c.result for c in old[action_id].constraints}
        after = {c.id: c.result for c in new[action_id].constraints}
        assert {k for k in after if after[k] != before[k]} <= {"required_disclosure"}, action_id
    moved = {k for k in new if _s05(new[k]).result != _s05(old[k]).result}
    assert moved == set(_offers_and_commits(new))


def test_the_fixture_claims_are_the_ones_capsulectl_sealed():
    """Both threads' claims: source_kind agent, class condition, in their own deal."""
    records, disclosed = _bundle_records()
    stated = [statement_record(r, disclosed.get(r["capsule_id"])) for r in records]
    stated = [s for s in stated if s is not None]
    assert [(s["asg_payload"][STATED]["class"], s["asg_payload"][STATED]["deal_id"]) for s in stated] == [
        ("condition", "deal-0e39b9755240e242"), ("condition", "deal-f9d99d4407f3eba8")]
    assert json.loads((FIXTURE / "inputs" / "claim-condition.json").read_text())["class"] == "condition"
