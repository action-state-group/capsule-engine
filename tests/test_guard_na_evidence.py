# SPDX-License-Identifier: Apache-2.0
"""No ``n/a`` constraint without evidence, and the two ``caps`` n/a cases are
distinguishable on the sealed record.

Before this, a ``caps`` constraint sealed identically whether no cap was
configured for the action class (the rule never applied) or a cap WAS
configured and the action carried no ``amount_minor`` (the rule applied and
could not be evaluated): ``id``, ``result`` and ``method`` matched and neither
carried an ``evidence_digest``. Every assertion below names the field on the
named record.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import ConstraintOutcome, not_applicable_evidence
from capsule_engine.guards.checks import check_plan_containment, check_verify_before_dispatch

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
CAP_MINOR = 1_000_000


def _caps_record(capsule: dict) -> dict:
    (record,) = [c for c in capsule["constraints"] if c["id"] == "caps"]
    return record


def _payment(**overrides) -> Action:
    fields = dict(
        verb="dispatch_payout",
        operator="acme",
        developer="agent@v1",
        action_class="money.transfer",
        currency="EUR",
        target="vendor/invoice-1",
        timestamp="2026-08-10T09:00:00Z",
    )
    fields.update(overrides)
    return Action(**fields)


def test_constraint_outcome_refuses_n_a_without_evidence():
    with pytest.raises(ValueError, match="requires an evidence object"):
        ConstraintOutcome(id="caps", result="n/a", reason="anything")


def test_not_applicable_evidence_is_facts_only():
    assert not_applicable_evidence("caps", in_scope=False) == {
        "constraint_id": "caps",
        "in_scope": False,
        "missing_field": None,
    }
    assert not_applicable_evidence("caps", in_scope=True, missing_field="amount_minor") == {
        "constraint_id": "caps",
        "in_scope": True,
        "missing_field": "amount_minor",
    }
    with pytest.raises(ValueError):
        not_applicable_evidence("caps", in_scope=False, missing_field="amount_minor")


def test_caps_out_of_scope_and_in_scope_missing_amount_seal_different_evidence_digests(store, caps_fold, signer):
    engine = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, caps_minor={"money.transfer": CAP_MINOR}
    )
    out_of_scope = engine.check(_payment(action_class="info.query", action_id="q/1"), dry_run=True).capsule
    in_scope_missing = engine.check(_payment(action_id="p/1"), dry_run=True).capsule

    out_record = _caps_record(out_of_scope)
    missing_record = _caps_record(in_scope_missing)

    assert out_record["result"] == "n/a"
    assert missing_record["result"] == "n/a"
    assert out_record["evidence_digest"] != missing_record["evidence_digest"]
    # A stranger can recompute both candidates from the facts alone.
    assert out_record["evidence_digest"] == json_digest(
        {"constraint_id": "caps", "in_scope": False, "missing_field": None}
    )
    assert missing_record["evidence_digest"] == json_digest(
        {"constraint_id": "caps", "in_scope": True, "missing_field": "amount_minor"}
    )


def test_every_runtime_n_a_path_carries_evidence(store, caps_fold, signer, hold_engine):
    engine = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, caps_minor={"money.transfer": CAP_MINOR}
    )
    capsules = [
        engine.check(_payment(action_class="info.query", action_id="q/2"), dry_run=True).capsule,
        engine.check(_payment(action_id="p/2"), dry_run=True).capsule,
        engine.check(_payment(amount_minor=10, action_id="p/3"), dry_run=True).capsule,
    ]
    # The hold engine only seals constraints on a non-allow decision: cite a
    # mandate that was never recorded so verify_before_dispatch denies it.
    hold = hold_engine.evaluate_and_reserve(
        _payment(action_class="info.query", amount_minor=10, cited_mandate_capsule_id="f" * 64, action_id="h/1")
    )
    assert hold.outcome == "deny"
    assert _caps_record(hold.capsule)["evidence_digest"] == json_digest(
        {"constraint_id": "caps", "in_scope": False, "missing_field": None}
    )
    capsules.append(hold.capsule)
    sealed_n_a = [c for capsule in capsules for c in capsule["constraints"] if c["result"] == "n/a"]
    assert {r["id"] for r in sealed_n_a} == {"caps", "verify_before_dispatch"}
    for record in sealed_n_a:
        assert record.get("evidence_digest"), record

    # plan_containment is only sealed when a plan is configured, so its n/a
    # path is checked on the outcome the check returns.
    plan_out = check_plan_containment(_payment(), None).constraint
    vbd_out = check_verify_before_dispatch(_payment(), store).constraint
    assert plan_out.result == "n/a"
    assert plan_out.evidence == not_applicable_evidence("plan_containment", in_scope=False)
    assert vbd_out.result == "n/a"
    assert vbd_out.evidence == not_applicable_evidence("verify_before_dispatch", in_scope=False)


def _n_a_constructions_without_evidence(path: Path) -> list[int]:
    tree = ast.parse(path.read_text())
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ConstraintOutcome"):
            continue
        kwargs = {k.arg: k.value for k in node.keywords}
        result = kwargs.get("result")
        if isinstance(result, ast.Constant) and result.value == "n/a" and "evidence" not in kwargs:
            lines.append(node.lineno)
    return lines


def test_no_source_in_guards_or_holds_builds_an_n_a_without_evidence():
    """Static half of the invariant: a check added later that writes
    ``result="n/a"`` with no ``evidence=`` fails here before it ever runs."""
    offenders = {}
    for sub in ("guards", "holds"):
        for path in sorted((PACKAGE_DIR / sub).rglob("*.py")):
            lines = _n_a_constructions_without_evidence(path)
            if lines:
                offenders[str(path.relative_to(PACKAGE_DIR))] = lines
    assert offenders == {}
