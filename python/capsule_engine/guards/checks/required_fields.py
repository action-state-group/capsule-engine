# SPDX-License-Identifier: Apache-2.0
"""required_fields check: an action a pack names by its action type carries
every field that type requires (capsule-engine#263).

A pack's ``action_semantics`` name action types (``inference.purchase``) and
the normalized fields each requires. An action names its type in
``Action.verb``: a capsule's own ``action_type`` is ``fyi`` or ``decide`` and
never a pack's name (``packs/schema.py`` ``ActionSemantic``). An action whose
verb is one of the pack's types is checked: with every required field set
(not ``None``, an empty string or an empty list) it passes; with one missing
it is ``n/a`` in scope naming the first missing field, in the order the pack
lists them, and fails closed (``CheckOutcome.fails_closed``): the engine
refuses it. An action whose verb names no type of the pack is not checked
and gets no constraint, so a decision on one keeps the bytes it had.

A decision seals only the digest of an ``equivalence_key``, never the key
(``guards/capsule.py``), and an action read back from it carries only
``equivalence_key_digest``. So ``equivalence_key`` counts as set when either
is: a purchase allowed live is not refused on replay.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_required_fields"]

_CHECK_ID = "required_fields"
_METHOD = "action_semantics_v0"


class RequiredFieldsEvidence(TypedDict):
    action_type: str
    required_fields: list[str]


# A required field and the field a decision seals in its place.
_SEALED_AS = {"equivalence_key": "equivalence_key_digest"}


def _missing(value: object) -> bool:
    return value is None or value == "" or value == ()


def _unset(action: Action, name: str) -> bool:
    sealed = _SEALED_AS.get(name)
    return _missing(getattr(action, name)) and (sealed is None or _missing(getattr(action, sealed)))


def check_required_fields(action: Action, required: Mapping[str, tuple[str, ...]]) -> CheckOutcome | None:
    """The check for ``action``, or ``None`` when its verb names no action
    type in ``required`` (action type -> its required fields)."""
    fields = required.get(action.verb)
    if fields is None:
        return None
    for name in fields:
        if _unset(action, name):
            return CheckOutcome(
                constraint=ConstraintOutcome(
                    id=_CHECK_ID, result="n/a",
                    reason=f"the action carries no {name}, which action type {action.verb} requires; it is refused",
                    evidence=not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=name),
                    check_type="policy", method=_METHOD,
                ),
                fails_closed=True,
            )
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result="pass", reason=f"the action carries every field action type {action.verb} requires",
            evidence=RequiredFieldsEvidence(action_type=action.verb, required_fields=list(fields)),
            check_type="policy", method=_METHOD,
        )
    )
