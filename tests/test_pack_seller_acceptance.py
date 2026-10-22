# SPDX-License-Identifier: Apache-2.0
"""seller pack acceptance: install in observe mode, run one sale with two
buyer threads through a pack-installed ``GuardEngine``, and check every
declared obligation fires both ways, on pack-attributed records.

Same discipline as ``test_pack_everyday_acceptance.py``: this script
regenerates the pack's checked-in ``fixtures/mini_ledger.jsonl`` and proves
it reproduces byte-for-byte. Run ``python -m tests.test_pack_seller_acceptance``
to rewrite the fixture after an intended change.

The scenario is synthetic and hand-built in the engine's action shape: one
synthetic seller, two synthetic buyers, each named only by an opaque
fingerprint reference. Steps run in order on one ledger, because the
required statement, the acceptance and the single commitment are read from
the history the earlier steps sealed.

Every assertion names the field on the named record.

A share to a buyer carries what capsulectl's share check gives the engine:
the classes of data it gives (``disclosed_classes``), the deal it is in
(``deal_id``) and the buyer's keyed fingerprints its check sealed
(``counterparty_ids`` under ``counterparty_fp_alg``). The rows from before
0.1.5 also keep their ``representation_class`` and their citation, so the
earlier packs decide them as they did. Under 0.1.5, s13 reads the sale's
acceptance from the history a live check is given; each share is decided
with that history (``release_history``): the acceptances sealed so far,
each written as ``history_ledger`` writes a history act, with the
counterparty its check sealed beside it (``history_act_payload``).
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from cll.ledger import LedgerStore

from capsule_engine.guards import Action, GuardDecision, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_engine.guards.checks import (
    AuthorizationRecord,
    CommercialBoundsOpening,
    TaskAuthorityRecord,
    authorization_record_digest,
    task_authority_record_digest,
)
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, record_pack_activation
from capsule_engine.report.live_history import history_act_payload

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "seller"
FIXTURE_PATH = PACK_DIR / "fixtures" / "mini_ledger.jsonl"

OPERATOR = "seller-fixture"
DEVELOPER = "seller-assistant@v1"
SIGNER_SECRET = b"seller-acceptance-fixture-fixed-key"


def _fingerprint(name: str) -> str:
    """An opaque counterparty reference: a digest of a synthetic name, never the name."""
    return "cp-fp:" + hashlib.sha256(f"seller-fixture/{name}".encode()).hexdigest()


BUYER_A = _fingerprint("buyer-a")
BUYER_B = _fingerprint("buyer-b")
# A third buyer who was never told the item's condition.
BUYER_C = _fingerprint("buyer-c")
ITEM = "item-ref:" + hashlib.sha256(b"seller-fixture/item-1").hexdigest()
# A second item the same user sells, accepted by buyer B: its acceptance is
# a sealed acceptance, but of another sale by another buyer.
ITEM_2 = "item-ref:" + hashlib.sha256(b"seller-fixture/item-2").hexdigest()
# A third item, which buyer A is offered on the payment rails a commit may
# state: no acceptance of it is sealed, so each commit is decided on its rail.
# The offers on the proposal they answer (capsule-engine#193) use it too. It
# is listed too: buyer D is told nothing in chat, so only the listing can
# have stated its condition (0.1.5).
ITEM_3 = "item-ref:" + hashlib.sha256(b"seller-fixture/item-3").hexdigest()
BUYER_D = _fingerprint("buyer-d")

# Each buyer's thread, as a check in it states it: its deal, and the buyer's
# keyed fingerprints by kind (a marketplace profile id, the name and the
# payee capsulectl seals beside it) under the thread's key.
FP_ALG = "hmac-sha256-chain-key"


def _ids(buyer: str, *, name: str | None = None) -> dict[str, str]:
    def fp(kind: str, value: str) -> str:
        return hashlib.sha256(f"seller-fixture/{kind}/{value}".encode()).hexdigest()

    shown = name or f"name of {buyer}"
    return {"profile_id": fp("profile_id", buyer), "name": fp("name", shown), "payee": fp("payee", shown)}


def _thread(buyer: str) -> dict:
    return {"deal_id": f"deal-{buyer}", "counterparty_ids": _ids(buyer), "counterparty_fp_alg": FP_ALG}


THREAD_A, THREAD_B, THREAD_C = _thread("buyer-a"), _thread("buyer-b"), _thread("buyer-c")
THREADS = {BUYER_A: THREAD_A, BUYER_B: THREAD_B, BUYER_C: THREAD_C}
# The class of data each pre-0.1.5 row's statement class stands for.
DATA_CLASS = {"address": "address", "pickup": "pickup_location", "other": "other_contact"}

# The user's private floor, 1,700.00: the record seals only its salted
# commitment, and the opening is supplied beside each decision. This is
# capsulectl's first commercial-bounds golden vector, the same one
# tests/test_seller_wickets.py uses.
OPENING: CommercialBoundsOpening = {
    "document": {"type": "commercial-bounds/v0", "min_total_minor": 170_000},
    "nonce": "43a3c8a2914ecd228f722ed9f47dc4b4005cc1e257bc64777c0635a41ba7ea67",
    "bounds_commitment": "13563d6685d99e1bd4b5508a85a6ca679331cb3ff60e8516177ccafcfd5b4998",
}
# One sale, one task authority, cited by every thread.
TASK: TaskAuthorityRecord = {
    "type": "task-authority/v0",
    "body": {"outcome_id": "seller.sell_one_item/1.0.0", "allowed_actions": ["offer", "accept", "sell"],
             "preconditions": [], "bounds_commitment": OPENING["bounds_commitment"]},
}
TASK_REF = task_authority_record_digest(TASK)
# The user's word for one warranty statement, sealed and cited by digest.
WARRANTY_APPROVAL: AuthorizationRecord = {"type": "approval/v0", "body": {"representation_class": "warranty"}}
WARRANTY_APPROVAL_REF = authorization_record_digest(WARRANTY_APPROVAL)
# A one-shot approval of sharing the address: it does not release it.
ADDRESS_APPROVAL: AuthorizationRecord = {"type": "approval/v0", "body": {"representation_class": "address"}}
ADDRESS_APPROVAL_REF = authorization_record_digest(ADDRESS_APPROVAL)
APPROVALS = {"statement-warranty-approved": WARRANTY_APPROVAL,
             "address-to-a-before-acceptance-approved": ADDRESS_APPROVAL}

# Steps recorded as real decisions rather than dry runs: only a real
# accepted statement counts as made, and only a real accepted acceptance
# seals the sale.
REAL_RUN = frozenset({"condition-told-to-a", "condition-told-to-b", "acceptance-in-a", "acceptance-in-b-of-item-2"})
# The step whose sealed acceptance later steps cite.
ACCEPTANCE = "acceptance-in-a"
# Buyer B's acceptance, of the other item.
ACCEPTANCE_B = "acceptance-in-b-of-item-2"
# A capsule id no record in the ledger has.
UNKNOWN_MANDATE = "6" * 64


def _signer() -> LocalSigner:
    return LocalSigner(key_id="seller-fixture-key", secret=SIGNER_SECRET)


def _at(minute: int, second: int = 0) -> str:
    return f"2026-10-09T10:{minute:02d}:{second:02d}Z"


# Every other step gives dedupe its own equivalence_key, so no two steps are
# the same act. This pair leaves the key to dedupe's formula, under an agent
# no other step uses, so the second is the first act repeated.
REPEATED = {"equivalence_key": None, "developer": "seller-assistant-repeat@v1"}


def _statement(name: str, minute: int, target: str, cls: str, **fields) -> Action:
    fields.setdefault("equivalence_key", f"tell/{name}")
    fields.setdefault("developer", DEVELOPER)
    return Action(verb="tell", operator=OPERATOR, action_class="communication.send",
                  target=target, representation_class=cls, action_id=f"tell/seller-fixture-{name}",
                  timestamp=_at(minute), **fields)


def _commitment(verb: str, action_class: str, name: str, minute: int, target: str, amount_minor: int,
                **fields) -> Action:
    fields.setdefault("proposal_at", _at(minute - 1))
    fields.setdefault("item_ref", ITEM)
    for name_, value in THREADS.get(target, {}).items():
        fields.setdefault(name_, value)
    return Action(verb=verb, operator=OPERATOR, developer=DEVELOPER, action_class=action_class, target=target,
                  amount_minor=amount_minor, currency="USD", task_authority_ref=TASK_REF,
                  equivalence_key=f"{verb}/{name}", action_id=f"{verb}/seller-fixture-{name}",
                  timestamp=fields.pop("timestamp", _at(minute)), **fields)


def _offer(name: str, minute: int, target: str, amount_minor: int, **fields) -> Action:
    return _commitment("offer", "marketplace.offer", name, minute, target, amount_minor, **fields)


def _address(name: str, minute: int, target: str, role: str, **fields) -> Action:
    """The user's personal data shared in the sale of ``ITEM``: the address
    unless ``representation_class`` names another class. The disclosure
    carries only its class, never the data."""
    fields.setdefault("item_ref", ITEM)
    fields.setdefault("action_class", "disclosure.personal")
    fields.setdefault("representation_class", "address")
    if role == "buyer":
        # What capsulectl gives for the same share (0.1.5 reads it).
        fields.setdefault("disclosed_classes", (DATA_CLASS[fields["representation_class"]],))
        for name_, value in THREADS.get(target, {}).items():
            fields.setdefault(name_, value)
    verb = "share_address" if fields["representation_class"] == "address" else "share_personal"
    return Action(verb=verb, operator=OPERATOR, developer=DEVELOPER,
                  target=target, recipient_role=role, task_authority_ref=TASK_REF,
                  equivalence_key=f"{verb}/{name}", action_id=f"{verb}/seller-fixture-{name}",
                  timestamp=_at(minute), **fields)


def _share(name: str, minute: int, classes: tuple[str, ...], *, second: int = 0, target: str = BUYER_A,
           thread: dict | None = None, **fields) -> Action:
    """A share to a buyer as capsulectl's share check states it: the classes
    of data, the item, the deal and the buyer's fingerprints, no statement
    class and no citation. ``thread`` replaces the target's own thread."""
    for name_, value in (THREADS[target] if thread is None else thread).items():
        fields.setdefault(name_, value)
    return Action(verb="share_contact", operator=OPERATOR, developer=DEVELOPER, action_class="disclosure.personal",
                  target=target, recipient_role="buyer", disclosed_classes=tuple(sorted(classes)), item_ref=ITEM,
                  task_authority_ref=TASK_REF, equivalence_key=f"share_contact/{name}",
                  action_id=f"share_contact/seller-fixture-{name}", timestamp=_at(minute, second), **fields)


