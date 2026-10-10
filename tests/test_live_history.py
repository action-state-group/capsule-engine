# SPDX-License-Identifier: Apache-2.0
"""A live check decides on a ledger built from its check input's history.

``fixtures/live-history/`` holds what capsulectl wrote, byte for byte
(``build.sh`` and ``README.md`` there): one sale, two buyer threads. Buyer A
is offered 1900, accepts, and the seller commits and acts; then buyer B is
offered 1850, accepts, and the seller's commit to B is checked; then the
seller's commit to A is checked again. ``check-inputs/`` holds the
external-check-input/v0 of each check.

Every act in those histories is sealed ``fyi`` with no disposition, as
capsulectl seals every act but a payment today. Each check input is decided
five ways: as sealed; with a sealed ``accept`` disposition added to every
history act (what capsulectl will seal once the act types are registered
effect types; synthetic, so those capsules no longer verify); with its
history removed; and, with that disposition, once with the ``item_ref``
removed from the history's accepted commitments and once with it removed
from the checked record; and, with that disposition, with every claim the
agent made in either thread before the check added to the history beside
the sale's ``item_ref`` (what capsulectl does not send today; synthetic, from
the claim records the bundles seal); and, with that disposition, with the
checked record's ``deal_claims`` (AMENDMENT 9) added by the documented
fixture step ``add_deal_claims.py``, which capsulectl does not pass yet
(``HISTORIES``). ``expected_live.json`` is every decision and the ledger each
was decided on, in sorted canonical JSON, compared byte for byte here and
handed to the Go plugin. Regenerate it with ``python -m tests.test_live_history``.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import json
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import TypedDict

import pytest
from capsule_ledger.ledger import LedgerStore

import capsule_engine
from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardDecision, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import not_applicable_evidence
from capsule_engine.guards.checks import RUNNABLE_CHECKS, check_dedupe
from capsule_engine.guards.engine import NOT_EVALUABLE
from capsule_engine.guards.history_state import (
    DISPOSITION,
    INCOMPLETE,
    LIVE_HISTORY,
    NO_DISPOSITION,
    STATEMENT,
    UNREAD,
)
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.live_history import HistoryLedger, history_ledger
from capsule_engine.report.replay import (
    action_for_check_input,
    action_for_history_entry,
    load_disclosed,
    load_records,
    load_withheld,
    replay,
)

ROOT = Path(capsule_engine.__file__).parent
FIXTURE = Path(__file__).parent / "fixtures" / "live-history"
OWN = (FIXTURE / "buyer-a.bundle.json", FIXTURE / "buyer-b.bundle.json")
INPUTS = ("a-offer-1900", "a-commit-1900", "b-offer-1850", "b-commit-1850", "a-commit-again-1900")
EXPECTED = FIXTURE / "expected_live.json"
_STEP = importlib.util.spec_from_file_location("add_deal_claims", FIXTURE / "add_deal_claims.py")
_step = importlib.util.module_from_spec(_STEP)
_STEP.loader.exec_module(_step)
with_deal_claims = _step.with_deal_claims
FIXTURE_SHA256 = {
    "buyer-a.bundle.json": "fff68aee95e7ade7c136a0cd5f4e9aa95f5af474b1beade2e90e21305fa519b7",
    "buyer-b.bundle.json": "2b4bd231e0b3a33813827a334691b0705de2e860cf6874b5cfd7e2aecbd6fea3",
    "check-inputs/a-commit-1900.json": "9c52de981c3c143af4edd0155dcd78e92157bafd8b4518fb7f9b2f7ad16cac2d",
    "check-inputs/a-commit-again-1900.json": "2dfe6cc733b6e950a3e5d9fc96ffec63d833442cccb737f06feee7c1000519a2",
    "check-inputs/a-offer-1900.json": "77ba3f4ffe3a007d23243430bd4739d6b09e96c5d0563cc96ba16be5077e6a3d",
    "check-inputs/b-commit-1850.json": "d543da57152c2ab946697e6bf370898057838f1181a25531160d5ac0251d4111",
    "check-inputs/b-offer-1850.json": "7eec70ffedd4a54a2162503ad1f4c8e96198f51e22b5c08360becaa0e43bb3ee",
}
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
PRODUCER = {"commit": "424e79479c40138fcb425674a2ec7c4e540ce478", "name": "capsulectl",
            "version": "v0.1.0-rc14-17-g424e794"}
S11 = "s11-one-commitment-per-sale"
SINGLE = load_definition_file(ROOT / "guards" / "wickets" / "catalog_defs" / "single_commitment.seller.yaml")
SPEND = load_fold(ROOT / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")

# How each check input is given to the engine: its history as sealed, with
# an accept disposition on every act, with none; with the disposition and
# no item_ref on its accepted commitments; and with the disposition and no
# item_ref on the checked record.
AS_SEALED, DISPOSED, ABSENT = "as-sealed", "disposition-accept", "absent"
HISTORY_NO_ITEM, RECORD_NO_ITEM = "disposition-accept-commit-names-no-item", "disposition-accept-record-names-no-item"
CLAIMS = "disposition-accept-claims-in-history"
DEAL_CLAIMS = "disposition-accept-record-deal-claims"
HISTORIES = (AS_SEALED, DISPOSED, ABSENT, HISTORY_NO_ITEM, RECORD_NO_ITEM, CLAIMS, DEAL_CLAIMS)
ACCEPTANCE_CLASS = "agreement.accept"


class LedgerRow(TypedDict):
    capsule_id: str
    live_history: str
    decision: str | None
    action_class: str | None
    deal_id: str | None
    item_ref: str | None
    task_authority_ref: str | None
    stated: str | None


class LiveDecision(TypedDict):
    input: str
    history: str
    ledger: list[LedgerRow]
    complete: bool
    outcome: str
    verdict: str
    single_commitment: dict[str, object]
    rules: dict[str, str]


class LiveDocument(TypedDict):
    pack: dict[str, str]
    producer: dict[str, str]
    decisions: list[LiveDecision]


# Reads the vendored fixture: the test's decoding boundary.
def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_input(name: str) -> dict:
    return _json(FIXTURE / "check-inputs" / f"{name}.json")


def claim_entries(before: str, item_ref: str) -> list[dict]:
    """Every claim the agent made in either thread sealed by ``before``,
    as a history entry: its capsule with its disclosed ``agent_input`` (what
    capsulectl puts in the history for an act) and the sale's ``item_ref``
    beside it, newest first."""
    entries = []
    for path in OWN:
        bundle = _json(path)
        for record in bundle["records"]:
            shown = (bundle["disclosures"].get(record["capsule_id"]) or {}).get("agent_input")
            if (shown or {}).get("x-deal-v0", {}).get("record_type") == "claim" and record["timestamp"] <= before:
                entries.append({**record, "agent_input": shown, "item_ref": item_ref})
    return sorted(entries, key=lambda e: e["timestamp"], reverse=True)


def _thread_bundle(capsule_id: str) -> dict:
    """The thread's own bundle that holds the checked record."""
    (bundle,) = [b for b in map(_json, OWN) if any(r["capsule_id"] == capsule_id for r in b["records"])]
    return bundle


