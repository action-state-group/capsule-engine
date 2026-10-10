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
import dataclasses
import importlib.util
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
    DealClaims,
    action_for_check_input,
    deal_claim_statements,
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
# Each buyer thread's deal id, as capsulectl wrote it (re-pinned with the fixture on a rebuild).
DEAL_A, DEAL_B = "deal-a4a44a1583055667", "deal-c8f7695110ba493e"
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
FROZEN_0_1_2 = load_pack_dir(Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.2")
S05 = "s05-required-statement-made-first"
OPERATOR = "synthetic-seller"


# -- building a bound claim ----------------------------------------------------------


def _claim(*, deal: str = "deal-a", record_type: str = "claim", **body) -> tuple[dict, dict]:
    """A capsule and the ``x-deal-v0`` record it binds by digest."""
    shown = {"body": {"source_kind": "agent", "class": "condition", "text_commitment": "ab" * 32, **body},
             "x-deal-v0": {"record_type": record_type, "deal_id": deal, "profile": "x-deal-v0", "seq": 3,
                           "at": "2026-10-10T00:41:06Z"}}
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
    assert stated["operator"] == OPERATOR and stated["timestamp"] == shown["x-deal-v0"]["at"]
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


def _envelope(*entries: dict, at: object = "2026-10-10T00:41:07Z") -> dict:
    return {"record": {"operator": OPERATOR, "timestamp": at}, "history": list(entries),
            "history_scope": {"complete": True, "days": 31, "max_records": 1000}}


def test_live_writes_the_replays_record_plus_its_marker(store):
    """The record a live check writes for a claim is the one the replay
    writes, with ``live_history`` ``statement`` added to its payload."""
    capsule, shown = _claim()
    entry = {**capsule, "agent_input": shown, "item_ref": "3f" * 32}
    history_ledger(_envelope(entry), store)
    (written,) = [r.capsule for r in store.scan()]
    replayed = statement_record(capsule, shown)
    assert statement_for_history_entry(entry) == replayed
    assert written == {**replayed, "asg_payload": {**replayed["asg_payload"], "live_history": "statement"}}


@pytest.mark.parametrize("at", ["2026-10-10T00:41:05Z", None, "yesterday"],
                         ids=["checked before the claim", "no checked time", "checked time unreadable"])
def test_live_counts_no_claim_sealed_after_the_check_or_when_its_time_is_unknown(store, at):
    capsule, shown = _claim()
    written = history_ledger(_envelope({**capsule, "agent_input": shown}, at=at), store)
    assert (written.statement, written.unread) == (0, 0)
    assert _check(store, _action()).result == "fail"


def test_live_counts_a_claim_sealed_in_the_checks_own_second(store):
    capsule, shown = _claim()
    history_ledger(_envelope({**capsule, "agent_input": shown}, at=capsule["timestamp"]), store)
    assert _check(store, _action()).result == "pass"


def test_a_live_claim_never_adds_to_a_spend_window(store):
    """Under a caps fold that counts every executed history act, a claim in
    the history is still only a statement: no spend record follows it."""
    capsule, shown = _claim()
    spend = load_fold(FOLDS / "spend.weekly.v3.1.yaml")
    history_ledger(_envelope({**capsule, "agent_input": shown}), store, caps_fold=spend)
    assert [r.capsule["asg_payload"].get("live_history") for r in store.scan()] == ["statement"]


def test_a_claim_given_twice_is_one_statement_live(store):
    capsule, shown = _claim()
    entry = {**capsule, "agent_input": shown}
    written = history_ledger(_envelope(entry, copy.deepcopy(entry)), store)
    assert written.statement == 1
    assert _check(store, _action()).evidence["stated_counts"] == {"condition": 1}


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
        f"{DEAL_A}/")]
    records.remove(claim)
    offer = next(i for i, r in enumerate(records) if r["action_id"] == f"{DEAL_A}/4")
    records.insert(offer + 1, claim)
    decisions = _replay(records, disclosed)
    assert _s05(decisions[f"{DEAL_A}/4"]).result == "fail"
    assert _s05(decisions[f"{DEAL_A}/8"]).result == "pass"


def test_a_claim_given_twice_is_one_statement_in_the_replay():
    """Overlapping sources give the claims twice; each is written once."""
    records, disclosed = _bundle_records()
    claims = [r for r in records if _is_claim(disclosed.get(r["capsule_id"]))]
    decisions = _replay(records + claims, disclosed)
    for action_id, decision in _offers_and_commits(decisions).items():
        assert _s05(decision).evidence["stated_counts"] == {"condition": 1}, action_id


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
        ("condition", DEAL_A), ("condition", DEAL_B)]
    assert json.loads((FIXTURE / "inputs" / "claim-condition.json").read_text())["class"] == "condition"


# -- the deal's claims beside the checked record (AMENDMENT 9) ----------------------


_STEP = importlib.util.spec_from_file_location("add_deal_claims", FIXTURE / "add_deal_claims.py")
_step = importlib.util.module_from_spec(_STEP)
_STEP.loader.exec_module(_step)


def _input_with_claims(name: str = "a-commit-1900") -> dict:
    """A real check input with ``record.deal_claims`` from the fixture step."""
    envelope = json.loads((FIXTURE / "check-inputs" / f"{name}.json").read_text())
    bundle = json.loads(OWN[0 if name.startswith("a-") else 1].read_text())
    return _step.with_deal_claims(envelope, bundle)


