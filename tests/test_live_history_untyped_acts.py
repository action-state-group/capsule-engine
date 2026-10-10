# SPDX-License-Identifier: Apache-2.0
"""A live check reads the acts capsulectl hands it as ``x-deal-v0`` action steps.

capsulectl puts most of a buyer's acts in a check input's history as untyped
``x-deal-v0`` records of ``record_type`` ``action`` (a pay, sealed with an
``accept`` disposition; a commit or a cancel, sealed with none), not as typed
``action-record/v0``. ``action_for_history_entry`` reads both shapes, so the
merchant the user already paid is seen before (r02, r06), and a repeat of an
act already carried out is matched by dedupe (r27) whether or not its capsule
seals a disposition. An act taken without a check (an ``outcome`` stating
``body.unchecked``) is never read as a checked act: it stays ``unread``, and is
never seen nor matched.

``fixtures/live-history-untyped-acts/`` holds real check inputs, byte for byte
as capsulectl handed them to a rules checker (``README.md`` there). Each is
decided live under everyday; every decision and the state of every history
record is ``expected_live.json``, compared byte for byte here and handed to the
Go plugin. Regenerate it with ``python -m tests.test_live_history_untyped_acts``.
"""
from __future__ import annotations

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
from capsule_engine.report.replay import action_for_check_input, action_for_history_entry

FIXTURE = Path(__file__).parent / "fixtures" / "live-history-untyped-acts"
EXPECTED = FIXTURE / "expected_live.json"
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")
FIXTURE_SHA256 = {
    "main-2214b0f/booking-at-another-merchant.input.json":
        "bb749b6787c9699c7fa09940e771784c7471b37b04a626cbb4de21ce0c770c2f",
    "main-2214b0f/pay-a-merchant-paid-before.input.json":
        "02d4522f418f7e871c7f90c0dbed8871c02e13cac1781e18ef06db83912fb689",
    "main-2214b0f/pay-after-a-partial-cancel.input.json":
        "d78581ab9927925f8a38d5bfcb32c07cd4ad808241b8a1e90f91edb7118ea107",
    "main-2214b0f/pay-after-an-unchecked-act.input.json":
        "ca8c36d1c76c8c6ca0b0b44a69e880e4a3df5c2651179c7206ec131a1a61a49d",
    "main-2214b0f/repeat-booking-in-the-same-deal.input.json":
        "df5b10bcd493fca08b4731edf5fbc8228253cedeebb6245f7492e8eb1758149f",
    "main-2214b0f/repeat-pay-in-another-deal.input.json":
        "45a63b3945aba886b7724fd1cf4b6f3d55f8af4ffebf8f57138070f04197fe4f",
    "main-2214b0f/repeat-pay-in-the-same-deal.input.json":
        "214474f8e95f450de8bb09bdaccc4b974330ecf44aa376cfe9a93938723530c2",
    "rc14/repeat-pay-in-the-same-deal.input.json":
        "adde8e8cc11a1f562c578beaaffcd1c5c15774e862c3aa2d23f824f835780f23",
}
INPUTS = tuple(sorted(FIXTURE_SHA256))
R02, R06, R27 = "r02-ordinary-purchase", "r06-new-merchant", "r27-no-commitment-beyond-task-bounds"


class HistoryRecord(TypedDict):
    capsule_id: str
    record_type: str | None
    live_history: str
    target: str | None


class LiveDecision(TypedDict):
    input: str
    history: list[HistoryRecord]
    outcome: str
    verdict: str
    dedupe: dict[str, str]
    rules: dict[str, str]


class LiveDocument(TypedDict):
    pack: dict[str, str]
    decisions: list[LiveDecision]


@cache
def _input(name: str) -> dict:
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


class Live(TypedDict):
    decision: GuardDecision
    states: dict[str, str]


@cache
def _live(name: str) -> Live:
    """The live decision on one check input under everyday, by a fresh engine
    whose ledger is the input's history, and the state each history record
    was written in, by capsule id."""
    return _decide_with_states(_input(name))


def _decide(envelope: dict) -> GuardDecision:
    return _decide_with_states(envelope)["decision"]