def _given(envelope: dict, history: str) -> dict:
    """``envelope`` with its history given as ``history`` says."""
    envelope = copy.deepcopy(envelope)
    if history in (DISPOSED, HISTORY_NO_ITEM, RECORD_NO_ITEM, CLAIMS, DEAL_CLAIMS):
        for entry in envelope["history"]:
            entry["disposition"] = {"decision": "accept"}
    if history == DEAL_CLAIMS:
        envelope = with_deal_claims(envelope, _thread_bundle(envelope["record"]["capsule_id"]))
    if history == CLAIMS:
        envelope["history"] = sorted(
            [*envelope["history"], *claim_entries(envelope["record"]["timestamp"], envelope["item_ref"])],
            key=lambda e: e["timestamp"], reverse=True)
    if history == HISTORY_NO_ITEM:
        for entry in envelope["history"]:
            if entry["agent_input"]["body"]["action_class"] == ACCEPTANCE_CLASS:
                del entry["item_ref"]
    elif history == RECORD_NO_ITEM:
        del envelope["item_ref"]
    elif history == ABSENT:
        del envelope["history"]
        del envelope["history_scope"]
    return envelope


@dataclasses.dataclass(frozen=True)
class Live:
    decision: GuardDecision
    ledger: list[dict]
    written: HistoryLedger


