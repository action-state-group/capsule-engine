# SPDX-License-Identifier: Apache-2.0
"""The seller-side wickets: price_floor, required_disclosure,
promise_requires_approval, promise_never and offer_expiry, plus the seller configurations of recipient_role and
destination_rail. Each reads one number, one member of a closed set, one
opaque reference or one timestamp from an action, never a list, an object
or text. None of them judges whether a statement is true: they check that a
statement was recorded, or that a bound holds."""
from __future__ import annotations

import copy
import dataclasses
from pathlib import Path

import pytest
import yaml

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import not_applicable_evidence
from capsule_engine.guards.checks import (
    AUTHORIZATION_CHECKS,
    COMMERCIAL_BOUNDS_CHECKS,
    CONFIGURED_CHECKS,
    TASK_AUTHORITY_CHECKS,
    AuthorizationRecord,
    CommercialBoundsOpening,
    TaskAuthorityRecord,
    authorization_record_digest,
    check_destination_rail,
    task_authority_record_digest,
)
from capsule_engine.guards.wickets import Catalog, WicketDefinition, load_definition_file
from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.report.result_from_folds import project_guard_constraint

ROOT = Path(__file__).parent.parent / "capsule_engine"
CATALOG = ROOT / "guards" / "wickets" / "catalog_defs"
FOLDS = ROOT / "folds" / "catalog_defs"
RETIRED = Path(__file__).parent / "fixtures" / "retired_wickets"
RETIRED_EXPIRY_DIGEST = "7b1072fc6997b07e7f08941a723e60d53fd3a54dbccfda6fa7391124ec2702ee"
RETIRED_FLOOR_DIGEST = "a7eb755a71cf9dabaf04fbd740fc9ceaad5f0ba883795adad6fd9038796dffa9"

FLOOR = load_definition_file(CATALOG / "price_floor.yaml")
DISCLOSURE = load_definition_file(CATALOG / "required_disclosure.yaml")
PROMISE_ASK = load_definition_file(CATALOG / "promise_requires_approval.yaml")
PROMISE_NEVER = load_definition_file(CATALOG / "promise_never.yaml")
EXPIRY = load_definition_file(CATALOG / "offer_expiry.yaml")
SELLER_ROLE = load_definition_file(CATALOG / "recipient_role.seller.yaml")
SELLER_RAIL = load_definition_file(CATALOG / "destination_rail.seller.yaml")
SPEND = load_fold(FOLDS / "spend.weekly.v3.yaml")
SELLER_DEFINITIONS = (FLOOR, DISCLOSURE, PROMISE_ASK, PROMISE_NEVER, EXPIRY, SELLER_ROLE, SELLER_RAIL)

# The closed set of representation classes, in this order.
REPRESENTATION_CLASSES = [
    "price", "condition", "features", "authenticity", "availability", "delivery_date", "service_scope",
    "warranty", "refund_terms", "payment_methods", "pickup", "deadline", "address", "other",
]

BUYER = "buyer/ref-7"


def _approval(cls: str) -> tuple[AuthorizationRecord, str]:
    """A sealed approval for one class of statement, and the digest an
    action cites it by."""
    record: AuthorizationRecord = {"type": "approval/v0", "body": {"representation_class": cls}}
    return record, authorization_record_digest(record)


WARRANTY_APPROVAL, APPROVAL_REF = _approval("warranty")
# Digest-shaped, but the digest of no record anyone supplies.
FAKE_REF = "5" * 64

# A seller's task authority for one used bicycle: ask 1,900.00, accept down
# to 1,700.00. The floor is private: the record seals only its commitment,
# and the opening comes beside the action. Both are capsulectl's first
# commercial-bounds golden vector (tests/fixtures/commercial-bounds/).
BICYCLE_OPENING: CommercialBoundsOpening = {
    "document": {"type": "commercial-bounds/v0", "min_total_minor": 170_000},
    "nonce": "43a3c8a2914ecd228f722ed9f47dc4b4005cc1e257bc64777c0635a41ba7ea67",
    "bounds_commitment": "13563d6685d99e1bd4b5508a85a6ca679331cb3ff60e8516177ccafcfd5b4998",
}
BICYCLE: TaskAuthorityRecord = {
    "type": "task-authority/v0",
    "body": {"outcome_id": "household.sell_the_bicycle/1.0.0", "allowed_actions": ["offer", "sell"],
             "preconditions": [], "bounds_commitment": BICYCLE_OPENING["bounds_commitment"]},
}
BICYCLE_REF = task_authority_record_digest(BICYCLE)
# The same task, with a warranty statement already inside its authority.
WARRANTY_TASK: TaskAuthorityRecord = {
    "type": "task-authority/v0",
    "body": {**BICYCLE["body"], "authorized_representation_classes": ["warranty"]},
}
WARRANTY_TASK_REF = task_authority_record_digest(WARRANTY_TASK)


