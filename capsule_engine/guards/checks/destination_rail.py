# SPDX-License-Identifier: Apache-2.0
"""destination_rail check: set membership of the action's payment rail.

Fails when ``Action.rail`` is one of the configured ``watched_rails`` (for
example a peer-to-peer or gift-card rail), or, when ``allowed_rails`` is
configured, is not one of those. A definition without ``allowed_rails``
records the evidence it always has. Applies only to the configured
``action_classes``. It records the rail it read and
the set it compared against; it does not judge why the rail was chosen.

A definition may also configure (``seller.destination_rail/1.1.0``):

- ``rail_aliases``: a rail token counted as another (``venmo`` as ``p2p``),
  so the sealed ``rail`` keeps the token the producer wrote and the
  definition, under its digest, says what it means;
- ``in_person_rails``: rails allowed only when ``Action.handover`` is
  ``in_person`` (cash at a pickup): one with any other handover, or none,
  fails;
- ``missing_rail``: per action class, ``fail`` to fail an action that
  carries no rail, which is otherwise not applicable.

Without them the check decides, and records, exactly as before.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["MISSING_RAIL_REASON", "check_destination_rail"]

_CHECK_ID = "destination_rail"
_METHOD = "set_membership_v0"
# The handover an in-person rail needs (Action.handover).
_IN_PERSON = "in_person"
# The reason an in-scope action that states no rail fails under
# ``missing_rail: fail``: what the user is asked about.
MISSING_RAIL_REASON = "payment method not stated"


class RailEvidence(TypedDict):
    rail: str | None
    watched_rails: list[str]
    allowed_rails: NotRequired[list[str]]
    counted_as: NotRequired[str | None]
    in_person_rails: NotRequired[list[str]]
    handover: NotRequired[str | None]
    missing_field: NotRequired[str]


def _outcome(result: str, reason: str, evidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_destination_rail(
    action: Action,
    *,
    watched_rails: list[str],
    action_classes: list[str],
    allowed_rails: list[str] | None = None,
    in_person_rails: list[str] | None = None,
    rail_aliases: dict[str, str] | None = None,
    missing_rail: dict[str, str] | None = None,
) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    # Configured by 1.1.0 and later: the evidence then also records how the
    # rail was counted and the handover beside it.
    extended = in_person_rails is not None or rail_aliases is not None or missing_rail is not None
    watched = sorted(watched_rails)
    if action.rail is None:
        if (missing_rail or {}).get(str(action.action_class)) == "fail":
            evidence = RailEvidence(rail=None, watched_rails=watched)
            _extend(evidence, action, None, allowed_rails, in_person_rails)
            evidence["missing_field"] = "rail"
            return _outcome("fail", MISSING_RAIL_REASON, evidence)
        return _outcome("n/a", "the action carries no rail; the destination rail could not be checked",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="rail"))
    counted = (rail_aliases or {}).get(action.rail, action.rail)
    evidence = RailEvidence(rail=action.rail, watched_rails=watched)
    if allowed_rails is not None:
        evidence["allowed_rails"] = sorted(allowed_rails)
    if extended:
        _extend(evidence, action, counted, allowed_rails, in_person_rails)
    named = repr(action.rail) if counted == action.rail else f"{action.rail!r} (counted as {counted!r})"
    if in_person_rails is not None and counted in in_person_rails:
        if action.handover != _IN_PERSON:
            how = "the handover is not stated" if action.handover is None else f"the handover is {action.handover!r}"
            return _outcome("fail", f"rail {named} is allowed only when the item is handed over in person; {how}",
                            evidence)
    elif allowed_rails is not None and counted not in allowed_rails:
        return _outcome("fail", f"rail {named} is not one of the allowed rails {sorted(allowed_rails)}", evidence)
    if counted in watched:
        return _outcome("fail", f"rail {named} is a watched rail", evidence)
    if not extended:
        return _outcome("pass", f"rail {named} is not a watched rail", evidence)
    if in_person_rails is not None and counted in in_person_rails:
        return _outcome("pass", f"rail {named} is allowed because the item is handed over in person", evidence)
    return _outcome("pass", f"rail {named} is one of the allowed rails", evidence)


def _extend(evidence: RailEvidence, action: Action, counted: str | None, allowed_rails: list[str] | None,
            in_person_rails: list[str] | None) -> None:
    """The members a 1.1.0 definition records beside the rail: what it was
    counted as, the in-person rails and the handover read."""
    if allowed_rails is not None:
        evidence["allowed_rails"] = sorted(allowed_rails)
    evidence["counted_as"] = counted
    evidence["in_person_rails"] = sorted(in_person_rails or [])
    evidence["handover"] = action.handover