def _live_engine(store: LedgerStore, tmp: str) -> GuardEngine:
    installed = install_pack(PACK, project_dir=Path(tmp) / "live-project", mode="observe")
    resolved = installed.resolved
    wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
    gates, asks = engine_ask_sets(installed.pack, wickets)
    signer = LocalSigner(key_id="live-history", secret=b"live-history-fixture")
    return GuardEngine(
        ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
        caps_minor=resolved.caps_minor() or {}, manifest_digest=resolved.manifest_digest, wickets=wickets,
        ask_gate_selectors=gates, ask_wickets=asks, evaluate_under_record_taxonomy=True,
    )


def _decide(envelope: dict, *, with_history: bool = True) -> Live:
    """The decision on one check input under the seller pack, by a fresh
    engine whose ledger is the input's history (``history_ledger``), or empty
    when ``with_history`` is false (the live path before it)."""
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        engine = _live_engine(store, tmp)
        written = history_ledger(envelope, store) if with_history else HistoryLedger(True, 0, 0, 0)
        ledger = [r.capsule for r in store.scan()]
        action = action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref"))
        decision = engine.check(
            action,
            task_authority_record=envelope.get("task_authority_record"),
            commercial_bounds_opening=envelope.get("commercial_bounds_opening"),
        )
        return Live(decision=decision, ledger=ledger, written=written)


@cache
def _live(name: str, history: str) -> Live:
    return _decide(_given(_check_input(name), history))


def _s11(decision: GuardDecision):
    (found,) = [c for c in decision.constraints if c.id == "single_commitment"]
    return found


def _row(record: dict) -> LedgerRow:
    payload = record["asg_payload"]
    return {
        "capsule_id": record["capsule_id"],
        "live_history": payload[LIVE_HISTORY],
        "decision": (record.get("disposition") or {}).get("decision"),
        "action_class": payload.get("action_class"),
        "deal_id": payload.get("deal_id"),
        "item_ref": payload.get("item_ref"),
        "task_authority_ref": payload.get("task_authority_ref"),
        "stated": (payload.get("stated") or {}).get("class"),
    }


def _rules(decision: GuardDecision) -> dict[str, str]:
    return {r.obligation_id: r.result for r in obligation_results(PACK, decision.constraints)}


def live_document() -> LiveDocument:
    """Every check input decided under every history, in ``INPUTS`` then
    ``HISTORIES`` order."""
    decisions: list[LiveDecision] = []
    for name in INPUTS:
        for history in HISTORIES:
            live = _live(name, history)
            s11 = _s11(live.decision)
            decisions.append({
                "input": f"check-inputs/{name}.json",
                "history": history,
                "ledger": [_row(r) for r in live.ledger],
                "complete": live.written.complete,
                "outcome": live.decision.outcome,
                "verdict": live.decision.verdict,
                "single_commitment": {"result": s11.result, "reason": s11.reason, "evidence": s11.evidence},
                "rules": _rules(live.decision),
            })
    return {
        "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
        "producer": PRODUCER,
        "decisions": decisions,
    }


def canonical(document: LiveDocument) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


# -- the fixture is capsulectl's bytes ---------------------------------------------


def test_the_fixture_is_the_bytes_capsulectl_wrote():
    for name, digest in FIXTURE_SHA256.items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name


def test_every_record_was_sealed_by_the_pinned_capsulectl():
    producers = [
        entry["agent_input"]["producer"]
        for name in INPUTS
        for entry in (_check_input(name)["record"], *_check_input(name).get("history", []))
    ]
    assert producers
    assert all(p == PRODUCER for p in producers)


def test_no_history_act_seals_a_disposition():
    """What capsulectl seals today: every act but a payment is ``fyi`` with
    no disposition, even when its basis is the task authority."""
    acts = [entry for name in INPUTS for entry in _check_input(name)["history"]]
    assert acts
    for entry in acts:
        assert entry["action_type"] == "fyi"
        assert "disposition" not in entry
        assert entry["agent_input"]["body"]["authority_basis"][0]["type"] == "task_authority"