def _action(**overrides) -> Action:
    fields = dict(
        verb="offer",
        operator="household",
        developer="assistant@v1",
        action_class="marketplace.offer",
        amount_minor=175_000,
        currency="USD",
        target=BUYER,
        timestamp="2026-10-08T09:00:00Z",
    )
    fields.update(overrides)
    return Action(**fields)


def _missing(constraint_id: str, field: str):
    return not_applicable_evidence(constraint_id, in_scope=True, missing_field=field)


def _engine(store, signer, *wickets: WicketDefinition) -> GuardEngine:
    return GuardEngine(ledger=store, caps_fold=SPEND, signer_provider=lambda: signer, wickets=wickets)


def _run(definition: WicketDefinition, action: Action, *, ledger=None, record=None, approval=None, opening=None):
    if definition.check in COMMERCIAL_BOUNDS_CHECKS:
        return COMMERCIAL_BOUNDS_CHECKS[definition.check](action, record, opening, definition.config).constraint
    if definition.check in TASK_AUTHORITY_CHECKS:
        return TASK_AUTHORITY_CHECKS[definition.check](action, record, definition.config).constraint
    if definition.check in AUTHORIZATION_CHECKS:
        return AUTHORIZATION_CHECKS[definition.check](action, record, approval, definition.config).constraint
    return CONFIGURED_CHECKS[definition.check](action, ledger, definition.config).constraint


# -- the input shape ------------------------------------------------------------


def test_every_new_action_field_is_one_optional_scalar():
    hints = {f.name: f.type for f in dataclasses.fields(Action)}
    for name in ("representation_class", "authorized_by", "proposal_at"):
        assert hints[name] == "str | None", name
        assert Action.__dataclass_fields__[name].default is None, name


def test_new_fields_are_sealed_only_when_set(store, signer):
    engine = _engine(store, signer)
    bare = engine.check(_action(action_id="offer/bare")).capsule["asg_payload"]
    for name in ("representation_class", "authorized_by", "proposal_at"):
        assert name not in bare
    sealed = engine.check(
        _action(action_id="offer/sealed", representation_class="warranty", authorized_by=APPROVAL_REF,
                proposal_at="2026-10-08T08:00:00Z")
    ).capsule["asg_payload"]
    assert sealed["representation_class"] == "warranty"
    assert sealed["authorized_by"] == APPROVAL_REF
    assert sealed["proposal_at"] == "2026-10-08T08:00:00Z"


def test_the_representation_class_set_is_pinned_verbatim():
    assert DISCLOSURE.config["representation_classes"] == REPRESENTATION_CLASSES
    assert PROMISE_ASK.config["representation_classes"] == REPRESENTATION_CLASSES
    assert PROMISE_NEVER.config["representation_classes"] == REPRESENTATION_CLASSES


def test_the_seller_configs_reuse_the_existing_checks_and_leave_v1_alone():
    assert SELLER_ROLE.check == "recipient_role"
    assert SELLER_ROLE.config["roles"] == ["buyer", "third_party", "self"]
    assert SELLER_RAIL.check == "destination_rail"
    v1_role = load_definition_file(CATALOG / "recipient_role.yaml")
    assert v1_role.config["roles"] == ["fulfilling_merchant", "third_party", "self"]
    v1_rail = load_definition_file(CATALOG / "destination_rail.yaml")
    assert "allowed_rails" not in v1_rail.config


def test_the_loader_reproduces_every_seller_digest():
    catalog = Catalog(CATALOG)
    for definition in SELLER_DEFINITIONS:
        entry = catalog.get(definition.wicket_id)
        assert entry is not None, definition.wicket_id
        assert entry.digest == definition.definition_digest()
        assert catalog.get(entry.digest).definition == definition


# -- price_floor ----------------------------------------------------------------


def _floor(record=BICYCLE, opening=BICYCLE_OPENING, **overrides):
    return _run(FLOOR, _action(task_authority_ref=BICYCLE_REF, **overrides), record=record, opening=opening)


def test_price_floor_on_the_bicycle():
    above = _floor(amount_minor=175_000)
    assert above.result == "pass"
    assert above.evidence == {"task_authority_ref": BICYCLE_REF, "bounds_commitment": BICYCLE_OPENING["bounds_commitment"],
                              "amount_minor": 175_000, "below_floor": False}
    below = _floor(amount_minor=168_000)
    assert below.result == "fail"
    assert below.evidence["below_floor"] is True


