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
     "expires_at": <RFC 3339 UTC>,
     "buyer":  {"operator": <text>, "book_key_id": <64 hex>, "deal_id": <text>,
                "pack": {"pack_id", "definition_digest"}},
     "seller": {... the same members ...},
     "referee": {"key_id": <64 hex>, "mailbox": <text>}}

``price`` is the settlement draft's exact amount triple (capsule-emit
``settlement.amount``), compared with the rail proof at a common scale.
``book_key_id`` is the key a party's deal records are signed with, and
``deal_id`` the party's own deal (x-deal-v0) those records are about. Both
halves must open to the same ``buyer``, ``seller`` and ``referee`` entries and
the same ``action``: no party chooses its own pack, book or referee alone.

**What it judges.**

(a) The shared deal terms, one claim each, under ``ec:deal-terms@1``
(:data:`DEAL_TERMS_CONTRACT`), tier ``recomputed``:

- ``price-agreed``: both halves open to the same ``price``, the payer's
  observation sent that amount, and the payee's observation agrees with it as
  settled (the settlement draft's ``agreed`` state, under the halves' keys);
- ``item-agreed``: both halves open to the same ``item``;
- ``delivered-before-expiry``: both halves open to the same ``expires_at``,
  and the buyer's first observation of the delivery is at or before it;
- ``paid-before-release``: the payee observed the payment settled at or before
  the seller's first observation of sending the delivery.

A disagreement is ``not_met`` and cites the records that disagree. An input
that is missing or unusable (no rail proof, no delivery leg, a leg the
settlement draft refuses, a payment the two sides do not join) is
``INSUFFICIENT`` and ``not_evaluable``, never ``not_met``.

The rail proof is the terms leg the seller sealed citing one of the deal's
halves; when the seller sealed several, the one the buyer's first payment
observation answers. A leg counts only when it passes the draft's structure
checks and is signed by its side's half key.

(b) Each party's own pack, over that party's own records only: the pack both
halves name for that role, replayed through ``GuardEngine``
(``report/replay.py``) over the party's records in sealed order. The buyer is
never judged under the seller's pack, nor the seller under the buyer's. Only
decisions on records of the party's named ``deal_id`` become claims; its
other records are history the replay reads. One claim per rule per decision:
pass ``met``, fail ``not_met``. An ``n/a`` the rule's check reports in scope,
for a missing field, is ``GAP``/``not_evaluable`` (``UNKNOWN`` when the
evidence names no cause); one out of scope, or a rule whose selector did not
match, is counted in ``excluded_not_applicable`` and is no claim, as
``report/result_from_folds.py`` projects a constraint. A rule the pack
declares ``declared_not_measured`` is one ``GAP``/``not_evaluable`` claim per
party whose ``analysis`` names its ``evidence_instrument``.

**Deterministic.** ``generated_at`` is the given as-of instant; claims are
ordered by contract, requirement and id; nothing comes from a clock, a random
value or a model. The same inputs give the same document.

**What the Result discloses.** Digests of the halves, the legs, the party's
records and the pack definition, and the verdicts. Never an opening, a terms
value or a decision's evidence: evidence objects can hold a caller's
equivalence key, whose digest is guessable (AGENTS.md, local-only fields), so
none is cited. No decision capsule leaves the referee.