def test_a_deal_claim_is_the_statement_the_replay_writes_for_that_claim():
    """Same claim, same record: the claim's sealed ``at`` is its capsule's
    ``timestamp`` in the fixture, so even the ``capsule_id`` agrees."""
    record = _input_with_claims()["record"]
    (stated,) = deal_claim_statements(record).statements
    records, disclosed = _bundle_records()
    (claim,) = [r for r in records if r["capsule_id"] == record["deal_claims"][0]["capsule_id"]]
    assert stated == statement_record(claim, disclosed[claim["capsule_id"]])


def test_deal_claims_stay_out_of_the_bound_capsule():
    """The action is the one the record states without them."""
    record = _input_with_claims()["record"]
    bare = {k: v for k, v in record.items() if k != "deal_claims"}
    assert action_for_check_input(record) == action_for_check_input(bare)


def _with(record: dict, **change) -> dict:
    record = copy.deepcopy(record)
    record["deal_claims"][0].update(change)
    record["deal_claims"][0] = {k: v for k, v in record["deal_claims"][0].items() if v is not None}
    return record


@pytest.mark.parametrize("change", [
    {"class": None}, {"class": ""}, {"source_kind": "counterparty"}, {"source_kind": None},
    {"record_digest": "AB" * 32}, {"record_digest": "ab" * 31}, {"capsule_id": None},
    {"text_commitment": None}, {"at": "yesterday"}, {"at": "2026-10-10T00:41:06"}, {"at": None},
    {"at": "2030-01-01T00:00:00Z"},
], ids=["no class", "empty class", "the buyer's", "no source_kind", "digest upper case", "digest short",
        "no capsule_id", "no commitment", "at not a time", "at with no zone", "no at", "at after the check"])
def test_a_deal_claim_out_of_shape_is_ignored_and_named_never_echoed(change):
    record = _with(_input_with_claims()["record"], **change)
    claims = deal_claim_statements(record)
    assert (claims.statements, claims.ignored) == ((), True)
    action = action_for_check_input(record)
    assert action.ignored_inputs == ("deal_claims",)
    # Nothing from the entry is carried: the action is the one without it, but for the name.
    bare = action_for_check_input({k: v for k, v in record.items() if k != "deal_claims"})
    assert dataclasses.replace(action, ignored_inputs=()) == bare


@pytest.mark.parametrize("value", ["condition", {"class": "condition"}, 5, True, [None], [["condition"]]],
                         ids=["a string", "a mapping", "a number", "a boolean", "a null entry", "a list entry"])
def test_deal_claims_not_a_list_of_entries_are_ignored_and_named(value):
    record = {**_input_with_claims()["record"], "deal_claims": value}
    assert deal_claim_statements(record) == DealClaims(statements=(), ignored=True)
    assert action_for_check_input(record).ignored_inputs == ("deal_claims",)


def test_deal_claims_on_a_record_its_capsule_does_not_bind_are_ignored():
    record = copy.deepcopy(_input_with_claims()["record"])
    record["agent_input"]["body"]["amount_minor"] += 1
    assert deal_claim_statements(record) == DealClaims(statements=(), ignored=True)


def test_one_bad_entry_leaves_the_good_one_counted_and_names_the_input():
    record = copy.deepcopy(_input_with_claims()["record"])
    record["deal_claims"].append({**record["deal_claims"][0], "class": ""})
    claims = deal_claim_statements(record)
    assert len(claims.statements) == 1 and claims.ignored


def test_a_deal_claim_given_twice_is_one_statement_and_delivery_promise_is_delivery_date():
    record = copy.deepcopy(_input_with_claims()["record"])
    record["deal_claims"].append(copy.deepcopy(record["deal_claims"][0]))
    assert len(deal_claim_statements(record).statements) == 1
    record = _with(_input_with_claims()["record"], **{"class": "delivery_promise"})
    (stated,) = deal_claim_statements(record).statements
    assert stated["asg_payload"][STATED]["class"] == "delivery_date"


def test_a_deal_claim_in_the_checks_own_second_counts():
    record = _input_with_claims()["record"]
    record = _with(record, at=record["timestamp"])
    assert len(deal_claim_statements(record).statements) == 1


def test_no_deal_claims_is_nothing_ignored():
    record = {k: v for k, v in _input_with_claims()["record"].items() if k != "deal_claims"}
    assert deal_claim_statements(record) == DealClaims(statements=(), ignored=False)
    assert deal_claim_statements({**record, "deal_claims": []}) == DealClaims(statements=(), ignored=False)


def test_a_statement_is_dated_by_the_claims_sealed_at_never_its_capsule_timestamp():
    """capsulectl reads the clock once: the claim's ``at`` and its capsule's
    ``timestamp`` are the same instant, but their strings may differ. The
    replay dates a statement by ``at``, the value a live check receives in
    ``deal_claims``, so live and replay write the same statement record."""
    capsule, shown = _claim()
    capsule = {**capsule, "timestamp": "2026-10-10T00:41:06.000Z"}
    stated = statement_record(capsule, shown)
    assert stated["timestamp"] == "2026-10-10T00:41:06Z"
    entry = {**capsule, "agent_input": shown}
    assert statement_for_history_entry(entry) == stated