def test_price_floor_passes_at_the_floor_itself():
    assert _floor(amount_minor=170_000).result == "pass"
    assert _floor(amount_minor=169_999).result == "fail"


def test_price_floor_reads_only_the_record_the_reference_binds():
    lowered = copy.deepcopy(BICYCLE)
    lowered["body"]["bounds_commitment"] = "0" * 64
    out = _floor(record=lowered, amount_minor=168_000)
    assert out.result == "n/a"
    assert out.evidence == _missing("price_floor", "task_authority_record")
    assert "ref mismatch" in out.reason


def test_price_floor_without_inputs_is_n_a_naming_the_input():
    assert _run(FLOOR, _action(), record=BICYCLE, opening=BICYCLE_OPENING).evidence == _missing(
        "price_floor", "task_authority_ref")
    assert _floor(record=None).evidence == _missing("price_floor", "task_authority_record")
    assert _floor(amount_minor=None).evidence == _missing("price_floor", "amount_minor")
    assert _floor(opening=None).evidence == _missing("price_floor", "commercial_bounds_opening")
    no_floor: TaskAuthorityRecord = {"body": {k: v for k, v in BICYCLE["body"].items() if k != "bounds_commitment"}}
    out = _run(FLOOR, _action(task_authority_ref=task_authority_record_digest(no_floor)), record=no_floor,
               opening=BICYCLE_OPENING)
    assert out.evidence == _missing("price_floor", "bounds_commitment")


def test_price_floor_never_reads_a_floor_in_clear_on_the_record():
    """price_floor/1.0.0 read min_total_minor from the record body; 2.0.0
    never does, so a record carrying one and no commitment is n/a."""
    clear: TaskAuthorityRecord = {"body": {**{k: v for k, v in BICYCLE["body"].items() if k != "bounds_commitment"},
                                           "min_total_minor": 170_000}}
    out = _run(FLOOR, _action(task_authority_ref=task_authority_record_digest(clear), amount_minor=1), record=clear)
    assert (out.result, out.evidence) == ("n/a", _missing("price_floor", "bounds_commitment"))


@pytest.mark.parametrize("bad", ["0" * 63, "A" * 64, 7, None])
def test_price_floor_reads_no_commitment_that_is_not_a_digest(bad):
    record: TaskAuthorityRecord = {"body": {**BICYCLE["body"], "bounds_commitment": bad}}
    out = _run(FLOOR, _action(task_authority_ref=task_authority_record_digest(record)), record=record,
               opening=BICYCLE_OPENING)
    assert (out.result, out.evidence) == ("n/a", _missing("price_floor", "bounds_commitment"))


def test_price_floor_out_of_scope_class():
    out = _run(FLOOR, _action(action_class="money.purchase", task_authority_ref=BICYCLE_REF), record=BICYCLE,
               opening=BICYCLE_OPENING)
    assert out.evidence == not_applicable_evidence("price_floor", in_scope=False)


# -- required_disclosure --------------------------------------------------------


def _disclose(engine: GuardEngine, n: int, *, cls="condition", target=BUYER, dry_run=False):
    out = engine.check(
        _action(verb="tell", action_class="communication.send", amount_minor=None, action_id=f"tell/{n}",
                target=target, representation_class=cls, equivalence_key=f"tell-{n}"),
        dry_run=dry_run,
    )
    assert out.capsule["disposition"]["decision"] == "accept"


def _act(store, signer, definition=DISCLOSURE):
    engine = _engine(store, signer, definition)
    (out,) = [c for c in engine.check(_action(action_id="offer/act")).constraints if c.id == "required_disclosure"]
    return out


def _plain(store, signer) -> GuardEngine:
    return _engine(store, signer)


def test_required_disclosure_missing_fails(store, signer):
    out = _act(store, signer)
    assert out.result == "fail"
    assert out.evidence["missing_classes"] == ["condition"]
    assert out.evidence["prior_counts"] == {"condition": 0}


def test_required_disclosure_present_passes(store, signer):
    _disclose(_plain(store, signer), 1)
    out = _act(store, signer)
    assert out.result == "pass"
    assert out.evidence["missing_classes"] == []
    assert out.evidence["prior_counts"] == {"condition": 1}
    assert out.evidence["scope"] == {"operator": "household", "target": BUYER}
    assert out.method == "disclosure.class_seen/1.0.0"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cls": "price"},  # a different class
        {"target": "buyer/ref-8"},  # the same class told to someone else
        {"dry_run": True},  # never sent
    ],
)
def test_required_disclosure_counts_only_this_class_to_this_counterparty(store, signer, kwargs):
    _disclose(_plain(store, signer), 1, **kwargs)
    assert _act(store, signer).result == "fail"


