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
from the checked record (``HISTORIES``). ``expected_live.json`` is every decision and the ledger each
was decided on, in sorted canonical JSON, compared byte for byte here and
handed to the Go plugin. Regenerate it with ``python -m tests.test_live_history``.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
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
from capsule_engine.guards.history_state import DISPOSITION, LIVE_HISTORY, NO_DISPOSITION
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
FIXTURE_SHA256 = {
    "buyer-a.bundle.json": "b815ac5fea77adc16a266eaf293f55aa54af9b50a7571c24a810974d2db05361",
    "buyer-b.bundle.json": "db31a7564eaf23cd29cab7856a12979dd4e9291bbc050393dfa564d82e80ad07",
    "check-inputs/a-commit-1900.json": "5ef77bc9658cc2f3bc90d2f7fd7f327a7255f9c91674940c481e144b23d4737c",
    "check-inputs/a-commit-again-1900.json": "20616bf8fc12431279c60a3778fd59da4e35d2ed64f3c3d113dd77ed698c88db",
    "check-inputs/a-offer-1900.json": "37de8286f546fa148d97d174b94585ef73ff1330f4421202f85d7b518bd4dbcb",
    "check-inputs/b-commit-1850.json": "4e60577005185eddb8ff811daa9c0d188a7abd8955c002ee2ed5f4804b33fe9b",
    "check-inputs/b-offer-1850.json": "0228cdb51b67133b900ba0ec2258a87d952f11f51f630cd396e6075482787df0",
}
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
PRODUCER = {"commit": "65f54e528a788cace28b8e2df92f652b5d5629a9", "name": "capsulectl",
            "version": "v0.1.0-rc14-11-g65f54e5"}
S11 = "s11-one-commitment-per-sale"
SINGLE = load_definition_file(ROOT / "guards" / "wickets" / "catalog_defs" / "single_commitment.seller.yaml")
SPEND = load_fold(ROOT / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")

# How each check input is given to the engine: its history as sealed, with
# an accept disposition on every act, with none; with the disposition and
# no item_ref on its accepted commitments; and with the disposition and no
# item_ref on the checked record.
AS_SEALED, DISPOSED, ABSENT = "as-sealed", "disposition-accept", "absent"
HISTORY_NO_ITEM, RECORD_NO_ITEM = "disposition-accept-commit-names-no-item", "disposition-accept-record-names-no-item"
HISTORIES = (AS_SEALED, DISPOSED, ABSENT, HISTORY_NO_ITEM, RECORD_NO_ITEM)
ACCEPTANCE_CLASS = "agreement.accept"


class LedgerRow(TypedDict):
    capsule_id: str
    live_history: str
    decision: str | None
    action_class: str | None
    deal_id: str | None
    item_ref: str | None
    task_authority_ref: str | None


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


def _given(envelope: dict, history: str) -> dict:
    """``envelope`` with its history given as ``history`` says."""
    envelope = copy.deepcopy(envelope)
    if history in (DISPOSED, HISTORY_NO_ITEM, RECORD_NO_ITEM):
        for entry in envelope["history"]:
            entry["disposition"] = {"decision": "accept"}
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


def test_dedupe_skips_an_act_sealed_with_no_disposition(store):
    store.append(_history_row(NO_DISPOSITION), consequential=False)
    assert check_dedupe(_bare_action(), store).constraint.result == "pass"


def test_dedupe_matches_an_act_whose_disposition_is_sealed(store):
    store.append(_history_row(DISPOSITION), consequential=False)
    assert check_dedupe(_bare_action(), store).constraint.result == "fail"


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
    # A replay never reads item_ref (no record carries it), so s11 is n/a
    # there; its acceptances would be dry runs, which never count.
    ("b-offer-1850", S11): ("n/a", "fail"),
    ("b-commit-1850", S11): ("n/a", "fail"),
    ("a-commit-again-1900", S11): ("n/a", "pass"),
    ("a-offer-1900", S11): ("n/a", "pass"),
    ("a-commit-1900", S11): ("n/a", "pass"),
    # The replay matches A's repeated commit against its own decision on the
    # first, which names A; the history's act record seals no counterparty,
    # so live it never matches the commit to A.
    ("a-commit-again-1900", "s09-no-repeated-act"): ("fail", "pass"),
}


def test_live_and_replay_differ_only_where_named():
    differences = {}
    for name in INPUTS:
        envelope = _check_input(name)
        live = _rules(_live(name, DISPOSED).decision)
        replayed = _rules(_replayed()[envelope["record"]["action_id"]])
        assert set(live) == set(replayed), name
        for rule in live:
            if rule in LIVE_INPUT_RULES:
                assert (replayed[rule], live[rule]) == ("n/a", "pass"), (name, rule)
            elif live[rule] != replayed[rule]:
                differences[(name, rule)] = (replayed[rule], live[rule])
    assert differences == LIVE_REPLAY_DIFFERENCES


if __name__ == "__main__":
    for path in sorted(p for p in FIXTURE.rglob("*.json") if p != EXPECTED):
        print(path.relative_to(FIXTURE), hashlib.sha256(path.read_bytes()).hexdigest(), file=sys.stderr)
    EXPECTED.write_text(canonical(live_document()), encoding="utf-8")
