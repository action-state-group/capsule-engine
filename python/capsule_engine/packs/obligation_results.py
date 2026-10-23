# SPDX-License-Identifier: Apache-2.0
"""One decision's result for each obligation a pack measures by a check.

An obligation with no ``selector`` takes its check's result. An obligation
with a ``selector`` (``schema.Obligation``) takes only that selector's part
of the check: when the check's evidence lists the selector among the ones
that matched, the result is that selector's ``on_match``; when it does not,
the obligation does not apply to this action and is ``n/a`` -- never a fail
and never a pass. When the check could not resolve the action's class, its
evidence lists no selectors and the check failed closed; every obligation
bound to one of its selectors fails with it, because none of them can be
ruled out.

The constraint records sealed on a capsule carry an evidence digest, not
the evidence, so this reads the ``ConstraintOutcome`` objects of the
decision itself (``GuardDecision.constraints``).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..guards.capsule import ConstraintOutcome
from .schema import PackDefinition

__all__ = ["ObligationResult", "obligation_results"]


@dataclass(frozen=True)
class ObligationResult:
    obligation_id: str
    result: str  # "pass" | "fail" | "n/a"
    reason: str | None


def _selector_result(obligation_id: str, selector: str, on_match: str, outcome: ConstraintOutcome) -> ObligationResult:
    if outcome.result == "n/a":
        return ObligationResult(obligation_id, "n/a", f"selector {selector!r} did not match: {outcome.reason}")
    evidence = outcome.evidence or {}
    matched = evidence.get("matched_selectors")
    if matched is None:
        return ObligationResult(obligation_id, outcome.result, outcome.reason)
    action_class = evidence.get("action_class")
    if selector in matched:
        return ObligationResult(obligation_id, on_match, f"action class {action_class!r} matched selector {selector!r}")
    return ObligationResult(
        obligation_id, "n/a", f"action class {action_class!r} did not match selector {selector!r}"
    )


def obligation_results(pack: PackDefinition, constraints: Sequence[ConstraintOutcome]) -> tuple[ObligationResult, ...]:
    """The result of every check-measured obligation in ``pack``, in pack order.

    ``constraints`` are one decision's outcomes from an engine built from
    ``pack``; an obligation whose check has no outcome among them raises
    ``ValueError``, since the outcomes came from some other configuration.
    """
    by_id = {c.id: c for c in constraints}
    selectors = {c.check: c.config["selectors"] for c in pack.constraints if "selectors" in c.config}
    results: list[ObligationResult] = []
    for o in pack.obligations:
        if o.check is None:
            continue
        outcome = by_id.get(o.check)
        if outcome is None:
            raise ValueError(f"obligation {o.id!r} cites check {o.check!r}, which has no outcome in this decision")
        if o.selector is None:
            results.append(ObligationResult(o.id, outcome.result, outcome.reason))
        else:
            on_match = selectors[o.check][o.selector]["on_match"]
            results.append(_selector_result(o.id, o.selector, on_match, outcome))
    return tuple(results)
