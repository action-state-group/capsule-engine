# SPDX-License-Identifier: Apache-2.0
"""The contract-named referee: recompute a deal's pinned terms, seal an Evidence Result.

A deal between a buyer and a seller is two halves, each sealed in its own
party's book (capsule-emit's ``seal_request`` / ``seal_action``), each
committing to ``action_digest`` = SHA-256 of the JCS of ``{"action", "terms"}``.
The terms name a third node, the referee, by key. Given both halves, the
openings of their commitments, each party's own deal records and the
step-06 rail proof (capsule-emit settlement legs), :func:`judge` recomputes
the terms and returns one Evidence Result v0 document (``report/result.py``),
and :func:`seal_result` seals it in the referee's own book.
:func:`verify_referee_result` is the receiving party's check.

**The deal terms** (the document each half commits to) carry, beside the
deal's own terms::

    {"item": <text>,
     "price": {"value": <decimal string>, "assetCode": <text>, "assetScale": <int>},
     "expires_at": <RFC 3339, UTC, seconds, Z>,
     "buyer":  {"operator": <text>, "book_key_id": <64 hex>, "pack": {"pack_id", "definition_digest"}},
     "seller": {"operator": <text>, "book_key_id": <64 hex>, "pack": {"pack_id", "definition_digest"}},
     "referee": {"key_id": <64 hex>, "mailbox": <text>}}

``price`` is the settlement draft's exact amount triple (capsule-emit
``settlement.amount``), so it compares with the rail proof without a currency
table. ``book_key_id`` is the key a party's deal records are signed with.

**What it judges.**

(a) The shared deal terms, one claim each, under ``ec:deal-terms@1``
(:data:`DEAL_TERMS_CONTRACT`), tier ``recomputed``:

- ``price-agreed``: both halves open to the same ``price``, the payer's
  observation sent exactly that amount, and the payee's observation agrees with
  it (the settlement draft's ``agreed`` state, under the halves' keys);
- ``item-agreed``: both halves open to the same ``item``;
- ``delivered-before-expiry``: both halves open to the same ``expires_at``,
  and the buyer observed the delivery at or before it;
- ``paid-before-release``: the payee observed the payment settled at or before
  the seller sent the delivery.

A disagreement is ``not_met`` and cites the records that disagree. An input
that is missing (no rail proof, no delivery leg) is ``INSUFFICIENT`` and
``not_evaluable``, never ``not_met``.

(b) Each party's own pack, over that party's own records only: the pack its
own half's terms name for its role, replayed through ``GuardEngine``
(``report/replay.py``). The buyer is never judged under the seller's pack, nor
the seller under the buyer's. One claim per rule per decision that rule
settled (pass ``met``, fail ``not_met``); a rule that did not apply is
counted in ``excluded_not_applicable``, never a claim. A rule the pack
declares ``declared_not_measured`` is one ``not_evaluable`` claim per party
(sufficiency ``GAP``) whose ``analysis`` names its ``evidence_instrument``.

**Deterministic.** ``generated_at`` is the given as-of instant; claims are
ordered by contract, requirement, party and record; nothing comes from a
clock, a random value or a model. The same inputs give the same document.

**Refusals** (:class:`RefereeError`): a half or a record that does not verify
under the key it carries; an opening that does not recompute its half's
``action_digest``; halves that do not answer each other; halves that name
different referees, or a referee that is not the signer; a party's records
signed by a key other than its ``book_key_id``; a pack the catalog does not
hold at the named digest.

The Result carries digests only: no opening, no terms value, no decision
evidence. It cites a decision's evidence by digest, as a sealed decision does.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

from agent_action_capsule import json_digest
from agent_action_capsule.canonical import jcs
from agent_action_capsule.contracts import ReferenceEntry
from capsule_emit import seal
from capsule_emit import settlement as _settlement
from capsule_emit.signing import AuthorshipVerdict, verify_capsule_signature_tristate
from capsule_emit.verification import verify_capsule

from .guards.checks import RUNNABLE_CHECKS
from .packs import install_pack, load_pack_dir, obligation_results
from .packs.schema import PackDefinition
from .report.replay import replay
from .report.result import (
    AnalysisCarrier,
    Claim,
    DigestRef,
    DisclosureCarrier,
    build_result,
    validate_against_schema,
    verify_result,
)

__all__ = [
    "DEAL_TERMS_CONTRACT",
    "DEAL_TERMS_REQUIREMENTS",
    "RESULT_BLOCK",
    "RESULT_SCHEMA",
    "Party",
    "RefereeError",
    "action_digest",
    "judge",
    "seal_result",
    "verify_referee_result",
]

DEAL_TERMS_CONTRACT = "ec:deal-terms@1"
PRICE, ITEM, DELIVERED, PAID = "price-agreed", "item-agreed", "delivered-before-expiry", "paid-before-release"
DEAL_TERMS_REQUIREMENTS = (PRICE, ITEM, DELIVERED, PAID)

#: The ``compute_attestation`` block a sealed Result carries, and its schema tag.
RESULT_BLOCK = "referee_result"
RESULT_SCHEMA = "capsule-engine/referee-result/v1"

ROLES = ("buyer", "seller")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CATALOG = Path(__file__).parent / "packs" / "catalog"


class RefereeError(ValueError):
    """The referee refuses to judge, or a receiver refuses a Result. ``code`` is stable."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class Party:
    """One party's inputs: its signed half, the opening of the half's commitment
    (``action``, ``terms``), and its own deal records as ``report/replay.py``
    reads them (``load_records``, ``load_disclosed``, ``load_withheld``)."""

    half: dict
    action: str
    terms: dict
    records: Sequence[dict] = ()
    disclosed: dict[str, dict] = field(default_factory=dict)
    withheld: frozenset[str] = frozenset()


