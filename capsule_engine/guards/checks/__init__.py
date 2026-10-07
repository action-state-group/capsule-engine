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
from .action_class_gate import check_action_class_gate
from .base import CheckOutcome
from .caps import cap_for, check_caps, resolve_caps_minor
from .counterparty_identity_change import check_counterparty_identity_change
from .counterparty_seen_before import check_counterparty_seen_before, seen_before_fold
from .credential_pattern import check_credential_pattern
from .dedupe import check_dedupe
from .destination_rail import check_destination_rail
from .plan_containment import check_plan_containment
from .recurring_charge import check_recurring_charge
from .verify_before_dispatch import check_verify_before_dispatch

CONFIGURED_CHECKS: dict[str, Callable[[Action, LedgerAPI, dict], CheckOutcome]] = {
    "destination_rail": lambda action, ledger, config: check_destination_rail(
        action, watched_rails=config["watched_rails"], action_classes=config["action_classes"]
    ),
    "counterparty_identity_change": lambda action, ledger, config: check_counterparty_identity_change(
        action, ledger, action_classes=config["action_classes"]
    ),
    "counterparty_seen_before": lambda action, ledger, config: check_counterparty_seen_before(
        action,
        ledger,
        definition=seen_before_fold(config["fold_id"], config["fold_digest"]),
        action_classes=config["action_classes"],
    ),
    "credential_pattern": lambda action, ledger, config: check_credential_pattern(
        action, patterns=config["patterns"]
    ),
    "recurring_charge": lambda action, ledger, config: check_recurring_charge(
        action, one_time_values=config["one_time_values"], action_classes=config["action_classes"]
    ),
    "action_class_gate": lambda action, ledger, config: check_action_class_gate(action, selectors=config["selectors"]),
}

__all__ = [
    "CONFIGURED_CHECKS",
    "CheckOutcome",
    "cap_for",
    "check_action_class_gate",
    "check_caps",
    "check_counterparty_identity_change",
    "check_counterparty_seen_before",
    "check_credential_pattern",
    "check_dedupe",
    "check_destination_rail",
    "check_plan_containment",
    "check_recurring_charge",
    "check_verify_before_dispatch",
    "resolve_caps_minor",
    "seen_before_fold",
]