def test_required_disclosure_does_not_count_a_refused_disclosure(store, signer):
    refusing = _engine(store, signer, PROMISE_NEVER)
    out = refusing.check(_action(verb="tell", action_class="communication.send", amount_minor=None,
                                 action_id="tell/refused", representation_class="authenticity"))
    assert out.capsule["disposition"]["decision"] == "reject"
    required = dataclasses.replace(DISCLOSURE, config={**DISCLOSURE.config, "required_classes": ["authenticity"]})
    assert _act(store, signer, required).result == "fail"


def test_required_disclosure_is_not_waived_by_an_approval(store, signer):
    record, ref = _approval("condition")
    engine = _engine(store, signer, DISCLOSURE, PROMISE_ASK)
    out = engine.check(_action(action_id="offer/approved", authorized_by=ref, representation_class="condition"),
                       authorization_record=record)
    (disclosure,) = [c for c in out.constraints if c.id == "required_disclosure"]
    assert disclosure.result == "fail"
    assert "an approval does not waive it" in disclosure.reason
    assert out.outcome == "deny"


def test_required_disclosure_without_a_target_is_n_a(store, signer):
    out = _run(DISCLOSURE, _action(target=None), ledger=store)
    assert out.evidence == _missing("required_disclosure", "target")


def test_required_disclosure_refuses_a_class_outside_the_set():
    bad = {**DISCLOSURE.config, "required_classes": ["condition", "mood"]}
    with pytest.raises(ValueError, match="mood"):
        CONFIGURED_CHECKS["required_disclosure"](_action(), None, bad)


def test_required_disclosure_fold_is_the_pinned_catalog_fold():
    fold = load_fold(FOLDS / "disclosure.class_seen.yaml")
    assert DISCLOSURE.config["fold_id"] == fold.fold_id
    assert DISCLOSURE.config["fold_digest"] == fold.definition_digest()


# -- promise_requires_approval ----------------------------------------------------


def _ask(*, record=None, approval=None, **overrides):
    return _run(PROMISE_ASK, _action(action_class="communication.send", **overrides), record=record,
                approval=approval)


@pytest.mark.parametrize("cls", ["warranty", "delivery_date"])
def test_promise_requires_approval_fails_without_one(cls):
    out = _ask(representation_class=cls)
    assert out.result == "fail"
    assert out.evidence == {"representation_class": cls, "recognised": True, "requires_approval": True,
                            "authority_basis": None, "task_authority_ref": None, "authorized_by": None}
    record, ref = _approval(cls)
    approved = _ask(representation_class=cls, authorized_by=ref, approval=record)
    assert approved.result == "pass"
    assert approved.evidence["authorized_by"] == ref
    assert approved.evidence["authority_basis"] == "authorized_by"


def test_promise_requires_approval_passes_an_ordinary_representation():
    out = _ask(representation_class="condition")
    assert out.result == "pass"
    assert out.evidence["requires_approval"] is False
    assert out.evidence["authority_basis"] is None


def test_promise_requires_approval_inside_task_authority_needs_no_approval():
    out = _ask(representation_class="warranty", task_authority_ref=WARRANTY_TASK_REF, record=WARRANTY_TASK)
    assert out.result == "pass"
    assert out.evidence["authority_basis"] == "task_authority_ref"
    assert out.evidence["task_authority_ref"] == WARRANTY_TASK_REF
    assert out.evidence["authorized_by"] is None


def test_promise_requires_approval_reads_only_the_task_authority_the_reference_binds():
    # The record lists warranty, but the action cites another record.
    assert _ask(representation_class="warranty", task_authority_ref=BICYCLE_REF, record=WARRANTY_TASK).result == "fail"
    assert _ask(representation_class="warranty", task_authority_ref=FAKE_REF, record=WARRANTY_TASK).result == "fail"
    # Bound, but it lists another class, or names none.
    assert _ask(representation_class="delivery_date", task_authority_ref=WARRANTY_TASK_REF,
                record=WARRANTY_TASK).result == "fail"
    assert _ask(representation_class="warranty", task_authority_ref=BICYCLE_REF, record=BICYCLE).result == "fail"
    as_text: TaskAuthorityRecord = {"body": {**BICYCLE["body"], "authorized_representation_classes": "warranty"}}
    assert _ask(representation_class="warranty", task_authority_ref=task_authority_record_digest(as_text),
                record=as_text).result == "fail"


def _warranty(ref, approval):
    return _ask(representation_class="warranty", authorized_by=ref, approval=approval)


def test_promise_requires_approval_a_fabricated_ref_fails():
    out = _warranty(FAKE_REF, None)
    assert out.result == "fail"
    assert out.evidence["authorized_by"] == FAKE_REF
    assert out.evidence["authority_basis"] is None
    assert "no approval record" in out.reason


