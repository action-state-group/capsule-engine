# SPDX-License-Identifier: Apache-2.0
"""The launch reference checks (dev-persona doc: "policy that runs like CI"),
plus ``plan_containment``: forward-compiled-plan
containment, a pure function of ``(action, plan)`` with no ledger read.

``CONFIGURED_CHECKS`` are the checks a decision runs only when a wicket
configuring them is in force (``GuardEngine(wickets=...)``); each takes the
action, the ledger and that wicket's ``config``."""
from collections.abc import Callable

from capsule_ledger.ledger.api import LedgerAPI

from ..action import Action
from .base import CheckOutcome
from .caps import check_caps
from .counterparty_identity_change import check_counterparty_identity_change
from .credential_pattern import check_credential_pattern
from .dedupe import check_dedupe
from .destination_rail import check_destination_rail
from .plan_containment import check_plan_containment
from .verify_before_dispatch import check_verify_before_dispatch

CONFIGURED_CHECKS: dict[str, Callable[[Action, LedgerAPI, dict], CheckOutcome]] = {
    "destination_rail": lambda action, ledger, config: check_destination_rail(
        action, watched_rails=config["watched_rails"], action_classes=config["action_classes"]
    ),
    "counterparty_identity_change": lambda action, ledger, config: check_counterparty_identity_change(
        action, ledger, action_classes=config["action_classes"]
    ),
    "credential_pattern": lambda action, ledger, config: check_credential_pattern(
        action, patterns=config["patterns"]
    ),
}

__all__ = [
    "CONFIGURED_CHECKS",
    "CheckOutcome",
    "check_caps",
    "check_counterparty_identity_change",
    "check_credential_pattern",
    "check_dedupe",
    "check_destination_rail",
    "check_plan_containment",
    "check_verify_before_dispatch",
]
