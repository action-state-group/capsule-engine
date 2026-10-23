# SPDX-License-Identifier: Apache-2.0
"""The common result shape every check returns."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..capsule import ConstraintOutcome

__all__ = ["CheckOutcome"]


@dataclass(frozen=True)
class CheckOutcome:
    """One check's result: the constraint record it produces, any fold
    envelope(s) it read as evidence, and an optional suggested chain link
    (e.g. dedupe/verify_before_dispatch citing the capsule they matched).
    ``asks_approver`` marks a failure the check itself found an approver may
    resolve (dedupe: the same act in another deal); the engine reads it.
    ``fails_closed`` marks an in-scope ``n/a`` the check could not evaluate
    and must not be allowed past (single_commitment: whether the sale has an
    acceptance is not known); the engine refuses the action and the
    decision's ``verdict`` is ``not_evaluable``. ``asks_when_unevaluated``
    marks an in-scope ``n/a`` an approver may still resolve (caps: an
    executed act in the window has a spend that cannot be read): the engine
    asks where it would allow, and the ``verdict`` is ``not_evaluable``."""

    constraint: ConstraintOutcome
    fold_envelopes: tuple[dict, ...] = field(default_factory=tuple)
    chain_parent: str | None = None
    chain_relation: str | None = None
    asks_approver: bool = False
    fails_closed: bool = False
    asks_when_unevaluated: bool = False