def test_promise_requires_approval_a_record_without_a_ref_fails():
    out = _warranty(None, WARRANTY_APPROVAL)
    assert out.result == "fail"
    assert out.evidence["authorized_by"] is None
    assert "cites no approval" in out.reason


def test_promise_requires_approval_a_record_the_ref_does_not_bind_fails():
    out = _warranty(FAKE_REF, WARRANTY_APPROVAL)
    assert out.result == "fail"
    assert "ref mismatch" in out.reason


def test_promise_requires_approval_a_bound_approval_for_another_class_fails():
    record, ref = _approval("delivery_date")
    out = _warranty(ref, record)
    assert out.result == "fail"
    assert out.evidence["authority_basis"] is None
    assert "delivery_date" in out.reason


@pytest.mark.parametrize(
    "record",
    [{"type": "approval/v0"}, {"body": "warranty"}, {"body": {}}, {"body": {"representation_class": ["warranty"]}}],
    ids=["no-body", "body-not-object", "no-class", "class-not-scalar"],
)
def test_promise_requires_approval_a_bound_approval_naming_no_class_fails(record):
    out = _warranty(authorization_record_digest(record), record)
    assert out.result == "fail"
    assert out.evidence["authority_basis"] is None


def test_promise_requires_approval_an_approval_with_no_digest_fails():
    record = {"body": {"representation_class": "warranty", "weight": 0.5}}
    out = _warranty(FAKE_REF, record)
    assert out.result == "fail"
    assert "no digest" in out.reason


def test_promise_requires_approval_through_the_engine(store, signer):
    engine = _engine(store, signer, PROMISE_ASK)
    action = _action(verb="tell", action_class="communication.send", amount_minor=None,
                     representation_class="warranty", authorized_by=APPROVAL_REF)

    def check(name, **kwargs):
        out = engine.check(dataclasses.replace(action, action_id=f"tell/{name}", equivalence_key=f"tell-{name}",
                                               **kwargs.pop("fields", {})), **kwargs)
        return out.outcome, {c.id: c.result for c in out.constraints}["promise_requires_approval"]

    assert check("bound", authorization_record=WARRANTY_APPROVAL) == ("allow", "pass")
    assert check("authority", fields={"authorized_by": None, "task_authority_ref": WARRANTY_TASK_REF},
                 task_authority_record=WARRANTY_TASK) == ("allow", "pass")
    # ASK, but the engine does not read a declared disposition yet: refused.
    assert check("unbound") == ("deny", "fail")
    assert check("fake", fields={"authorized_by": FAKE_REF}, authorization_record=WARRANTY_APPROVAL) == ("deny", "fail")


def test_promise_requires_approval_unrecognised_class_fails_closed():
    record, ref = _approval("guarantee")
    out = _ask(representation_class="guarantee", authorized_by=ref, approval=record)
    assert out.result == "fail"
    assert out.evidence["recognised"] is False


def test_promise_requires_approval_without_a_class_is_n_a():
    assert _ask().evidence == _missing("promise_requires_approval", "representation_class")


# -- promise_never --------------------------------------------------------------


def _never(**overrides):
    return _run(PROMISE_NEVER, _action(action_class="communication.send", **overrides))


def test_the_two_promise_rules_split_the_classes():
    assert PROMISE_ASK.config["requires_approval"] == ["warranty", "delivery_date"]
    assert PROMISE_NEVER.config["never"] == ["authenticity"]


def test_promise_never_fails_a_never_class():
    out = _never(representation_class="authenticity")
    assert out.result == "fail"
    assert out.evidence == {"representation_class": "authenticity", "recognised": True, "never": True,
                            "authorized_by": None}
    assert "only a change to the rule set" in out.reason


def test_promise_never_a_bound_approval_still_fails(store, signer):
    record, ref = _approval("authenticity")
    authority: TaskAuthorityRecord = {"body": {**BICYCLE["body"], "authorized_representation_classes": ["authenticity"]}}
    authority_ref = task_authority_record_digest(authority)
    assert _never(representation_class="authenticity", authorized_by=ref).result == "fail"
    assert _never(representation_class="authenticity", authorized_by=ref).evidence["authorized_by"] == ref
    engine = _engine(store, signer, PROMISE_NEVER, PROMISE_ASK)
    out = engine.check(
        _action(verb="tell", action_class="communication.send", amount_minor=None, action_id="tell/never",
                representation_class="authenticity", authorized_by=ref, task_authority_ref=authority_ref),
        authorization_record=record, task_authority_record=authority,
    )
    assert {c.id: c.result for c in out.constraints}["promise_never"] == "fail"
    assert out.outcome == "deny"


