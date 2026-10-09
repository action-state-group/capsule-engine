# SPDX-License-Identifier: Apache-2.0
"""``Action``: the guard API's own input contract for ``GuardEngine.check()``.

Not a Capsule. An Action is the thing a caller wants to do, *before* any
decision has been made about it. ``GuardEngine.check()`` evaluates one and
produces a decision, which is what becomes a Capsule (``guards/capsule.py``).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _new_action_id(verb: str) -> str:
    return f"{verb}/{uuid.uuid4()}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# All digits once spaces, hyphens and dots are removed, six or more of them
# (card, account and routing numbers), or IBAN-shaped.
_RAW_DIGITS = re.compile(r"^\d{6,}$")
_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$", re.IGNORECASE)


def _looks_like_raw_account_number(value: str) -> bool:
    compact = re.sub(r"[\s.\-]", "", value)
    return bool(_RAW_DIGITS.match(compact) or _IBAN.match(compact))


@dataclass(frozen=True)
class Action:
    """One candidate action, as presented to the guard for a decision.

    ``action_class`` is looked up in the taxonomy (``classes.py``);
    absent or unrecognized both resolve to the consequential/fail-closed
    default. ``amount_minor``/``currency`` are integer-minor-units money
    fields (never floats, per the fold engine's own determinism rule) read
    by the ``caps`` check; ``amount_minor`` is the expected capture.
    ``spend_authorized_minor`` is the most the payment may take when that was
    declared (a card hold, a pre-authorisation with a buffer), read by a caps
    per-action limit configured with ``per_action_reads``; it is never sealed
    on the capsule's payload, so the rolling total never sums it. ``target`` is an optional dedupe discriminator
    (e.g. a counterparty or recipient reference). ``cited_mandate_capsule_id``
    is the prior capsule this action claims authorization from, checked by
    ``verify_before_dispatch``. ``equivalence_key`` lets a caller override the
    dedupe check's default equivalence formula for this action.

    ``rail`` names the payment rail or destination type (e.g. ``"card"``,
    ``"p2p"``), read by ``destination_rail``. ``counterparty_account_ref`` is
    an OPAQUE reference to the account a counterparty is paid into, read by
    ``counterparty_identity_change`` and sealed on the capsule: a token or a
    digest that is stable per account, never the account, card or IBAN number
    itself. A value shaped like a raw number is refused at construction (see
    ``_looks_like_raw_account_number``); that check catches the common shapes,
    it cannot prove a value is opaque.
    ``outgoing_content`` is text the action sends out, read by
    ``credential_pattern``; it is never written to the capsule.
    ``counterparty_ids`` are the counterparty's keyed fingerprints by kind
    (e.g. ``{"payee": <hex>}``) as a producer sealed them, and
    ``counterparty_fp_alg`` the algorithm that made them, read by
    ``counterparty_list``; neither is written to the capsule, and the clear
    value they fingerprint never reaches the guard.
    ``recurrence`` says whether a payment repeats (e.g. ``"one_time"``,
    ``"monthly"``), read by ``recurring_charge``.
    The fields below are each one number, one member of a closed set, or one
    opaque reference, never an object, a diff or text, and each is sealed on
    the capsule when set. ``recipient_role`` is the role of whoever receives a
    disclosure, one member of the role set the wicket pins
    (``fulfilling_merchant``, ``third_party``, ``self``; a seller's set has
    ``buyer`` in place of ``fulfilling_merchant``), read by
    ``recipient_role``. ``refundable`` is whether the payment can be refunded,
    read by ``refundability``. ``material_fields_changed`` and
    ``offer_fields_changed`` count how many fields on a list pinned in the
    wicket config differ from what the user approved or stated, and
    ``material_fields_basis``/``offer_fields_basis`` are the digest of the
    list the producer counted over, read by the checks of the same names.
    ``channel`` is the kind of channel the counterparty is using now and
    ``first_contact_channel`` the one the relationship started on (``email``,
    ``sms``, ...), never an address, read by ``channel_change``.
    ``upfront_amount_minor`` is the stated deposit, in the same minor units
    as ``amount_minor``, read by ``upfront_amount``. ``task_authority_ref`` is
    the SHA-256 digest of the sealed task-authority record the action cites,
    read by ``task_authority``.
    ``representation_class`` is the one class of statement the action makes
    to a counterparty (``price``, ``condition``, ``warranty``, ...), read by
    ``promise_requires_approval`` and ``promise_never`` and counted by
    ``required_disclosure``; a message that
    makes several statements is several actions, one class each. The
    statement itself never reaches the guard. ``authorized_by`` is the
    SHA-256 digest of the whole sealed approval record the action cites,
    read by ``promise_requires_approval`` against the record supplied with
    the decision (``promise_never`` records it and ignores it).
    ``proposal_at`` is the RFC 3339 UTC timestamp of the proposal the action
    acts on, read by ``offer_expiry``.
    ``item_ref`` is an opaque reference to the item a sale is about, stable
    across every buyer thread of that sale, compared for equality and never
    opened, read by ``single_commitment``.
    ``taxonomy_version`` (normally ``classes.TAXONOMY_VERSION``) is sealed
    beside ``action_class`` when set, so a count over trigger classes can be
    recomputed against the table that was live; unset, the record keeps its
    prior bytes.
    ``returned_minor`` is the amount a record moving money in (a refund, a
    partial cancel) returns, in the same minor units as ``amount_minor``,
    which stays the spend (``0`` for money in); ``reverses_ref`` is the
    SHA-256 digest of the record of the act it reverses. Both are set only
    when money moves in, sealed when set, and read by ``dedupe``.
    ``deal_id`` is the deal the act is checked in, as the deal's own sealed
    record states it (``x-deal-v0.deal_id``, see ``report/replay.py``),
    sealed when set and read by ``dedupe``: a repeat in the same deal is
    refused, one in another deal may ask an approver. It never enters the
    act key.
    ``states_act`` is ``False`` for a record that states no act of its own
    (a deal's baseline, verdict or approval; see ``report/replay.py``), read
    by ``dedupe``, which does not apply to it; it is never sealed.
    """

    verb: str
    operator: str
    developer: str
    action_class: str | None = None
    action_id: str | None = None
    action_type: str = "decide"
    timestamp: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    target: str | None = None
    cited_mandate_capsule_id: str | None = None
    equivalence_key: str | None = None
    model_id: str | None = None
    provider: str | None = None
    rail: str | None = None
    counterparty_account_ref: str | None = None
    outgoing_content: str | None = None
    recurrence: str | None = None
    extra: dict = field(default_factory=dict)
    taxonomy_version: str | None = None
    spend_authorized_minor: int | None = None
    counterparty_ids: dict[str, str] | None = None
    counterparty_fp_alg: str | None = None
    recipient_role: str | None = None
    refundable: bool | None = None
    material_fields_changed: int | None = None
    material_fields_basis: str | None = None
    offer_fields_changed: int | None = None
    offer_fields_basis: str | None = None
    channel: str | None = None
    first_contact_channel: str | None = None
    upfront_amount_minor: int | None = None
    task_authority_ref: str | None = None
    representation_class: str | None = None
    authorized_by: str | None = None
    proposal_at: str | None = None
    item_ref: str | None = None
    returned_minor: int | None = None
    reverses_ref: str | None = None
    deal_id: str | None = None
    states_act: bool = True

    def __post_init__(self) -> None:
        if self.counterparty_account_ref is not None and _looks_like_raw_account_number(
            self.counterparty_account_ref
        ):
            raise ValueError(
                "counterparty_account_ref looks like a raw account, card or IBAN number; it is sealed on the "
                "capsule, so pass an opaque reference (a token or a digest) instead"
            )

    def resolved_action_id(self) -> str:
        return self.action_id or _new_action_id(self.verb)

    def resolved_timestamp(self) -> str:
        return self.timestamp or _utc_now()

    @classmethod
    def from_capsule(
        cls,
        capsule: dict,
        *,
        action_class: str | None = None,
        cited_mandate_capsule_id: str | None = None,
    ) -> Action:
        """Build an ``Action`` from a capsule already sitting in a ledger.

        Used to replay a historical or foreign capsule back through the
        guard for a dry-run or end-to-end reproduction. A truly foreign
        capsule (one this guard did not itself produce) carries none of
        ``asg_payload``'s extension fields, so its ``action_class`` must be
        supplied by the caller -- there is nothing in the -02 schema to
        infer it from. But a capsule THIS guard (or a pack installed
        through it) originally produced carries its own ``action_class`` in
        ``asg_payload`` (``build_decision_capsule``), so replaying one of
        those back (the normal dry-run-report path) reads it from the
        capsule itself when the caller doesn't override it -- an explicit
        ``action_class`` argument always wins, for the genuinely-foreign
        case this was written for.
        """
        action_id = capsule.get("action_id", "")
        verb = action_id.split("/", 1)[0] if action_id else "unknown"
        payload = capsule.get("asg_payload") or {}
        return cls(
            verb=verb,
            operator=capsule.get("operator", ""),
            developer=capsule.get("developer", ""),
            action_class=action_class or payload.get("action_class"),
            action_id=action_id or None,
            action_type=capsule.get("action_type", "decide"),
            timestamp=capsule.get("timestamp"),
            amount_minor=payload.get("amount_minor"),
            currency=payload.get("currency"),
            target=payload.get("target"),
            cited_mandate_capsule_id=cited_mandate_capsule_id,
            rail=payload.get("rail"),
            counterparty_account_ref=payload.get("counterparty_account_ref"),
            recurrence=payload.get("recurrence"),
            taxonomy_version=payload.get("taxonomy_version"),
            returned_minor=payload.get("returned_minor"),
            reverses_ref=payload.get("reverses_ref"),
            deal_id=payload.get("deal_id"),
        )
