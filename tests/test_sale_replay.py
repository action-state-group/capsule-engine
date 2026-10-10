# SPDX-License-Identifier: Apache-2.0
"""A replay of a sale bundle decides s11 as a live check does.

``fixtures/sale-replay/`` holds what capsulectl wrote, byte for byte
(``build.sh`` and ``README.md`` there). It has one sale and two buyer threads, A and
B, with the steps of ``fixtures/live-history/``: A is offered 1900, accepts,
and the seller commits and acts; then B is offered 1850, accepts, and the
seller's commit to B is checked; then the seller's commit to A is checked
again. ``check-inputs/`` holds the external-check-input/v0 of each check.
``sale.bundle.json`` is the user's own copy of the sale as capsulectl writes
it: it seals each thread's report, then a ``sale_cut`` naming each thread's
last record, then cuts the sale's checkpoint, after every check
(AMENDMENT 11).

Each check is decided live (its check input, as ``test_live_history``
decides one) and by the replay of a sale bundle (``replay_sale``), with its
acts as sealed and with an ``accept`` disposition added to every act
(``fixture_steps.with_act_dispositions``; capsulectl seals none on an offer
or commit yet). ``expected_live.json`` and ``expected_replay.json`` are
every decision, in sorted canonical JSON, compared byte for byte here and
handed to the Go plugin. Regenerate them with
``python -m tests.test_sale_replay``.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

import capsule_engine
from capsule_engine.guards import GuardDecision, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import not_applicable_evidence
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.history_state import INCOMPLETE, LIVE_HISTORY, STATEMENT, UNREAD
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report import sale_bundle
from capsule_engine.report.live_history import history_ledger
from capsule_engine.report.replay import (
    action_for_check_input,
    load_disclosed,
    load_records,
    load_withheld,
    replay,
)
from capsule_engine.report.sale_bundle import (
    SALE_EXTENSION,
    SaleBundle,
    load_sale_bundle,
    read_sale_bundle,
    replay_sale,
    sale_history,
)

ROOT = Path(capsule_engine.__file__).parent
FIXTURE = Path(__file__).parent / "fixtures" / "sale-replay"
INPUTS = ("a-offer-1900", "a-commit-1900", "b-offer-1850", "b-commit-1850", "a-commit-again-1900")
EXPECTED_LIVE = FIXTURE / "expected_live.json"
EXPECTED_REPLAY = FIXTURE / "expected_replay.json"
_STEPS = importlib.util.spec_from_file_location("fixture_steps", FIXTURE / "fixture_steps.py")
_steps = importlib.util.module_from_spec(_STEPS)
_STEPS.loader.exec_module(_steps)
with_act_dispositions = _steps.with_act_dispositions
withhold_thread = _steps.withhold_thread
FIXTURE_SHA256 = {
    "check-inputs/a-commit-1900.json": "384d7d9d8d93a61b66610f92acacb3c0a381aa9455b64dd3b7b28baac49e6e24",
    "check-inputs/a-commit-again-1900.json": "5249332dae104838405995cc741bd1bab3a75484347221eda044b5178703a403",
    "check-inputs/a-offer-1900.json": "c30d3e57ee1f3339f5880ebf2414e6a2b1d3c04467e897a5195324e73fe5def1",
    "check-inputs/b-commit-1850.json": "02771f9c209aff888ba1f61eeedea48ed4803e3e8c1eb1e89f6293bd7a501380",
    "check-inputs/b-offer-1850.json": "389f5d85f0cc3a40c905cb087f8edb072eb23e0c73cd63955a145c947be76919",
    "sale.bundle.json": "67cb7ef7bdafef928110f4459742c7b7b6ef273a6780cbf41b06895bd4ffc422",
}
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
PRODUCER = {"commit": "9691acadf137a92735f128ae5c3ecc27bc7d9390", "name": "capsulectl",
            "version": "v0.1.0-rc14-23-g9691aca"}
SALE_ID = "sale-c7b4c07ced894df9"
THREAD_A, THREAD_B = "deal-ae1582c2fe995822", "deal-73bb7fe1c1462a86"
S11 = "s11-one-commitment-per-sale"
# Rules that read an input only a live check is given (the floor's opening,
# the task-authority record), so a replay holds them n/a on every check.
LIVE_INPUT_RULES = ("s03-price-below-the-floor", "s08-stay-within-task-bounds")

AS_SEALED, DISPOSED = "as-sealed", "disposition-accept"
HISTORIES = (AS_SEALED, DISPOSED)
# The sale bundle each replay reads: as capsulectl wrote it, and that copy
# with B withheld.
AS_WRITTEN, B_WITHHELD = "sale.bundle.json", "sale-b-withheld"
SALES = (AS_WRITTEN, B_WITHHELD)


class LedgerRow(TypedDict):
    capsule_id: str
    live_history: str
    decision: str | None
    action_class: str | None
    deal_id: str | None
    item_ref: str | None
    target: str | None


class Decided(TypedDict):
    input: str
    history: str
    ledger: list[LedgerRow]
    outcome: str
    verdict: str
    single_commitment: dict[str, object]
    rules: dict[str, str]


class ReplayDecided(Decided):
    sale: str


# Reads the vendored fixture: the test's decoding boundary.
def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_input(name: str) -> dict:
    return _json(FIXTURE / "check-inputs" / f"{name}.json")


@cache
def _sale(name: str) -> SaleBundle:
    if name == B_WITHHELD:
        return read_sale_bundle(withhold_thread(_json(FIXTURE / AS_WRITTEN), THREAD_B))
    return load_sale_bundle(FIXTURE / name)


def _given_sale(name: str, history: str) -> SaleBundle:
    return with_act_dispositions(_sale(name)) if history == DISPOSED else _sale(name)


def _given_input(name: str, history: str) -> dict:
    envelope = copy.deepcopy(_check_input(name))
    if history == DISPOSED:
        for entry in envelope["history"]:
            entry["disposition"] = {"decision": "accept"}
    return envelope


def _engine(store: LedgerStore, tmp: str) -> GuardEngine:
    installed = install_pack(PACK, project_dir=Path(tmp) / "live-project", mode="observe")
    resolved = installed.resolved
    wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
    gates, asks = engine_ask_sets(installed.pack, wickets)
    signer = LocalSigner(key_id="sale-replay", secret=b"sale-replay-fixture")
    return GuardEngine(
        ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
        caps_minor=resolved.caps_minor() or {}, manifest_digest=resolved.manifest_digest, wickets=wickets,
        ask_gate_selectors=gates, ask_wickets=asks, evaluate_under_record_taxonomy=True,
    )


@dataclasses.dataclass(frozen=True)
class Live:
    decision: GuardDecision
    ledger: list[dict]


@cache
def _live(name: str, history: str) -> Live:
    """The live decision on one check input, by a fresh engine whose ledger
    is its history (``history_ledger``), as ``test_live_history`` decides."""
    envelope = _given_input(name, history)
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        engine = _engine(store, tmp)
        history_ledger(envelope, store)
        ledger = [r.capsule for r in store.scan()]
        action = action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref"))
        decision = engine.check(
            action, task_authority_record=envelope.get("task_authority_record"),
            commercial_bounds_opening=envelope.get("commercial_bounds_opening"),
        )
    return Live(decision=decision, ledger=ledger)


def _replay_options(tmp: str) -> dict:
    installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
    resolved = installed.resolved
    return dict(
        caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
        per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
        manifest_digest=resolved.manifest_digest, wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
        pack=installed.pack,
    )


@cache
def _replayed(sale: str, history: str) -> dict[str, GuardDecision]:
    """The replay's decision on each check of the sale, by ``action_id``."""
    with tempfile.TemporaryDirectory() as tmp:
        result = replay_sale(_given_sale(sale, history), **_replay_options(tmp))
    return {s.record["action_id"]: s.decision for s in result.decisions}


