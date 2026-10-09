# SPDX-License-Identifier: Apache-2.0
"""everyday pack acceptance: install in observe mode, run a deterministic
scenario set through a pack-installed ``GuardEngine``, and check every
declared obligation fires both ways, on pack-attributed records.

Same discipline as ``test_pack_payments_safety_acceptance.py``: this script
regenerates the pack's checked-in ``fixtures/mini_ledger.jsonl`` and proves
it reproduces byte-for-byte. Run ``python -m tests.test_pack_everyday_acceptance``
to rewrite the fixture after an intended change.

Every assertion names the field on the named record.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_engine.guards.checks import fields_basis, task_authority_record_digest
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, record_pack_activation

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
FIXTURE_PATH = PACK_DIR / "fixtures" / "mini_ledger.jsonl"
WICKETS = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"

OPERATOR = "household-fixture"
PER_ACTION_MINOR = 2_500  # caps/5.0.0's per-action default, cited by the pack
SIGNER_SECRET = b"everyday-acceptance-fixture-fixed-key"
# Scenarios recorded as real decisions rather than dry runs: only a real
# accepted action makes a merchant known to counterparty_seen_before.
REAL_RUN = frozenset({"merchant-history-real-payment", "recipient-history-real-payment"})
# The whole sealed task-authority record a payment cites by digest, supplied
# with the decision; the plan inside it is in guards/plan.py's shape.
TASK_AUTHORITY = {"body": {"outcome_id": "household.pay_the_plumber/1.0.0", "allowed_actions": ["make_payment"],
                           "preconditions": [], "binding": {"subject": "service/plumber"}}}
TASK_AUTHORITY_REF = task_authority_record_digest(TASK_AUTHORITY)
TASK_RECORDS = {"task-inside-authority": TASK_AUTHORITY, "task-outside-authority": TASK_AUTHORITY}
MATERIAL_BASIS = fields_basis(load_definition_file(WICKETS / "material_fields_changed.yaml").config["counted_fields"])
OFFER_BASIS = fields_basis(load_definition_file(WICKETS / "offer_fields_changed.yaml").config["counted_fields"])


def _signer() -> LocalSigner:
    return LocalSigner(key_id="everyday-fixture-key", secret=SIGNER_SECRET)


def _payment(name: str, minute: int, developer: str, *, operator: str = OPERATOR, **fields) -> Action:
    fields.setdefault("recurrence", "one_time")
    return Action(
        verb="make_payment",
        operator=operator,
        developer=developer,
        action_class="money.transfer",
        currency="EUR",
        action_id=f"make_payment/everyday-fixture-{name}",
        timestamp=f"2026-08-10T10:{minute:02d}:00Z",
        **fields,
    )


def _message(name: str, minute: int, developer: str, **fields) -> Action:
    return Action(
        verb="send_message",
        operator=OPERATOR,
        developer=developer,
        action_class="comms.external",
        action_id=f"send_message/everyday-fixture-{name}",
        timestamp=f"2026-08-10T10:{minute:02d}:00Z",
        **fields,
    )


def _purchase(name: str, minute: int, developer: str, **fields) -> Action:
    return Action(
        verb="make_purchase",
        operator=OPERATOR,
        developer=developer,
        action_class="money.purchase",
        currency="EUR",
        rail="card",
        action_id=f"make_purchase/everyday-fixture-{name}",
        timestamp=f"2026-08-10T10:{minute:02d}:00Z",
        **fields,
    )


def _disclosure(name: str, minute: int, target: str = "shop/bakery", **fields) -> Action:
    return Action(
        verb="share_address",
        operator=OPERATOR,
        developer="household-assistant-k@v1",
        action_class="disclosure.personal",
        target=target,
        action_id=f"share_address/everyday-fixture-{name}",
        timestamp=f"2026-08-10T10:{minute:02d}:00Z",
        **fields,
    )


class DeclaredInputs(TypedDict):
    amount_minor: int
    target: str
    rail: str
    counterparty_account_ref: str
    refundable: bool
    material_fields_changed: int
    material_fields_basis: str
    offer_fields_changed: int
    offer_fields_basis: str
    channel: str
    first_contact_channel: str
    upfront_amount_minor: int


def _declared(payee: str = "m", **overrides) -> DeclaredInputs:
    """A payment's declared inputs, each inside its limit; ``overrides``
    moves one past it."""
    fields = DeclaredInputs(
        amount_minor=2_000,
        target=f"seller/bike-{payee}",
        rail="card",
        counterparty_account_ref=f"acct-ref-bike-{payee}",
        refundable=True,
        material_fields_changed=0,
        material_fields_basis=MATERIAL_BASIS,
        offer_fields_changed=0,
        offer_fields_basis=OFFER_BASIS,
        channel="marketplace",
        first_contact_channel="marketplace",
        upfront_amount_minor=500,
    )
    fields.update(overrides)
    return fields


def _scenarios() -> list[tuple[str, Action, str]]:
    water = dict(target="utility/water-co", rail="bank_transfer", developer="household-assistant-a@v1")
    dup = dict(
        target="shop/hardware-store",
        rail="card",
        counterparty_account_ref="acct-ref-hardware-1",
        amount_minor=1_200,
        developer="household-assistant-d@v1",
    )
    return [
        # First recorded payment to the water company: nothing to compare the
        # account against yet, so counterparty_identity_change is out of scope.
        ("baseline-allow", _payment("baseline", 1, **water, amount_minor=1_500,
         counterparty_account_ref="acct-ref-water-1", equivalence_key="water-co/2026-08"), ALLOW),
        ("counterparty-same-account", _payment("same-account", 2, **water, amount_minor=1_700,
         counterparty_account_ref="acct-ref-water-1", equivalence_key="water-co/2026-09"), ALLOW),
        ("counterparty-account-changed", _payment("account-changed", 3, **water, amount_minor=1_600,
         counterparty_account_ref="acct-ref-water-2", equivalence_key="water-co/2026-10"), DENY),
        ("rail-watched", _payment("rail-watched", 4, "household-assistant-b@v1", amount_minor=1_000,
         target="person/neighbour-1", rail="p2p", counterparty_account_ref="acct-ref-neighbour-1"), DENY),
        ("credential-in-content", _message("credential-in-content", 5, "household-assistant-c@v1",
         target="contact/support-desk", outgoing_content="Your verification code is 482913"), DENY),
        # A real accepted payment to the landlord: the earlier action that makes
        # the landlord a known recipient for the message below (see REAL_RUN).
        ("recipient-history-real-payment", _payment("rent", 6, "household-assistant-c@v1", amount_minor=900,
         target="contact/landlord", rail="bank_transfer", counterparty_account_ref="acct-ref-landlord-1"), ALLOW),
        ("credential-absent", _message("credential-absent", 6, "household-assistant-c@v1",
         target="contact/landlord", outgoing_content="The March rent is paid; the receipt is attached."), ALLOW),
        ("dedupe-original", _payment("dedupe-original", 7, **dup), ALLOW),
        ("dedupe-repeat", _payment("dedupe-repeat", 8, **dup), DENY),
        # The cap totals per operator, so each caps scenario below pays for its
        # own household and starts from that household's own total.
        # Boundary: amount == the per-action limit exactly; caps compares with
        # <=, so this passes.
        ("caps-boundary-at-cap", _payment("caps-boundary", 9, "household-assistant-e@v1", operator=f"{OPERATOR}-e",
         amount_minor=PER_ACTION_MINOR,
         target="dealer/car", rail="card", counterparty_account_ref="acct-ref-dealer-1"), ALLOW),
        ("caps-first-draw", _payment("caps-first-draw", 10, "household-assistant-f@v1", operator=f"{OPERATOR}-f", amount_minor=2_000,
         target="contractor/roof", rail="bank_transfer", counterparty_account_ref="acct-ref-roof-1"), ALLOW),
        # caps fails here (over the per-action limit) alongside
        # destination_rail, so the decision is a deny;
        # caps-over-limit-escalates below is the sole-failure case.
        ("caps-over-limit-on-watched-rail", _payment("caps-over-limit", 11, "household-assistant-f@v1", operator=f"{OPERATOR}-f",
         amount_minor=3_000, target="contractor/roof-extra", rail="p2p",
         counterparty_account_ref="acct-ref-roof-2"), DENY),
        # Population (a): a cap IS configured for money.transfer and the
        # action carries no amount_minor -- the rule applied and could not be
        # evaluated. The engine still allows it; the record says why.
        ("caps-in-scope-amount-missing", _payment("caps-amount-missing", 12, "household-assistant-g@v1",
         target="utility/power-co", rail="card", counterparty_account_ref="acct-ref-power-1"), ALLOW),
        # Population (c): no cap is configured for info.query, and none of the
        # configured checks applies to it.
        ("out-of-scope-action", Action(verb="check_balance", operator=OPERATOR, developer="household-assistant-g@v1",
         action_class="info.query", target="bank/account-summary",
         action_id="check_balance/everyday-fixture-out-of-scope", timestamp="2026-08-10T10:13:00Z"), ALLOW),
        ("recurring-charge-set-up", _payment("recurring-charge", 14, "household-assistant-h@v1", amount_minor=1_299,
         target="service/streaming", rail="card", counterparty_account_ref="acct-ref-streaming-1",
         recurrence="monthly"), DENY),
        ("caps-second-first-draw", _payment("caps-second-first-draw", 15, "household-assistant-i@v1", operator=f"{OPERATOR}-i",
         amount_minor=2_500, target="builder/extension", rail="card",
         counterparty_account_ref="acct-ref-builder-1"), ALLOW),
        # caps (per-action limit) is the SOLE failing check and money.transfer has an approver
        # role, so the decision escalates: disposition.decision needs_input,
        # disposition.verdict_class hitl_dispatched.
        ("caps-over-limit-escalates", _payment("caps-escalates", 16, "household-assistant-i@v1", operator=f"{OPERATOR}-i",
         amount_minor=3_000, target="builder/extension-phase-2", rail="card",
         counterparty_account_ref="acct-ref-builder-2"), ESCALATE),
        # A real (not dry-run) accepted payment to the bakery: the earlier
        # action that makes the bakery a known merchant (see REAL_RUN).
        ("merchant-history-real-payment", _payment("merchant-history", 17, "household-assistant-j@v1",
         amount_minor=700, target="shop/bakery", rail="card", counterparty_account_ref="acct-ref-bakery-1"), ALLOW),
        # No accepted action with the garden centre yet: counterparty_seen_before
        # fails, and money.purchase has no approver role, so the decision denies.
        ("merchant-first-purchase", _purchase("merchant-first", 18, "household-assistant-j@v1", amount_minor=1_800,
         target="shop/garden-centre"), DENY),
        # The bakery has a real accepted payment, so a purchase there is a
        # repeat merchant and passes.
        ("merchant-repeat-purchase", _purchase("merchant-repeat", 19, "household-assistant-j@v1", amount_minor=900,
         target="shop/bakery"), ALLOW),
        # booking.create matches the gate's booking_create selector, which fails.
        ("booking-create", Action(verb="book_table", operator=OPERATOR, developer="household-assistant-k@v1",
         action_class="booking.create", amount_minor=2_000, currency="EUR", target="venue/restaurant",
         action_id="book_table/everyday-fixture-booking-create", timestamp="2026-08-10T10:20:00Z"), DENY),
        # household.unlisted has no row in the action taxonomy: action_class_gate
        # fails closed, as the only failing check, so the decision denies.
        ("off-taxonomy-class", Action(verb="do_unlisted", operator=OPERATOR, developer="household-assistant-l@v1",
         action_class="household.unlisted", target="service/unlisted",
         action_id="do_unlisted/everyday-fixture-off-taxonomy", timestamp="2026-08-10T10:21:00Z"), DENY),
        # The role of a disclosure's recipient. Every personal-data disclosure
        # also fails action_class_gate, so both deny.
        ("disclosure-to-fulfilling-merchant", _disclosure("fulfilling-merchant", 22, recipient_role="fulfilling_merchant"),
         DENY),
        ("disclosure-to-third-party", _disclosure("third-party", 23, recipient_role="third_party",
                                                     target="person/neighbour-2"), DENY),
        # Every declared input inside its limits: refundable, no pinned field
        # changed, the first-contact channel, a deposit of exactly a quarter.
        ("declared-terms-in-bounds", _payment("terms-in-bounds", 24, "household-assistant-m@v1",
         operator=f"{OPERATOR}-m", **_declared()), ALLOW),
        # Each of the following moves one declared input past its limit, on its
        # own household and payee.
        ("non-refundable-payment", _payment("non-refundable", 25, "household-assistant-m@v1",
         operator=f"{OPERATOR}-n", **_declared("n", refundable=False)), DENY),
        ("material-terms-changed", _payment("material-changed", 26, "household-assistant-m@v1",
         operator=f"{OPERATOR}-o", **_declared("o", material_fields_changed=2)), DENY),
        ("offer-differs-from-stated", _payment("offer-differs", 27, "household-assistant-m@v1",
         operator=f"{OPERATOR}-p", **_declared("p", offer_fields_changed=1)), DENY),
        ("channel-moved", _payment("channel-moved", 28, "household-assistant-m@v1",
         operator=f"{OPERATOR}-q", **_declared("q", channel="whatsapp")), DENY),
        ("deposit-over-a-quarter", _payment("deposit-over", 29, "household-assistant-m@v1",
         operator=f"{OPERATOR}-r", **_declared("r", upfront_amount_minor=501)), DENY),
        # A payment citing a task-authority record, supplied with the decision
        # (TASK_RECORDS): inside its plan, and to a payee its plan does not bind.
        ("task-inside-authority", _payment("task-inside", 30, "household-assistant-s@v1", operator=f"{OPERATOR}-s",
         amount_minor=2_000, target="service/plumber", rail="card", counterparty_account_ref="acct-ref-plumber-1",
         task_authority_ref=TASK_AUTHORITY_REF), ALLOW),
        ("task-outside-authority", _payment("task-outside", 31, "household-assistant-s@v1", operator=f"{OPERATOR}-t",
         amount_minor=2_000, target="service/roofer", rail="card", counterparty_account_ref="acct-ref-roofer-1",
         task_authority_ref=TASK_AUTHORITY_REF), DENY),
    ]


def _run_scenarios(ledger, *, project_dir):
    installed = install_pack(load_pack_dir(PACK_DIR), project_dir=project_dir, mode="observe")
    signer = _signer()
    engine = build_engine(installed, ledger=ledger, signer_provider=lambda: signer)
    activation = record_pack_activation(
        installed,
        ledger=ledger,
        operator=OPERATOR,
        developer="capsule-init-tool",
        signer=signer,
        timestamp="2026-08-10T10:00:00Z",
        action_id="policy_manifest_activated/everyday-fixture-install",
    )
    capsules: dict[str, dict] = {}
    for name, action, expected in _scenarios():
        decision = engine.check(action, dry_run=name not in REAL_RUN, task_authority_record=TASK_RECORDS.get(name))
        if decision.outcome != expected:
            raise AssertionError(f"scenario {name!r}: expected {expected!r}, got {decision.outcome!r} ({decision.reason})")
        capsules[name] = decision.capsule
    return installed, activation, capsules, list(ledger.scan())


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("everyday")
    store = LedgerStore(tmp / "ledger")
    try:
        installed, activation, capsules, records = _run_scenarios(store, project_dir=tmp / "project")
        verified = {name: store.verify(c["capsule_id"]) for name, c in capsules.items()}
    finally:
        store.close()
    return installed, activation, capsules, records, verified


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _constraint(capsule: dict, constraint_id: str) -> dict:
    (record,) = [c for c in capsule["constraints"] if c["id"] == constraint_id]
    return record


def test_every_declared_scenario_ran_at_its_declared_outcome(run):
    installed, _, capsules, records, verified = run
    declared = {s.id: s.outcome for s in installed.pack.fixtures.scenarios}
    assert set(declared) == set(capsules)
    for name, result in verified.items():
        assert result.ok, f"{name}: {[f.detail for f in result.findings]}"
    assert len(records) == 1 + len(capsules)


def test_records_are_pack_attributed_and_observe_mode(run):
    installed, activation, capsules, _, _ = run
    for name, capsule in capsules.items():
        assert capsule["asg_payload"]["manifest_digest"] == installed.resolved.manifest_digest, name
        assert capsule["asg_payload"]["checkpoint"].get("dry_run") is (True if name not in REAL_RUN else None), name
    assert activation["asg_payload"]["detail"]["packs"] == [
        {"pack_id": "asg/everyday/0.3.2", "digest": installed.pack.definition_digest(), "mode": "observe"}
    ]


def test_cited_definitions_resolve_to_the_built_in_digests(run):
    installed, _, _, _, _ = run
    pinned = {w.wicket_id: w.digest for w in installed.manifest.wickets}
    assert pinned["caps/5.0.0"] == "2807e174dc7c817917621f90a53f3fa54992b76fe3ec28e8567f814b9e72a741"
    assert pinned["dedupe/1.0.0"] == "18ab5d489f1e5774d576b8f99897edd4f4b20f609b85683456a3e3b6b4912abb"


def test_caps_in_scope_missing_amount_and_out_of_scope_seal_different_facts(run):
    _, _, capsules, _, _ = run
    in_scope = _constraint(capsules["caps-in-scope-amount-missing"], "caps")
    out_of_scope = _constraint(capsules["out-of-scope-action"], "caps")
    assert in_scope["result"] == "n/a"
    assert out_of_scope["result"] == "n/a"
    assert in_scope["evidence_digest"] != out_of_scope["evidence_digest"]
    assert in_scope["evidence_digest"] == json_digest(
        {"constraint_id": "caps", "in_scope": True, "missing_field": "amount_minor"}
    )
    assert out_of_scope["evidence_digest"] == json_digest(
        {"constraint_id": "caps", "in_scope": False, "missing_field": None}
    )


def test_the_sole_caps_failure_escalates_with_the_donated_disposition_pair(run):
    _, _, capsules, _, _ = run
    escalated = capsules["caps-over-limit-escalates"]
    assert _constraint(escalated, "caps")["result"] == "fail"
    assert [c["id"] for c in escalated["constraints"] if c["result"] == "fail"] == ["caps"]
    assert escalated["disposition"]["decision"] == "needs_input"
    assert escalated["disposition"]["verdict_class"] == "hitl_dispatched"


def test_an_off_taxonomy_class_fails_the_gate_closed_and_says_so(run):
    from capsule_engine.guards.classes import TAXONOMY_VERSION

    capsule = run[2]["off-taxonomy-class"]
    assert [c["id"] for c in capsule["constraints"] if c["result"] == "fail"] == ["action_class_gate"]
    assert _constraint(capsule, "action_class_gate")["evidence_digest"] == json_digest(
        {"action_class": "household.unlisted", "in_taxonomy": False, "taxonomy_version": TAXONOMY_VERSION}
    )
    assert capsule["disposition"]["decision"] == "reject"


def test_a_repeat_payment_chains_to_the_payment_it_repeats(run):
    _, _, capsules, _, _ = run
    repeat = capsules["dedupe-repeat"]
    assert repeat["chain"]["parent_capsule_id"] == capsules["dedupe-original"]["capsule_id"]
    assert repeat["chain"]["relation"] == "confirms"


def test_outgoing_content_is_not_on_any_record(run):
    _, _, capsules, _, _ = run
    for name in ("credential-in-content", "credential-absent"):
        assert "outgoing_content" not in capsules[name]["asg_payload"]
    assert "482913" not in FIXTURE_PATH.read_text()
    # The sealed evidence carries pattern ids only, nothing derived from the content.
    assert _constraint(capsules["credential-in-content"], "credential_pattern")["evidence_digest"] == json_digest(
        {"matched_pattern_ids": ["one_time_code"], "pattern_ids": ["card_security_code", "one_time_code", "password_value"]}
    )


def test_fixture_is_reproducible_byte_for_byte(run):
    _, _, _, records, _ = run
    regenerated = [json.dumps(r.capsule, separators=(",", ":")) for r in records]
    assert regenerated == FIXTURE_PATH.read_text().splitlines()


def _regenerate_fixture() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store = LedgerStore(tmp / "ledger")
        try:
            _, _, _, records = _run_scenarios(store, project_dir=tmp / "project")
        finally:
            store.close()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FIXTURE_PATH, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.capsule, separators=(",", ":")) + "\n")
    print(f"wrote {len(records)} record(s) to {FIXTURE_PATH}")


if __name__ == "__main__":
    _regenerate_fixture()
