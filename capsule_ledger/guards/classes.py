# SPDX-License-Identifier: Apache-2.0
"""Starter action-class taxonomy (gating decisions doc §1) and the
classification default.

An action with no declared class -- or one not in this taxonomy -- is
CONSEQUENTIAL, fail-closed. This is deliberate: it is the loophole every
other rule in the failure-semantics table would otherwise escape through.
The taxonomy itself is kept small (per the doc: "a handful of named
classes ... keep it small") so day-one users are not blocked on everything.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ActionClass", "TAXONOMY", "UNCLASSIFIED_DEFAULT", "classify"]


@dataclass(frozen=True)
class ActionClass:
    name: str
    consequential: bool
    # Whether this class MAY be configured to fail open on a stale view or an
    # unreachable engine (gating doc §1: "fail-open permitted for low-risk
    # classes when explicitly configured"). This never makes fail-open a
    # default by itself -- a caller must still opt in per-class on the engine.
    fail_open_allowed: bool = False
    # The role that can resolve a cap-exceeded hold on this class via the
    # HITL bridge (D2, 2026-08-05). None means no approver is configured for
    # this class -- a cap-exceeded action in it hard-denies rather than
    # escalating; this is the "classes explicitly marked deny" half of D2.
    approver_role: str | None = None


MONEY_TRANSFER = ActionClass("money.transfer", consequential=True, approver_role="treasury-approver")
DATA_DELETE = ActionClass("data.delete", consequential=True)
COMMS_EXTERNAL = ActionClass("comms.external", consequential=True)
# The one low-risk class in the starter set, so "fail-open only where
# declared" is a real, exercised path rather than a theoretical one.
INFO_QUERY = ActionClass("info.query", consequential=False, fail_open_allowed=True)

TAXONOMY: dict[str, ActionClass] = {
    c.name: c for c in (MONEY_TRANSFER, DATA_DELETE, COMMS_EXTERNAL, INFO_QUERY)
}

UNCLASSIFIED_DEFAULT = ActionClass("unclassified", consequential=True)


def classify(action_class: str | None) -> ActionClass:
    """Resolve a declared class name to its policy.

    Absent, or not present in the taxonomy, both resolve to the
    consequential/fail-closed default -- the classification default is not
    conditioned on the name being *recognized*, only on it being *declared
    and known*.
    """
    if action_class is None:
        return UNCLASSIFIED_DEFAULT
    return TAXONOMY.get(action_class, UNCLASSIFIED_DEFAULT)
