# SPDX-License-Identifier: Apache-2.0
"""The launch reference checks (dev-persona doc: "policy that runs like CI"),
plus ``plan_containment``: forward-compiled-plan
containment, a pure function of ``(action, plan)`` with no ledger read.

``CONFIGURED_CHECKS`` are the checks a decision runs only when a wicket
configuring them is in force (``GuardEngine(wickets=...)``); each takes the
action, the ledger and that wicket's ``config``. ``TASK_AUTHORITY_CHECKS`` run the
same way but take the task-authority body supplied with the decision in place
of the ledger. ``RUNNABLE_CHECKS``
names both."""
from collections.abc import Callable

from capsule_ledger.ledger.api import LedgerAPI

from ..action import Action
from .action_class_gate import check_action_class_gate
from .base import CheckOutcome
from .caps import LimitSources, cap_for, check_caps, require_per_action_reads, resolve_caps_minor
from .channel_change import check_channel_change
from .counterparty_identity_change import check_counterparty_identity_change
from .counterparty_list import check_counterparty_list, require_disposition
from .counterparty_seen_before import (
    check_counterparty_seen_before,
    check_recipient_seen_before,
    seen_before_fold,
)
from .credential_pattern import check_credential_pattern
from .dedupe import check_dedupe
from .destination_rail import check_destination_rail
from .field_change_count import check_material_fields_changed, check_offer_fields_changed, fields_basis
from .plan_containment import check_plan_containment
from .recipient_role import check_recipient_role
from .recurring_charge import check_recurring_charge
from .refundability import check_refundability
from .task_authority import (
    PLAN_PATH,
    TaskAuthorityBody,
    TaskAuthorityRecord,
    check_task_authority,
    task_authority_record_digest,
)
from .upfront_amount import check_upfront_amount
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
    "counterparty_list": lambda action, ledger, config: check_counterparty_list(
        action, mode=config["mode"], entries=config["entries"], action_classes=config["action_classes"]
    ),
    "recipient_role": lambda action, ledger, config: check_recipient_role(
        action, roles=config["roles"], allowed_roles=config["allowed_roles"], action_classes=config["action_classes"]
    ),
    "refundability": lambda action, ledger, config: check_refundability(
        action, action_classes=config["action_classes"]
    ),
    "material_fields_changed": lambda action, ledger, config: check_material_fields_changed(
        action,
        counted_fields=config["counted_fields"],
        max_changed=config["max_changed"],
        action_classes=config["action_classes"],
    ),
    "offer_fields_changed": lambda action, ledger, config: check_offer_fields_changed(
        action,
        counted_fields=config["counted_fields"],
        max_changed=config["max_changed"],
        action_classes=config["action_classes"],
    ),
    "recipient_seen_before": lambda action, ledger, config: check_recipient_seen_before(
        action,
        ledger,
        definition=seen_before_fold(config["fold_id"], config["fold_digest"]),
        action_classes=config["action_classes"],
    ),
    "channel_change": lambda action, ledger, config: check_channel_change(
        action, seeded_channels=config["seeded_channels"], action_classes=config["action_classes"]
    ),
    "upfront_amount": lambda action, ledger, config: check_upfront_amount(
        action,
        upfront_max_minor=config["upfront_max_minor"],
        upfront_max_bps=config["upfront_max_bps"],
        action_classes=config["action_classes"],
    ),
}

TASK_AUTHORITY_CHECKS: dict[str, Callable[[Action, TaskAuthorityRecord | None, dict], CheckOutcome]] = {
    "task_authority": lambda action, record, config: check_task_authority(
        action, record, action_classes=config["action_classes"]
    ),
}

RUNNABLE_CHECKS = frozenset(CONFIGURED_CHECKS) | frozenset(TASK_AUTHORITY_CHECKS)

__all__ = [
    "CONFIGURED_CHECKS",
    "RUNNABLE_CHECKS",
    "TASK_AUTHORITY_CHECKS",
    "PLAN_PATH",
    "TaskAuthorityBody",
    "TaskAuthorityRecord",
    "CheckOutcome",
    "LimitSources",
    "cap_for",
    "check_action_class_gate",
    "check_caps",
    "check_channel_change",
    "check_counterparty_identity_change",
    "check_counterparty_list",
    "check_counterparty_seen_before",
    "check_credential_pattern",
    "check_dedupe",
    "check_destination_rail",
    "check_material_fields_changed",
    "check_offer_fields_changed",
    "check_plan_containment",
    "check_recipient_role",
    "check_recipient_seen_before",
    "check_recurring_charge",
    "check_refundability",
    "check_task_authority",
    "task_authority_record_digest",
    "check_upfront_amount",
    "check_verify_before_dispatch",
    "fields_basis",
    "require_disposition",
    "require_per_action_reads",
    "resolve_caps_minor",
    "seen_before_fold",
]
