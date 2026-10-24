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
ruled out. An ``action_class_gate`` obligation's reason names neither a selector
nor an action class (capsule-engine#182); see ``_gate_result``.

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


# The reasons an action_class_gate rule shows the user. None names a selector
# or an action class (capsule-engine#182): those stay in the check's evidence.
# go/guard/report.go words the same sentences; conformance-vectors/guard/reasons/
# gate-reasons.json is the one table that this module's tests and the Go tests
# both read, so a change in one language alone fails.
GATE_NOT_APPLICABLE = "This rule does not apply to this action."
GATE_FLAGGED = "Asks: this kind of action needs your approval first."
GATE_UNRECOGNISED = "The rules do not recognise this kind of action, so it is held."
GATE_FIRST_SHARE_UNKNOWN = "Asks: first time sharing your details with this recipient."
# Selectors that ask on the first telling of a class, or to a new party.
_FIRST_SHARE_SELECTORS = frozenset({"personal_disclosure_new_class", "personal_disclosure_new_party"})
_PARTY_WORDS = {
    "fulfilling_merchant": "the merchant",
    "platform": "the platform",
    "buyer": "the buyer",
    "third_party": "a third party",
    "self": "your own account",
}


def _words(names: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``, with underscores read as spaces."""
    spoken = [n.replace("_", " ") for n in names]
    return spoken[0] if len(spoken) == 1 else f"{', '.join(spoken[:-1])} and {spoken[-1]}"


def first_share_reason(role: object, classes: object) -> str:
    """The ruled reason for r11/r12 (capsule-engine#182), filled from the role
    and data classes the recipient_role check recorded; the generic sentence
    when either was not recorded."""
    if not isinstance(role, str) or not isinstance(classes, list) or not classes:
        return GATE_FIRST_SHARE_UNKNOWN
    if not all(isinstance(c, str) for c in classes):
        return GATE_FIRST_SHARE_UNKNOWN
    party = _PARTY_WORDS.get(role, "this recipient")
    return f"Asks: first time sharing your {_words(sorted(classes))} with {party}."


def _gate_result(
    obligation_id: str, selector: str | None, on_match: str, outcome: ConstraintOutcome, recorded: ConstraintOutcome | None
) -> ObligationResult:
    """An action_class_gate obligation's result, with a reason that carries no
    selector or class name."""
    evidence = outcome.evidence or {}
    matched = evidence.get("matched_selectors")
    if matched is None:
        if outcome.result == "fail":
            return ObligationResult(obligation_id, "fail", GATE_UNRECOGNISED)
        if evidence.get("taxonomy_mismatch") is not None:
            # Held, not out of scope: the check's own reason names only the two
            # taxonomy versions, as Go's not_evaluable reason does.
            return ObligationResult(obligation_id, "n/a", outcome.reason)
        return ObligationResult(obligation_id, "n/a", GATE_NOT_APPLICABLE)
    if selector is not None and selector not in matched:
        return ObligationResult(obligation_id, "n/a", GATE_NOT_APPLICABLE)
    result = on_match if selector is not None else outcome.result
    if result != "fail":
        return ObligationResult(obligation_id, result, None)
    if selector in _FIRST_SHARE_SELECTORS:
        seen = (recorded.evidence or {}) if recorded is not None else {}
        return ObligationResult(
            obligation_id, "fail", first_share_reason(seen.get("recipient_role"), seen.get("disclosed_classes"))
        )
    return ObligationResult(obligation_id, "fail", GATE_FLAGGED)


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
        on_match = selectors[o.check][o.selector]["on_match"] if o.selector is not None else outcome.result
        if o.check == "action_class_gate":
            results.append(_gate_result(o.id, o.selector, on_match, outcome, by_id.get("recipient_role")))
        elif o.selector is None:
            results.append(ObligationResult(o.id, outcome.result, outcome.reason))
        else:
            results.append(_selector_result(o.id, o.selector, on_match, outcome))
    return tuple(results)
