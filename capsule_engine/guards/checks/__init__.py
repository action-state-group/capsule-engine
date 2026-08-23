# SPDX-License-Identifier: Apache-2.0
"""The launch reference checks (dev-persona doc: "policy that runs like CI"),
plus ``plan_containment`` (``[ldg-plan-containment]``): forward-compiled-plan
containment, a pure function of ``(action, plan)`` with no ledger read."""
from .base import CheckOutcome
from .caps import check_caps
from .dedupe import check_dedupe
from .plan_containment import check_plan_containment
from .verify_before_dispatch import check_verify_before_dispatch

__all__ = [
    "CheckOutcome",
    "check_caps",
    "check_dedupe",
    "check_plan_containment",
    "check_verify_before_dispatch",
]