def action_digest(action: str, terms: Any) -> str:
    """The commitment a half seals: SHA-256 of the JCS of ``{"action", "terms"}``."""
    return hashlib.sha256(jcs({"action": action, "terms": terms})).hexdigest()


def _compute(capsule: dict) -> dict:
    return (capsule.get("model_attestation") or {}).get("compute_attestation") or {}


def _authentic(capsule: dict) -> bool:
    """The capsule recomputes and its signature verifies under the key_id it carries."""
    return verify_capsule(capsule).ok and verify_capsule_signature_tristate(capsule)[0] is AuthorshipVerdict.AUTHORED


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not _INSTANT.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _hex(value: object, what: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise RefereeError("terms_malformed", f"{what} must be 64 lowercase hex")
    return value


def _check_half(role: str, party: Party) -> None:
    if not _authentic(party.half):
        raise RefereeError("half_unverified", f"the {role}'s half does not verify under the key it carries")
    if not isinstance(party.terms, dict) or not isinstance(party.terms.get(role), dict):
        raise RefereeError("terms_malformed", f"the {role}'s terms name no {role}")
    if _compute(party.half).get("action_digest") != action_digest(party.action, party.terms):
        raise RefereeError("opening_mismatch", f"the {role}'s opening does not recompute its half's action_digest")
    if party.half.get("operator") != party.terms[role].get("operator"):
        raise RefereeError("party_mismatch", f"the {role}'s half is not sealed by the operator its terms name")


def _check_pair(buyer: Party, seller: Party) -> None:
    """One half answers the other: the responder's ``request_capsule_id`` is the requester's half."""
    b, s = buyer.half, seller.half
    pairs = ((b, s), (s, b))
    if not any(_compute(req).get("role") == "requester" and _compute(resp).get("role") == "responder"
               and _compute(resp).get("request_capsule_id") == req.get("capsule_id") for req, resp in pairs):
        raise RefereeError("halves_unpaired", "neither half answers the other")
    if b.get("key_id") == s.get("key_id"):
        raise RefereeError("halves_unpaired", "both halves are signed by one key")


def _named_referee(buyer: Party, seller: Party) -> str:
    named = []
    for role, party in (("buyer", buyer), ("seller", seller)):
        ref = party.terms.get("referee")
        if not isinstance(ref, dict):
            raise RefereeError("referee_unnamed", f"the {role}'s terms name no referee")
        named.append((_hex(ref.get("key_id"), "referee.key_id"), ref.get("mailbox")))
    if named[0] != named[1]:
        raise RefereeError("referee_disagrees", "the two halves name different referees")
    return named[0][0]


def _check_records(role: str, party: Party) -> None:
    key = _hex(party.terms[role].get("book_key_id"), f"{role}.book_key_id")
    for rec in party.records:
        if rec.get("key_id") != key:
            raise RefereeError("records_foreign", f"a {role} record is not signed by the {role}'s book key")
        if not _authentic(rec):
            raise RefereeError("records_unverified", f"a {role} record does not verify")


@cache
def _catalog() -> dict[str, PackDefinition]:
    packs = {}
    for d in sorted(p for p in _CATALOG.iterdir() if (p / "pack.yaml").is_file()):
        pack = load_pack_dir(d)
        packs[pack.pack_id] = pack
    return packs


def _pack_for(role: str, party: Party) -> PackDefinition:
    ref = party.terms[role].get("pack")
    if not isinstance(ref, dict):
        raise RefereeError("pack_unnamed", f"the {role}'s terms name no pack")
    pack = _catalog().get(ref.get("pack_id"))
    if pack is None or pack.definition_digest() != ref.get("definition_digest"):
        raise RefereeError("pack_not_held", f"no pack {ref.get('pack_id')!r} at the named digest")
    return pack


# ---------------------------------------------------------------------------
# (a) The shared deal terms
# ---------------------------------------------------------------------------


def _refs(*capsules: dict) -> tuple[DigestRef, ...]:
    seen: list[str] = []
    for c in capsules:
        if c["capsule_id"] not in seen:
            seen.append(c["capsule_id"])
    return tuple(DigestRef(digest=cid) for cid in seen)


def _terms_claim(requirement: str, status: str, evidence: tuple[DigestRef, ...]) -> Claim:
    if status == "SATISFIED":
        sufficiency, verdict = "SATISFIED", "met"
    elif status == "CONTRADICTED":
        sufficiency, verdict = "SATISFIED", "not_met"
    else:
        sufficiency, verdict = "INSUFFICIENT", "not_evaluable"
    return Claim(
        id=f"deal-terms/{requirement}",
        contract_ref=DEAL_TERMS_CONTRACT,
        requirement_ref=requirement,
        tier="recomputed",
        grade="self-attested",
        sufficiency=sufficiency,
        verdict=verdict,
        evidence=evidence,
        proofs=(),
        presentation=DisclosureCarrier(status=status, evidence=evidence),
    )


@dataclass(frozen=True)
class _Rail:
    """The settlement the seller's terms leg opened for this deal, its legs by kind."""

    terms: dict | None = None
    legs: dict[str, dict] = field(default_factory=dict)
    agreed: bool = False


def _rail(legs: Sequence[dict], buyer: Party, seller: Party) -> _Rail:
    """The rail proof for this deal: the terms leg the seller sealed citing one of
    the deal's halves, and the newest leg of each kind answering it under the
    party's own key (payer: the buyer's half key, payee: the seller's)."""
    payer, payee = buyer.half["key_id"], seller.half["key_id"]
    halves = {buyer.half["capsule_id"], seller.half["capsule_id"]}
    authentic = [c for c in legs if isinstance(c, dict) and _settlement.SETTLEMENT_MEMBER in c and _authentic(c)]
    terms = [
        c for c in authentic
        if c[_settlement.SETTLEMENT_MEMBER].get("leg") == "terms" and c.get("key_id") == payee
        and any(r.get("digest") in halves for r in c.get("references") or [])
    ]
    if len(terms) != 1:
        return _Rail()
    t = terms[0]
    want = {"payer_observed": payer, "payee_observed": payee, "delivered": None}
    by_kind: dict[str, dict] = {}
    for c in authentic:
        s = c[_settlement.SETTLEMENT_MEMBER]
        kind = s.get("leg")
        if kind not in want or s.get("terms_ref") != t["capsule_id"]:
            continue
        if kind == "delivered":
            direction = (s.get("delivery") or {}).get("direction")
            if direction not in ("sent", "received"):
                continue
            kind = f"delivered:{direction}"
            key = payee if direction == "sent" else payer
        else:
            key = want[kind]
        if c.get("key_id") != key:
            continue
        prior = by_kind.get(kind)
        if prior is None or (str(s.get("observed_at")), c["capsule_id"]) > (
                str(prior[_settlement.SETTLEMENT_MEMBER].get("observed_at")), prior["capsule_id"]):
            by_kind[kind] = c
    chosen = [t, *by_kind.values()]
    report = _settlement.verify_settlements(chosen, key_policy={"payer": payer, "payee": payee})
    entry = next((e for e in report.settlements if e.get("terms") == t["capsule_id"]), {})
    return _Rail(terms=t, legs=by_kind, agreed=entry.get("payment_state") == "agreed")


def _leg(rail: _Rail, kind: str) -> dict | None:
    c = rail.legs.get(kind)
    return c[_settlement.SETTLEMENT_MEMBER] if c else None


def _price_claim(buyer: Party, seller: Party, rail: _Rail) -> Claim:
    halves = (buyer.half, seller.half)
    if buyer.terms.get("price") != seller.terms.get("price"):
        return _terms_claim(PRICE, "CONTRADICTED", _refs(*halves))
    payer, payee = _leg(rail, "payer_observed"), _leg(rail, "payee_observed")
    if rail.terms is None or payer is None or payee is None:
        return _terms_claim(PRICE, "INSUFFICIENT", _refs(*halves))
    cited = _refs(*halves, rail.terms, rail.legs["payer_observed"], rail.legs["payee_observed"])
    if payer.get("amount") != seller.terms.get("price") or not rail.agreed:
        return _terms_claim(PRICE, "CONTRADICTED", cited)
    return _terms_claim(PRICE, "SATISFIED", cited)


def _item_claim(buyer: Party, seller: Party) -> Claim:
    halves = _refs(buyer.half, seller.half)
    items = (buyer.terms.get("item"), seller.terms.get("item"))
    if not all(isinstance(i, str) and i for i in items):
        return _terms_claim(ITEM, "INSUFFICIENT", halves)
    return _terms_claim(ITEM, "SATISFIED" if items[0] == items[1] else "CONTRADICTED", halves)


def _delivered_claim(buyer: Party, seller: Party, rail: _Rail) -> Claim:
    if buyer.terms.get("expires_at") != seller.terms.get("expires_at"):
        return _terms_claim(DELIVERED, "CONTRADICTED", _refs(buyer.half, seller.half))
    received = _leg(rail, "delivered:received")
    expires = _instant(seller.terms.get("expires_at"))
    if received is None or expires is None:
        return _terms_claim(DELIVERED, "INSUFFICIENT", _refs(buyer.half, seller.half))
    cited = _refs(buyer.half, seller.half, rail.terms, rail.legs["delivered:received"])
    at = _instant(received.get("observed_at"))
    if at is None:
        return _terms_claim(DELIVERED, "INSUFFICIENT", cited)
    return _terms_claim(DELIVERED, "SATISFIED" if at <= expires else "CONTRADICTED", cited)


def _paid_claim(buyer: Party, seller: Party, rail: _Rail) -> Claim:
    paid, sent = _leg(rail, "payee_observed"), _leg(rail, "delivered:sent")
    if paid is None or sent is None:
        return _terms_claim(PAID, "INSUFFICIENT", _refs(buyer.half, seller.half))
    cited = _refs(buyer.half, seller.half, rail.terms, rail.legs["payee_observed"], rail.legs["delivered:sent"])
    paid_at, sent_at = _instant(paid.get("observed_at")), _instant(sent.get("observed_at"))
    if paid_at is None or sent_at is None:
        return _terms_claim(PAID, "INSUFFICIENT", cited)
    ok = paid.get("status") == "settled" and paid_at <= sent_at
    return _terms_claim(PAID, "SATISFIED" if ok else "CONTRADICTED", cited)


# ---------------------------------------------------------------------------
# (b) Each party's own pack, over its own records
# ---------------------------------------------------------------------------


def _replay(pack: PackDefinition, party: Party):
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(pack, project_dir=Path(tmp) / "referee", mode="observe")
        resolved = installed.resolved
        return replay(
            list(party.records),
            caps_fold=resolved.caps_fold(),
            caps_minor=resolved.caps_minor(),
            per_action_minor=resolved.per_action_minor(),
            per_action_reads=resolved.per_action_reads(),
            manifest_digest=resolved.manifest_digest,
            disclosed=dict(party.disclosed),
            withheld=party.withheld,
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
            pack=installed.pack,
        )


def _pack_claims(role: str, party: Party) -> tuple[list[Claim], int]:
    pack = _pack_for(role, party)
    contract_ref = pack.pack_id.rsplit("/", 1)
    contract_ref = f"{contract_ref[0]}@{contract_ref[1]}"
    claims: list[Claim] = []
    excluded = 0
    for o in pack.obligations:
        if o.measurability == "declared_not_measured":
            pack_ref = (DigestRef(digest=pack.definition_digest()),)
            instrument = o.evidence_instrument.field or o.evidence_instrument.name if o.evidence_instrument else None
            claims.append(Claim(
                id=f"{role}/{o.id}",
                contract_ref=contract_ref,
                requirement_ref=o.id,
                tier="recomputed",
                grade="self-attested",
                sufficiency="GAP",
                verdict="not_evaluable",
                evidence=pack_ref,
                proofs=(),
                presentation=AnalysisCarrier(
                    status="NOT_FOUND",
                    summary=f"The pack does not measure this rule yet: it waits on the {instrument} field, "
                            "which no record carries.",
                ),
            ))
    for sourced in _replay(pack, party).decisions:
        record_id = sourced.record["capsule_id"]
        outcomes = {c.id: c for c in sourced.decision.constraints}
        for res in obligation_results(pack, sourced.decision.constraints):
            if res.result == "n/a":
                excluded += 1
                continue
            obligation = next(o for o in pack.obligations if o.id == res.obligation_id)
            outcome = outcomes.get(obligation.check)
            evidence = [DigestRef(digest=record_id)]
            if outcome is not None and outcome.evidence is not None:
                evidence.append(DigestRef(digest=json_digest(outcome.evidence)))
            status = "SATISFIED" if res.result == "pass" else "CONTRADICTED"
            claims.append(Claim(
                id=f"{role}/{res.obligation_id}/{record_id}",
                contract_ref=contract_ref,
                requirement_ref=res.obligation_id,
                tier="recomputed",
                grade="self-attested",
                sufficiency="SATISFIED",
                verdict="met" if res.result == "pass" else "not_met",
                evidence=tuple(evidence),
                proofs=(),
                presentation=DisclosureCarrier(status=status, evidence=tuple(evidence)),
            ))
    return claims, excluded


# ---------------------------------------------------------------------------
# The Result
# ---------------------------------------------------------------------------


def judge(buyer: Party, seller: Party, rail_legs: Sequence[dict], *, as_of: str, referee_key_id: str) -> dict:
    """The referee's Result on one deal, as an Evidence Result v0 document.

    *referee_key_id* is the key this referee signs with; the judgment is refused
    unless both halves name it. Raises :class:`RefereeError` on every refusal in
    the module docstring.
    """
    if _instant(as_of) is None:
        raise RefereeError("as_of_malformed", "as_of must be RFC 3339 UTC with seconds and Z")
    for role, party in (("buyer", buyer), ("seller", seller)):
        _check_half(role, party)
    _check_pair(buyer, seller)
    if _named_referee(buyer, seller) != referee_key_id:
        raise RefereeError("not_named_referee", "this referee's key is not the one the deal terms name")
    for role, party in (("buyer", buyer), ("seller", seller)):
        _check_records(role, party)

    rail = _rail(rail_legs, buyer, seller)
    claims = [
        _price_claim(buyer, seller, rail),
        _item_claim(buyer, seller),
        _delivered_claim(buyer, seller, rail),
        _paid_claim(buyer, seller, rail),
    ]
    excluded = 0
    for role, party in (("buyer", buyer), ("seller", seller)):
        own, n = _pack_claims(role, party)
        claims += own
        excluded += n
    claims.sort(key=lambda c: (c.contract_ref, c.requirement_ref, c.id))
    doc = build_result(claims, generated_at=as_of, excluded_not_applicable=excluded).to_dict()
    validate_against_schema(doc)
    verify_result(doc)
    return doc


def result_digest(doc: dict) -> str:
    """The digest a sealed Result names: SHA-256 of the Result's JCS bytes."""
    return hashlib.sha256(jcs(doc)).hexdigest()


def seal_result(
    doc: dict,
    *,
    buyer_half: dict,
    seller_half: dict,
    ledger: str,
    operator: str = "",
    developer: str = "",
    **kwargs: Any,
):
    """Seal *doc* in the referee's own book: a capsule committing to the Result
    (as its ``agent_input``) that names its digest and both halves in
    ``compute_attestation.referee_result`` and cites both halves in
    ``references[]`` (``responds_to``). Other keywords pass to ``seal``."""
    halves = (buyer_half["capsule_id"], seller_half["capsule_id"])
    return seal(
        doc,
        action="referee.result",
        operator=operator,
        developer=developer,
        verdict="assessed",
        ledger=ledger,
        extra_compute={RESULT_BLOCK: {
            "schema": RESULT_SCHEMA,
            "result_digest": result_digest(doc),
            "contract_ref": DEAL_TERMS_CONTRACT,
            "buyer_half": halves[0],
            "seller_half": halves[1],
        }},
        references=tuple(ReferenceEntry(type="agent-action-capsule", digest_alg="SHA-256", digest=h,
                                        citation_purpose="responds_to") for h in halves),
        **kwargs,
    )


def verify_referee_result(capsule: dict, doc: dict, *, buyer_half: dict, seller_half: dict,
                          buyer_terms: dict, seller_terms: dict) -> None:
    """A receiving party's check of a sealed Result: it verifies, it is signed by
    the referee both halves' terms name, it cites exactly these halves, and *doc*
    is the Result it names. Raises :class:`RefereeError`."""
    if not _authentic(capsule):
        raise RefereeError("result_unverified", "the Result capsule does not verify under the key it carries")
    keys = {(t.get("referee") or {}).get("key_id") for t in (buyer_terms, seller_terms)}
    if len(keys) != 1 or capsule.get("key_id") not in keys:
        raise RefereeError("not_named_referee", "the Result is not signed by the referee the deal terms name")
    block = _compute(capsule).get(RESULT_BLOCK)
    if not isinstance(block, dict) or block.get("schema") != RESULT_SCHEMA:
        raise RefereeError("not_a_result", "the capsule carries no referee Result")
    if (block.get("buyer_half"), block.get("seller_half")) != (buyer_half["capsule_id"], seller_half["capsule_id"]):
        raise RefereeError("other_deal", "the Result names other halves")
    if block.get("result_digest") != result_digest(doc):
        raise RefereeError("result_mismatch", "the document is not the Result the capsule names")
    validate_against_schema(doc)
    verify_result(doc)
