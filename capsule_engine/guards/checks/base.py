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
    (e.g. dedupe/verify_before_dispatch citing the capsule they matched)."""

    constraint: ConstraintOutcome
    fold_envelopes: tuple[dict, ...] = field(default_factory=tuple)
    chain_parent: str | None = None
    chain_relation: str | None = None
