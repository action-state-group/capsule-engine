# SPDX-License-Identifier: Apache-2.0
"""Offer/response recording -- the denominator primitive (design §4b gap 2).

*An offer was made; a response occurred; the response is recorded whether
or not it was the desired one.* One deployment's negative case is a
decline from a person offered a choice; another's is an operator who read
a recommendation and did something else instead. Same primitive underneath
both, which is exactly why it is named ``offer``/``response``/
``response_class`` here and not ``consent`` -- that word imports one
deployment's domain vocabulary into the shared format, precisely what the
admission rule (design §1: "nothing enters the format on one deployment's
evidence alone") forbids.

Two passive ``fyi`` records, same mechanism ``conversation/capsules.py``
uses: an offer capsule (what was proposed, and to whom) and a response
capsule chained to it (what happened). The response is REQUIRED to exist
for every offer, even when ``response_class="no_response"`` -- an offer
with no matching response capsule is a missing record, not a "no response"
one; recording "nothing happened by <as-of time>" is itself an act, because
without it the positive case (``accepted``) has no denominator.

**The option set (design/agent-human-engagement §3.1, §5b).** The offer
capsule alone used to carry only ``offer_digest`` -- a verifier could not
tell *"here are three options, pick one"* apart from *"click OK to
proceed."* This is a FORMAT change, not a pack field: it is justified on
both partners it was checked against (an engineer who acted on the one
thing an agent proposed and an engineer who chose one of three are not
making the same act), never on the single-partner "no denominator"
argument alone, which the admission rule rejects by itself. ``option_digests``
records one digest per option offered; ``option_count`` is that list's own
length, carried explicitly on the capsule so a verifier reads it off the
record rather than recomputing it. A response may name
``selected_option_digest`` -- which of the offer's own options was taken --
and the guard below refuses to let that claim stand against an offer that
only ever had one option: a choice claim is enforceable at act time, not
merely reportable.
"""
from __future__ import annotations

from collections.abc import Sequence

from agent_action_capsule.contracts import is_hex64

from capsule_engine.compiler.vocabulary import RESPONSE_CLASSES
from capsule_engine.guards.capsule import build_event_capsule
from capsule_engine.guards.signing import Signer

__all__ = [
    "EVENT_OFFER",
    "EVENT_RESPONSE",
    "ChoiceClaimRequiresMultipleOptions",
    "InvalidResponseClass",
    "build_offer_capsule",
    "build_response_capsule",
]

EVENT_OFFER = "compiler.offer"
EVENT_RESPONSE = "compiler.response"


class InvalidResponseClass(ValueError):
    """``response_class`` is not one of ``vocabulary.RESPONSE_CLASSES``."""


class ChoiceClaimRequiresMultipleOptions(ValueError):
    """``selected_option_digest`` was claimed against an offer whose
    ``option_digests`` has fewer than two entries -- there was nothing to
    choose between, so no choice can be claimed for it."""


def _require_response_class(response_class: str) -> None:
    if response_class not in RESPONSE_CLASSES:
        raise InvalidResponseClass(f"response_class must be one of {sorted(RESPONSE_CLASSES)}; got {response_class!r}")


def _require_offer_digest(offer_digest: str) -> None:
    if not is_hex64(offer_digest):
        raise ValueError(f"offer_digest must be a 64-hex SHA-256 digest; got {offer_digest!r}")


def _require_option_digests(option_digests: Sequence[str]) -> None:
    if not option_digests:
        raise ValueError("option_digests must name at least one option -- an offer of nothing is not an offer")
    for digest in option_digests:
        if not is_hex64(digest):
            raise ValueError(f"each option digest must be a 64-hex SHA-256 digest; got {digest!r}")


def _require_selected_option(selected_option_digest: str, offer_option_digests: Sequence[str] | None) -> None:
    if offer_option_digests is None:
        raise ValueError(
            "selected_option_digest requires offer_option_digests -- the guard verifies the choice claim "
            "against the offer's own options, it does not take the claim on trust"
        )
    if len(offer_option_digests) < 2:
        raise ChoiceClaimRequiresMultipleOptions(
            f"an offer with option_count={len(offer_option_digests)} cannot claim a choice was made"
        )
    if selected_option_digest not in offer_option_digests:
        raise ValueError("selected_option_digest must be one of the offer's own option_digests")


def build_offer_capsule(
    *,
    offer_id: str,
    offer_digest: str,
    option_digests: Sequence[str],
    operator: str,
    developer: str,
    signer: Signer,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    """Seal the offer itself: ``offer_digest`` commits to the offer's own
    content (the recommendation text, the proposed change) without carrying
    that content on-capsule -- same disclose-by-digest discipline as every
    other payload this codebase digests rather than embeds. ``option_digests``
    commits to each option offered the same way; ``option_count`` is carried
    on the capsule as that list's length, not a caller-supplied number that
    could disagree with it."""
    _require_offer_digest(offer_digest)
    _require_option_digests(option_digests)
    detail = {
        "offer_id": offer_id,
        "offer_digest": offer_digest,
        "option_count": len(option_digests),
        "option_digests": list(option_digests),
    }
    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_OFFER,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id or f"compiler.offer/{offer_id}",
    )


def build_response_capsule(
    *,
    offer_id: str,
    offer_capsule_id: str,
    response_class: str,
    operator: str,
    developer: str,
    signer: Signer,
    response_digest: str | None = None,
    selected_option_digest: str | None = None,
    offer_option_digests: Sequence[str] | None = None,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    """Seal the response to ``offer_capsule_id``. ``response_digest`` is
    optional and only meaningful for ``accepted``/``declined``/``deferred``
    (a digest of what was said or done); ``no_response`` never carries one
    -- there is nothing to digest, only an as-of claim that none arrived.

    ``selected_option_digest`` claims which of the offer's own options was
    taken. That claim is verified, not trusted: it must be passed alongside
    ``offer_option_digests`` (the offer capsule's own ``option_digests``),
    must appear in that list, and the list must hold at least two options --
    ``ChoiceClaimRequiresMultipleOptions`` refuses a choice claimed against
    a one-option offer."""
    _require_response_class(response_class)
    if response_class == "no_response" and response_digest is not None:
        raise ValueError("no_response must not carry a response_digest -- there is nothing recorded to digest")
    if response_digest is not None and not is_hex64(response_digest):
        raise ValueError(f"response_digest must be a 64-hex SHA-256 digest; got {response_digest!r}")
    if selected_option_digest is not None:
        _require_selected_option(selected_option_digest, offer_option_digests)

    detail: dict = {"offer_id": offer_id, "response_class": response_class}
    if response_digest is not None:
        detail["response_digest"] = response_digest
    if selected_option_digest is not None:
        detail["selected_option_digest"] = selected_option_digest
    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_RESPONSE,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id or f"compiler.response/{offer_id}",
        chain_parent=offer_capsule_id,
        chain_relation="follows",
    )