def _decide_with_states(envelope: dict) -> Live:
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        installed = install_pack(PACK, project_dir=Path(tmp) / "live-project", mode="observe")
        resolved = installed.resolved
        history_ledger(envelope, store, caps_fold=resolved.caps_fold())
        states = {r.capsule_id: r.capsule["asg_payload"]["live_history"] for r in store.scan()
                  if "live_history" in r.capsule.get("asg_payload", {})}
        wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
        gates, asks = engine_ask_sets(installed.pack, wickets)
        signer = LocalSigner(key_id="live-history-untyped-acts", secret=b"live-history-untyped-acts-fixture")
        engine = GuardEngine(
            ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
            caps_minor=resolved.caps_minor() or {}, per_action_minor=resolved.per_action_minor(),
            per_action_reads=resolved.per_action_reads(), manifest_digest=resolved.manifest_digest,
            wickets=wickets, ask_gate_selectors=gates, ask_wickets=asks,
        )
        decision = engine.check(action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref")))
    return {"decision": decision, "states": states}


def _rules(decision: GuardDecision) -> dict[str, str]:
    return {r.obligation_id: r.result for r in obligation_results(PACK, decision.constraints)}


def _dedupe(decision: GuardDecision):
    (found,) = [c for c in decision.constraints if c.id == "dedupe"]
    return found


def _history(name: str) -> list[HistoryRecord]:
    states = _live(name)["states"]
    records: list[HistoryRecord] = []
    for entry in _input(name)["history"]:
        action = action_for_history_entry(entry)
        records.append({
            "capsule_id": entry["capsule_id"],
            "record_type": entry["agent_input"].get("x-deal-v0", {}).get("record_type"),
            "live_history": states[entry["capsule_id"]],
            "target": action.target if action is not None else None,
        })
    return records


def live_document() -> LiveDocument:
    decisions: list[LiveDecision] = []
    for name in INPUTS:
        decision = _live(name)["decision"]
        dedupe = _dedupe(decision)
        decisions.append({
            "input": name,
            "history": _history(name),
            "outcome": decision.outcome,
            "verdict": decision.verdict,
            "dedupe": {"result": dedupe.result, "reason": dedupe.reason},
            "rules": _rules(decision),
        })
    return {"pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()}, "decisions": decisions}


def canonical(document: LiveDocument) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


# -- the fixture is capsulectl's bytes ---------------------------------------------


def test_the_fixture_is_the_bytes_capsulectl_wrote():
    for name, digest in FIXTURE_SHA256.items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name


def test_the_decisions_are_expected_live_json():
    assert EXPECTED.read_text(encoding="utf-8") == canonical(live_document())


@pytest.mark.parametrize("name", INPUTS)
def test_the_history_acts_are_untyped_action_steps(name):
    """What makes this fixture: no typed act record, so every act read here is
    read as an ``x-deal-v0`` step."""
    for entry in _input(name)["history"]:
        assert "type" not in entry["agent_input"]
        assert entry["agent_input"]["x-deal-v0"]["record_type"] in ("action", "outcome")


# -- every action step is read -------------------------------------------------------


@pytest.mark.parametrize("name", INPUTS)
def test_every_action_step_is_read_and_only_an_unchecked_outcome_is_not(name):
    for record in _history(name):
        if record["record_type"] == "action":
            assert record["live_history"] in ("disposition", "no_disposition")
        else:
            assert record["live_history"] == "unread"


def test_a_pay_is_read_with_its_sealed_disposition_deal_and_spend():
    entry = _input("main-2214b0f/pay-a-merchant-paid-before.input.json")["history"][0]
    act = action_for_history_entry(entry)
    body, block = entry["agent_input"]["body"], entry["agent_input"]["x-deal-v0"]
    assert entry["disposition"]["decision"] == "accept"
    assert (act.action_class, act.amount_minor, act.deal_id) == (body["action_class"], body["spend_minor"], block["deal_id"])
    assert act.target == f"payee-fp:hmac-sha256-profile-key:{entry['counterparty_profile']['ids']['payee']}"


def test_without_its_profile_key_an_action_step_keys_on_the_payee_it_seals():
    entry = dict(_input("main-2214b0f/pay-a-merchant-paid-before.input.json")["history"][0])
    del entry["counterparty_profile"]
    payee = entry["agent_input"]["x-deal-v0"]["counterparty"]["ids"]["payee"]
    assert action_for_history_entry(entry).target == f"payee-fp:hmac-sha256-deal-key:{payee}"


def test_a_partial_cancel_is_read_as_money_in():
    (cancel,) = [e for e in _input("main-2214b0f/pay-after-a-partial-cancel.input.json")["history"]
                 if e["agent_input"]["body"]["action"] == "cancel"]
    act = action_for_history_entry(cancel)
    assert (act.amount_minor, act.returned_minor) == (0, cancel["agent_input"]["body"]["cancelled_amount_minor"])
    assert "disposition" not in cancel


def test_an_action_step_names_the_act_it_reverses_in_its_refs():
    """capsulectl seals a reversal's ``reverses`` ref on the step; none of the
    real steps here reverses an earlier one, so the ref is added (the capsule
    re-bound to it) and read."""
    entry = json.loads(json.dumps(_input("main-2214b0f/pay-after-a-partial-cancel.input.json")["history"][0]))
    reversed_digest = "ab" * 32
    entry["agent_input"]["x-deal-v0"]["refs"].append(
        {"digest": reversed_digest, "digest_alg": "SHA-256", "rel": "reverses", "type": "deal-record"})
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    assert action_for_history_entry(entry).reverses_ref == reversed_digest


def test_only_an_action_step_names_its_reversal_in_its_refs():
    """A check names the act it reverses in its body (``reverses_ref``): a
    ``reverses`` ref on a check is not read, so the check's dedupe key is
    unchanged."""
    entry = json.loads(json.dumps(_input("main-2214b0f/pay-after-a-partial-cancel.input.json")["history"][0]))
    block = entry["agent_input"]["x-deal-v0"]
    block["record_type"] = "check"
    block["refs"].append({"digest": "ab" * 32, "digest_alg": "SHA-256", "rel": "reverses", "type": "deal-record"})
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    checked = action_for_check_input(entry)
    assert checked.returned_minor == entry["agent_input"]["body"]["cancelled_amount_minor"]
    assert checked.reverses_ref is None


def test_an_action_step_stating_an_unchecked_act_is_not_read():
    entry = json.loads(json.dumps(_input("main-2214b0f/pay-a-merchant-paid-before.input.json")["history"][0]))
    entry["agent_input"]["body"]["unchecked"] = dict(entry["agent_input"]["body"])
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    assert action_for_history_entry(entry) is None


def test_an_action_step_its_capsule_does_not_bind_is_not_read():
    entry = json.loads(json.dumps(_input("main-2214b0f/pay-a-merchant-paid-before.input.json")["history"][0]))
    entry["agent_input"]["body"]["spend_minor"] += 1
    assert action_for_history_entry(entry) is None


# -- what the checks read --------------------------------------------------------------


def test_a_merchant_the_user_already_paid_is_seen_and_the_purchase_allowed():
    decision = _live("main-2214b0f/pay-a-merchant-paid-before.input.json")["decision"]
    assert (_rules(decision)[R02], _rules(decision)[R06]) == ("pass", "pass")
    assert decision.outcome == "allow"


@pytest.mark.parametrize("name", ("main-2214b0f/repeat-pay-in-the-same-deal.input.json",
                                  "rc14/repeat-pay-in-the-same-deal.input.json"))
def test_a_repeat_of_a_paid_act_in_the_same_deal_is_refused(name):
    decision = _live(name)["decision"]
    assert _rules(decision)[R27] == "fail"
    assert decision.outcome == "deny"


def test_a_repeat_of_a_paid_act_in_another_deal_is_asked():
    decision = _live("main-2214b0f/repeat-pay-in-another-deal.input.json")["decision"]
    assert _rules(decision)[R27] == "fail"
    assert decision.outcome == "escalate"


def test_a_repeat_of_a_booking_sealed_with_no_disposition_is_refused():
    """A booking's act carries no disposition: it was still carried out."""
    decision = _live("main-2214b0f/repeat-booking-in-the-same-deal.input.json")["decision"]
    assert _rules(decision)[R27] == "fail"
    assert decision.outcome == "deny"


def test_a_booking_at_another_merchant_is_no_repeat():
    assert _rules(_live("main-2214b0f/booking-at-another-merchant.input.json")["decision"])[R27] == "pass"


def test_an_unchecked_act_is_never_seen_even_at_the_checks_own_merchant():
    """An act taken without a check was never authorized, so it is no merchant
    the user went ahead with: given beside it the check's own profile key, the
    merchant is still new, and the act is still not read."""
    envelope = json.loads(json.dumps(_input("main-2214b0f/pay-after-an-unchecked-act.input.json")))
    (outcome,) = [e for e in envelope["history"] if e["agent_input"]["x-deal-v0"]["record_type"] == "outcome"]
    outcome["counterparty_profile"] = envelope["record"]["counterparty_profile"]
    assert action_for_history_entry(outcome) is None
    rules = _rules(_decide(envelope))
    assert (rules[R02], rules[R06], rules[R27]) == ("fail", "fail", "pass")


def test_a_paid_act_given_the_checks_own_merchant_is_seen():
    """The other half: the same history's checked pay, given the check's own
    profile key, makes the merchant seen."""
    envelope = json.loads(json.dumps(_input("main-2214b0f/pay-after-an-unchecked-act.input.json")))
    (pay,) = [e for e in envelope["history"] if e["agent_input"]["x-deal-v0"]["record_type"] == "action"]
    pay["counterparty_profile"] = envelope["record"]["counterparty_profile"]
    rules = _rules(_decide(envelope))
    assert (rules[R02], rules[R06]) == ("pass", "pass")


if __name__ == "__main__":
    for path in sorted(p for p in FIXTURE.rglob("*.json") if p != EXPECTED):
        print(path.relative_to(FIXTURE), hashlib.sha256(path.read_bytes()).hexdigest(), file=sys.stderr)
    EXPECTED.write_text(canonical(live_document()), encoding="utf-8")
