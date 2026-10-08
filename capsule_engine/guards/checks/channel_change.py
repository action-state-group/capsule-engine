# SPDX-License-Identifier: Apache-2.0
"""channel_change check: compare the channel a counterparty is using with
the one the relationship started on, both read off the same action.

``Action.channel`` is the channel KIND in use at this action and
``Action.first_contact_channel`` the kind the relationship started on
(``email``, ``sms``, ...), never an address. They are compared for equality.
A value outside the pinned ``seeded_channels`` (a namespaced one) is compared
the same way, and the evidence records whether each was seeded. No history is
read: the producer states both values on the one record. Applies only to the
configured ``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_channel_change"]

_CHECK_ID = "channel_change"
_METHOD = "same_record_v0"


class ChannelEvidence(TypedDict):
    channel: str
    first_contact_channel: str
    channel_seeded: bool
    first_contact_channel_seeded: bool


def _outcome(result: str, reason: str, evidence: ChannelEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _missing(name: str) -> CheckOutcome:
    return _outcome("n/a", f"the action carries no {name}; the channels could not be compared",
                    not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=name))


def check_channel_change(action: Action, *, seeded_channels: list[str], action_classes: list[str]) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    if action.channel is None:
        return _missing("channel")
    if action.first_contact_channel is None:
        return _missing("first_contact_channel")
    evidence = ChannelEvidence(
        channel=action.channel,
        first_contact_channel=action.first_contact_channel,
        channel_seeded=action.channel in seeded_channels,
        first_contact_channel_seeded=action.first_contact_channel in seeded_channels,
    )
    if action.channel != action.first_contact_channel:
        return _outcome("fail", f"the counterparty moved from {action.first_contact_channel!r} to {action.channel!r}",
                        evidence)
    return _outcome("pass", "the counterparty is on the channel the relationship started on", evidence)