**Refusals** (:class:`RefereeError`, with a stable ``code``): a half or a
record that does not verify under the key it carries; an opening that does
not recompute its half's ``action_digest``; halves that do not answer each
other, or that open to different parties, packs, referees or actions; a
referee that is not the signer; a party's records signed by another key, out
of sealed order, none at all, or none about its ``deal_id``; a pack the installed
catalog does not hold at the named digest (fail closed: a referee is pinned to
an engine that holds the deal's packs).
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
from agent_action_capsule.canonical import jcs
from agent_action_capsule.contracts import ReferenceEntry
from capsule_emit import seal
from capsule_emit import settlement as _settlement
from capsule_emit.signing import AuthorshipVerdict, verify_capsule_signature_tristate
from capsule_emit.verification import verify_capsule

from .guards.checks import RUNNABLE_CHECKS
from .packs import install_pack, load_pack_dir, obligation_results
from .packs.schema import PackDefinition
from .report.errors import ResultError
from .report.replay import _deal_id, replay
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
    "result_digest",
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
_SHARED = ("buyer", "seller", "referee")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
_CATALOG = Path(__file__).parent / "packs" / "catalog"
_LEG = _settlement.SETTLEMENT_MEMBER


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


def result_digest(doc: dict) -> str:
    """The digest a sealed Result names: SHA-256 of the Result's JCS bytes."""
    return hashlib.sha256(jcs(doc)).hexdigest()


def _compute(capsule: dict) -> dict:
    ma = capsule.get("model_attestation")
    ca = ma.get("compute_attestation") if isinstance(ma, dict) else None
    return ca if isinstance(ca, dict) else {}


def _authentic(capsule: object) -> bool:
    """The capsule recomputes and its signature verifies under the key_id it carries."""
    return (isinstance(capsule, dict) and verify_capsule(capsule).ok
            and verify_capsule_signature_tristate(capsule)[0] is AuthorshipVerdict.AUTHORED)


def _instant(value: object) -> datetime | None:
    """An RFC 3339 UTC instant (``Z``, optional fraction of a second), or ``None``."""
    if not isinstance(value, str) or not _INSTANT.match(value):
        return None
    try:
        return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _cited(capsule: dict) -> set[str]:
    """The capsule ids a capsule points back at: its chain parent and its references."""
    out = set()
    chain = capsule.get("chain")
    if isinstance(chain, dict) and isinstance(chain.get("parent_capsule_id"), str):
        out.add(chain["parent_capsule_id"])
    refs = capsule.get("references")
    for r in refs if isinstance(refs, list) else ():
        if isinstance(r, dict) and r.get("type") == "agent-action-capsule" and isinstance(r.get("digest"), str):
            out.add(r["digest"])
    return out


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
    """One half answers the other: the responder names the requester's half, by its
    ``request_capsule_id`` or as capsule-emit links it (chain parent or a reference)."""
    b, s = buyer.half, seller.half
    if b.get("key_id") == s.get("key_id"):
        raise RefereeError("halves_unpaired", "both halves are signed by one key")
    for req, resp in ((b, s), (s, b)):
        if _compute(req).get("role") != "requester" or _compute(resp).get("role") != "responder":
            continue
        named = _cited(resp) | {_compute(resp).get("request_capsule_id")}
        if req.get("capsule_id") in named:
            return
    raise RefereeError("halves_unpaired", "neither half answers the other")


def _check_shared(buyer: Party, seller: Party) -> str:
    """Both halves open to the same parties, referee and action; returns the referee key."""
    if buyer.action != seller.action:
        raise RefereeError("terms_disagree", "the two halves open to different actions")
    for member in _SHARED:
        if buyer.terms.get(member) != seller.terms.get(member):
            raise RefereeError("referee_disagrees" if member == "referee" else "terms_disagree",
                               f"the two halves open to different {member} entries")
    ref = buyer.terms.get("referee")
    if not isinstance(ref, dict):
        raise RefereeError("referee_unnamed", "the terms name no referee")
    return _hex(ref.get("key_id"), "referee.key_id")


def _own_records(role: str, party: Party) -> tuple[list[dict], set[str]]:
    """The party's records, once each, checked; and the ids of those about its deal."""
    entry = party.terms[role]
    key = _hex(entry.get("book_key_id"), f"{role}.book_key_id")
    deal = entry.get("deal_id")
    if not isinstance(deal, str) or not deal:
        raise RefereeError("terms_malformed", f"{role}.deal_id must be non-empty text")
    records: list[dict] = []
    seen: set[str] = set()
    for rec in party.records:
        if not isinstance(rec, dict) or rec.get("key_id") != key:
            raise RefereeError("records_foreign", f"a {role} record is not signed by the {role}'s book key")
        if not _authentic(rec):
            raise RefereeError("records_unverified", f"a {role} record does not verify")
        if rec["capsule_id"] not in seen:
            seen.add(rec["capsule_id"])
            records.append(rec)
    # Sealed order: timestamps never go back, and a record follows its chain parent.
    stamps = [str(r.get("timestamp", "")) for r in records]
    at = {r["capsule_id"]: i for i, r in enumerate(records)}
    parents = [((r.get("chain") or {}).get("parent_capsule_id") if isinstance(r.get("chain"), dict) else None)
               for r in records]
    if stamps != sorted(stamps) or any(p in at and at[p] >= i for i, p in enumerate(parents)):
        raise RefereeError("records_out_of_order", f"the {role}'s records are not in sealed order")
    in_deal = {
        r["capsule_id"] for r in records
        if isinstance(party.disclosed.get(r["capsule_id"]), dict)
        and _deal_id(party.disclosed[r["capsule_id"]]).value == deal
    }
    if not records:
        raise RefereeError("records_missing", f"the {role} gave no records of its deal")
    if not in_deal:
        raise RefereeError("records_other_deal", f"no {role} record is about deal {deal!r}")
    return records, in_deal


@cache
def _catalog() -> dict[str, PackDefinition]:
    packs = {}
    for d in sorted(p for p in _CATALOG.iterdir() if (p / "pack.yaml").is_file()):
        pack = load_pack_dir(d)
        packs[pack.pack_id] = pack
    return packs


def _pack_for(role: str, terms: dict) -> PackDefinition:
    ref = terms[role].get("pack")
    if not isinstance(ref, dict):
        raise RefereeError("pack_unnamed", f"the terms name no pack for the {role}")
    pack = _catalog().get(ref.get("pack_id"))
    if pack is None or pack.definition_digest() != ref.get("definition_digest"):
        raise RefereeError("pack_not_held", f"no pack {ref.get('pack_id')!r} at the named digest")
    return pack


# ---------------------------------------------------------------------------
# (a) The shared deal terms
# ---------------------------------------------------------------------------


def _refs(*capsules: dict | None) -> tuple[DigestRef, ...]:
    seen: list[str] = []
    for c in capsules:
        if c is not None and c["capsule_id"] not in seen:
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
    """The settlement the seller's terms leg opened for this deal, and its legs by kind."""

    terms: dict | None = None
    legs: dict[str, dict] = field(default_factory=dict)
    entry: dict = field(default_factory=dict)


def _usable(c: object) -> bool:
    """An authentic capsule whose settlement member passes the draft's structure checks."""
    return (_authentic(c) and isinstance(c.get(_LEG), dict)
            and not _settlement.structure_failures(c[_LEG]))


def _rail(legs: Sequence[object], buyer: Party, seller: Party) -> _Rail:
    """The rail proof for this deal (module docstring). Of the legs of one kind
    signed by the side's half key, the newest observation counts for the payer and
    payee observations (pending, then settled) and the first for each delivery."""
    payer, payee = buyer.half["key_id"], seller.half["key_id"]
    halves = {buyer.half["capsule_id"], seller.half["capsule_id"]}
    usable = [c for c in legs if _usable(c)]
    terms = {
        c["capsule_id"]: c for c in usable
        if c[_LEG]["leg"] == "terms" and c.get("key_id") == payee and _cited(c) & halves
    }
    by_kind: dict[str, list[dict]] = {}
    for c in usable:
        s = c[_LEG]
        if s["leg"] == "terms" or s.get("terms_ref") not in terms:
            continue
        if s["leg"] == "delivered":
            kind = f"delivered:{s['delivery']['direction']}"
            key = payee if s["delivery"]["direction"] == "sent" else payer
        else:
            kind, key = s["leg"], payer if s["leg"] == "payer_observed" else payee
        if c.get("key_id") == key and _instant(s.get("observed_at")) is not None:
            by_kind.setdefault(kind, []).append(c)

    def order(c: dict) -> tuple[datetime, str]:
        return _instant(c[_LEG]["observed_at"]), c["capsule_id"]

    if len(terms) == 1:
        chosen = next(iter(terms))
    else:
        paid = sorted(by_kind.get("payer_observed", []), key=order)
        if not paid:
            return _Rail()
        chosen = paid[0][_LEG]["terms_ref"]
    picked: dict[str, dict] = {}
    for kind, found in by_kind.items():
        found = sorted((c for c in found if c[_LEG]["terms_ref"] == chosen), key=order)
        if found:
            picked[kind] = found[-1] if kind.endswith("_observed") else found[0]
    report = _settlement.verify_settlements([terms[chosen], *picked.values()],
                                            key_policy={"payer": payer, "payee": payee})
    entry = next((e for e in report.settlements if e.get("terms") == chosen), {})
    return _Rail(terms=terms[chosen], legs=picked, entry=entry)


def _leg(rail: _Rail, kind: str) -> dict | None:
    c = rail.legs.get(kind)
    return c[_LEG] if c else None


_DIGITS = re.compile(r"^(0|[1-9][0-9]*)$")


def _amount(value: object) -> tuple[str, int, str] | None:
    """An amount triple as (asset, scale, digits), or ``None`` when it is not the draft's form."""
    if not isinstance(value, dict):
        return None
    digits, code, scale = value.get("value"), value.get("assetCode"), value.get("assetScale")
    if (not isinstance(digits, str) or not _DIGITS.match(digits) or not isinstance(code, str) or not code
            or not isinstance(scale, int) or isinstance(scale, bool) or not 0 <= scale <= 255):
        return None
    return code, scale, digits


def _same_amount(a: tuple[str, int, str], b: tuple[str, int, str]) -> bool:
    """Equal amounts at any two scales: the same asset, and equal digits at the larger scale."""
    top = max(a[1], b[1])
    return a[0] == b[0] and a[2] + "0" * (top - a[1]) == b[2] + "0" * (top - b[1])


def _price_claim(buyer: Party, seller: Party, rail: _Rail) -> Claim:
    halves = (buyer.half, seller.half)
    prices = _amount(buyer.terms.get("price")), _amount(seller.terms.get("price"))
    if None in prices:
        return _terms_claim(PRICE, "INSUFFICIENT", _refs(*halves))
    if not _same_amount(*prices):
        return _terms_claim(PRICE, "CONTRADICTED", _refs(*halves))
    payer = _leg(rail, "payer_observed")
    if rail.terms is None or payer is None or "payee_observed" not in rail.legs:
        return _terms_claim(PRICE, "INSUFFICIENT", _refs(*halves))
    cited = _refs(*halves, rail.terms, rail.legs["payer_observed"], rail.legs["payee_observed"])
    state, sent = rail.entry.get("payment_state"), _amount(payer.get("amount"))
    if sent is None:
        return _terms_claim(PRICE, "INSUFFICIENT", cited)
    if not _same_amount(sent, prices[0]) or (state == "mismatch" and "amount" in rail.entry.get("differs", ())):
        return _terms_claim(PRICE, "CONTRADICTED", cited)
    if state == "agreed" and rail.entry.get("agreed_status") == "settled":
        return _terms_claim(PRICE, "SATISFIED", cited)
    return _terms_claim(PRICE, "INSUFFICIENT", cited)


def _item_claim(buyer: Party, seller: Party) -> Claim:
    halves = _refs(buyer.half, seller.half)
    items = (buyer.terms.get("item"), seller.terms.get("item"))
    if not all(isinstance(i, str) and i for i in items):
        return _terms_claim(ITEM, "INSUFFICIENT", halves)
    return _terms_claim(ITEM, "SATISFIED" if items[0] == items[1] else "CONTRADICTED", halves)


def _delivered_claim(buyer: Party, seller: Party, rail: _Rail) -> Claim:
    halves = _refs(buyer.half, seller.half)
    expiries = _instant(buyer.terms.get("expires_at")), _instant(seller.terms.get("expires_at"))
    if None in expiries:
        return _terms_claim(DELIVERED, "INSUFFICIENT", halves)
    if expiries[0] != expiries[1]:
        return _terms_claim(DELIVERED, "CONTRADICTED", halves)
    received = _leg(rail, "delivered:received")
    if received is None:
        return _terms_claim(DELIVERED, "INSUFFICIENT", halves)
    cited = _refs(buyer.half, seller.half, rail.terms, rail.legs["delivered:received"])
    ok = _instant(received["observed_at"]) <= expiries[0]
    return _terms_claim(DELIVERED, "SATISFIED" if ok else "CONTRADICTED", cited)


def _paid_claim(buyer: Party, seller: Party, rail: _Rail) -> Claim:
    paid, sent = _leg(rail, "payee_observed"), _leg(rail, "delivered:sent")
    if paid is None or sent is None:
        return _terms_claim(PAID, "INSUFFICIENT", _refs(buyer.half, seller.half))
    cited = _refs(buyer.half, seller.half, rail.terms, rail.legs["payee_observed"], rail.legs["delivered:sent"])
    ok = paid["status"] == "settled" and _instant(paid["observed_at"]) <= _instant(sent["observed_at"])
    return _terms_claim(PAID, "SATISFIED" if ok else "CONTRADICTED", cited)


# ---------------------------------------------------------------------------
# (b) Each party's own pack, over its own records
# ---------------------------------------------------------------------------


def _replay(pack: PackDefinition, party: Party, records: list[dict]):
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(pack, project_dir=Path(tmp) / "referee", mode="observe")
        resolved = installed.resolved
        return replay(
            records,
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


def _not_settled(outcome) -> tuple[str, str] | None:
    """An ``n/a`` constraint projected as ``result_from_folds`` does: ``None`` when out
    of scope (excluded), else (status, sufficiency): NOT_FOUND/GAP for a named missing
    field, UNKNOWN/UNKNOWN when the evidence names no cause."""
    evidence = outcome.evidence if outcome is not None and isinstance(outcome.evidence, dict) else {}
    if evidence.get("in_scope") is False:
        return None
    if evidence.get("in_scope") is True and evidence.get("missing_field"):
        return "NOT_FOUND", "GAP"
    return "UNKNOWN", "UNKNOWN"


def _pack_claims(role: str, party: Party, terms: dict, records: list[dict], in_deal: set[str]) -> tuple[list[Claim], int]:
    pack = _pack_for(role, terms)
    name, version = pack.pack_id.rsplit("/", 1)
    contract_ref = f"{name}@{version}"
    pack_ref = (DigestRef(digest=pack.definition_digest()),)
    claims: list[Claim] = []
    excluded = 0

    def claim(claim_id: str, rule: str, status: str, sufficiency: str, verdict: str, evidence, presentation=None):
        claims.append(Claim(
            id=claim_id, contract_ref=contract_ref, requirement_ref=rule, tier="recomputed", grade="self-attested",
            sufficiency=sufficiency, verdict=verdict, evidence=evidence, proofs=(),
            presentation=presentation or DisclosureCarrier(status=status, evidence=evidence),
        ))

    for o in pack.obligations:
        if o.measurability == "declared_not_measured":
            ins = o.evidence_instrument
            instrument = (ins.field or ins.name) if ins is not None else "unnamed"
            claim(f"{role}/{o.id}", o.id, "NOT_FOUND", "GAP", "not_evaluable", pack_ref, AnalysisCarrier(
                status="NOT_FOUND",
                summary=f"The pack does not measure this rule yet: it waits on the {instrument} field, "
                        "which no record carries.",
            ))
    by_id = {o.id: o for o in pack.obligations}
    for sourced in _replay(pack, party, records).decisions:
        record_id = sourced.record["capsule_id"]
        if record_id not in in_deal:
            continue
        outcomes = {c.id: c for c in sourced.decision.constraints}
        evidence = (DigestRef(digest=record_id),)
        for res in obligation_results(pack, sourced.decision.constraints):
            claim_id = f"{role}/{res.obligation_id}/{record_id}"
            if res.result in ("pass", "fail"):
                met = res.result == "pass"
                claim(claim_id, res.obligation_id, "SATISFIED" if met else "CONTRADICTED", "SATISFIED",
                      "met" if met else "not_met", evidence)
                continue
            outcome = outcomes.get(by_id[res.obligation_id].check)
            projected = _not_settled(outcome) if outcome is not None and outcome.result == "n/a" else None
            if projected is None:
                excluded += 1
            else:
                claim(claim_id, res.obligation_id, projected[0], projected[1], "not_evaluable", evidence)
    return claims, excluded


# ---------------------------------------------------------------------------
# The Result
# ---------------------------------------------------------------------------


def judge(buyer: Party, seller: Party, rail_legs: Sequence[object], *, as_of: str, referee_key_id: str) -> dict:
    """The referee's Result on one deal, as an Evidence Result v0 document.

    *referee_key_id* is the key this referee signs with; the judgment is refused
    unless both halves name it. Raises :class:`RefereeError` on every refusal in
    the module docstring.
    """
    if _instant(as_of) is None:
        raise RefereeError("as_of_malformed", "as_of must be an RFC 3339 UTC instant ending in Z")
    for role, party in (("buyer", buyer), ("seller", seller)):
        _check_half(role, party)
    _check_pair(buyer, seller)
    if _check_shared(buyer, seller) != referee_key_id:
        raise RefereeError("not_named_referee", "this referee's key is not the one the deal terms name")
    own = {role: _own_records(role, party) for role, party in (("buyer", buyer), ("seller", seller))}

    rail = _rail(rail_legs, buyer, seller)
    claims = [
        _price_claim(buyer, seller, rail),
        _item_claim(buyer, seller),
        _delivered_claim(buyer, seller, rail),
        _paid_claim(buyer, seller, rail),
    ]
    excluded = 0
    for role, party in (("buyer", buyer), ("seller", seller)):
        records, in_deal = own[role]
        found, n = _pack_claims(role, party, buyer.terms, records, in_deal)
        claims += found
        excluded += n
    claims.sort(key=lambda c: (c.contract_ref, c.requirement_ref, c.id))
    try:
        doc = build_result(claims, generated_at=as_of, excluded_not_applicable=excluded).to_dict()
        validate_against_schema(doc)
        verify_result(doc)
    except (ResultError, ValueError, jsonschema.ValidationError) as exc:
        raise RefereeError("result_invalid", str(exc)) from exc
    return doc


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
    the referee both halves' terms name, its block and its references name exactly
    these halves, and *doc* is the Result it names. Raises :class:`RefereeError`."""
    if not _authentic(capsule):
        raise RefereeError("result_unverified", "the Result capsule does not verify under the key it carries")
    keys = {(t.get("referee") or {}).get("key_id") for t in (buyer_terms, seller_terms)}
    if len(keys) != 1 or capsule.get("key_id") not in keys:
        raise RefereeError("not_named_referee", "the Result is not signed by the referee the deal terms name")
    block = _compute(capsule).get(RESULT_BLOCK)
    if not isinstance(block, dict) or block.get("schema") != RESULT_SCHEMA:
        raise RefereeError("not_a_result", "the capsule carries no referee Result")
    halves = (buyer_half["capsule_id"], seller_half["capsule_id"])
    if (block.get("buyer_half"), block.get("seller_half")) != halves or not set(halves) <= _cited(capsule):
        raise RefereeError("other_deal", "the Result names other halves")
    if block.get("result_digest") != result_digest(doc):
        raise RefereeError("result_mismatch", "the document is not the Result the capsule names")
    try:
        validate_against_schema(doc)
        verify_result(doc)
    except (ResultError, ValueError, jsonschema.ValidationError) as exc:
        raise RefereeError("result_invalid", str(exc)) from exc
