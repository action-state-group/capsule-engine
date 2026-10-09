# SPDX-License-Identifier: Apache-2.0
"""everyday 0.3.2: the eight rules measured by action_class_gate each name
their own gate selector, so one action fails only the rule whose selector
matched its class, and the others do not apply to it.

In 0.3.1 every one of the eight took the gate's single result, so a booking
failed all eight. Each action below is evaluated by a pack-installed engine,
then read per rule with ``packs.obligation_results``.
"""
from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY
from capsule_engine.packs import (
    PackDefinitionError,
    build_engine,
    install_pack,
    load_pack_dir,
    obligation_results,
)

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
PACK = load_pack_dir(PACK_DIR)
PACK_DIGEST = "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2"

SELECTOR = {
    "r01-research-and-prepare": "non_consequential",
    "r04-prepare-without-committing": "non_consequential",
    "r11-home-address-to-an-individual": "personal_disclosure",
    "r12-personal-contact-to-a-new-party": "personal_disclosure",
    "r15-public-posting": "public_posting",
    "r16-booking-with-a-commitment": "booking_create",
    "r17-cancellation": "booking_cancel",
    "r18-delete-persistent-data": "destructive_mutation",
}
GATE_RULES = tuple(SELECTOR)
# Action class -> the gate rules that apply to it, and the result each takes.
# Every other gate rule is n/a for that class.
APPLIES = {
    "info.query": {"r01-research-and-prepare": "pass", "r04-prepare-without-committing": "pass"},
    "disclosure.personal": {"r11-home-address-to-an-individual": "fail", "r12-personal-contact-to-a-new-party": "fail"},
    "communication.publish": {"r15-public-posting": "fail"},
    "booking.create": {"r16-booking-with-a-commitment": "fail"},
    "booking.cancel": {"r17-cancellation": "fail"},
    "data.delete": {"r18-delete-persistent-data": "fail"},
    "money.transfer": {},
}


def _action(n: int, action_class: str, **fields) -> Action:
    return Action(
        verb=f"act_{n}",
        operator="household-selectors",
        developer=f"household-assistant-{n}@v1",
        action_class=action_class,
        target=fields.pop("target", f"service/selector-{n}"),
        action_id=f"act/everyday-selectors-{n}",
        timestamp=f"2026-08-10T11:{n:02d}:00Z",
        **fields,
    )


ACTIONS = {
    "info.query": _action(1, "info.query"),
    "disclosure.personal": _action(2, "disclosure.personal", recipient_role="fulfilling_merchant"),
    "communication.publish": _action(3, "communication.publish"),
    # A booking with an amount, in the shape a household assistant records one.
    "booking.create": _action(4, "booking.create", amount_minor=2_000, currency="EUR", target="venue/restaurant"),
    "booking.cancel": _action(5, "booking.cancel"),
    "data.delete": _action(6, "data.delete"),
    # An ordinary payment under the per-action limit, every declared input in bounds.
    "money.transfer": _action(7, "money.transfer", amount_minor=1_500, currency="EUR", rail="card",
                              counterparty_account_ref="acct-ref-selectors-1", recurrence="one_time"),
    "household.unlisted": _action(8, "household.unlisted"),
}