def test_promise_never_passes_other_classes():
    out = _never(representation_class="warranty")
    assert out.result == "pass"
    assert out.evidence["never"] is False


def test_promise_never_unrecognised_class_fails_closed():
    out = _never(representation_class="guarantee")
    assert out.result == "fail"
    assert out.evidence["recognised"] is False


def test_promise_never_without_a_class_is_n_a():
    assert _never().evidence == _missing("promise_never", "representation_class")


@pytest.mark.parametrize("definition,field", [(PROMISE_ASK, "requires_approval"), (PROMISE_NEVER, "never")])
def test_promise_rules_refuse_a_class_outside_the_set(definition, field):
    mutant = dataclasses.replace(definition, config={**definition.config, field: ["mood"]})
    with pytest.raises(ValueError, match="mood"):
        _run(mutant, _action(action_class="communication.send", representation_class="price"))


# -- offer_expiry ---------------------------------------------------------------


def _expiry(**overrides):
    return _run(EXPIRY, _action(**overrides))


def _unverified(field: str):
    return {"expiry_unverified": True, "missing_field": field}


def test_offer_expiry_within_the_limit_passes():
    out = _expiry(proposal_at="2026-10-07T09:00:00Z")  # exactly max_age_seconds old
    assert out.result == "pass"
    assert out.evidence == {"proposal_at": "2026-10-07T09:00:00Z", "decided_at": "2026-10-08T09:00:00Z",
                            "age_seconds": 86_400, "max_age_seconds": 86_400, "expired": False}


def test_offer_expiry_past_the_limit_fails_and_needs_a_new_proposal():
    out = _expiry(proposal_at="2026-10-07T08:59:59Z")
    assert out.result == "fail"
    assert out.reason.startswith("proposal expired")
    assert "a new proposed action is required" in out.reason
    assert out.evidence == {"proposal_at": "2026-10-07T08:59:59Z", "decided_at": "2026-10-08T09:00:00Z",
                            "age_seconds": 86_401, "max_age_seconds": 86_400, "expired": True}


def test_an_expired_proposal_is_never_not_evaluable(store, signer):
    engine = _engine(store, signer, EXPIRY)
    capsule = engine.check(_action(action_id="offer/stale", proposal_at="2026-10-01T09:00:00Z")).capsule
    (record,) = [c for c in capsule["constraints"] if c["id"] == "offer_expiry"]
    assert record["result"] == "fail"
    projected = project_guard_constraint(record, candidate_fields=frozenset({"proposal_at"}))
    assert projected.verdict == "not_met"


def test_offer_expiry_reads_fractional_seconds_down():
    out = _expiry(proposal_at="2026-10-07T09:00:00.5Z", timestamp="2026-10-08T09:00:00.4Z")
    assert out.evidence["age_seconds"] == 86_399


def test_offer_expiry_without_inputs_fails_unverified():
    out = _expiry()
    assert out.result == "fail"
    assert out.evidence == _unverified("proposal_at")
    out = _expiry(proposal_at="2026-10-07T09:00:00Z", timestamp=None)
    assert out.result == "fail"
    assert out.evidence == _unverified("timestamp")
    assert _expiry(proposal_at="2026-10-07T09:00:00Z", timestamp="now").evidence == _unverified("timestamp")


@pytest.mark.parametrize("bad", ["yesterday", "2026-10-07", "2026-10-07T09:00:00", "2026-13-07T09:00:00Z", "2026-10-09T09:00:00Z"])
def test_offer_expiry_unreadable_or_future_proposal_fails_unverified(bad):
    out = _expiry(proposal_at=bad)
    assert out.result == "fail"
    assert out.evidence == _unverified("proposal_at")
    assert "could not be established" in out.reason


def test_offer_expiry_out_of_scope_class():
    out = _expiry(action_class="money.purchase", proposal_at="2026-10-01T09:00:00Z")
    assert out.evidence == not_applicable_evidence("offer_expiry", in_scope=False)


# -- seller recipient_role and destination_rail ---------------------------------


def test_seller_recipient_role():
    def role(r):
        return _run(SELLER_ROLE, _action(action_class="disclosure.personal", recipient_role=r))

    assert role("buyer").result == "pass"
    assert role("self").result == "pass"
    assert role("third_party").result == "fail"
    assert role("fulfilling_merchant").evidence["recognised"] is False


def test_seller_destination_rail_is_an_allow_list():
    def rail(r):
        return _run(SELLER_RAIL, _action(action_class="marketplace.sale", rail=r))

    assert rail("card").result == "pass"
    assert rail("p2p").result == "pass"
    out = rail("check")
    assert out.result == "fail"
    assert out.evidence == {"rail": "check", "watched_rails": [], "allowed_rails": ["card", "p2p"]}