def test_the_decisions_are_expected_live_json():
    assert EXPECTED.read_text(encoding="utf-8") == canonical(live_document())


# -- the acceptance lines -----------------------------------------------------------


def test_b_commit_after_a_accepted_is_denied_naming_s11():
    live = _live("b-commit-1850", DISPOSED)
    s11 = _s11(live.decision)
    assert (s11.result, s11.evidence) == ("fail", {"constraint_id": "single_commitment", "sale_has_acceptance": True})
    assert live.decision.outcome == "deny"
    assert _rules(live.decision)[S11] == "fail"


def test_b_offer_after_a_accepted_is_denied_naming_s11():
    """An offer is a commitment the rule applies to."""
    assert _s11(_live("b-offer-1850", DISPOSED).decision).result == "fail"


def test_a_follow_through_in_its_own_thread_passes():
    """A's thread is A's: the acceptance's deal is the commitment's. The act
    record seals no counterparty and the check cites nothing, so the deal is
    what ties them."""
    s11 = _s11(_live("a-commit-again-1900", DISPOSED).decision)
    assert (s11.result, s11.evidence) == ("pass", {"constraint_id": "single_commitment", "sale_has_acceptance": True})


def test_the_first_commit_of_a_sale_passes_on_its_own_offer():
    """A's own offer is in its history, sealed fyi: it is not an acceptance
    class, so it leaves nothing unknown."""
    for history in (AS_SEALED, DISPOSED):
        s11 = _s11(_live("a-commit-1900", history).decision)
        assert (s11.result, s11.evidence) == (
            "pass", {"constraint_id": "single_commitment", "sale_has_acceptance": False})


@pytest.mark.parametrize("name", ["b-offer-1850", "b-commit-1850", "a-commit-again-1900"])
def test_an_act_sealed_with_no_disposition_is_not_evaluable_and_refused(name):
    live = _live(name, AS_SEALED)
    s11 = _s11(live.decision)
    assert (s11.result, s11.evidence) == (
        "n/a", not_applicable_evidence("single_commitment", in_scope=True, missing_field="disposition"))
    assert "seals no disposition" in s11.reason
    assert live.decision.outcome == "deny"
    assert live.decision.not_evaluated_checks == ("single_commitment",)


@pytest.mark.parametrize("name", INPUTS)
def test_no_history_is_not_evaluable_and_never_passes(name):
    live = _live(name, ABSENT)
    s11 = _s11(live.decision)
    assert (s11.result, s11.evidence) == (
        "n/a", not_applicable_evidence("single_commitment", in_scope=True, missing_field="history"))
    assert live.decision.outcome == "deny"
    assert not live.written.complete


def test_an_accepted_commit_in_the_history_naming_no_item_is_not_evaluable():
    """It may be this sale's: never a pass. A's commit, the history's one
    accepted commitment, names no item."""
    live = _live("b-commit-1850", HISTORY_NO_ITEM)
    s11 = _s11(live.decision)
    assert (s11.result, s11.evidence) == (
        "n/a", not_applicable_evidence("single_commitment", in_scope=True, missing_field="item_ref"))
    assert "names no item" in s11.reason
    assert live.decision.outcome == "deny"
    assert live.decision.not_evaluated_checks == ("single_commitment",)


def test_an_unaccepted_commit_naming_no_item_leaves_the_sale_to_the_others():
    """Only an accepted commitment is held unknown for its missing item: as
    sealed today (no disposition) the act counts for nothing, and the
    history's other acts decide."""
    envelope = _given(_check_input("b-commit-1850"), AS_SEALED)
    for entry in envelope["history"]:
        if entry["agent_input"]["body"]["action_class"] == ACCEPTANCE_CLASS:
            del entry["item_ref"]
    s11 = _s11(_decide(envelope).decision)
    assert (s11.result, s11.evidence) == ("pass", {"constraint_id": "single_commitment", "sale_has_acceptance": False})


@pytest.mark.parametrize("name", ["a-commit-1900", "b-commit-1850", "a-commit-again-1900"])
def test_a_checked_commit_naming_no_item_is_not_evaluable_and_refused(name):
    live = _live(name, RECORD_NO_ITEM)
    s11 = _s11(live.decision)
    assert (s11.result, s11.evidence) == (
        "n/a", not_applicable_evidence("single_commitment", in_scope=True, missing_field="item_ref"))
    assert live.decision.outcome == "deny"
    assert live.decision.not_evaluated_checks == ("single_commitment",)