def _replayed_check(sale: str, history: str, name: str) -> GuardDecision:
    return _replayed(sale, history)[_check_input(name)["record"]["action_id"]]


def _sale_ledger(sale: SaleBundle, name: str) -> list[dict]:
    """The ledger the replay's ``single_commitment`` reads for one check:
    ``sale_history`` written by ``history_ledger``."""
    envelope = sale_history(sale, _check_input(name)["record"]["capsule_id"])
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(tmp) as store:
        history_ledger(envelope, store)
        return [r.capsule for r in store.scan()]


def _s11(decision: GuardDecision):
    (found,) = [c for c in decision.constraints if c.id == "single_commitment"]
    return found


def _cell(decision: GuardDecision) -> tuple:
    s11 = _s11(decision)
    return s11.result, s11.reason, s11.evidence


def _rules(decision: GuardDecision) -> dict[str, str]:
    return {r.obligation_id: r.result for r in obligation_results(PACK, decision.constraints)}


def _row(record: dict) -> LedgerRow:
    payload = record["asg_payload"]
    return {
        "capsule_id": record["capsule_id"],
        "live_history": payload[LIVE_HISTORY],
        "decision": (record.get("disposition") or {}).get("decision"),
        "action_class": payload.get("action_class"),
        "deal_id": payload.get("deal_id"),
        "item_ref": payload.get("item_ref"),
        "target": payload.get("target"),
    }


def _decided(name: str, history: str, decision: GuardDecision, ledger: list[dict]) -> Decided:
    s11 = _s11(decision)
    return {
        "input": f"check-inputs/{name}.json",
        "history": history,
        "ledger": [_row(r) for r in ledger],
        "outcome": decision.outcome,
        "verdict": decision.verdict,
        "single_commitment": {"result": s11.result, "reason": s11.reason, "evidence": s11.evidence},
        "rules": _rules(decision),
    }


def live_document() -> dict:
    """Every check input decided live, in ``INPUTS`` then ``HISTORIES`` order."""
    return {
        "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
        "producer": PRODUCER,
        "decisions": [_decided(n, h, _live(n, h).decision, _live(n, h).ledger) for n in INPUTS for h in HISTORIES],
    }


def replay_document() -> dict:
    """Every check the replay of each sale bundle holds, decided, in
    ``SALES``, ``HISTORIES`` then ``INPUTS`` order, with the ledger its
    ``single_commitment`` read. A copy with B withheld holds none of B's."""
    decisions: list[ReplayDecided] = []
    for sale in SALES:
        for history in HISTORIES:
            for name in INPUTS:
                if _check_input(name)["record"]["action_id"] not in _replayed(sale, history):
                    continue
                decided = _decided(name, history, _replayed_check(sale, history, name),
                                   _sale_ledger(_given_sale(sale, history), name))
                decisions.append(ReplayDecided(**decided, sale=sale))
    return {
        "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
        "producer": PRODUCER,
        "sales": {s: {"complete": _sale(s).complete, "findings": list(_sale(s).findings)} for s in SALES},
        "decisions": decisions,
    }


def canonical(document: dict) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _with_thread(sale: SaleBundle, thread_id: str, edit) -> SaleBundle:
    """``sale`` with ``edit`` applied to each record of one carried thread."""
    threads = tuple(dataclasses.replace(t, records=tuple(edit(dict(r)) for r in t.records))
                    if t.thread_id == thread_id else t for t in sale.threads)
    return dataclasses.replace(sale, threads=threads)


def _capsule_of(thread_id: str, record_type: str, action: str | None = None) -> str:
    """The ``capsule_id`` of a thread's record of one type (and action)."""
    (thread,) = [t for t in _sale(AS_WRITTEN).threads if t.thread_id == thread_id]
    found = [cid for cid, s in thread.disclosed.items()
             if s.get("type") == record_type and (action is None or (s.get("body") or {}).get("action") == action)]
    return found[0]


# -- the fixture is capsulectl's bytes ---------------------------------------------


def test_the_fixture_is_the_bytes_capsulectl_wrote():
    for name, digest in FIXTURE_SHA256.items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name


def test_every_record_was_sealed_by_the_pinned_capsulectl():
    producers = [e["agent_input"]["producer"] for n in INPUTS
                 for e in (_check_input(n)["record"], *_check_input(n)["history"])]
    bundle = _json(FIXTURE / AS_WRITTEN)
    bundles = [bundle, *bundle["extensions"][SALE_EXTENSION]["threads"].values()]
    producers += [(d["agent_input"].get("x-deal-v0") or d["agent_input"]).get("producer")
                  for b in bundles for d in b["disclosures"].values()
                  if "agent_input" in d and "report" not in d["agent_input"]]
    assert producers
    assert all(p == PRODUCER for p in producers)