# The contact classes s13 releases to the buyer who accepted (the public
# pickup area aside).
CONTACT_CLASSES = ("pickup_location", "phone", "email", "name", "home_address", "address")


# A step that cites an acceptance names it by a marker; the runner replaces
# it with that acceptance's capsule_id once it is sealed.
CITES_ACCEPTANCE = "<acceptance>"
CITES_ACCEPTANCE_B = "<acceptance-b>"
CITED = {CITES_ACCEPTANCE: ACCEPTANCE, CITES_ACCEPTANCE_B: ACCEPTANCE_B}


def _scenarios() -> list[tuple[str, Action, str]]:
    return [
        # -- before any acceptance ------------------------------------------
        # The listing's required statement, made in each thread (real runs).
        ("condition-told-to-a", _statement("condition-a", 1, BUYER_A, "condition"), ALLOW),
        ("condition-told-to-b", _statement("condition-b", 2, BUYER_B, "condition"), ALLOW),
        # Thread A: a counter above the floor, inside the task, on a fresh proposal.
        ("counter-above-floor-in-a", _offer("counter-a", 3, BUYER_A, 180_000), ALLOW),
        # Thread B: below the floor. price_floor is the sole failure and
        # declares ASK, and marketplace.offer names an approver, so it asks.
        ("offer-below-floor-in-b", _offer("below-floor-b", 4, BUYER_B, 168_000), ESCALATE),
        # A buyer never told the item's condition: the offer cannot proceed
        # as constructed, and an approval would not clear it.
        ("offer-before-required-statement", _offer("no-condition-c", 5, BUYER_C, 180_000), ESCALATE),
        # Acting on a proposal two days old.
        ("offer-on-a-stale-proposal", _offer("stale-a", 6, BUYER_A, 180_000, proposal_at="2026-10-07T10:06:00Z"),
         ESCALATE),
        # A verb the task authority's plan does not allow.
        ("offer-outside-the-task", _commitment("offer_bundle", "marketplace.offer", "bundle-a", 7, BUYER_A, 190_000),
         ESCALATE),
        ("statement-warranty-unapproved", _statement("warranty-unapproved", 8, BUYER_A, "warranty"), ESCALATE),
        ("statement-warranty-approved", _statement("warranty-approved", 9, BUYER_A, "warranty",
                                                   authorized_by=WARRANTY_APPROVAL_REF), ALLOW),
        # A class never made: refused whatever it cites.
        ("statement-authenticity", _statement("authenticity", 10, BUYER_A, "authenticity",
                                              authorized_by=WARRANTY_APPROVAL_REF), DENY),
        # The same act twice: one agent, one verb, one recipient, keyed by
        # dedupe's own formula (no equivalence_key given).
        ("pickup-told-to-a", _statement("pickup-a", 11, BUYER_A, "pickup", **REPEATED), ALLOW),
        ("pickup-told-to-a-again", _statement("pickup-a-again", 12, BUYER_A, "pickup", **REPEATED), DENY),
        # The address to buyer A before the sale is accepted: no acceptance
        # to cite, so release_on_acceptance refuses.
        ("address-to-a-before-acceptance", _address("a-before", 13, BUYER_A, "buyer"), DENY),
        # The same, with the user's one-shot approval of sharing the address:
        # an approval does not release it.
        ("address-to-a-before-acceptance-approved", _address("a-before-approved", 14, BUYER_A, "buyer",
                                                             authorized_by=ADDRESS_APPROVAL_REF), DENY),
        # A disclosure citing a mandate that is not in the ledger.
        # Personal data of any other class is held back the same way: a
        # pickup detail and an unclassed detail before acceptance.
        ("pickup-detail-to-a-before-acceptance", _address("pickup-a-before", 15, BUYER_A, "buyer",
                                                          representation_class="pickup"), DENY),
        ("other-detail-to-a-before-acceptance", _address("other-a-before", 16, BUYER_A, "buyer",
                                                         representation_class="other"), DENY),
        ("address-citing-an-unknown-mandate", _address("a-unknown-mandate", 17, BUYER_A, "buyer",
                                                       cited_mandate_capsule_id=UNKNOWN_MANDATE), DENY),
        # A non-consequential class: nothing here measures it.
        ("listing-views-read", Action(verb="read_listing_views", operator=OPERATOR, developer=DEVELOPER,
                                      action_class="info.query", target="listing/own",
                                      equivalence_key="read_listing_views/1",
                                      action_id="read_listing_views/seller-fixture-1", timestamp=_at(18)), ALLOW),
        # Shares as capsulectl states them (0.1.5). The public pickup area
        # goes to a buyer before acceptance; every contact class waits for
        # it, and so does the area beside a phone.
        ("share-pickup-area-to-a-before-acceptance", _share("pickup-area-a-before", 18, ("pickup_area",), second=1), ALLOW),
        *[(f"share-{cls.replace('_', '-')}-to-a-before-acceptance",
           _share(f"{cls}-a-before", 18, (cls,), second=2 + n), DENY) for n, cls in enumerate(CONTACT_CLASSES)],
        ("share-pickup-area-and-phone-to-a-before-acceptance",
         _share("pickup-area-and-phone-a-before", 18, ("pickup_area", "phone"), second=9), DENY),
        # -- buyer A accepts --------------------------------------------------
        # The commit states its rail, which 0.1.5 checks there (s07).
        ("acceptance-in-a", _commitment("accept", "agreement.accept", "accept-a", 19, BUYER_A, 175_000,
                                        rail="card"), ALLOW),
        # -- after the acceptance ---------------------------------------------
        # Any commitment for the same sale in thread B is refused.
        ("second-commitment-in-b", _offer("after-accept-b", 20, BUYER_B, 180_000), DENY),
        # Buyer B accepts the other item: a sealed acceptance of another sale.
        ("acceptance-in-b-of-item-2", _commitment("accept", "agreement.accept", "accept-b-item-2", 21, BUYER_B,
                                                  175_000, item_ref=ITEM_2, rail="card"), ALLOW),
        # The address to buyer A, citing A's acceptance: released.
        # recipient_role passes for the buyer and the acceptance re-verifies.
        ("address-to-a-after-acceptance", _address("a-after", 22, BUYER_A, "buyer",
                                                   cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        # The same for the other classes: released to A once A accepted.
        ("pickup-detail-to-a-after-acceptance", _address("pickup-a-after", 23, BUYER_A, "buyer",
                                                         representation_class="pickup",
                                                         cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        ("other-detail-to-a-after-acceptance", _address("other-a-after", 24, BUYER_A, "buyer",
                                                        representation_class="other",
                                                        cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        # The address to buyer C, a buyer too, citing A's acceptance: C did
        # not accept, so it is refused though recipient_role passes.
        ("address-to-c-after-acceptance", _address("c-after", 25, BUYER_C, "buyer",
                                                   cited_mandate_capsule_id=CITES_ACCEPTANCE), DENY),
        # The address in A's thread citing B's acceptance (of the other item):
        # refused on the citation before 0.1.5; 0.1.5 reads no citation, and
        # the share is in A's own accepted deal, so it is released.
        ("address-to-a-citing-b-acceptance", _address("a-cites-b", 26, BUYER_A, "buyer",
                                                      cited_mandate_capsule_id=CITES_ACCEPTANCE_B), ALLOW),
        # The address to buyer C with its action class left out: nothing can
        # show it is out of scope, so it is refused rather than passed as n/a.
        ("address-with-no-action-class", _address("c-no-class", 27, BUYER_C, "buyer", action_class=None,
                                                  cited_mandate_capsule_id=CITES_ACCEPTANCE), DENY),
        # The same address to someone who is not the buyer.
        ("address-to-a-third-party", _address("third-party", 28, _fingerprint("someone-else"), "third_party",
                                              cited_mandate_capsule_id=CITES_ACCEPTANCE), DENY),
        # The sale to buyer A, following its own acceptance, on an allowed rail.
        ("sale-to-a-on-an-allowed-rail", _commitment("sell", "marketplace.sale", "sale-a", 29, BUYER_A, 175_000,
                                                     rail="card", cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        ("sale-to-a-on-a-rail-not-allowed", _commitment("sell", "marketplace.sale", "sale-a-cheque", 30, BUYER_A,
                                                        175_000, rail="cheque",
                                                        cited_mandate_capsule_id=CITES_ACCEPTANCE), ESCALATE),
        # The user's data to the platform the item is listed on, citing no
        # acceptance (0.1.4). It reaches no buyer, so s13 does not apply, and
        # s01 passes only email: the rest refuse. Each names its own platform,
        # so no row repeats another for dedupe.
        ("email-to-the-platform", _address("platform-email", 31, _fingerprint("platform-a"), "platform",
                                           representation_class="other", disclosed_classes=("email",)), ALLOW),
        ("phone-to-the-platform", _address("platform-phone", 32, _fingerprint("platform-b"), "platform",
                                           representation_class="other", disclosed_classes=("phone",)), DENY),
        ("address-to-the-platform", _address("platform-address", 33, _fingerprint("platform-c"), "platform",
                                             disclosed_classes=("address",)), DENY),
        ("platform-disclosure-naming-no-class", _address("platform-no-class", 34, _fingerprint("platform-d"),
                                                         "platform", representation_class="other"), DENY),
        ("email-and-phone-to-the-platform", _address("platform-email-and-phone", 35, _fingerprint("platform-e"),
                                                     "platform", representation_class="other",
                                                     disclosed_classes=("email", "phone")), DENY),
        # The listing's public pickup area to the platform (0.1.5): s01 passes
        # it beside an email; the exact spot still refuses.
        ("pickup-area-to-the-platform", _address("platform-pickup-area", 36, _fingerprint("platform-f"), "platform",
                                                 representation_class="other", disclosed_classes=("pickup_area",)),
         ALLOW),
        ("pickup-location-to-the-platform", _address("platform-pickup-location", 37, _fingerprint("platform-g"),
                                                     "platform", representation_class="other",
                                                     disclosed_classes=("pickup_location",)), DENY),
        ("email-and-pickup-area-to-the-platform", _address("platform-email-and-pickup-area", 38,
                                                           _fingerprint("platform-h"), "platform",
                                                           representation_class="other",
                                                           disclosed_classes=("email", "pickup_area")), ALLOW),
        # -- shares as capsulectl states them, after A accepted (0.1.5) --------
        # Each contact class, and the area, to A in A's thread: released.
        *[(f"share-{cls.replace('_', '-')}-to-a-after-acceptance", _share(f"{cls}-a-after", 39 + n, (cls,)), ALLOW)
          for n, cls in enumerate((*CONTACT_CLASSES, "pickup_area"))],
        # To buyer B, in B's own thread: B did not accept; the area is public.
        ("share-phone-to-b-after-acceptance", _share("phone-b-after", 46, ("phone",), target=BUYER_B), DENY),
        ("share-pickup-area-to-b-after-acceptance", _share("pickup-area-b-after", 47, ("pickup_area",), target=BUYER_B),
         ALLOW),
        # In A's deal, but the check names another buyer (B's fingerprints).
        ("share-phone-in-a-to-another-buyer", _share("phone-a-other-buyer", 48, ("phone",),
                                               thread={**THREAD_A, "counterparty_ids": _ids("buyer-b")}), DENY),
        # In A's deal, A's profile id under another spelling of A's name.
        ("share-phone-to-a-under-another-name", _share("phone-a-other-name", 49, ("phone",),
                                                 thread={**THREAD_A,
                                                         "counterparty_ids": _ids("buyer-a", name="Buyer A.")}),
         ALLOW),
        # A's buyer, but no deal; a deal stated twice; no fingerprints.
        ("share-phone-to-a-naming-no-deal", _share("phone-a-no-deal", 50, ("phone",), thread={**THREAD_A, "deal_id": None}),
         DENY),
        ("share-phone-to-a-naming-two-deals", _share("phone-a-two-deals", 51, ("phone",),
                                               thread={**THREAD_A, "deal_id": None},
                                               deal_id_conflict=("chain_id", "deal_id")), DENY),
        ("share-phone-to-a-with-no-fingerprint", _share("phone-a-no-fingerprint", 52, ("phone",),
                                                  thread={"deal_id": THREAD_A["deal_id"]}), DENY),
        # A class s13 never releases, to the buyer who accepted.
        ("share-credential-to-a-after-acceptance", _share("credential-a-after", 53, ("credential",)), DENY),
        # The payment rail at the seller's commit (0.1.5, s07 on agreement.accept):
        # cash passes only in person, the payment apps count as p2p, and a
        # commit that states no rail asks. Each commits buyer A to ITEM_3,
        # which no sealed acceptance holds.
        ("commit-cash-in-person", _rail_commit("cash-in-person", 54, 0, rail="cash", handover="in_person"), ALLOW),
        ("commit-cash-shipped", _rail_commit("cash-shipped", 54, 1, rail="cash", handover="shipped"), ESCALATE),
        ("commit-cash-with-no-handover", _rail_commit("cash-no-handover", 54, 2, rail="cash"), ESCALATE),
        ("commit-on-a-rail-not-allowed", _rail_commit("cheque", 54, 3, rail="cheque"), ESCALATE),
        ("commit-by-venmo", _rail_commit("venmo", 54, 4, rail="venmo"), ALLOW),
        ("commit-by-zelle", _rail_commit("zelle", 54, 5, rail="zelle"), ALLOW),
        ("commit-stating-no-rail", _rail_commit("no-rail", 54, 6), ESCALATE),
        # The same payment apps on the sale to buyer A, following its acceptance.
        ("sale-to-a-by-venmo", _commitment("sell", "marketplace.sale", "sale-a-venmo", 55, BUYER_A, 175_000,
                                           rail="venmo", cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        ("sale-to-a-by-zelle", _commitment("sell", "marketplace.sale", "sale-a-zelle", 56, BUYER_A, 175_000,
                                           rail="zelle", cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        # The proposal an offer answers (0.1.5): a first offer before any
        # buyer message carries the sealed no_proposal and no proposal time,
        # so s06 does not apply. With neither, the age is unknown and it asks;
        # no_proposal beside a proposal time contradicts itself and asks. (A
        # check naming both no_proposal and a source message never bridges
        # no_proposal: tests/test_offer_expiry_no_proposal.py.)
        ("first-offer-answering-no-proposal", _offer("no-proposal-a", 57, BUYER_A, 180_000, item_ref=ITEM_3,
                                                     proposal_at=None, no_proposal=True), ALLOW),
        ("offer-answering-an-unnamed-message", _offer("unnamed-message-a", 58, BUYER_A, 180_000, item_ref=ITEM_3,
                                                      proposal_at=None), ESCALATE),
        ("no-proposal-beside-a-proposal-time", _offer("no-proposal-and-time-a", 59, BUYER_A, 180_000,
                                                      item_ref=ITEM_3, no_proposal=True), ESCALATE),
        # The sale's listings in force, as each check's listing_classes seals
        # them (0.1.5, required_disclosure/1.2.0), each a second after the
        # last minute's step. Buyer D was told nothing in
        # chat: a listing stating the condition is the statement; one that
        # dropped it, or listings sharing no class, are none, so s05 asks.
        ("offer-after-a-listing-stating-the-condition", _offer("listing-d", 59, BUYER_D, 180_000, item_ref=ITEM_3,
                                                               listing_classes=("condition", "price"),
                                                               timestamp=_at(59, 1)), ALLOW),
        ("offer-after-a-listing-without-the-condition", _offer("listing-no-condition-d", 59, BUYER_D, 180_000,
                                                               item_ref=ITEM_3, listing_classes=("price",),
                                                               timestamp=_at(59, 2)),
         ESCALATE),
        ("offer-after-listings-sharing-no-class", _offer("listings-no-class-d", 59, BUYER_D, 180_000,
                                                         item_ref=ITEM_3, listing_classes=(),
                                                         timestamp=_at(59, 3)), ESCALATE),
    ]


def _rail_commit(name: str, minute: int, second: int, **fields) -> Action:
    """Buyer A's commit to ``ITEM_3`` on the rail and handover ``fields`` state."""
    return _commitment("accept", "agreement.accept", f"rail-{name}", minute, BUYER_A, 175_000, item_ref=ITEM_3,
                       timestamp=_at(minute, second), **fields)


# The scenarios that decide otherwise under an earlier pack. Before 0.1.4
# there is no platform role, so an email to the platform is refused; before
# 0.1.5 a platform may be given only email, so the pickup area is refused,
# and the payment rail is not checked at the commit (BEFORE_COMMIT_RAIL). A
# comparison with an earlier pack (test_pack_seller.py) sets
# EXPECTED_OVERRIDES to these.
# s13 before 0.1.5 reads the statement class and a citation, which a share
# as capsulectl states it carries neither of, so every such share to a buyer
# refuses; and the address in A's thread citing B's acceptance refuses on
# the citation, where 0.1.5 reads no citation and releases it in A's own
# accepted deal.
BEFORE_RELEASE_FROM_HISTORY = {
    "share-pickup-area-to-a-before-acceptance": DENY,
    **{f"share-{cls.replace('_', '-')}-to-a-after-acceptance": DENY for cls in (*CONTACT_CLASSES, "pickup_area")},
    "share-pickup-area-to-b-after-acceptance": DENY,
    "share-phone-to-a-under-another-name": DENY,
    "address-to-a-citing-b-acceptance": DENY,
}
# Before 0.1.5 s07 does not run at the commit and counts no payment app as
# p2p, so the commits it asks on are allowed and the sales by Venmo or Zelle ask.
BEFORE_COMMIT_RAIL = {"commit-cash-shipped": ALLOW, "commit-cash-with-no-handover": ALLOW,
                      "commit-on-a-rail-not-allowed": ALLOW, "commit-stating-no-rail": ALLOW,
                      "sale-to-a-by-venmo": ESCALATE, "sale-to-a-by-zelle": ESCALATE}
# Before 0.1.5, s06 (offer_expiry/1.0.1) does not read no_proposal: an offer
# with no proposal time asks, and one with a fresh time passes.
BEFORE_NO_PROPOSAL = {"first-offer-answering-no-proposal": ESCALATE, "no-proposal-beside-a-proposal-time": ALLOW}
# Before 0.1.5 a listing counts for nothing on s05, so an offer whose
# listing stated the condition still asks.
BEFORE_LISTING = {"offer-after-a-listing-stating-the-condition": ESCALATE}
BEFORE_PICKUP_AREA = {"pickup-area-to-the-platform": DENY, "email-and-pickup-area-to-the-platform": DENY,
                      **BEFORE_RELEASE_FROM_HISTORY, **BEFORE_COMMIT_RAIL, **BEFORE_NO_PROPOSAL,
                      **BEFORE_LISTING}
BEFORE_PLATFORM = {"email-to-the-platform": DENY, **BEFORE_PICKUP_AREA}
EXPECTED_OVERRIDES: dict[str, str] = {}


def _run_scenarios(ledger, *, project_dir, decisions: dict[str, GuardDecision] | None = None):
    """Run every step in order; ``decisions``, when given, collects each
    step's ``GuardDecision`` (its unsealed reasons and evidence)."""
    import dataclasses

    installed = install_pack(load_pack_dir(PACK_DIR), project_dir=project_dir, mode="observe")
    signer = _signer()
    engine = build_engine(installed, ledger=ledger, signer_provider=lambda: signer)
    activation = record_pack_activation(
        installed,
        ledger=ledger,
        operator=OPERATOR,
        developer="capsule-init-tool",
        signer=signer,
        timestamp="2026-10-09T10:00:00Z",
        action_id="policy_manifest_activated/seller-fixture-install",
    )
    capsules: dict[str, dict] = {}
    history: list[dict] = []
    for name, action, expected in _scenarios():
        expected = EXPECTED_OVERRIDES.get(name, expected)
        if action.cited_mandate_capsule_id in CITED:
            cited = capsules[CITED[action.cited_mandate_capsule_id]]["capsule_id"]
            action = dataclasses.replace(action, cited_mandate_capsule_id=cited)
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(tmp) as release_history:
            for record in history:
                release_history.append(dict(record), consequential=False)
            decision = engine.check(
                action,
                dry_run=name not in REAL_RUN,
                task_authority_record=TASK,
                authorization_record=APPROVALS.get(name),
                commercial_bounds_opening=OPENING,
                release_history=release_history,
            )
        if name in REAL_RUN and decision.outcome == ALLOW and action.action_class == "agreement.accept":
            history.append(_history_act(action, decision.capsule))
        if decision.outcome != expected:
            raise AssertionError(f"scenario {name!r}: expected {expected!r}, got {decision.outcome!r} ({decision.reason})")
        capsules[name] = decision.capsule
        if decisions is not None:
            decisions[name] = decision
    return installed, activation, capsules, list(ledger.scan())


def _history_act(action: Action, capsule: dict) -> dict:
    """The record ``history_ledger`` writes for a sealed acceptance when a
    live check's history gives it: the act as a decision seals it, its
    disposition, and the counterparty its check sealed beside it."""
    return {"capsule_id": capsule["capsule_id"], "operator": capsule["operator"], "developer": capsule["developer"],
            "action_type": capsule["action_type"], "action_id": capsule["action_id"],
            "timestamp": capsule["timestamp"], "disposition": {"decision": "accept"},
            "asg_payload": history_act_payload(action, sealed_disposition=True)}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("seller")
    store = LedgerStore(tmp / "ledger")
    decisions: dict[str, GuardDecision] = {}
    try:
        installed, activation, capsules, records = _run_scenarios(store, project_dir=tmp / "project",
                                                                  decisions=decisions)
        verified = {name: store.verify(c["capsule_id"]) for name, c in capsules.items()}
    finally:
        store.close()
    return installed, activation, capsules, records, verified, decisions


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _constraint(capsule: dict, constraint_id: str) -> dict:
    (record,) = [c for c in capsule["constraints"] if c["id"] == constraint_id]
    return record


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _failing(capsule: dict) -> list[str]:
    return [c["id"] for c in capsule["constraints"] if c["result"] == "fail"]


def test_every_declared_scenario_ran_at_its_declared_outcome(run):
    installed, _, capsules, records, verified, _ = run
    declared = {s.id: s.outcome for s in installed.pack.fixtures.scenarios}
    assert list(declared) == list(capsules)
    for name, result in verified.items():
        assert result.ok, f"{name}: {[f.detail for f in result.findings]}"
    assert len(records) == 1 + len(capsules)


def test_records_are_pack_attributed_and_observe_mode(run):
    installed, activation, capsules, _, _, _ = run
    for name, capsule in capsules.items():
        assert capsule["asg_payload"]["manifest_digest"] == installed.resolved.manifest_digest, name
        assert capsule["asg_payload"]["checkpoint"].get("dry_run") is (True if name not in REAL_RUN else None), name
    assert activation["asg_payload"]["detail"]["packs"] == [
        {"pack_id": "asg/seller/0.1.5", "digest": installed.pack.definition_digest(), "mode": "observe"}
    ]


# -- the acceptance criteria, each on its named record ---------------------------


# release_on_acceptance's own reasons (1.2.0): fixed sentences, never a value.
NOT_ACCEPTED = "the history given shows no acceptance of this sale by this buyer in this deal"
RELEASE_REASONS = {
    NOT_ACCEPTED,
    "the history given shows this buyer accepted this sale in this deal",
    "every class the disclosure names may go to a buyer before acceptance",
    "a credential, a verification code, a payment card or an ID document is never shared with anyone",
    "the share names no class of data",
    "the disclosure has no deal_id; it cannot be bound to an acceptance",
    "the disclosure has no counterparty; it cannot be bound to an acceptance",
    "the action class has no taxonomy row; it cannot be shown to be outside the rule",
}


def _release(run, name: str):
    (outcome,) = [c for c in run[5][name].constraints if c.id == "release_on_acceptance"]
    return outcome


def _refused_on_release_alone(run, name: str) -> None:
    capsule = run[2][name]
    assert _constraint(capsule, "recipient_role")["result"] == "pass"
    assert _failing(capsule) == ["release_on_acceptance"]
    assert capsule["disposition"]["decision"] == "reject"
    # The decision's reason names the rule that refused it.
    assert run[5][name].outcome == DENY
    assert "release_on_acceptance=fail" in run[5][name].reason


def test_the_address_before_acceptance_is_refused_by_the_release_rule(run):
    _refused_on_release_alone(run, "address-to-a-before-acceptance")
    assert _release(run, "address-to-a-before-acceptance").reason == NOT_ACCEPTED


def test_a_one_shot_approval_does_not_release_the_address(run):
    name = "address-to-a-before-acceptance-approved"
    assert run[2][name]["asg_payload"]["authorized_by"] == ADDRESS_APPROVAL_REF
    _refused_on_release_alone(run, name)


def test_the_address_after_acceptance_is_released_to_the_buyer_who_accepted(run):
    capsules = run[2]
    capsule = capsules["address-to-a-after-acceptance"]
    assert capsule["asg_payload"]["recipient_role"] == "buyer"
    assert _constraint(capsule, "recipient_role")["result"] == "pass"
    assert _constraint(capsule, "verify_before_dispatch")["result"] == "pass"
    assert _constraint(capsule, "release_on_acceptance")["result"] == "pass"
    # The cited acceptance is sealed as the record's chain parent.
    assert capsule["chain"]["parent_capsule_id"] == capsules[ACCEPTANCE]["capsule_id"]
    assert _failing(capsule) == []
    assert capsule["disposition"]["decision"] == "accept"


def test_the_address_to_another_buyer_citing_the_acceptance_is_refused(run):
    name = "address-to-c-after-acceptance"
    assert run[2][name]["asg_payload"]["recipient_role"] == "buyer"
    _refused_on_release_alone(run, name)


def test_the_address_in_the_accepting_deal_is_released_whatever_it_cites(run):
    """0.1.5 reads no citation: the address in A's own accepted deal, citing
    B's real acceptance of the other item, is released to A."""
    capsules = run[2]
    capsule = capsules["address-to-a-citing-b-acceptance"]
    assert capsules[ACCEPTANCE_B]["disposition"]["decision"] == "accept"
    assert _constraint(capsule, "verify_before_dispatch")["result"] == "pass"
    assert _constraint(capsule, "release_on_acceptance")["result"] == "pass"
    assert capsule["disposition"]["decision"] == "accept"


def test_the_release_rule_names_no_value(run):
    """Every release_on_acceptance record, pass or fail, holds none of the
    references, counterparties or acceptance ids, and its reason is one of
    the fixed sentences."""
    capsules = run[2]
    values = [BUYER_A, BUYER_B, BUYER_C, _fingerprint("someone-else"), ITEM, ITEM_2, TASK_REF,
              capsules[ACCEPTANCE]["capsule_id"], capsules[ACCEPTANCE_B]["capsule_id"],
              *(v for t in (THREAD_A, THREAD_B, THREAD_C) for v in (t["deal_id"], *t["counterparty_ids"].values()))]
    measured = 0
    for name in capsules:
        outcome = _release(run, name)
        if outcome.result == "n/a":
            continue
        measured += 1
        assert outcome.reason in RELEASE_REASONS, name
        assert set(outcome.evidence) == {"constraint_id", "disclosed_classes", "bound_to_acceptance",
                                         "missing_field"}, name
        serialised = json.dumps({"reason": outcome.reason, "evidence": outcome.evidence})
        for value in values:
            assert value not in serialised, (name, value)
    assert measured == 35


@pytest.mark.parametrize("cls", ["pickup", "other"])
def test_personal_data_of_another_class_is_held_until_the_buyer_accepts(run, cls):
    capsules = run[2]
    _refused_on_release_alone(run, f"{cls}-detail-to-a-before-acceptance")
    after = capsules[f"{cls}-detail-to-a-after-acceptance"]
    assert _failing(after) == []
    assert after["disposition"]["decision"] == "accept"
    assert _constraint(after, "release_on_acceptance")["result"] == "pass"


# -- shares as capsulectl states them (0.1.5) ------------------------------------


def test_the_public_pickup_area_goes_to_a_buyer_before_acceptance(run):
    outcome = _release(run, "share-pickup-area-to-a-before-acceptance")
    assert (outcome.result, outcome.evidence) == ("pass", {
        "constraint_id": "release_on_acceptance", "disclosed_classes": ["pickup_area"], "bound_to_acceptance": False,
        "missing_field": None})
    assert run[2]["share-pickup-area-to-a-before-acceptance"]["disposition"]["decision"] == "accept"


@pytest.mark.parametrize("cls", ["pickup_location", "phone", "email", "name", "home_address", "address"])
def test_each_contact_class_waits_for_the_acceptance_then_goes_to_the_buyer_who_accepted(run, cls):
    slug = cls.replace("_", "-")
    _refused_on_release_alone(run, f"share-{slug}-to-a-before-acceptance")
    assert _release(run, f"share-{slug}-to-a-before-acceptance").reason == NOT_ACCEPTED
    after = run[2][f"share-{slug}-to-a-after-acceptance"]
    assert _failing(after) == []
    assert after["disposition"]["decision"] == "accept"
    outcome = _release(run, f"share-{slug}-to-a-after-acceptance")
    assert (outcome.evidence["disclosed_classes"], outcome.evidence["bound_to_acceptance"]) == ([cls], True)


def test_the_area_beside_a_contact_class_waits_for_the_acceptance(run):
    _refused_on_release_alone(run, "share-pickup-area-and-phone-to-a-before-acceptance")
    assert _release(run, "share-pickup-area-and-phone-to-a-before-acceptance").evidence["disclosed_classes"] == [
        "phone", "pickup_area"]


@pytest.mark.parametrize("name", ["share-phone-to-b-after-acceptance", "share-phone-in-a-to-another-buyer"])
def test_a_buyer_who_did_not_accept_is_refused_with_the_same_reason(run, name):
    """Another thread's buyer, and another buyer named in the accepting
    deal: one reason, as for no acceptance, so neither learns A accepted."""
    _refused_on_release_alone(run, name)
    assert _release(run, name).reason == NOT_ACCEPTED


def test_the_public_pickup_area_goes_to_another_threads_buyer_too(run):
    assert _release(run, "share-pickup-area-to-b-after-acceptance").result == "pass"
    assert run[2]["share-pickup-area-to-b-after-acceptance"]["disposition"]["decision"] == "accept"


def test_the_buyer_is_matched_on_the_profile_id_not_the_name(run):
    assert _release(run, "share-phone-to-a-under-another-name").result == "pass"


@pytest.mark.parametrize(("name", "field"), [("share-phone-to-a-naming-no-deal", "deal_id"),
                                             ("share-phone-to-a-naming-two-deals", "deal_id"),
                                             ("share-phone-to-a-with-no-fingerprint", "counterparty")])
def test_a_share_missing_its_deal_or_its_buyer_is_refused(run, name, field):
    outcome = _release(run, name)
    assert (outcome.result, outcome.evidence["missing_field"]) == ("fail", field)
    assert run[2][name]["disposition"]["decision"] == "reject"


def test_a_credential_never_goes_to_the_buyer_who_accepted(run):
    _refused_on_release_alone(run, "share-credential-to-a-after-acceptance")
    assert _release(run, "share-credential-to-a-after-acceptance").reason == (
        "a credential, a verification code, a payment card or an ID document is never shared with anyone")


def test_an_address_with_no_action_class_is_refused(run):
    name = "address-with-no-action-class"
    assert _failing(run[2][name]) == ["release_on_acceptance"]
    assert run[2][name]["disposition"]["decision"] == "reject"
    assert _release(run, name).evidence["missing_field"] == "action_class"


def test_the_address_to_anyone_but_the_buyer_is_refused(run):
    capsule = run[2]["address-to-a-third-party"]
    assert _constraint(capsule, "recipient_role")["result"] == "fail"
    assert capsule["disposition"]["decision"] == "reject"


def test_an_email_to_the_platform_passes_without_an_acceptance(run):
    capsule = run[2]["email-to-the-platform"]
    assert _constraint(capsule, "recipient_role")["result"] == "pass"
    assert _constraint(capsule, "release_on_acceptance")["result"] == "n/a"
    assert capsule["disposition"]["decision"] == "accept"


@pytest.mark.parametrize("name", ["pickup-area-to-the-platform", "email-and-pickup-area-to-the-platform"])
def test_the_listing_pickup_area_to_the_platform_passes_without_an_acceptance(run, name):
    capsule = run[2][name]
    assert _constraint(capsule, "recipient_role")["result"] == "pass"
    assert _constraint(capsule, "release_on_acceptance")["result"] == "n/a"
    assert capsule["disposition"]["decision"] == "accept"


@pytest.mark.parametrize("name", ["phone-to-the-platform", "address-to-the-platform",
                                  "platform-disclosure-naming-no-class", "email-and-phone-to-the-platform",
                                  "pickup-location-to-the-platform"])
def test_anything_but_an_email_or_the_pickup_area_to_the_platform_is_refused(run, name):
    capsule = run[2][name]
    assert _failing(capsule) == ["recipient_role"]
    assert capsule["disposition"]["decision"] == "reject"


def test_a_first_offer_answering_no_proposal_is_not_applicable_to_s06(run):
    capsule = run[2]["first-offer-answering-no-proposal"]
    assert capsule["asg_payload"]["no_proposal"] is True
    assert "proposal_at" not in capsule["asg_payload"]
    assert _constraint(capsule, "offer_expiry")["result"] == "n/a"
    assert _failing(capsule) == []
    assert capsule["disposition"]["decision"] == "accept"


def test_no_proposal_beside_a_proposal_time_asks_on_s06_alone(run):
    name = "no-proposal-beside-a-proposal-time"
    capsule = run[2][name]
    assert _failing(capsule) == ["offer_expiry"]
    assert capsule["disposition"]["decision"] == "needs_input"
    (outcome,) = [c for c in run[5][name].constraints if c.id == "offer_expiry"]
    assert outcome.evidence == {"expiry_unverified": True, "no_proposal_beside": "proposal_at"}


def test_an_offer_naming_no_proposal_and_no_time_still_asks(run):
    """No proposal time and no no_proposal: the age is unknown, never read
    as not applicable."""
    name = "offer-answering-an-unnamed-message"
    assert _failing(run[2][name]) == ["offer_expiry"]
    (outcome,) = [c for c in run[5][name].constraints if c.id == "offer_expiry"]
    assert outcome.evidence == {"expiry_unverified": True, "missing_field": "proposal_at"}


def test_a_below_floor_offer_asks_on_price_floor_alone(run):
    capsule = run[2]["offer-below-floor-in-b"]
    assert _failing(capsule) == ["price_floor"]
    assert capsule["disposition"]["decision"] == "needs_input"
    assert capsule["disposition"]["verdict_class"] == "hitl_dispatched"


def test_the_floor_never_reaches_a_record(run):
    floor = str(OPENING["document"]["min_total_minor"])
    for name, capsule in run[2].items():
        assert floor not in json.dumps(capsule["asg_payload"]), name
    assert OPENING["nonce"] not in FIXTURE_PATH.read_text()


def test_a_statement_never_made_is_refused_even_with_an_approval(run):
    capsule = run[2]["statement-authenticity"]
    assert _failing(capsule) == ["promise_never"]
    assert capsule["disposition"]["decision"] == "reject"


def test_a_second_commitment_in_another_thread_is_refused(run):
    capsules = run[2]
    capsule = capsules["second-commitment-in-b"]
    assert _failing(capsule) == ["single_commitment"]
    assert capsule["disposition"]["decision"] == "reject"
    assert _constraint(capsules["counter-above-floor-in-a"], "single_commitment")["result"] == "pass"


def test_the_sale_following_its_own_acceptance_passes(run):
    capsule = run[2]["sale-to-a-on-an-allowed-rail"]
    assert _constraint(capsule, "single_commitment")["result"] == "pass"
    assert _constraint(capsule, "destination_rail")["result"] == "pass"
    assert _failing(capsule) == []


@pytest.mark.parametrize("name", ["commit-cash-in-person", "commit-by-venmo", "commit-by-zelle",
                                  "sale-to-a-by-venmo", "sale-to-a-by-zelle", "acceptance-in-a"])
def test_a_commit_or_sale_on_an_allowed_rail_passes_s07(run, name):
    capsule = run[2][name]
    assert _constraint(capsule, "destination_rail")["result"] == "pass"
    assert _failing(capsule) == []
    assert capsule["disposition"]["decision"] == "accept"


@pytest.mark.parametrize("name", ["commit-cash-shipped", "commit-cash-with-no-handover",
                                  "commit-on-a-rail-not-allowed", "commit-stating-no-rail"])
def test_a_commit_on_a_rail_the_user_does_not_allow_asks_on_s07_alone(run, name):
    capsule = run[2][name]
    assert _failing(capsule) == ["destination_rail"]
    assert capsule["disposition"]["decision"] == "needs_input"


def test_the_handover_is_sealed_and_a_commit_with_no_rail_names_the_gap(run):
    assert run[2]["commit-cash-in-person"]["asg_payload"]["handover"] == "in_person"
    assert run[2]["commit-cash-shipped"]["asg_payload"]["handover"] == "shipped"
    assert "handover" not in run[2]["commit-cash-with-no-handover"]["asg_payload"]
    (outcome,) = [c for c in run[5]["commit-stating-no-rail"].constraints if c.id == "destination_rail"]
    assert (outcome.reason, outcome.evidence["missing_field"]) == ("payment method not stated", "rail")


def test_an_offer_before_the_required_statement_asks_and_is_not_cleared(run):
    capsule = run[2]["offer-before-required-statement"]
    assert _failing(capsule) == ["required_disclosure"]
    assert capsule["disposition"]["decision"] == "needs_input"


def test_a_denial_in_thread_b_names_nothing_from_thread_a(run):
    capsules = run[2]
    record = _constraint(capsules["second-commitment-in-b"], "single_commitment")
    for value in (BUYER_A, ITEM, TASK_REF, capsules[ACCEPTANCE]["capsule_id"]):
        assert value not in json.dumps(record), value


def test_fixture_is_reproducible_byte_for_byte(run):
    _, _, _, records, _, _ = run
    regenerated = [json.dumps(r.capsule, separators=(",", ":")) for r in records]
    assert regenerated == FIXTURE_PATH.read_text().splitlines()


def _regenerate_fixture() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store = LedgerStore(tmp / "ledger")
        try:
            _, _, _, records = _run_scenarios(store, project_dir=tmp / "project")
        finally:
            store.close()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FIXTURE_PATH, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.capsule, separators=(",", ":")) + "\n")
    print(f"wrote {len(records)} record(s) to {FIXTURE_PATH}")


if __name__ == "__main__":
    _regenerate_fixture()