def test_without_the_history_ledger_b_commit_passed():
    """The defect this closes: decided on an empty ledger, as the live path
    did, a second buyer's commit passes."""
    envelope = _given(_check_input("b-commit-1850"), DISPOSED)
    assert _s11(_decide(envelope, with_history=False).decision).result == "pass"


# -- the ledger ---------------------------------------------------------------------


def test_the_ledger_is_in_ledger_order_oldest_first():
    """capsulectl gives the history newest first; acts sealed in the same
    second keep the order the envelope gives them."""
    envelope = _check_input("b-commit-1850")
    given = [e["capsule_id"] for e in envelope["history"]]
    written = [r["capsule_id"] for r in _live("b-commit-1850", AS_SEALED).ledger]
    by_time = sorted(range(len(given)), key=lambda i: (envelope["history"][i]["timestamp"], i))
    assert written == [given[i] for i in by_time]
    assert written != given


def test_each_act_is_written_with_its_sale_deal_and_task_authority():
    envelope = _check_input("b-commit-1850")
    rows = {r["capsule_id"]: _row(r) for r in _live("b-commit-1850", AS_SEALED).ledger}
    for entry in envelope["history"]:
        body = entry["agent_input"]["body"]
        row = rows[entry["capsule_id"]]
        assert row["item_ref"] == entry["item_ref"] == envelope["item_ref"]
        assert row["deal_id"] == entry["agent_input"]["chain_id"]
        assert row["task_authority_ref"] == body["authority_basis"][0]["ref"]["digest"]
        assert row["action_class"] == body["action_class"]


def test_each_thread_seals_its_own_task_authority():
    """Why the sale is the item: two threads of one sale never cite one task
    authority."""
    a = _check_input("a-commit-1900")["record"]["agent_input"]["body"]["task_authority_ref"]
    b = _check_input("b-commit-1850")["record"]["agent_input"]["body"]["task_authority_ref"]
    assert a != b
    assert _check_input("a-commit-1900")["item_ref"] == _check_input("b-commit-1850")["item_ref"]


def test_a_decision_is_never_inferred_from_the_authority_basis():
    rows = _live("b-commit-1850", AS_SEALED).ledger
    assert [r["asg_payload"][LIVE_HISTORY] for r in rows] == [NO_DISPOSITION] * 3
    assert all("disposition" not in r for r in rows)
    disposed = _live("b-commit-1850", DISPOSED).ledger
    assert [(r["asg_payload"][LIVE_HISTORY], r["disposition"]["decision"]) for r in disposed] == [
        (DISPOSITION, "accept")] * 3


@pytest.mark.parametrize("scope", [None, {"complete": False, "days": 31, "max_records": 1000}, {"complete": "true"}],
                         ids=["no scope", "declared incomplete", "complete not true"])
def test_a_history_not_declared_complete_is_incomplete(scope):
    envelope = _given(_check_input("a-commit-1900"), DISPOSED)
    if scope is None:
        del envelope["history_scope"]
    else:
        envelope["history_scope"] = scope
    live = _decide(envelope)
    assert not live.written.complete
    assert _s11(live.decision).evidence["missing_field"] == "history"


@pytest.mark.parametrize("field,value", [("timestamp", "yesterday"), ("timestamp", None), ("item_ref", "AB" * 32)],
                         ids=["timestamp not a time", "no timestamp", "item_ref out of shape"])
def test_an_entry_out_of_shape_makes_the_history_incomplete(field, value):
    envelope = _given(_check_input("a-commit-1900"), DISPOSED)
    envelope["history"][0][field] = value
    live = _decide(envelope)
    assert not live.written.complete
    assert _s11(live.decision).evidence["missing_field"] == "history"


