# SPDX-License-Identifier: Apache-2.0
"""A history act's target is its check's, from the counterparty given beside it.

An act record seals no counterparty. capsulectl gives each history act, beside
its capsule, ``counterparty``: the block the act's check sealed, exactly as
sealed, keyed per deal. ``action_for_history_entry`` reads it through the same
path a check's sealed block takes, so a repeat booking in one deal keys the
same target on the act and on the new check, and a booking at another hotel
does not. ``counterparty_profile`` still replaces the target, on both sides.

``fixtures/history-act-counterparty/`` holds what capsulectl wrote, byte for
byte (``build.sh`` and ``README.md`` there): for each record set (x-deal-v0
and typed), deal 1 books a room at one hotel and checks the same booking
again; deal 2 checks the same room, same amount, at another hotel. Each check
input is decided live under everyday five ways (``VARIANTS``); every decision,
and the target each history act was read with, is ``expected_live.json``,
compared byte for byte here and handed to the Go plugin. Regenerate it with
``python -m tests.test_history_act_counterparty``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

import capsule_engine
from capsule_engine.guards import GuardDecision, GuardEngine, LocalSigner
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.live_history import history_ledger
from capsule_engine.report.replay import (
    action_for_check_input,
    action_for_history_entry,
    load_disclosed,
    load_records,
    load_withheld,
    replay,
)

FIXTURE = Path(__file__).parent / "fixtures" / "history-act-counterparty"
EXPECTED = FIXTURE / "expected_live.json"
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")
PRODUCER = {"commit": "424e79479c40138fcb425674a2ec7c4e540ce478", "name": "capsulectl",
            "version": "v0.1.0-rc14-17-g424e794"}
FIXTURE_SHA256 = {
    "typed/deal-1.bundle.json": "63ba1757c183c885e4f15a72c84e8985572068be31edce571baaa323063ace80",
    "typed/deal-2.bundle.json": "1ee204456e1559d18aa041abb165ef77eb1fe78d3e28fcdf6abf82e4db2e9477",
    "typed/other-merchant.input.json": "a11ca0ae8cb71e0ad98cdb05a750050c633d75268f8af0596c5c570589d75c13",
    "typed/repeat-in-one-deal.input.json": "f87c759254528ba5c9fb1532e2d06148fe34aedca2452fa048940200de2a4212",
    "x-deal-v0/deal-1.bundle.json": "569cba0b6b78adfabd1791c4d7d8a0fc4c3a10b9cda4757155f6415baf71286c",
    "x-deal-v0/deal-2.bundle.json": "5e826c27e0d488c7e00fd2716a40ea24a1097fd83231d71c1b46c1b69532b4e6",
    "x-deal-v0/other-merchant.input.json": "0c4b707dc8b44243596bae40435837b268d76169e6bf41d605fc3b923b17c7e8",
    "x-deal-v0/repeat-in-one-deal.input.json": "ac32f3312d8c7b19695c95378d0cfd5eeccef8c09d1cb0a23ca66bb1b34d395a",
}
SETS = ("x-deal-v0", "typed")
REPEAT, OTHER = "repeat-in-one-deal", "other-merchant"
INPUTS = (REPEAT, OTHER)
R27 = "r27-no-commitment-beyond-task-bounds"

# How each check input is given to the engine: as sealed; with a sealed
# ``accept`` disposition added to every history act (capsulectl seals a
# disposition on a payment only, and seen_before counts only an accepted act;
# synthetic, so those capsules no longer verify);
# with that disposition and no ``counterparty_profile`` on either side (an act
# checked before checks had a companion: the per-deal key is the only one);
# and, on top of that, with each history act's ``counterparty`` dropped or
# given in a shape that is not a check's.
AS_SEALED, DISPOSED, NO_PROFILE = "as-sealed", "disposition-accept", "disposition-accept-no-profile"
NO_COUNTERPARTY, MALFORMED = "disposition-accept-no-profile-no-counterparty", "disposition-accept-no-profile-malformed"
VARIANTS = (AS_SEALED, DISPOSED, NO_PROFILE, NO_COUNTERPARTY, MALFORMED)

# A history act's counterparty in a shape that is not a check's. The first is
# the one ``MALFORMED`` gives.
BAD_BLOCKS: dict[str, object] = {
    "uppercase hex": None,  # set from the fixture's own block in _bad_block
    "a profile key": None,
    "no fp_alg": None,
    "ids not an object": {"fp_alg": "hmac-sha256-chain-key", "ids": "payee"},
    "a short id": None,
    "an id not a string": None,
    "a string": "hmac-sha256-chain-key",
}


class CounterpartyBlock(TypedDict):
    """A check's counterparty block, as capsulectl gives it beside an act."""

    fp_alg: str
    ids: dict[str, str]