def test_no_act_seals_a_disposition():
    """Why the PASS cells need ``with_act_dispositions``: capsulectl seals
    no disposition on an offer or commit act."""
    acts = [r for t in _sale(AS_WRITTEN).threads for r in t.records
            if (t.disclosed.get(r["capsule_id"]) or {}).get("type") == "action-record/v0"]
    assert len(acts) == 3
    assert not any("disposition" in r for r in acts)
    assert not any("disposition" in e for n in INPUTS for e in _check_input(n)["history"])


def test_the_copy_capsulectl_writes_is_certified_after_every_check():
    """The bundle step seals each thread's report, then the ``sale_cut``,
    then cuts the sale's checkpoint: its last record is the cut, and every
    checkpoint is after every check."""
    checks = [_check_input(n)["record"]["timestamp"] for n in INPUTS]
    bundle = _json(FIXTURE / AS_WRITTEN)
    assert bundle["checkpoint"]["timestamp"] > max(checks)
    assert all(t["checkpoint"]["timestamp"] > max(checks) for t in _threads(bundle).values())
    assert _sale_record(bundle, "sale_cut") == bundle["records"][-1]


# -- the sale bundle ----------------------------------------------------------------


def test_the_sale_is_complete_and_keyed_on_its_task_authority():
    sale = _sale(AS_WRITTEN)
    assert (sale.complete, sale.findings) == (True, ())
    assert [t.thread_id for t in sale.threads] == [THREAD_A, THREAD_B]
    openings = {t.thread_id: next(s["report"]["sale_authority_opening"]["text"] for s in t.disclosed.values()
                                  if "report" in s) for t in sale.threads}
    assert set(openings.values()) == {sale.key}
    assert all(_check_input(n)["item_ref"] != sale.key for n in INPUTS)


def test_the_sale_records_are_in_seal_order():
    records = _sale(AS_WRITTEN).records()
    assert [r["timestamp"] for r in records] == sorted(r["timestamp"] for r in records)
    assert len(records) == sum(len(t.records) for t in _sale(AS_WRITTEN).threads)


def test_the_decisions_are_expected_live_json():
    assert EXPECTED_LIVE.read_text(encoding="utf-8") == canonical(live_document())


def test_the_decisions_are_expected_replay_json():
    assert EXPECTED_REPLAY.read_text(encoding="utf-8") == canonical(replay_document())


# -- the acceptance lines (AMENDMENT 10) ---------------------------------------------


@pytest.mark.parametrize("history", HISTORIES)
@pytest.mark.parametrize("name", INPUTS)
def test_the_replay_decides_s11_as_live_does(name, history):
    """Cell for cell: the result, reason and evidence."""
    assert _cell(_replayed_check(AS_WRITTEN, history, name)) == _cell(_live(name, history).decision)


@pytest.mark.parametrize("history", HISTORIES)
@pytest.mark.parametrize("name", INPUTS)
def test_the_replay_reads_the_ledger_live_reads(name, history):
    """One reader: the acts in the ledger ``single_commitment`` reads in the
    replay are the ones ``history_ledger`` writes live, record for record,
    but for the item reference, which is the sale's key in the replay. The
    live ledger also holds the statements of the checked record's
    ``deal_claims``, which ``single_commitment`` does not read. Only the
    sale's first check has no act before it."""
    replayed = _sale_ledger(_given_sale(AS_WRITTEN, history), name)
    live = [r for r in _live(name, history).ledger if r["asg_payload"][LIVE_HISTORY] != STATEMENT]
    key, item = _sale(AS_WRITTEN).key, _check_input(name)["item_ref"]
    assert bool(replayed) == (name != "a-offer-1900")
    assert [json.dumps(r, sort_keys=True).replace(key, item) for r in replayed] == [
        json.dumps(r, sort_keys=True) for r in live]


def test_a_follow_through_passes():
    for name in ("a-offer-1900", "a-commit-1900", "a-commit-again-1900"):
        s11 = _s11(_replayed_check(AS_WRITTEN, DISPOSED, name))
        assert s11.result == "pass", name
    assert _s11(_replayed_check(AS_WRITTEN, DISPOSED, "a-commit-again-1900")).evidence["sale_has_acceptance"] is True


@pytest.mark.parametrize("name", ["b-offer-1850", "b-commit-1850"])
def test_b_after_a_is_denied_naming_s11(name):
    decision = _replayed_check(AS_WRITTEN, DISPOSED, name)
    assert _cell(decision)[0::2] == ("fail", {"constraint_id": "single_commitment", "sale_has_acceptance": True})
    assert decision.outcome == "deny"
    assert _rules(decision)[S11] == "fail"


@pytest.mark.parametrize("name", ["b-offer-1850", "b-commit-1850", "a-commit-again-1900"])
def test_as_sealed_an_act_with_no_disposition_is_not_evaluable(name):
    s11 = _s11(_replayed_check(AS_WRITTEN, AS_SEALED, name))
    assert (s11.result, s11.evidence) == (
        "n/a", not_applicable_evidence("single_commitment", in_scope=True, missing_field="disposition"))


HISTORY_UNKNOWN = ("n/a", not_applicable_evidence("single_commitment", in_scope=True, missing_field="history"))


@pytest.mark.parametrize("history", HISTORIES)
@pytest.mark.parametrize("name", INPUTS)
def test_with_b_withheld_every_s11_cell_is_not_evaluable(name, history):
    assert _sale(B_WITHHELD).findings == ("thread_not_shown",)
    decision = _replayed_check(B_WITHHELD, history, name) if name.startswith("a-") else None
    if decision is None:
        # B's checks are not in the copy: nothing of B is replayed.
        assert _check_input(name)["record"]["action_id"] not in _replayed(B_WITHHELD, history)
        return
    assert _cell(decision)[0::2] == HISTORY_UNKNOWN
    assert decision.outcome == "deny"