def test_an_unread_act_of_this_sale_is_not_evaluable():
    """An entry whose act cannot be read (here, its record no longer matches
    the digest its capsule binds) leaves the sale's acceptance unknown."""
    envelope = _given(_check_input("b-commit-1850"), DISPOSED)
    for entry in envelope["history"]:
        entry["agent_input"]["body"]["amount_minor"] += 1
    assert all(action_for_history_entry(e) is None for e in envelope["history"])
    live = _decide(envelope)
    assert live.written.unread == 3
    assert _s11(live.decision).evidence["missing_field"] == "history"


def test_an_unread_act_of_another_sale_is_not_read_as_this_one():
    envelope = _given(_check_input("b-commit-1850"), DISPOSED)
    for entry in envelope["history"]:
        entry["agent_input"]["body"]["amount_minor"] += 1
        entry["item_ref"] = "0" * 64
    s11 = _s11(_decide(envelope).decision)
    assert (s11.result, s11.evidence["sale_has_acceptance"]) == ("pass", False)


# -- the engine and dedupe on such a ledger ------------------------------------------


def _bare_action(**overrides) -> Action:
    fields = dict(verb="commit", operator="deal", developer="capsulectl-deal", action_class="agreement.accept",
                  amount_minor=0, currency="USD", target="buyer/ref-b", timestamp="2026-10-10T00:31:58Z",
                  task_authority_ref="a" * 64, item_ref="3f" * 32, taxonomy_version="6", deal_id="deal-b")
    fields.update(overrides)
    return Action(**fields)


def _history_row(state: str, **payload) -> dict:
    record = {"capsule_id": "c" * 64, "operator": "deal", "developer": "capsulectl-deal", "action_type": "fyi",
              "timestamp": "2026-10-10T00:31:57Z",
              "asg_payload": {"action_class": "agreement.accept", "amount_minor": 0, "currency": "USD",
                              "target": "buyer/ref-b", "item_ref": "3f" * 32, "deal_id": "deal-a",
                              "taxonomy_version": "6", LIVE_HISTORY: state, **payload}}
    if state == DISPOSITION:
        record["disposition"] = {"decision": "accept"}
    return record


def test_a_check_that_fails_closed_with_nothing_failed_is_not_evaluable(store, signer):
    engine = GuardEngine(ledger=store, caps_fold=SPEND, signer_provider=lambda: signer, wickets=(SINGLE,))
    store.append(_history_row(NO_DISPOSITION, target="buyer/ref-a"), consequential=False)
    decision = engine.check(_bare_action())
    assert (decision.outcome, decision.verdict) == ("deny", NOT_EVALUABLE)
    assert decision.capsule["disposition"]["decision"] == "reject"
    assert "not evaluable: single_commitment" in decision.reason


def test_dedupe_matches_an_act_sealed_with_no_disposition(store):
    """Every history act was carried out, so a repeat of one is a duplicate,
    whether or not its capsule seals a disposition."""
    store.append(_history_row(NO_DISPOSITION), consequential=False)
    assert check_dedupe(_bare_action(), store).constraint.result == "fail"


def test_dedupe_matches_an_act_whose_disposition_is_sealed(store):
    store.append(_history_row(DISPOSITION), consequential=False)
    assert check_dedupe(_bare_action(), store).constraint.result == "fail"


@pytest.mark.parametrize("state", (UNREAD, STATEMENT, INCOMPLETE))
def test_dedupe_never_matches_a_record_that_is_not_a_read_act(store, state):
    """An entry that could not be read, a statement and the incomplete marker
    are not acts, even with an act's fields."""
    store.append(_history_row(state), consequential=False)
    assert check_dedupe(_bare_action(), store).constraint.result == "pass"


# -- live and replay ----------------------------------------------------------------


@cache
def _replayed() -> dict[str, GuardDecision]:
    """The replay's decision on each check, by the check's ``action_id``."""
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        result = replay(
            load_records(OWN), caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
            per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
            manifest_digest=resolved.manifest_digest, disclosed=load_disclosed(OWN), withheld=load_withheld(OWN),
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack,
        )
    return {s.record["action_id"]: s.decision for s in result.decisions}