class HistoryAct(TypedDict):
    capsule_id: str
    target: str | None
    ignored_inputs: list[str]


class LiveDecision(TypedDict):
    set: str
    input: str
    variant: str
    history: list[HistoryAct]
    outcome: str
    verdict: str
    dedupe: dict[str, str]
    rules: dict[str, str]


class LiveDocument(TypedDict):
    pack: dict[str, str]
    producer: dict[str, str]
    decisions: list[LiveDecision]


# Reads the vendored fixture: the test's decoding boundary.
def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_input(record_set: str, name: str) -> dict:
    return _json(FIXTURE / record_set / f"{name}.input.json")


def _bad_block(name: str, good: CounterpartyBlock) -> object:
    """``BAD_BLOCKS[name]``, made from the act's own well-formed block where
    it names one."""
    payee = good["ids"]["payee"]
    made = {
        "uppercase hex": {"fp_alg": good["fp_alg"], "ids": {"payee": payee.upper()}},
        "a profile key": {"fp_alg": "hmac-sha256-profile-key", "ids": {"payee": payee}},
        "no fp_alg": {"ids": {"payee": payee}},
        "a short id": {"fp_alg": good["fp_alg"], "ids": {"payee": payee[:63]}},
        "an id not a string": {"fp_alg": good["fp_alg"], "ids": {"payee": 7}},
    }
    return made.get(name, BAD_BLOCKS[name])


def _given(envelope: dict, variant: str, bad: str = "uppercase hex") -> dict:
    """``envelope`` given as ``variant`` says."""
    envelope = copy.deepcopy(envelope)
    if variant == AS_SEALED:
        return envelope
    for entry in envelope["history"]:
        entry["disposition"] = {"decision": "accept"}
    if variant == DISPOSED:
        return envelope
    envelope["record"].pop("counterparty_profile", None)
    for entry in envelope["history"]:
        entry.pop("counterparty_profile", None)
        if variant == NO_COUNTERPARTY:
            entry.pop("counterparty", None)
        elif variant == MALFORMED:
            entry["counterparty"] = _bad_block(bad, entry["counterparty"])
    return envelope


def _engine(store: LedgerStore, tmp: str, envelope: dict) -> GuardEngine:
    """A fresh engine under everyday, its ledger ``store`` with ``envelope``'s
    history written into it (``history_ledger``)."""
    installed = install_pack(PACK, project_dir=Path(tmp) / "live-project", mode="observe")
    resolved = installed.resolved
    history_ledger(envelope, store, caps_fold=resolved.caps_fold())
    wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
    gates, asks = engine_ask_sets(installed.pack, wickets)
    signer = LocalSigner(key_id="history-act-counterparty", secret=b"history-act-counterparty-fixture")
    return GuardEngine(
        ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
        caps_minor=resolved.caps_minor() or {}, per_action_minor=resolved.per_action_minor(),
        per_action_reads=resolved.per_action_reads(), manifest_digest=resolved.manifest_digest,
        wickets=wickets, ask_gate_selectors=gates, ask_wickets=asks, evaluate_under_record_taxonomy=True,
    )


