# SPDX-License-Identifier: Apache-2.0
"""Offer/response recording (design §4b gap 2): the denominator primitive,
named response_class -- never consent. A response is required to exist for
every offer, even when nothing happened by some as-of time.

Also covers the option-set amendment (design/agent-human-engagement §3.1,
§5b): ``option_count``/``option_digests`` on the offer, ``selected_option_digest``
on the response, and the guard that refuses a choice claimed against a
one-option offer."""
from __future__ import annotations

import hashlib

import pytest

from capsule_engine.compiler.offer_response import (
    EVENT_OFFER,
    EVENT_RESPONSE,
    ChoiceClaimRequiresMultipleOptions,
    InvalidResponseClass,
    build_offer_capsule,
    build_response_capsule,
)
from capsule_engine.compiler.vocabulary import RESPONSE_CLASSES

OPERATOR = "test-operator"
DEVELOPER = "test-developer@v1"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


ONE_OPTION = [_digest("option-a")]
TWO_OPTIONS = [_digest("option-a"), _digest("option-b")]


def test_offer_capsule_round_trips(signer):
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer text"),
        option_digests=ONE_OPTION,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    assert offer["asg_payload"]["event"] == EVENT_OFFER
    assert offer["asg_payload"]["detail"]["offer_id"] == "offer-1"
    assert offer["action_type"] == "fyi"


def test_offer_capsule_rejects_a_non_digest_offer_digest(signer):
    with pytest.raises(ValueError, match="offer_digest"):
        build_offer_capsule(
            offer_id="offer-1",
            offer_digest="not-a-digest",
            option_digests=ONE_OPTION,
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
        )


def test_offer_capsule_records_option_count_and_digests(signer):
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer text"),
        option_digests=TWO_OPTIONS,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    detail = offer["asg_payload"]["detail"]
    assert detail["option_count"] == 2
    assert detail["option_digests"] == TWO_OPTIONS


def test_offer_capsule_rejects_an_empty_option_set(signer):
    with pytest.raises(ValueError, match="option_digests"):
        build_offer_capsule(
            offer_id="offer-1",
            offer_digest=_digest("offer text"),
            option_digests=[],
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
        )


def test_offer_capsule_rejects_a_non_digest_option(signer):
    with pytest.raises(ValueError, match="option digest"):
        build_offer_capsule(
            offer_id="offer-1",
            offer_digest=_digest("offer text"),
            option_digests=["not-a-digest"],
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
        )


@pytest.mark.parametrize("response_class", sorted(RESPONSE_CLASSES))
def test_response_capsule_accepts_every_registered_class(signer, response_class):
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer"),
        option_digests=ONE_OPTION,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    response_digest = None if response_class == "no_response" else _digest(response_class)
    response = build_response_capsule(
        offer_id="offer-1",
        offer_capsule_id=offer["capsule_id"],
        response_class=response_class,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
        response_digest=response_digest,
    )
    assert response["asg_payload"]["event"] == EVENT_RESPONSE
    assert response["asg_payload"]["detail"]["response_class"] == response_class
    assert response["chain"]["parent_capsule_id"] == offer["capsule_id"]


def test_response_capsule_rejects_an_unregistered_response_class(signer):
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer"),
        option_digests=ONE_OPTION,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    with pytest.raises(InvalidResponseClass):
        build_response_capsule(
            offer_id="offer-1",
            offer_capsule_id=offer["capsule_id"],
            response_class="consent",  # the exact word the design rules out
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
        )


def test_no_response_must_not_carry_a_response_digest(signer):
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer"),
        option_digests=ONE_OPTION,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    with pytest.raises(ValueError, match="no_response"):
        build_response_capsule(
            offer_id="offer-1",
            offer_capsule_id=offer["capsule_id"],
            response_class="no_response",
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
            response_digest=_digest("something"),
        )


# --- the option-count amendment's guard: a one-option offer cannot claim a
# choice was made (design/agent-human-engagement §3.1: "A real choice
# existed" requires option_count >= 2). RED-before-green: the rejection
# below is the mutant proof -- flip ONE_OPTION to TWO_OPTIONS in this same
# test and it must start passing, which the second test demonstrates. -----


def test_RED_a_one_option_offer_cannot_claim_a_selected_option(signer):
    """The rejection firing: an offer with exactly one option, and a
    response claiming a selection against it, must be refused -- there was
    nothing to choose between."""
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer"),
        option_digests=ONE_OPTION,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    with pytest.raises(ChoiceClaimRequiresMultipleOptions):
        build_response_capsule(
            offer_id="offer-1",
            offer_capsule_id=offer["capsule_id"],
            response_class="accepted",
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
            selected_option_digest=ONE_OPTION[0],
            offer_option_digests=ONE_OPTION,
        )


def test_GREEN_an_option_count_two_offer_can_claim_a_selected_option(signer):
    """The same claim, now against an offer with two real options, passes
    and records which one was selected."""
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer"),
        option_digests=TWO_OPTIONS,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    response = build_response_capsule(
        offer_id="offer-1",
        offer_capsule_id=offer["capsule_id"],
        response_class="accepted",
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
        selected_option_digest=TWO_OPTIONS[0],
        offer_option_digests=TWO_OPTIONS,
    )
    assert response["asg_payload"]["detail"]["selected_option_digest"] == TWO_OPTIONS[0]


def test_selected_option_must_be_one_of_the_offers_own_options(signer):
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer"),
        option_digests=TWO_OPTIONS,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    with pytest.raises(ValueError, match="selected_option_digest"):
        build_response_capsule(
            offer_id="offer-1",
            offer_capsule_id=offer["capsule_id"],
            response_class="accepted",
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
            selected_option_digest=_digest("option-never-offered"),
            offer_option_digests=TWO_OPTIONS,
        )


def test_selected_option_requires_offer_option_digests_to_verify_against(signer):
    offer = build_offer_capsule(
        offer_id="offer-1",
        offer_digest=_digest("offer"),
        option_digests=TWO_OPTIONS,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    with pytest.raises(ValueError, match="offer_option_digests"):
        build_response_capsule(
            offer_id="offer-1",
            offer_capsule_id=offer["capsule_id"],
            response_class="accepted",
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
            selected_option_digest=TWO_OPTIONS[0],
        )