# Rules that read an input only a live check is given (the floor's opening,
# the task-authority record), so a replay holds them n/a on every check.
LIVE_INPUT_RULES = ("s03-price-below-the-floor", "s08-stay-within-task-bounds")
# Where else the replay and the live check, on histories with their
# disposition sealed, decide a rule differently, and why. Every other rule of
# every check is decided the same.
LIVE_REPLAY_DIFFERENCES = {
    # A replay of thread bundles alone has no item_ref (no record carries
    # it), so s11 is n/a there; its acceptances would be dry runs, which
    # never count. A replay of a sale bundle decides s11 as live does
    # (test_sale_replay.py).
    ("b-offer-1850", S11): ("n/a", "fail"),
    ("b-commit-1850", S11): ("n/a", "fail"),
    ("a-commit-again-1900", S11): ("n/a", "pass"),
    ("a-offer-1900", S11): ("n/a", "pass"),
    ("a-commit-1900", S11): ("n/a", "pass"),
}


S05 = "s05-required-statement-made-first"


@pytest.mark.parametrize("given", [DEAL_CLAIMS, CLAIMS])
def test_live_and_replay_differ_only_where_named(given):
    """Live with the dispositions sealed and the deal's agent claims given,
    beside the checked record (AMENDMENT 9) or in the history, which is
    what the replay reads from the bundles."""
    differences = {}
    for name in INPUTS:
        envelope = _check_input(name)
        live = _rules(_live(name, given).decision)
        replayed = _rules(_replayed()[envelope["record"]["action_id"]])
        assert set(live) == set(replayed), name
        for rule in live:
            if rule in LIVE_INPUT_RULES:
                assert (replayed[rule], live[rule]) == ("n/a", "pass"), (name, rule)
            elif live[rule] != replayed[rule]:
                differences[(name, rule)] = (replayed[rule], live[rule])
    assert differences == LIVE_REPLAY_DIFFERENCES



def test_without_the_claims_in_its_history_a_live_check_asks_for_the_statement():
    """capsulectl does not put a claim in the history today, so live, every
    offer and commit fails s05 and asks, while the replay, which reads the
    claim in the bundle, passes it. The only other rule that moves is the
    one already named."""
    for name in INPUTS:
        envelope = _check_input(name)
        without, with_claims = _rules(_live(name, DISPOSED).decision), _rules(_live(name, CLAIMS).decision)
        replayed = _rules(_replayed()[envelope["record"]["action_id"]])
        assert (replayed[S05], with_claims[S05], without[S05]) == ("pass", "pass", "fail"), name
        assert _live(name, DISPOSED).decision.outcome in ("escalate", "deny"), name
        assert {r for r in without if without[r] != with_claims[r]} == {S05}, name


def test_a_claim_in_the_history_is_a_statement_never_an_unread_act():
    """The claim entries are written as statements, so s11 reads the same
    sale with them as without them: a claim is not an act of the sale."""
    for name in INPUTS:
        live, without = _live(name, CLAIMS), _live(name, DISPOSED)
        claims = claim_entries(_check_input(name)["record"]["timestamp"], _check_input(name)["item_ref"])
        assert claims and live.written.statement == len(claims) and live.written.unread == 0, name
        assert [_row(r)["stated"] for r in live.ledger if _row(r)["live_history"] == STATEMENT] == [
            "condition"] * len(claims), name
        assert (_s11(live.decision).result, _s11(live.decision).evidence) == (
            _s11(without.decision).result, _s11(without.decision).evidence), name


def test_a_claim_in_another_thread_is_not_a_statement_to_this_buyer():
    """A's second commit comes after B's claim: the history holds both, and
    only A's own deal's claim counts."""
    live = _live("a-commit-again-1900", CLAIMS)
    (s05,) = [c for c in live.decision.constraints if c.id == "required_disclosure"]
    assert len(claim_entries(_check_input("a-commit-again-1900")["record"]["timestamp"], "")) == 2
    assert s05.evidence["stated_counts"] == {"condition": 1}
    envelope = _given(_check_input("a-commit-again-1900"), CLAIMS)
    own = _check_input("a-commit-again-1900")["record"]["agent_input"]["chain_id"]
    envelope["history"] = [e for e in envelope["history"] if e["agent_input"].get("x-deal-v0", {}).get(
        "record_type") != "claim" or e["agent_input"]["x-deal-v0"]["deal_id"] != own]
    assert len(envelope["history"]) == len(_given(_check_input("a-commit-again-1900"), CLAIMS)["history"]) - 1
    (s05,) = [c for c in _decide(envelope).decision.constraints if c.id == "required_disclosure"]
    assert (s05.result, s05.evidence["stated_counts"]) == ("fail", {"condition": 0})