def test_destination_rail_v1_evidence_is_unchanged():
    out = check_destination_rail(_action(action_class="money.transfer", rail="card"),
                                 watched_rails=["p2p"], action_classes=["money.transfer"]).constraint
    assert out.evidence == {"rail": "card", "watched_rails": ["p2p"]}


# -- one mutant per config field ------------------------------------------------

# For every config field of every new definition: the vector, the field's
# mutated value, and the result before and after. A field whose mutation
# does not move the result is a field the check does not read.
MUTANTS = [
    (FLOOR, "action_classes", dict(amount_minor=168_000, task_authority_ref=BICYCLE_REF), ["money.purchase"],
     "fail", "n/a"),
    (PROMISE_ASK, "action_classes", dict(action_class="communication.send", representation_class="warranty"),
     ["marketplace.sale"], "fail", "n/a"),
    (PROMISE_ASK, "requires_approval", dict(action_class="communication.send", representation_class="warranty"),
     ["delivery_date"], "fail", "pass"),
    (PROMISE_ASK, "representation_classes", dict(action_class="communication.send", representation_class="condition"),
     [c for c in REPRESENTATION_CLASSES if c != "condition"], "pass", "fail"),
    (PROMISE_NEVER, "action_classes", dict(action_class="communication.send", representation_class="authenticity"),
     ["marketplace.sale"], "fail", "n/a"),
    (PROMISE_NEVER, "never", dict(action_class="communication.send", representation_class="authenticity"),
     ["warranty"], "fail", "pass"),
    (PROMISE_NEVER, "representation_classes", dict(action_class="communication.send", representation_class="price"),
     [c for c in REPRESENTATION_CLASSES if c != "price"], "pass", "fail"),
    (EXPIRY, "action_classes", dict(proposal_at="2026-10-07T09:00:00Z"), ["agreement.accept"], "pass", "n/a"),
    (EXPIRY, "max_age_seconds", dict(proposal_at="2026-10-07T09:00:00Z"), 86_399, "pass", "fail"),
    (SELLER_ROLE, "action_classes", dict(action_class="disclosure.personal", recipient_role="third_party"),
     ["communication.send"], "fail", "n/a"),
    (SELLER_ROLE, "roles", dict(action_class="disclosure.personal", recipient_role="buyer"), ["self"],
     "pass", "fail"),
    (SELLER_ROLE, "allowed_roles", dict(action_class="disclosure.personal", recipient_role="buyer"), ["self"],
     "pass", "fail"),
    (SELLER_RAIL, "action_classes", dict(action_class="marketplace.sale", rail="check"), ["money.transfer"],
     "fail", "n/a"),
    (SELLER_RAIL, "allowed_rails", dict(action_class="marketplace.sale", rail="check"), ["check"], "fail", "pass"),
]


@pytest.mark.parametrize("definition,field,vector,mutated,before,after", MUTANTS,
                         ids=[f"{m[0].wicket_id}:{m[1]}" for m in MUTANTS])
def test_each_config_field_moves_the_result(definition, field, vector, mutated, before, after):
    action = _action(**vector)
    assert _run(definition, action, record=BICYCLE, opening=BICYCLE_OPENING).result == before
    mutant = dataclasses.replace(definition, config={**definition.config, field: mutated})
    assert _run(mutant, action, record=BICYCLE, opening=BICYCLE_OPENING).result == after


@pytest.mark.parametrize(
    "field,mutated",
    [("action_classes", ["money.purchase"]), ("required_classes", ["price"]),
     ("representation_classes", REPRESENTATION_CLASSES[:1] + REPRESENTATION_CLASSES[2:]),
     ("fold_digest", "0" * 64), ("fold_id", "counterparty.seen_before/2.0.0")],
)
def test_each_required_disclosure_config_field_is_read(store, signer, field, mutated):
    _disclose(_plain(store, signer), 1)
    assert _act(store, signer).result == "pass"
    mutant = dataclasses.replace(DISCLOSURE, config={**DISCLOSURE.config, field: mutated})
    if field in ("representation_classes", "fold_digest", "fold_id"):
        with pytest.raises(ValueError):
            _act(store, signer, mutant)
        return
    assert _act(store, signer, mutant).result == ("n/a" if field == "action_classes" else "fail")


def test_every_config_field_has_a_mutant():
    covered = {(m[0].wicket_id, m[1]) for m in MUTANTS}
    covered |= {(DISCLOSURE.wicket_id, f) for f in
                ("action_classes", "required_classes", "representation_classes", "fold_digest", "fold_id")}
    declared = {(d.wicket_id, f) for d in SELLER_DEFINITIONS for f in d.config}
    assert declared == covered


