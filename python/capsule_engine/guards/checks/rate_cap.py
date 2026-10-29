# SPDX-License-Identifier: Apache-2.0
"""rate_cap check: the advertised rates of the peer an inference is bought
from, against the operator's two caps (capsule-engine#263).

Reads ``Action.input_msat_per_million`` and ``Action.output_msat_per_million``,
the selected peer's prices per million input and output tokens in
millisatoshis (mesh-llm's ``Pricing`` units), and fails when either is above
``max_input_msat_per_million`` or ``max_output_msat_per_million``. A rate at
its cap passes. An action that carries either rate is checked on both; one
that carries neither, or only one, is ``n/a`` in scope naming the first rate
missing, and fails closed (``CheckOutcome.fails_closed``): a price that cannot
be read is refused, never allowed. Applies only to the configured
``action_classes``.

The wicket ships both caps at the largest integer every JSON reader holds
exactly, so the pack sets no bound of its own; the operator's policy profile
sets them (``policy/profile.py``, ``RATE_CAP_KEYS``).
"""
from __future__ import annotations

from typing import Literal, TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["RATE_FIELDS", "check_rate_cap"]

_CHECK_ID = "rate_cap"
_METHOD = "rate_threshold_v0"

# Each rate the check reads, and the cap it is compared with.
RATE_FIELDS = (
    ("input_msat_per_million", "max_input_msat_per_million"),
    ("output_msat_per_million", "max_output_msat_per_million"),
)


class RateOver(TypedDict):
    """One cap a rate exceeded."""

    cap: Literal["max_input_msat_per_million", "max_output_msat_per_million"]
    cap_msat_per_million: int
    observed_msat_per_million: int


class RateCapEvidence(TypedDict):
    input_msat_per_million: int
    output_msat_per_million: int
    max_input_msat_per_million: int
    max_output_msat_per_million: int
    # Empty on a pass.
    over: list[RateOver]


def _outcome(
    result: str, reason: str, evidence: RateCapEvidence | NotApplicableEvidence, *, fails_closed: bool = False
) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        ),
        fails_closed=fails_closed,
    )


def check_rate_cap(
    action: Action, *, max_input_msat_per_million: int, max_output_msat_per_million: int, action_classes: list[str]
) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    caps = {"max_input_msat_per_million": max_input_msat_per_million,
            "max_output_msat_per_million": max_output_msat_per_million}
    rates = {field: getattr(action, field) for field, _ in RATE_FIELDS}
    for field, _ in RATE_FIELDS:
        if rates[field] is None:
            return _outcome(
                "n/a", f"the action carries no {field}; the price cannot be read, so it is refused",
                not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=field), fails_closed=True,
            )
    over = [
        RateOver(cap=cap, cap_msat_per_million=caps[cap], observed_msat_per_million=rates[field])
        for field, cap in RATE_FIELDS
        if rates[field] > caps[cap]
    ]
    evidence = RateCapEvidence(**rates, **caps, over=over)
    if over:
        which = "; ".join(f"{o['cap']} {o['cap_msat_per_million']}, observed {o['observed_msat_per_million']}"
                          for o in over)
        return _outcome("fail", which, evidence)
    return _outcome("pass", "both rates are at or under their caps", evidence)
