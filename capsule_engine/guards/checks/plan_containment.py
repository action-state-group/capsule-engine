# SPDX-License-Identifier: Apache-2.0
"""plan_containment check: is this action inside the declared plan's allowed
action set, correctly bound, and (where required) citing the record its
precondition names?

The forward half of the design doc's one idea (``plan-containment-demo-
design-2026-08-12.md`` §1): a declared outcome compiles BACKWARD into an
attainment fold (checked at report time -- the existing hand-written folds in
``examples/``) and FORWARD into this containment check (checked at act time).
Containment never claims the outcome was reached -- it only ever answers "was
this action inside the plan that was supposed to serve it". Attainment is a
separate question, checked separately, by a separate reader. This check must
never be described, in code or output, as guaranteeing an outcome; a
containment PASS on every action in a run is compatible with the outcome
never being attained (Run C in the design doc's fixtures makes exactly this
point).

**Pure function of ``(action, plan)`` -- no ledger read.** Unlike ``caps``
(``guards/checks/caps.py``, a real fold replay over ledger history), this
check never calls ``ledger.scan``/``ledger.fetch``. That means it has no
read-decide-append window and is not exposed to ``[ldg-guardengine-caps-
race]`` -- see ``tests/test_plan_containment_check.py``'s explicit
lock-independence test, which is the property under test, not a comment
asserting it.

That purity has one direct consequence for how preconditions are checked:
this function can confirm a precondition's citation is PRESENT
(``action.cited_mandate_capsule_id is not None``) but never that the cited
capsule is genuine or actually carries the claimed content -- verifying a
citation's content requires a ledger read, which is exactly what
``verify_before_dispatch`` (``guards/checks/verify_before_dispatch.py``,
already wired into ``GuardEngine.check()``) does independently, against the
same ``cited_mandate_capsule_id`` field. The two checks compose: this one
enforces "a citation was declared where the plan requires one"; that one
enforces "the cited record is real and unaltered". Neither check alone
verifies both; together they do.

A departure from the plan (an attempted verb outside the allowed set, a
binding mismatch, or a missing required citation) is a ``fail`` -- never
escalated, never routed through a judge (the judge only ever records,
per the design doc's lines-to-hold; it has no role in this check or in
``GuardEngine._decide``'s outcome).
"""
from __future__ import annotations

from ..action import Action
from ..capsule import ConstraintOutcome
from ..plan import PlanDefinition
from .base import CheckOutcome

__all__ = ["check_plan_containment"]

_CHECK_ID = "plan_containment"
_METHOD = "plan_containment/pure"


def _binding_mismatch_reason(action: Action, plan: PlanDefinition) -> str | None:
    """The only binding key this v0 independently verifies is ``subject``,
    read off ``Action.target`` ("an optional dedupe discriminator, e.g. a
    counterparty or recipient reference" -- guards/action.py -- exactly what
    a plan's bound subject is). Other binding keys (e.g. ``window``) are
    recorded on the plan and echoed in evidence for the record, but are not
    independently re-derivable from an ``Action``'s own fields without a
    ledger read (which window a session belongs to), so this pure check
    declares them rather than enforcing them."""
    subject = plan.binding.get("subject")
    if subject is not None and action.target != subject:
        return f"plan binds subject={subject!r}, action target is {action.target!r}"
    return None


def check_plan_containment(action: Action, plan: PlanDefinition | None) -> CheckOutcome:
    if plan is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="n/a",
                reason="no plan is bound to this decision",
                check_type="policy",
                method=_METHOD,
            )
        )

    allowed_set_digest = plan.allowed_set_digest()
    plan_digest = plan.definition_digest()
    step_index = plan.step_index(action.verb)

    def _evidence(**extra: object) -> dict:
        evidence = {
            "outcome_id": plan.outcome_id,
            "plan_digest": plan_digest,
            "attempted_verb": action.verb,
            "allowed_set_digest": allowed_set_digest,
            "step_index": step_index,
            # Label re-derivability ON the record, not just in a docstring
            # (a doc claim discovered missing after history is sealed is an
            # embarrassment; a doc claim absent from the record it describes
            # cannot be checked at all). "sealed_pure": every input this
            # evidence was computed from (the plan, cited by plan_digest, and
            # the action record) is itself sealed on or alongside the
            # capsule -- a holder of both can recompute this exact evidence
            # and its digest, forever, with no ledger access. Contrast
            # ``caps`` (``guards/checks/caps.py``): its evidence is a
            # function of ledger state read at decision time
            # (``ledger.scan``), which is not itself sealed onto the
            # capsule, so a caps verdict is attested, not replayed --
            # see ``tests/test_plan_containment_check.py::
            # test_evidence_is_re_derivable_from_the_disclosed_record_alone``.
            "replay_class": "sealed_pure",
            # Over-breadth measure (see ``PlanDefinition.
            # admitted_action_space_size``'s own docstring for what it does
            # and does NOT claim): sealed at digest-freeze, disclosed on
            # every decision so a vacuously broad plan is visible in the
            # receipt rather than flattered by it.
            "admitted_action_space_size": plan.admitted_action_space_size(),
        }
        evidence.update(extra)
        return evidence

    if step_index is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="fail",
                reason=(
                    f"{action.verb!r} is a departure from plan {plan.outcome_id!r} -- not in its allowed "
                    "action set"
                ),
                evidence=_evidence(reason_kind="not_in_allowed_set"),
                check_type="policy",
                method=_METHOD,
            )
        )

    mismatch = _binding_mismatch_reason(action, plan)
    if mismatch is not None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="fail",
                reason=f"{action.verb!r} is a departure from plan {plan.outcome_id!r} -- {mismatch}",
                evidence=_evidence(reason_kind="binding_mismatch", binding=dict(plan.binding)),
                check_type="policy",
                method=_METHOD,
            )
        )

    precondition = plan.precondition_for(action.verb)
    if precondition is not None and action.cited_mandate_capsule_id is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="fail",
                reason=(
                    f"{action.verb!r} is a departure from plan {plan.outcome_id!r} -- its precondition "
                    f"requires citing {precondition.citing!r}, and no citation was given"
                ),
                evidence=_evidence(reason_kind="precondition_uncited", requires_citation_for=precondition.citing),
                check_type="policy",
                method=_METHOD,
            )
        )

    extra: dict[str, object] = {}
    if precondition is not None:
        extra["cited_capsule_id"] = action.cited_mandate_capsule_id
        extra["satisfied_precondition"] = precondition.citing
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID,
            result="pass",
            reason=f"{action.verb!r} is step {step_index} of plan {plan.outcome_id!r} -- in bounds",
            evidence=_evidence(**extra),
            check_type="policy",
            method=_METHOD,
        )
    )
