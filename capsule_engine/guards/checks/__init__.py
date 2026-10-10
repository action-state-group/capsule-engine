# SPDX-License-Identifier: Apache-2.0
"""The launch reference checks (dev-persona doc: "policy that runs like CI"),
plus ``plan_containment``: forward-compiled-plan
containment, a pure function of ``(action, plan)`` with no ledger read.

``CONFIGURED_CHECKS`` are the checks a decision runs only when a wicket
configuring them is in force (``GuardEngine(wickets=...)``); each takes the
action, the ledger and that wicket's ``config``. ``TASK_AUTHORITY_CHECKS`` run the
same way but take the task-authority body supplied with the decision in place
of the ledger, ``AUTHORIZATION_CHECKS`` both the task-authority record and
the approval record supplied with it, and ``COMMERCIAL_BOUNDS_CHECKS`` the
task-authority record and the commercial-bounds opening supplied with it.
``RUNNABLE_CHECKS`` names all four."""
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
from .offer_expiry import check_offer_expiry
from .plan_containment import check_plan_containment
from .price_floor import COMMITMENT_PATH, CommercialBoundsOpening, bounds_commitment, check_price_floor
from .promise_never import check_promise_never
from .promise_requires_approval import (
    AUTHORIZED_PATH,
    CLASS_PATH,
    AuthorizationBody,
    AuthorizationRecord,
    authorization_record_digest,
    check_promise_requires_approval,
)
from .recipient_role import check_recipient_role
from .recurring_charge import check_recurring_charge
from .refundability import check_refundability
from .release_on_acceptance import check_release_on_acceptance
from .required_disclosure import check_required_disclosure
from .single_commitment import check_single_commitment
from .task_authority import (
    PLAN_PATH,
    TaskAuthorityBody,
    TaskAuthorityRecord,
    UnboundRecord,
    bind_task_authority_record,
    check_task_authority,
    task_authority_record_digest,
)
from .upfront_amount import check_upfront_amount
from .verify_before_dispatch import check_verify_before_dispatch

CONFIGURED_CHECKS: dict[str, Callable[[Action, LedgerAPI, dict], CheckOutcome]] = {
    "destination_rail": lambda action, ledger, config: check_destination_rail(
        action,
        watched_rails=config.get("watched_rails", []),
        allowed_rails=config.get("allowed_rails"),
        action_classes=config["action_classes"],
    ),
    "counterparty_identity_change": lambda action, ledger, config: check_counterparty_identity_change(
        action, ledger, action_classes=config["action_classes"]
    ),
    "counterparty_seen_before": lambda action, ledger, config: check_counterparty_seen_before(
        action,
        ledger,
        definition=seen_before_fold(config["fold_id"], config["fold_digest"]),
        action_classes=config["action_classes"],
        missing_target=config.get("missing_target", "n/a"),
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
    "required_disclosure": lambda action, ledger, config: check_required_disclosure(
        action,
        ledger,
        definition=seen_before_fold(config["fold_id"], config["fold_digest"]),
        representation_classes=config["representation_classes"],
        required_classes=config["required_classes"],
        action_classes=config["action_classes"],
        statement_definition=(
            seen_before_fold(config["statement_fold_id"], config["statement_fold_digest"])
            if "statement_fold_id" in config else None
        ),
    ),
    "offer_expiry": lambda action, ledger, config: check_offer_expiry(
        action, max_age_seconds=config["max_age_seconds"], action_classes=config["action_classes"]
    ),
    "promise_never": lambda action, ledger, config: check_promise_never(
        action,
        representation_classes=config["representation_classes"],
        never=config["never"],
        action_classes=config["action_classes"],
    ),
    "single_commitment": lambda action, ledger, config: check_single_commitment(
        action, ledger, acceptance_classes=config["acceptance_classes"], commit_classes=config["commit_classes"]
    ),
    "release_on_acceptance": lambda action, ledger, config: check_release_on_acceptance(
        action,
        ledger,
        release_classes=config["release_classes"],
        acceptance_classes=config["acceptance_classes"],
        action_classes=config["action_classes"],
    ),
}

TASK_AUTHORITY_CHECKS: dict[str, Callable[[Action, TaskAuthorityRecord | None, dict], CheckOutcome]] = {
    "task_authority": lambda action, record, config: check_task_authority(
        action, record, action_classes=config["action_classes"]
    ),
}

COMMERCIAL_BOUNDS_CHECKS: dict[
    str, Callable[[Action, TaskAuthorityRecord | None, CommercialBoundsOpening | None, dict], CheckOutcome]
] = {
    "price_floor": lambda action, record, opening, config: check_price_floor(
        action, record, opening, action_classes=config["action_classes"]
    ),
}

AUTHORIZATION_CHECKS: dict[
    str, Callable[[Action, TaskAuthorityRecord | None, AuthorizationRecord | None, dict], CheckOutcome]
] = {
    "promise_requires_approval": lambda action, task_authority, approval, config: check_promise_requires_approval(
        action,
        task_authority,
        approval,
        representation_classes=config["representation_classes"],
        requires_approval=config["requires_approval"],
        action_classes=config["action_classes"],
    ),
}

RUNNABLE_CHECKS = (
    frozenset(CONFIGURED_CHECKS)
    | frozenset(TASK_AUTHORITY_CHECKS)
    | frozenset(AUTHORIZATION_CHECKS)
    | frozenset(COMMERCIAL_BOUNDS_CHECKS)
)

__all__ = [
    "AUTHORIZATION_CHECKS",
    "AUTHORIZED_PATH",
    "CLASS_PATH",
    "COMMERCIAL_BOUNDS_CHECKS",
    "COMMITMENT_PATH",
    "AuthorizationBody",
    "AuthorizationRecord",
    "CONFIGURED_CHECKS",
    "RUNNABLE_CHECKS",
    "TASK_AUTHORITY_CHECKS",
    "PLAN_PATH",
    "TaskAuthorityBody",
    "TaskAuthorityRecord",
    "UnboundRecord",
    "CheckOutcome",
    "CommercialBoundsOpening",
    "LimitSources",
    "authorization_record_digest",
    "bounds_commitment",
    "bind_task_authority_record",
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
    "check_offer_expiry",
    "check_offer_fields_changed",
    "check_plan_containment",
    "check_price_floor",
    "check_promise_never",
    "check_promise_requires_approval",
    "check_recipient_role",
    "check_recipient_seen_before",
    "check_recurring_charge",
    "check_refundability",
    "check_release_on_acceptance",
    "check_required_disclosure",
    "check_single_commitment",
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