# -- the deal's claims beside the checked record (AMENDMENT 9) ----------------------


def test_with_deal_claims_every_check_finds_its_threads_statement():
    """Each check input gets its own deal's claim, made before it, and passes
    s05 on it alone: the history holds no claim."""
    for name in INPUTS:
        envelope = _given(_check_input(name), DEAL_CLAIMS)
        (claim,) = envelope["record"]["deal_claims"]
        assert not any(e["agent_input"].get("x-deal-v0") for e in envelope["history"]), name
        live = _live(name, DEAL_CLAIMS)
        (s05,) = [c for c in live.decision.constraints if c.id == "required_disclosure"]
        assert (s05.result, s05.evidence["stated_counts"]) == ("pass", {"condition": 1}), name
        assert live.written.statement == 1 and live.written.unread == 0, name
        assert action_for_check_input(envelope["record"], item_ref=envelope["item_ref"]).ignored_inputs == (), name
        assert [_row(r)["stated"] for r in live.ledger if _row(r)["live_history"] == STATEMENT] == ["condition"]
        assert claim["source_kind"] == "agent" and claim["class"] == "condition", name


def test_deal_claims_move_only_s05():
    for name in INPUTS:
        without, given = _rules(_live(name, DISPOSED).decision), _rules(_live(name, DEAL_CLAIMS).decision)
        assert {r for r in given if given[r] != without[r]} == {S05}, name


@pytest.mark.parametrize("at", ["as sealed", "+00:00"], ids=["same time", "same instant written otherwise"])
def test_deal_claims_and_the_history_claim_are_one_statement(at):
    """The same claim given both ways is written once, however each dates
    it: the claim's own capsule_id makes it one."""
    envelope = _given(_check_input("a-commit-again-1900"), DEAL_CLAIMS)
    if at != "as sealed":
        for claim in envelope["record"]["deal_claims"]:
            claim["at"] = claim["at"].replace("Z", at)
    envelope["history"] = _given(_check_input("a-commit-again-1900"), CLAIMS)["history"]
    live = _decide(envelope)
    (s05,) = [c for c in live.decision.constraints if c.id == "required_disclosure"]
    assert s05.evidence["stated_counts"] == {"condition": 1}
    assert live.written.statement == 2  # A's claim once, and B's, which is another deal's


@pytest.mark.parametrize("given", [DEAL_CLAIMS, CLAIMS])
def test_live_and_replay_count_the_same_statements(given):
    """Beyond the result: s05's counts, per class, agree on every check."""
    for name in INPUTS:
        envelope = _check_input(name)
        (live,) = [c for c in _live(name, given).decision.constraints if c.id == "required_disclosure"]
        (replayed,) = [c for c in _replayed()[envelope["record"]["action_id"]].constraints
                       if c.id == "required_disclosure"]
        assert live.evidence["stated_counts"] == replayed.evidence["stated_counts"] == {"condition": 1}, name
        assert live.evidence["prior_counts"] == replayed.evidence["prior_counts"], name


def test_the_fixture_step_refuses_an_input_that_already_has_deal_claims():
    envelope = _given(_check_input("a-offer-1900"), DEAL_CLAIMS)
    with pytest.raises(SystemExit, match="rebuild the fixture"):
        with_deal_claims(envelope, _thread_bundle(envelope["record"]["capsule_id"]))


def test_no_vendored_check_input_carries_deal_claims_yet():
    """When capsulectl passes them, the vendored inputs carry them and the
    fixture step must go (it refuses such an input)."""
    for name in INPUTS:
        assert "deal_claims" not in _check_input(name)["record"], name

if __name__ == "__main__":
    for path in sorted(p for p in FIXTURE.rglob("*.json") if p != EXPECTED):
        print(path.relative_to(FIXTURE), hashlib.sha256(path.read_bytes()).hexdigest(), file=sys.stderr)
    EXPECTED.write_text(canonical(live_document()), encoding="utf-8")