# -- the engine, and the gap it still has ---------------------------------------


def test_engine_decides_the_bicycle(store, signer):
    engine = _engine(store, signer, FLOOR)
    ok = engine.check(_action(action_id="offer/1750", task_authority_ref=BICYCLE_REF), task_authority_record=BICYCLE,
                      commercial_bounds_opening=BICYCLE_OPENING)
    assert ok.outcome == "allow"
    low = engine.check(_action(action_id="offer/1680", amount_minor=168_000, task_authority_ref=BICYCLE_REF),
                       task_authority_record=BICYCLE, commercial_bounds_opening=BICYCLE_OPENING)
    # The definition's disposition is ASK, but the engine does not read a
    # declared disposition: a failing rule outside its escalatable set
    # refuses. Pinned here so the change that makes ASK pause shows up.
    assert low.outcome == "deny"
    assert {c.id: c.result for c in low.constraints}["price_floor"] == "fail"


def test_engine_refuses_an_expired_proposal(store, signer):
    """offer_expiry fails an expired proposal, so the engine no longer lets
    it through. Declared ASK; refused for the same gap as above."""
    engine = _engine(store, signer, EXPIRY)
    out = engine.check(_action(action_id="offer/stale", proposal_at="2026-10-01T09:00:00Z"))
    assert {c.id: c.result for c in out.constraints}["offer_expiry"] == "fail"
    assert out.outcome == "deny"


# -- a pack citing them ---------------------------------------------------------


def _write_seller_pack(tmp_path, constraints) -> None:
    pack = {
        "pack_id": "test_pub/seller-everyday/0.1.0",
        "obligations": [{"id": f"o{i}", "statement": d.wicket_id, "check": d.check}
                        for i, d in enumerate(SELLER_DEFINITIONS)],
        "action_semantics": [{
            "action_type": "offer.make",
            "action_class": "marketplace.offer",
            "required_fields": ["amount_minor", "task_authority_ref", "target"],
            "optional_fields": ["representation_class", "authorized_by", "proposal_at", "recipient_role", "rail"],
        }],
        "constraints": constraints,
        "folds": [{"file": "seen.yaml"}],
    }
    (tmp_path / "pack.yaml").write_text(yaml.dump(pack))
    (tmp_path / "seen.yaml").write_text((FOLDS / "disclosure.class_seen.yaml").read_text())


def test_a_seller_pack_citing_every_seller_definition_validates(tmp_path):
    catalog = Catalog(CATALOG)
    _write_seller_pack(tmp_path, [{"wicket_ref": d.wicket_id, "digest": catalog.get(d.wicket_id).digest}
                                  for d in SELLER_DEFINITIONS])
    loaded = load_pack_dir(tmp_path)
    assert {c.wicket_id for c in loaded.constraints} == {d.wicket_id for d in SELLER_DEFINITIONS}
    assert EXPIRY.wicket_id == "offer_expiry/1.0.1"


def test_a_seller_pack_citing_the_retired_offer_expiry_is_refused(tmp_path):
    """offer_expiry/1.0.0 changed meaning under its digest, so a pack pinned
    to it is refused and told what replaced it, not reported as unknown."""
    catalog = Catalog(CATALOG)
    constraints = [{"wicket_ref": d.wicket_id, "digest": catalog.get(d.wicket_id).digest}
                   for d in SELLER_DEFINITIONS if d is not EXPIRY]
    constraints.append({"wicket_ref": "offer_expiry/1.0.0", "digest": RETIRED_EXPIRY_DIGEST})
    _write_seller_pack(tmp_path, constraints)
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "retired_catalog_ref"
    assert "offer_expiry/1.0.1" in str(exc_info.value)


def test_a_seller_pack_citing_the_retired_price_floor_is_refused(tmp_path):
    """price_floor/1.0.0 read the floor in clear from the task authority, so a
    pack pinned to it is refused and told what replaced it."""
    catalog = Catalog(CATALOG)
    constraints = [{"wicket_ref": d.wicket_id, "digest": catalog.get(d.wicket_id).digest}
                   for d in SELLER_DEFINITIONS if d is not FLOOR]
    constraints.append({"wicket_ref": "price_floor/1.0.0", "digest": RETIRED_FLOOR_DIGEST})
    _write_seller_pack(tmp_path, constraints)
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "retired_catalog_ref"
    assert "price_floor/2.0.0" in str(exc_info.value)


def test_a_seller_pack_inlining_the_retired_offer_expiry_is_refused(tmp_path):
    inline = yaml.safe_load((RETIRED / "offer_expiry.1.0.0.yaml").read_text())
    _write_seller_pack(tmp_path, [inline])
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "invalid_constraint"
    assert "retired_definition" in str(exc_info.value)