def _decide(envelope: dict) -> GuardDecision:
    """The decision on one check input under everyday, by a fresh engine
    whose ledger is the input's history (``history_ledger``)."""
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        return _engine(store, tmp, envelope).check(action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref")))


@cache
def _live(record_set: str, name: str, variant: str) -> GuardDecision:
    return _decide(_given(_check_input(record_set, name), variant))


def _rules(decision: GuardDecision) -> dict[str, str]:
    return {r.obligation_id: r.result for r in obligation_results(PACK, decision.constraints)}


def _dedupe(decision: GuardDecision):
    (found,) = [c for c in decision.constraints if c.id == "dedupe"]
    return found


def _history_acts(envelope: dict) -> list[HistoryAct]:
    acts: list[HistoryAct] = []
    for entry in envelope["history"]:
        action = action_for_history_entry(entry)
        acts.append({
            "capsule_id": entry["capsule_id"],
            "target": action.target if action is not None else None,
            "ignored_inputs": list(action.ignored_inputs) if action is not None else [],
        })
    return acts


def live_document() -> LiveDocument:
    """Every check input decided under every variant, in ``SETS``, ``INPUTS``
    then ``VARIANTS`` order."""
    decisions: list[LiveDecision] = []
    for record_set in SETS:
        for name in INPUTS:
            for variant in VARIANTS:
                decision = _live(record_set, name, variant)
                dedupe = _dedupe(decision)
                decisions.append({
                    "set": record_set,
                    "input": f"{record_set}/{name}.input.json",
                    "variant": variant,
                    "history": _history_acts(_given(_check_input(record_set, name), variant)),
                    "outcome": decision.outcome,
                    "verdict": decision.verdict,
                    "dedupe": {"result": dedupe.result, "reason": dedupe.reason},
                    "rules": _rules(decision),
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


def test_the_decisions_are_expected_live_json():
    assert EXPECTED.read_text(encoding="utf-8") == canonical(live_document())


@pytest.mark.parametrize("record_set", SETS)
def test_each_history_act_carries_its_checks_counterparty_beside_it(record_set):
    for name in INPUTS:
        (entry,) = _check_input(record_set, name)["history"]
        assert "counterparty" in entry
        assert "counterparty" not in entry["agent_input"].get("body", {})


# -- the act's target ---------------------------------------------------------------


def test_a_repeat_in_one_deal_keys_the_act_on_the_new_checks_target():
    envelope = _given(_check_input("typed", REPEAT), NO_PROFILE)
    (entry,) = envelope["history"]
    act, check = action_for_history_entry(entry), action_for_check_input(envelope["record"])
    assert act.target is not None
    assert act.target == check.target
    assert (act.counterparty_ids, act.counterparty_fp_alg) == (check.counterparty_ids, check.counterparty_fp_alg)
    assert act.ignored_inputs == ()


def test_another_hotel_keys_the_act_on_another_target():
    envelope = _given(_check_input("typed", OTHER), NO_PROFILE)
    (entry,) = envelope["history"]
    act, check = action_for_history_entry(entry), action_for_check_input(envelope["record"])
    assert None not in (act.target, check.target)
    assert act.target != check.target


def test_without_its_counterparty_the_act_has_no_target():
    (entry,) = _given(_check_input("typed", REPEAT), NO_COUNTERPARTY)["history"]
    act = action_for_history_entry(entry)
    assert (act.target, act.ignored_inputs) == (None, ())


def test_the_profile_key_still_replaces_the_acts_target():
    (entry,) = _check_input("typed", REPEAT)["history"]
    act = action_for_history_entry(entry)
    assert act.target == f"payee-fp:hmac-sha256-profile-key:{entry['counterparty_profile']['ids']['payee']}"


@pytest.mark.parametrize("bad", sorted(BAD_BLOCKS))
def test_a_counterparty_in_another_shape_is_ignored_and_named(bad):
    (entry,) = _given(_check_input("typed", REPEAT), MALFORMED, bad)["history"]
    act = action_for_history_entry(entry)
    assert (act.target, act.counterparty_ids, act.ignored_inputs) == (None, None, ("counterparty",))


def test_an_ignored_counterparty_is_never_echoed():
    """Its value (uppercase hex here) reaches no constraint and no sealed
    field; the check's own lowercase payee is its target, as before."""
    envelope = _given(_check_input("typed", REPEAT), MALFORMED)
    (entry,) = envelope["history"]
    payee = entry["counterparty"]["ids"]["payee"]
    decision = _decide(envelope)
    sealed = json.dumps([c.__dict__ for c in decision.constraints], default=str) + json.dumps(decision.capsule)
    assert payee != payee.lower()
    assert payee not in sealed


def test_an_ignored_counterparty_beside_a_profile_keeps_the_profile_target():
    envelope = copy.deepcopy(_check_input("typed", REPEAT))
    (entry,) = envelope["history"]
    entry["counterparty"] = "not a block"
    act = action_for_history_entry(entry)
    assert act.target.startswith("payee-fp:hmac-sha256-profile-key:")
    assert act.ignored_inputs == ("counterparty",)


def test_both_inputs_ignored_are_named_profile_first():
    envelope = copy.deepcopy(_check_input("typed", REPEAT))
    (entry,) = envelope["history"]
    entry["counterparty"], entry["counterparty_profile"] = "not a block", "not a block"
    assert action_for_history_entry(entry).ignored_inputs == ("counterparty_profile", "counterparty")


def test_a_counterparty_the_act_record_seals_stands_over_the_one_beside_it():
    """No act record seals one today; if one does, what it seals is read, and
    the block beside it is not."""
    envelope = _given(_check_input("typed", REPEAT), NO_PROFILE)
    (entry,) = envelope["history"]
    sealed = {"fp_alg": entry["counterparty"]["fp_alg"], "ids": {"payee": "ab" * 32}}
    entry["agent_input"]["body"]["counterparty"] = sealed
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    act = action_for_history_entry(entry)
    assert (act.target, act.ignored_inputs) == (f"payee-fp:{sealed['fp_alg']}:{'ab' * 32}", ())


# -- r27 on a live check ------------------------------------------------------------


@pytest.mark.parametrize("variant", (DISPOSED, NO_PROFILE))
def test_r27_fails_a_repeat_booking_in_one_deal(variant):
    assert _rules(_live("typed", REPEAT, variant))[R27] == "fail"


@pytest.mark.parametrize("variant", (DISPOSED, NO_PROFILE))
def test_r27_passes_the_same_amount_at_another_hotel(variant):
    assert _rules(_live("typed", OTHER, variant))[R27] == "pass"


@pytest.mark.parametrize("variant", (NO_COUNTERPARTY, MALFORMED))
def test_without_a_readable_counterparty_the_repeat_is_not_matched(variant):
    """The per-deal key is the only one, and the act has none: what this
    change adds, shown by its absence."""
    assert _rules(_live("typed", REPEAT, variant))[R27] == "pass"


@pytest.mark.parametrize("record_set", SETS)
def test_as_sealed_an_act_with_no_disposition_is_matched(record_set):
    """capsulectl seals no disposition on a booking's act, and dedupe matches
    every act the history holds: it was carried out, so as sealed the repeat
    fails."""
    assert _rules(_live(record_set, REPEAT, AS_SEALED))[R27] == "fail"


@pytest.mark.parametrize("record_set", SETS)
def test_as_sealed_the_same_amount_at_another_hotel_passes(record_set):
    assert _rules(_live(record_set, OTHER, AS_SEALED))[R27] == "pass"


def test_an_x_deal_act_record_is_read_with_its_checks_target():
    """A live history's x-deal-v0 action step is read as an act, keyed on the
    same target as the new check, so the repeat is matched."""
    envelope = _given(_check_input("x-deal-v0", REPEAT), NO_PROFILE)
    (entry,) = envelope["history"]
    assert entry["agent_input"]["x-deal-v0"]["record_type"] == "action"
    act, check = action_for_history_entry(entry), action_for_check_input(envelope["record"])
    assert act.target is not None
    assert act.target == check.target
    assert _rules(_live("x-deal-v0", REPEAT, DISPOSED))[R27] == "fail"


# -- live and replay ----------------------------------------------------------------


@cache
def _replayed(record_set: str) -> dict[str, GuardDecision]:
    """The replay's decision on each check of ``record_set``'s two deals, by
    the check's ``action_id``."""
    own = (FIXTURE / record_set / "deal-1.bundle.json", FIXTURE / record_set / "deal-2.bundle.json")
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        result = replay(
            load_records(own), caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
            per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
            manifest_digest=resolved.manifest_digest, disclosed=load_disclosed(own), withheld=load_withheld(own),
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack,
        )
    return {s.record["action_id"]: s.decision for s in result.decisions}


def test_live_and_replay_decide_r27_alike():
    differences = {}
    for record_set in SETS:
        for name in INPUTS:
            envelope = _check_input(record_set, name)
            live = _rules(_live(record_set, name, DISPOSED))[R27]
            replayed = _rules(_replayed(record_set)[envelope["record"]["action_id"]])[R27]
            if live != replayed:
                differences[(record_set, name)] = (replayed, live)
    assert differences == {}


if __name__ == "__main__":
    for path in sorted(p for p in FIXTURE.rglob("*.json") if p != EXPECTED):
        print(path.relative_to(FIXTURE), hashlib.sha256(path.read_bytes()).hexdigest(), file=sys.stderr)
    EXPECTED.write_text(canonical(live_document()), encoding="utf-8")