@pytest.mark.parametrize("history", HISTORIES)
def test_a_check_after_the_certified_checkpoint_is_not_evaluable(history):
    """A copy certified only before its checks, as capsulectl wrote one
    before its bundle step sealed a cut: a thread registered after the
    checkpoint would not be in it, so no check after it is complete."""
    first = min(_parse(_check_input(n)["record"]["timestamp"]) for n in INPUTS)
    stale = dataclasses.replace(_given_sale(AS_WRITTEN, history), certified_until=first)
    with tempfile.TemporaryDirectory() as tmp:
        decided = replay_sale(stale, **_replay_options(tmp)).decisions
    by_action = {s.record["action_id"]: s.decision for s in decided}
    for name in INPUTS:
        decision = by_action[_check_input(name)["record"]["action_id"]]
        assert _cell(decision)[0::2] == HISTORY_UNKNOWN, name
        assert decision.outcome == "deny", name


@pytest.mark.parametrize("thread_id", [THREAD_A, THREAD_B])
def test_a_thread_replayed_alone_stays_not_evaluable(thread_id):
    """No sale bundle, no key: s11 never passes on one thread's copy,
    replayed from its file as any deal bundle is."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "thread.bundle.json"
        path.write_text(json.dumps(_threads(_json(FIXTURE / AS_WRITTEN))[thread_id]), encoding="utf-8")
        result = replay(load_records([path]), disclosed=load_disclosed([path]), withheld=load_withheld([path]),
                        **_replay_options(tmp))
    decided = [s.decision for s in result.decisions]
    assert decided
    for decision in decided:
        assert (_s11(decision).result, _s11(decision).evidence["missing_field"]) == ("n/a", "item_ref")
        assert decision.outcome == "deny"


@pytest.mark.parametrize("history", HISTORIES)
def test_live_and_replay_differ_only_on_the_live_inputs(history):
    for name in INPUTS:
        live, replayed = _rules(_live(name, history).decision), _rules(_replayed_check(AS_WRITTEN, history, name))
        assert set(live) == set(replayed), name
        assert {r for r in live if live[r] != replayed[r]} == set(LIVE_INPUT_RULES), name
        assert all((replayed[r], live[r]) == ("n/a", "pass") for r in LIVE_INPUT_RULES), name


def test_the_dispositions_step_refuses_a_sale_that_already_seals_them():
    with pytest.raises(SystemExit, match="rebuild the fixture"):
        with_act_dispositions(with_act_dispositions(_sale(AS_WRITTEN)))


# -- every link is verified: a mismatch leaves the sale incomplete -------------------


def _edited(edit) -> SaleBundle:
    bundle = copy.deepcopy(_json(FIXTURE / AS_WRITTEN))
    edit(bundle)
    return read_sale_bundle(bundle)


def _threads(bundle: dict) -> dict:
    return bundle["extensions"][SALE_EXTENSION]["threads"]


def _entries(bundle: dict) -> list:
    return bundle["extensions"]["x-deal-v0"]["sale_threads"]


def _report(thread_bundle: dict) -> dict:
    pointer = thread_bundle["extensions"]["x-deal-v0"]["sealed_report"]
    return thread_bundle["disclosures"][pointer]["agent_input"]["report"]


def _sale_record(bundle: dict, record_type: str) -> dict:
    return next(r for r in bundle["records"]
                if (bundle["disclosures"][r["capsule_id"]]["agent_input"].get("x-deal-v0") or {}).get(
                    "record_type") == record_type)


def _carry_a_thread_with_no_entry(bundle: dict) -> None:
    """B's entry dropped, and B's copy still carried."""
    _entries(bundle).pop(1)


def _open_another_registration(bundle: dict) -> None:
    """B's entry given A's opening, beside B's registration."""
    _entries(bundle)[1] = {**_entries(bundle)[0], "registration": _entries(bundle)[1]["registration"]}


EDITS = {
    "a thread's opening edited": (lambda b: _entries(b)[0].update(nonce="0" * 64),
                                  "registration_opening_does_not_match"),
    "a present thread not carried": (lambda b: _threads(b).pop(THREAD_B), "carried_threads_are_not_the_present_ones"),
    "a thread carried with no entry": (_carry_a_thread_with_no_entry, "sale_threads_do_not_match_the_registrations"),
    "A's copy carried as B's": (lambda b: _threads(b).update({THREAD_B: _threads(b)[THREAD_A]}),
                                "thread_has_no_task_authority_of_this_thread"),
    "a carried thread that does not verify": (lambda b: _threads(b)[THREAD_A]["records"][3].update(operator="other"),
                                              "thread_not_valid"),
    "a thread's sale-authority opening edited": (
        lambda b: _report(_threads(b)[THREAD_A])["sale_authority_opening"].update(nonce="0" * 64),
        "thread_is_not_under_this_sale"),
    "no sale_threads": (lambda b: b["extensions"]["x-deal-v0"].pop("sale_threads"), "no_sale_threads"),
    "threads predate registration": (lambda b: b["extensions"]["x-deal-v0"].update(threads_predate_registration=True),
                                     "threads_predate_registration"),
    "the entries out of order": (lambda b: _entries(b).reverse(), "sale_threads_do_not_match_the_registrations"),
    "an entry of an unknown state": (lambda b: _entries(b)[1].update(member="opened"), "unknown_thread_state"),
    "the sale's own record edited": (lambda b: _sale_record(b, "thread").update(operator="other"),
                                     "sale_bundle_not_valid"),
    "an entry opening another registration": (_open_another_registration, "registration_opening_does_not_match"),
    "a sale record carried undisclosed": (lambda b: b["disclosures"].pop(_sale_record(b, "thread")["capsule_id"]),
                                          "sale_record_not_disclosed"),
    "the sale's certificate from its second record": (lambda b: b["completeness_certificate"].update(first_seq=2),
                                                      "sale_bundle_does_not_cover_its_log"),
    "the sale's certificate short of its checkpoint": (lambda b: b["completeness_certificate"].update(last_seq=4),
                                                       "sale_bundle_does_not_cover_its_log"),
    "the sale's certificate of another log": (lambda b: b["completeness_certificate"].update(log_id="deal/other"),
                                              "sale_bundle_does_not_cover_its_log"),
    "the sale's last record dropped": (lambda b: b["records"].pop(), "sale_bundle_does_not_cover_its_log"),
    "a thread's last record dropped": (lambda b: _threads(b)[THREAD_A]["records"].pop(),
                                       "thread_does_not_cover_its_log"),
    "a thread's certificate short of its checkpoint": (
        lambda b: _threads(b)[THREAD_A]["completeness_certificate"].update(
            last_seq=_threads(b)[THREAD_A]["completeness_certificate"]["last_seq"] - 1),
        "thread_does_not_cover_its_log"),
    "a thread's checkpoint under another key": (
        lambda b: _threads(b)[THREAD_A]["checkpoint"].update(key_id="0" * 64), "thread_not_signed_as_the_sale"),
    "a thread's records under another key": (
        lambda b: [r.update(key_id="0" * 64) for r in _threads(b)[THREAD_A]["records"]],
        "thread_not_signed_as_the_sale"),
}