@pytest.fixture(scope="module")
def decisions(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("everyday-selectors")
    store = LedgerStore(tmp / "ledger")
    try:
        installed = install_pack(PACK, project_dir=tmp / "project", mode="observe")
        signer = LocalSigner(key_id="everyday-selectors-key", secret=b"everyday-selectors-fixed-key")
        engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
        return {cls: engine.check(action, dry_run=True) for cls, action in ACTIONS.items()}
    finally:
        store.close()


def _by_rule(pack, decision) -> dict[str, tuple[str, str | None]]:
    return {r.obligation_id: (r.result, r.reason) for r in obligation_results(pack, decision.constraints)}


def _gate_table(pack, decisions) -> dict[str, dict[str, str]]:
    return {
        cls: {rule: _by_rule(pack, decisions[cls])[rule][0] for rule in GATE_RULES}
        for cls in APPLIES
    }


def test_each_gate_rule_names_its_selector_and_the_digest_is_recorded():
    assert PACK.pack_id == "asg/everyday/0.3.2"
    assert {o.id: o.selector for o in PACK.obligations if o.check == "action_class_gate"} == SELECTOR
    assert all(o.selector is None for o in PACK.obligations if o.check != "action_class_gate")
    assert PACK.definition_digest() == PACK_DIGEST


@pytest.mark.parametrize("action_class", sorted(APPLIES))
def test_a_gate_rule_takes_a_result_only_when_its_selector_matched(decisions, action_class):
    expected = {rule: APPLIES[action_class].get(rule, "n/a") for rule in GATE_RULES}
    assert _gate_table(PACK, decisions)[action_class] == expected


def test_a_booking_fails_only_the_booking_rule_and_says_why_the_others_do_not_apply(decisions):
    decision = decisions["booking.create"]
    results = _by_rule(PACK, decision)
    assert decision.outcome == DENY
    assert [rule for rule, (result, _) in results.items() if result == "fail"] == ["r16-booking-with-a-commitment"]
    for rule in set(GATE_RULES) - {"r16-booking-with-a-commitment"}:
        result, reason = results[rule]
        assert result == "n/a", rule
        assert reason == f"action class 'booking.create' did not match selector {SELECTOR[rule]!r}", rule


def test_public_posting_fails_exactly_the_public_posting_rule(decisions):
    results = _by_rule(PACK, decisions["communication.publish"])
    assert [rule for rule, (result, _) in results.items() if result == "fail"] == ["r15-public-posting"]


def test_an_ordinary_payment_under_the_limit_is_allowed_and_fails_no_rule(decisions):
    decision = decisions["money.transfer"]
    results = _by_rule(PACK, decision)
    assert decision.outcome == ALLOW
    assert [rule for rule, (result, _) in results.items() if result == "fail"] == []
    assert results["r05-spending-limits"][0] == "pass"
    assert all(results[rule][0] == "n/a" for rule in GATE_RULES)


def test_an_off_taxonomy_class_fails_every_gate_rule_closed(decisions):
    results = _by_rule(PACK, decisions["household.unlisted"])
    assert {rule: results[rule][0] for rule in GATE_RULES} == dict.fromkeys(GATE_RULES, "fail")


def _rebind(pack, rule: str, selector: str | None):
    return dataclasses.replace(
        pack,
        obligations=tuple(dataclasses.replace(o, selector=selector) if o.id == rule else o for o in pack.obligations),
    )


@pytest.mark.parametrize(
    ("rule", "other"),
    [(rule, other) for rule in GATE_RULES for other in sorted(set(SELECTOR.values()) - {SELECTOR[rule]})],
)
def test_every_selector_binding_is_load_bearing(decisions, rule, other):
    rebound = _gate_table(_rebind(PACK, rule, other), decisions)
    assert rebound != _gate_table(PACK, decisions)


def test_binding_a_rule_to_the_whole_gate_result_brings_back_the_false_fail(decisions):
    rebound = _by_rule(_rebind(PACK, "r11-home-address-to-an-individual", None), decisions["booking.create"])
    assert rebound["r11-home-address-to-an-individual"][0] == "fail"


def _pack_with(tmp_path: Path, old: str, new: str) -> Path:
    pack_dir = tmp_path / "everyday"
    shutil.copytree(PACK_DIR, pack_dir)
    text = (pack_dir / "pack.yaml").read_text()
    assert text.count(old) == 1
    (pack_dir / "pack.yaml").write_text(text.replace(old, new))
    return pack_dir


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ("    selector: public_posting\n", "    selector: public_postings\n", "unknown_obligation_selector"),
        ("    selector: public_posting\n", "", "missing_obligation_selector"),
        ("    check: refundability\n", "    check: refundability\n    selector: public_posting\n",
         "invalid_obligation_selector"),
        ("    evidence_instrument: {kind: structured_field, field: user_control_state}\n",
         "    evidence_instrument: {kind: structured_field, field: user_control_state}\n    selector: public_posting\n",
         "invalid_obligation_selector"),
        ("    selector: public_posting\n", "    selector: public_posting\n    mode: judged\n",
         "invalid_obligation_selector"),
        ("    selector: public_posting\n", "    selector: [public_posting]\n", "unknown_obligation_selector"),
    ],
    ids=["unknown", "missing", "on-a-check-without-selectors", "on-a-rule-with-no-check", "on-a-judged-rule",
         "not-a-string"],
)
def test_the_loader_refuses_a_missing_misplaced_or_unknown_selector(tmp_path, old, new, reason):
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(_pack_with(tmp_path, old, new))
    assert exc.value.reason == reason