@pytest.mark.parametrize("edit,finding", EDITS.values(), ids=EDITS.keys())
def test_a_link_that_does_not_hold_leaves_the_sale_incomplete(edit, finding):
    """Every check the copy still carries has an incomplete history. Where
    no carried thread holds a check, the replay decides none of them."""
    sale = _edited(edit)
    assert finding in sale.findings
    assert not sale.complete
    carried = [cid for t in sale.threads for cid in (r["capsule_id"] for r in t.records)]
    checks = [n for n in INPUTS if _check_input(n)["record"]["capsule_id"] in carried]
    if not checks:
        with tempfile.TemporaryDirectory() as tmp:
            assert replay_sale(sale, **_replay_options(tmp)).decisions == ()
    for name in checks:
        assert _scope(sale, name) is False, name


def _rebind(thread_bundle: dict, capsule_id: str, edit) -> dict:
    """``edit`` applied to a record a thread discloses, and the record made
    to bind the edited one (its ``agent_input_digest``). Its capsule no
    longer verifies, so the thread is also not valid; the binding is still
    checked, and named."""
    shown = thread_bundle["disclosures"][capsule_id]["agent_input"]
    edit(shown)
    (record,) = [r for r in thread_bundle["records"] if r["capsule_id"] == capsule_id]
    record["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(shown)
    return shown


def _task_authority_id(thread_bundle: dict) -> str:
    return next(cid for cid, d in thread_bundle["disclosures"].items()
                if d.get("agent_input", {}).get("type") == "task-authority/v0")


def _name_b_registration(bundle: dict) -> None:
    a = _threads(bundle)[THREAD_A]
    other = _entries(bundle)[1]["registration"]["digest"]
    _rebind(a, _task_authority_id(a), lambda ta: [r.update(digest=other) for r in ta["refs"] if r["rel"] == "registration"])
    _reopen(a)


def _reopen(thread_bundle: dict, text: str | None = None) -> None:
    """The sealed report's opening made to name the thread's task authority
    as it now is (and to open ``text`` when given), the report re-bound."""
    authority = thread_bundle["disclosures"][_task_authority_id(thread_bundle)]["agent_input"]
    pointer = thread_bundle["extensions"]["x-deal-v0"]["sealed_report"]

    def edit(report_record):
        opening = report_record["report"]["sale_authority_opening"]
        opening["record_digest"] = json_digest(authority)
        if text is not None:
            opening["text"] = text
    _rebind(thread_bundle, pointer, edit)


def _open_to_nothing(bundle: dict) -> None:
    a = _threads(bundle)[THREAD_A]
    _rebind(a, a["extensions"]["x-deal-v0"]["sealed_report"],
            lambda r: r["report"]["sale_authority_opening"].update(nonce="0" * 64))


def _under_another_sale(bundle: dict) -> None:
    """A's task authority and report made to open another sale's key, each
    consistent with the other."""
    a = _threads(bundle)[THREAD_A]
    nonce = _report(a)["sale_authority_opening"]["nonce"]
    other = "1" * 64
    _rebind(a, _task_authority_id(a),
            lambda ta: ta["body"].update(sale_authority_commitment=json_digest({"nonce": nonce, "text": other})))
    _reopen(a, other)


def _stale_record_digest(bundle: dict) -> None:
    """A's task authority changed and re-bound; the opening still names the
    record it replaced."""
    a = _threads(bundle)[THREAD_A]
    _rebind(a, _task_authority_id(a), lambda ta: ta.update(note="changed"))


REBOUND = {
    "an opening naming another task authority record": (_stale_record_digest, "thread_is_not_under_this_sale"),
    "a thread naming another registration": (_name_b_registration, "thread_does_not_name_its_registration"),
    "an opening that does not open the commitment": (_open_to_nothing, "thread_is_not_under_this_sale"),
    "a thread under another sale": (_under_another_sale, "thread_is_not_under_this_sale"),
}


@pytest.mark.parametrize("edit,finding", REBOUND.values(), ids=REBOUND.keys())
def test_a_bound_thread_that_is_not_this_sales_is_named(edit, finding):
    """Each record still binds what it discloses: only the named link
    fails, beside the thread's own verification."""
    sale = _edited(edit)
    assert set(sale.findings) == {"thread_not_valid", finding}


def _registration(text: str, nonce: str) -> dict:
    return {"x-deal-v0": {"record_type": "thread", "seq": 3},
            "body": {"thread_ref_commitment": json_digest({"nonce": nonce, "text": text})}}


@pytest.mark.parametrize("ids", [(THREAD_A, THREAD_A), (SALE_ID, THREAD_B)],
                         ids=["one thread registered twice", "an id that is not a thread's"])
def test_an_entry_opening_to_no_new_thread_is_named(ids):
    """A producer that sealed two registrations of one thread, or one of an
    id that is no thread's: each opening matches its record, and is still
    refused. No sealed log here can show it, so the reader's step is run on
    the records alone."""
    nonces = ("a" * 64, "b" * 64)
    registrations = [_registration(t, n) for t, n in zip(ids, nonces, strict=True)]
    listed = [{"registration": {"type": "deal-record", "digest_alg": "SHA-256", "digest": json_digest(r)},
               "nonce": n, "thread_id": t, "member": "present"}
              for r, t, n in zip(registrations, ids, nonces, strict=True)]
    findings: list[str] = []
    present = sale_bundle._present(listed, registrations, {}, None, findings)
    assert findings == ["registration_opening_does_not_match"]
    assert list(present) == ([THREAD_B] if ids[0] == SALE_ID else [THREAD_A])


def test_never_opened_is_checked_against_the_sale_log():
    """A copy that relabels B ``never_opened`` and leaves it out: the sale's
    log seals B's ``thread_opened``, so the copy is incomplete, and A's
    checks with it."""
    def relabel(b):
        _entries(b)[1] = {k: v for k, v in _entries(b)[1].items() if k not in ("opened", "head")}
        _entries(b)[1]["member"] = "never_opened"
        _threads(b).pop(THREAD_B)
    sale = _edited(relabel)
    assert sale.findings == ("never_opened_but_opened",)
    assert [t.thread_id for t in sale.threads] == [THREAD_A]
    assert not any(_scope(sale, n) for n in INPUTS if n.startswith("a-"))


# -- the sale's log shows each thread opened and whole (AMENDMENT 11) -------------


def _rebind_sale(bundle: dict, record_type: str, edit) -> dict:
    """``edit`` applied to the sale's first record of ``record_type``, and
    its capsule made to bind the edited record. The capsule no longer
    verifies, so the sale is also not valid; the link is still checked,
    and named."""
    record = _sale_record(bundle, record_type)
    shown = bundle["disclosures"][record["capsule_id"]]["agent_input"]
    edit(shown)
    record["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(shown)
    return shown


def _registration_of(bundle: dict, index: int) -> str:
    return _entries(bundle)[index]["registration"]["digest"]


def test_the_sale_log_opens_each_thread_and_cuts_it_whole():
    """The fixture's records as A11 names them: one ``thread_opened`` per
    thread, in A then B's order, and one ``sale_cut`` with a head for each."""
    bundle = _json(FIXTURE / AS_WRITTEN)
    shown = [d["agent_input"] for d in bundle["disclosures"].values()]
    opened = [s["x-deal-v0"]["refs"][0]["digest"] for s in sorted(shown, key=lambda s: s.get("x-deal-v0", {}).get("seq", 0))
              if s.get("x-deal-v0", {}).get("record_type") == "thread_opened"]
    assert opened == [_registration_of(bundle, 0), _registration_of(bundle, 1)]
    cut = bundle["disclosures"][_sale_record(bundle, "sale_cut")["capsule_id"]]["agent_input"]
    assert [h["registration"]["digest"] for h in cut["body"]["thread_heads"]] == opened


def _truncate(thread_bundle: dict) -> None:
    """A thread's copy cut short by its last record: the record, its
    membership and the certificate's ``last_seq`` all go."""
    certificate = thread_bundle["completeness_certificate"]
    last = thread_bundle["records"].pop()
    certificate["memberships"].pop(last["capsule_id"])
    certificate["last_seq"] -= 1


def _a_thread_opened_naming_b(bundle: dict) -> None:
    """A's ``thread_opened`` re-bound to name B's registration: A has none,
    B has two."""
    other = _registration_of(bundle, 1)
    _rebind_sale(bundle, "thread_opened", lambda s: s["x-deal-v0"]["refs"][0].update(digest=other))


def _a_dropped_from_the_cut(bundle: dict) -> None:
    _rebind_sale(bundle, "sale_cut", lambda s: s["body"]["thread_heads"].pop(0))


def _the_cut_unreadable(bundle: dict) -> None:
    _rebind_sale(bundle, "sale_cut", lambda s: s["body"].update(thread_heads="all"))


def _the_opened_record_unreadable(bundle: dict) -> None:
    _rebind_sale(bundle, "thread_opened", lambda s: s["x-deal-v0"].pop("refs"))


CUT_AND_OPENED = {
    "a thread's head nonce edited": (lambda b: _entries(b)[0]["head"].update(nonce="0" * 64),
                                     {"thread_not_whole_at_the_cut"}),
    "a thread's head naming another record": (
        lambda b: _entries(b)[0]["head"].update(record_digest=_entries(b)[1]["head"]["record_digest"]),
        {"thread_not_whole_at_the_cut"}),
    "a present entry with no head": (lambda b: _entries(b)[0].pop("head"), {"thread_not_whole_at_the_cut"}),
    "a thread truncated by its last record": (
        lambda b: _truncate(_threads(b)[THREAD_A]),
        # Its last record is its sealed report, so its sale opening goes with it.
        {"thread_not_valid", "thread_does_not_cover_its_log", "thread_is_not_under_this_sale"}),
    "a thread's opened nonce edited": (lambda b: _entries(b)[0]["opened"].update(nonce="0" * 64),
                                       {"opening_does_not_match_the_thread"}),
    "a present entry with no opened": (lambda b: _entries(b)[0].pop("opened"), {"opening_does_not_match_the_thread"}),
    "A's opened opening B's task authority": (
        lambda b: _entries(b)[0].update(opened=_entries(b)[1]["opened"]), {"opening_does_not_match_the_thread"}),
    "a thread_opened naming another registration": (_a_thread_opened_naming_b,
                                                     {"sale_bundle_not_valid", "opened_not_evidenced"}),
    "a thread_opened that cannot be read": (_the_opened_record_unreadable,
                                            {"sale_bundle_not_valid", "thread_opened_not_read", "opened_not_evidenced"}),
    "a thread missing from the cut": (_a_dropped_from_the_cut, {"sale_bundle_not_valid", "thread_not_in_the_cut"}),
    "a cut that cannot be read": (_the_cut_unreadable,
                                  {"sale_bundle_not_valid", "no_sale_cut"}),
}


@pytest.mark.parametrize("edit,findings", CUT_AND_OPENED.values(), ids=CUT_AND_OPENED.keys())
def test_a_thread_not_shown_opened_and_whole_leaves_the_sale_incomplete(edit, findings):
    """Each named, nothing else; every check the copy carries is then
    incomplete, so s11 is n/a on it."""
    sale = _edited(edit)
    assert set(sale.findings) == findings
    for name in INPUTS:
        assert _scope(sale, name) is False, name


def test_a_truncated_thread_is_not_whole_at_the_cut():
    """A's copy without its last record, the sale's own openings read from
    the sale's log: its last record is no longer the cut's head for it."""
    bundle = _json(FIXTURE / AS_WRITTEN)
    found: list[str] = []
    shown = [d["agent_input"] for d in bundle["disclosures"].values()]
    opened_by, heads = sale_bundle._sale_log_openings(shown, found)
    a = _threads(bundle)[THREAD_A]
    authority = next(json_digest(d["agent_input"]) for d in a["disclosures"].values()
                     if d["agent_input"].get("type") == "task-authority/v0")
    whole = sale_bundle._opened_and_whole(_entries(bundle)[0], _registration_of(bundle, 0), authority, a,
                                          opened_by, heads)
    _truncate(a)
    cut_short = sale_bundle._opened_and_whole(_entries(bundle)[0], _registration_of(bundle, 0), authority, a,
                                              opened_by, heads)
    assert (found, whole, cut_short) == ([], [], ["thread_not_whole_at_the_cut"])


def _sale_log(thread_opened: bool, cut: bool) -> list[dict]:
    """The sale's bound records an older producer, or this one, seals: two
    registrations, a ``thread_opened`` for the first, and a cut naming it."""
    registrations = [_registration(t, n) for t, n in ((THREAD_A, "a" * 64), (THREAD_B, "b" * 64))]
    first = {"type": "deal-record", "digest_alg": "SHA-256", "digest": json_digest(registrations[0])}
    log = list(registrations)
    if thread_opened:
        log.append({"x-deal-v0": {"record_type": "thread_opened", "seq": 4, "refs": [{"rel": "registration", **first}]},
                    "body": {"task_authority_commitment": "c" * 64}})
    if cut:
        log.append({"x-deal-v0": {"record_type": "sale_cut", "seq": 6},
                    "body": {"thread_heads": [{"registration": first, "head_commitment": "d" * 64}]}})
    return log


def _never_opened(registrations: list[dict], index: int, thread_id: str, nonce: str) -> dict:
    return {"registration": {"type": "deal-record", "digest_alg": "SHA-256", "digest": json_digest(registrations[index])},
            "nonce": nonce, "thread_id": thread_id, "member": "never_opened"}


@pytest.mark.parametrize("opened,cut,findings", [
    (False, False, []),
    (True, False, ["never_opened_but_opened"]),
    (False, True, ["never_opened_but_in_the_cut"]),
], ids=["no thread_opened and no head: never opened", "a thread_opened names it", "the cut names it"])
def test_never_opened_requires_no_opening_on_the_sale_log(opened, cut, findings):
    """The first registration listed ``never_opened``: it holds only when no
    ``thread_opened`` and no head in the cut names it. The reader's steps
    are run on the records alone, as no sealed log here leaves a thread
    unopened."""
    log = _sale_log(opened, cut)
    registrations = [r for r in log if r["x-deal-v0"]["record_type"] == "thread"]
    found: list[str] = []
    opened_by, heads = sale_bundle._sale_log_openings(log, found)
    listed = [_never_opened(registrations, 0, THREAD_A, "a" * 64), _never_opened(registrations, 1, THREAD_B, "b" * 64)]
    assert sale_bundle._present(listed, registrations, opened_by, heads, found) == {}
    assert found == findings


def test_an_old_copy_with_no_opening_and_no_cut_is_incomplete():
    """A copy written before the sale's log sealed ``thread_opened`` and
    ``sale_cut``: a present thread is shown neither opened nor whole."""
    found: list[str] = []
    opened_by, heads = sale_bundle._sale_log_openings(_sale_log(False, False), found)
    assert (opened_by, heads, found) == ({}, None, [])
    bundle = _json(FIXTURE / AS_WRITTEN)
    entry, registration = _entries(bundle)[0], _registration_of(bundle, 0)
    authority = next(json_digest(d["agent_input"]) for d in _threads(bundle)[THREAD_A]["disclosures"].values()
                     if d["agent_input"].get("type") == "task-authority/v0")
    why = sale_bundle._opened_and_whole(entry, registration, authority, _threads(bundle)[THREAD_A], opened_by, heads)
    assert why == ["opened_not_evidenced", "no_sale_cut"]


def test_the_latest_cut_is_read():
    """Two cuts: the later one's heads are the ones read."""
    log = _sale_log(True, True)
    later = copy.deepcopy(log[-1])
    later["x-deal-v0"]["seq"] = 8
    later["body"]["thread_heads"][0]["head_commitment"] = "e" * 64
    found: list[str] = []
    _, heads = sale_bundle._sale_log_openings([later, *log], found)
    assert (list(heads.values()), found) == (["e" * 64], [])


# -- what one check's history holds ----------------------------------------------------


def _scope(sale: SaleBundle, name: str) -> bool:
    return sale_history(sale, _check_input(name)["record"]["capsule_id"])["history_scope"]["complete"]


def test_every_check_of_the_complete_sale_has_a_complete_history():
    assert all(_scope(_sale(AS_WRITTEN), n) for n in INPUTS)


def test_a_check_is_complete_only_when_its_whole_second_is_certified():
    """A check sealed at second T may be as late as T + 1s: the checkpoint
    must be at or after that. An earlier check is still complete."""
    sale = _sale(AS_WRITTEN)
    last = _parse(_check_input("a-commit-again-1900")["record"]["timestamp"])
    assert _scope(dataclasses.replace(sale, certified_until=last + timedelta(seconds=1)), "a-commit-again-1900")
    within = dataclasses.replace(sale, certified_until=last + timedelta(milliseconds=999))
    assert not _scope(within, "a-commit-again-1900")
    assert _scope(within, "b-commit-1850")


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _retimed(sale: SaleBundle, capsule_id: str, timestamp: str) -> SaleBundle:
    def edit(record):
        return {**record, "timestamp": timestamp} if record["capsule_id"] == capsule_id else record
    return dataclasses.replace(sale, threads=tuple(dataclasses.replace(t, records=tuple(map(edit, t.records)))
                                                   for t in sale.threads))


def test_another_threads_act_in_the_checks_second_is_not_ordered():
    """A's commit act sealed in the second of B's commit check: before or
    after it is not sealed."""
    sale = _sale(AS_WRITTEN)
    act = _capsule_of(THREAD_A, "action-record/v0", "commit")
    retimed = _retimed(sale, act, _check_input("b-commit-1850")["record"]["timestamp"])
    assert not _scope(retimed, "b-commit-1850")
    assert _scope(retimed, "a-commit-again-1900")


def test_two_threads_acts_in_one_second_are_not_ordered():
    """A's commit act and B's offer act sealed in one second, both before
    B's commit check: which came first is not sealed."""
    sale = _sale(AS_WRITTEN)
    b_offer = _capsule_of(THREAD_B, "action-record/v0", "offer")
    a_commit = next(r for t in sale.threads for r in t.records
                    if r["capsule_id"] == _capsule_of(THREAD_A, "action-record/v0", "commit"))
    retimed = _retimed(sale, b_offer, a_commit["timestamp"])
    assert not _scope(retimed, "b-commit-1850")


def test_a_fraction_of_a_second_is_the_same_second():
    """A's commit act written at half past the second of B's commit check
    is still in that second: before or after the check is not sealed."""
    second = _check_input("b-commit-1850")["record"]["timestamp"]
    sale = _retimed(_sale(AS_WRITTEN), _capsule_of(THREAD_A, "action-record/v0", "commit"),
                    second.replace("Z", ".500Z"))
    assert not _scope(sale, "b-commit-1850")


def test_an_act_carries_its_checks_counterparty_profile():
    """As capsulectl gives an act the profile-keyed payee its check's
    companion seals. The companion's ``about`` ref names the check's sealed
    record by digest, the typed ``proposed-action/v0`` in a typed deal
    (capsule-cli 9691acadf137 deal_profile.go, ``digestOf(cp.Check)``). The
    fixture's synthetic profile keys no payee, so here a companion is added
    to A's thread for the check A's commit act rests on."""
    sale = _sale(AS_WRITTEN)
    (thread,) = [t for t in sale.threads if t.thread_id == THREAD_A]
    check = thread.disclosed[_capsule_of(THREAD_A, "proposed-action/v0", "commit")]
    block = {"fp_alg": "hmac-sha256-profile-key", "ids": {"payee": "ab" * 32}}
    companion = {"x-deal-v0": {"record_type": "counterparty_profile", "counterparty_profile": block,
                               "refs": [{"rel": "about", "type": "deal-record", "digest_alg": "SHA-256",
                                         "digest": json_digest(check)}]}}
    record = {"capsule_id": "c" * 64, "timestamp": "2026-10-10T08:01:24Z",
              "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(companion)}}}
    with_companion = dataclasses.replace(sale, threads=tuple(
        dataclasses.replace(t, records=(*t.records, record), disclosed={**t.disclosed, record["capsule_id"]: companion})
        if t.thread_id == THREAD_A else t for t in sale.threads))
    envelope = sale_history(with_companion, _check_input("a-commit-again-1900")["record"]["capsule_id"])
    act = _capsule_of(THREAD_A, "action-record/v0", "commit")
    (entry,) = [e for e in envelope["history"] if e["capsule_id"] == act]
    assert entry["counterparty_profile"] == block
    (offer,) = [e for e in envelope["history"] if e["capsule_id"] == _capsule_of(THREAD_A, "action-record/v0", "offer")]
    assert "counterparty_profile" not in offer


def test_an_act_with_no_readable_time_is_not_complete():
    sale = _retimed(_sale(AS_WRITTEN), _capsule_of(THREAD_A, "action-record/v0", "offer"), "yesterday")
    assert not _scope(sale, "b-commit-1850")


def test_a_record_carried_undisclosed_before_the_check_is_unread():
    """A's offer act, carried without its record: whether it is an act cannot
    be read, and it comes before the sale's first acceptance, so B's commit
    cannot know that acceptance."""
    sale = _sale(AS_WRITTEN)
    act = _capsule_of(THREAD_A, "action-record/v0", "offer")
    withheld = dataclasses.replace(sale, threads=tuple(
        dataclasses.replace(t, disclosed={k: v for k, v in t.disclosed.items() if k != act}) for t in sale.threads))
    envelope = sale_history(withheld, _check_input("b-commit-1850")["record"]["capsule_id"])
    (entry,) = [e for e in envelope["history"] if e["capsule_id"] == act]
    assert "agent_input" not in entry and entry["item_ref"] == sale.key
    rows = [_row(r) for r in _sale_ledger(withheld, "b-commit-1850")]
    assert [r["live_history"] for r in rows if r["capsule_id"] == act] == [UNREAD]
    with tempfile.TemporaryDirectory() as tmp:
        decided = replay_sale(with_act_dispositions(withheld), **_replay_options(tmp))
    (b_commit,) = [s.decision for s in decided.decisions
                   if s.record["action_id"] == _check_input("b-commit-1850")["record"]["action_id"]]
    assert _cell(b_commit)[0::2] == HISTORY_UNKNOWN


def test_an_incomplete_history_is_marked_once_after_the_acts():
    rows = [_row(r) for r in _sale_ledger(_sale(B_WITHHELD), "a-commit-again-1900")]
    assert [r["live_history"] for r in rows].count(INCOMPLETE) == 1
    assert rows[-1]["live_history"] == INCOMPLETE


def test_the_sale_history_is_read_by_single_commitment_alone():
    """Given with every check of the sale, it moves s11 only: every other
    rule reads the replay's own view."""
    for name in INPUTS:
        with_sale = _rules(_replayed_check(AS_WRITTEN, DISPOSED, name))
        with tempfile.TemporaryDirectory() as tmp:
            sale = with_act_dispositions(_sale(AS_WRITTEN))
            plain = replay(sale.records(), disclosed=sale.disclosed(), **_replay_options(tmp))
        (without,) = [_rules(s.decision) for s in plain.decisions
                      if s.record["action_id"] == _check_input(name)["record"]["action_id"]]
        assert {r for r in with_sale if with_sale[r] != without[r]} == {S11}, name


if __name__ == "__main__":
    for path in sorted(p for p in FIXTURE.rglob("*.json") if p not in (EXPECTED_LIVE, EXPECTED_REPLAY)):
        print(path.relative_to(FIXTURE), hashlib.sha256(path.read_bytes()).hexdigest(), file=sys.stderr)
    EXPECTED_LIVE.write_text(canonical(live_document()), encoding="utf-8")
    EXPECTED_REPLAY.write_text(canonical(replay_document()), encoding="utf-8")
