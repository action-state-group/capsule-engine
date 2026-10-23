# SPDX-License-Identifier: Apache-2.0
"""A typed check's payee is its target, as an ``x-deal-v0`` check's is.

capsulectl seals an ``x-deal-v0`` check's counterparty in its block
(``x-deal-v0.counterparty``, ``fp_alg`` ``hmac-sha256-deal-key``) and moves
the same fingerprints into a typed ``proposed-action/v0``'s body
(``body.counterparty``, ``fp_alg`` ``hmac-sha256-chain-key``). The bridge
reads each from where its record kind seals it, so the payee-keyed checks
(``counterparty_seen_before``, ``dedupe``, ``single_commitment``) key a typed
check on its payee: the per-deal fingerprint, or the profile-scoped one when
the checker input or a companion supplies it. A typed check sealing no
counterparty has no target. Under everyday 0.3.5
``counterparty_seen_before/4.0.0`` fails it as a first-time payee ("no payee
named", tests/test_counterparty_seen_before_no_payee.py); under 0.3.4
version 3 recorded it ``n/a`` naming ``target``, the field it could not read.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TypedDict

from agent_action_capsule import json_digest

import capsule_engine
from capsule_engine.guards.capsule import DENY
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.report.replay import action_for_check_input, action_for_record, replay

PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")

CHAIN_ALG = "hmac-sha256-chain-key"
DEAL_ALG = "hmac-sha256-deal-key"
PROFILE_ALG = "hmac-sha256-profile-key"
PAYEE = "a" * 64
OTHER_PAYEE = "b" * 64
PROFILE_PAYEE = "d" * 64
DEAL = "deal-aaaaaaaaaaaaaaaa"
DAY_1 = "2026-10-07T12:00:00Z"
DAY_2 = "2026-10-08T12:00:00Z"


class Counterparty(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class CheckBody(TypedDict, total=False):
    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    amount_minor: int
    spend_minor: int
    counterparty: Counterparty


class TypedCheck(TypedDict):
    """capsulectl's typed header around a check body."""

    type: str
    canonicalization: str
    chain_id: str
    seq: int
    at: str
    body: CheckBody


class DealBlock(TypedDict, total=False):
    record_type: str
    deal_id: str
    seq: int
    counterparty: Counterparty


_UntypedFields = TypedDict("_UntypedFields", {"body": CheckBody, "x-deal-v0": DealBlock})


class UntypedCheck(_UntypedFields):
    """An ``x-deal-v0`` check."""


class BoundCapsule(TypedDict):
    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]


class ProfileBlock(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class AboutRef(TypedDict):
    rel: str
    type: str
    digest_alg: str
    digest: str


class CompanionBlock(TypedDict):
    record_type: str
    deal_id: str
    seq: int
    refs: list[AboutRef]
    counterparty_profile: ProfileBlock


class EmptyBody(TypedDict):
    """A companion states nothing in its body."""


_CompanionFields = TypedDict("_CompanionFields", {"body": EmptyBody, "x-deal-v0": CompanionBlock})


class Companion(_CompanionFields):
    """A ``counterparty_profile`` companion record."""


class Entry(BoundCapsule, total=False):
    """An external-check-input/v0 ``record`` entry."""

    agent_input: TypedCheck
    counterparty_profile: ProfileBlock


_SEQ = iter(range(1, 1_000))


def _counterparty(alg: str, payee: str) -> Counterparty:
    return {"fp_alg": alg, "ids": {"payee": payee}}


def _body() -> CheckBody:
    return {
        "action": "pay",
        "action_class": "money.purchase",
        "taxonomy_version": TAXONOMY_VERSION,
        "currency": "USD",
        "amount_minor": 4_500,
        "spend_minor": 4_500,
    }


def _bound(record: object, seq: int, at: str) -> BoundCapsule:
    return {
        "capsule_id": f"{seq:064x}",
        "action_id": f"{DEAL}/{seq}",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": at,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }


def _typed(payee: str | None, at: str = DAY_1) -> tuple[BoundCapsule, TypedCheck]:
    """A typed check sealing ``payee`` in its body the way capsulectl moves
    it there, or no counterparty when ``payee`` is ``None``."""
    seq = next(_SEQ)
    body = _body()
    if payee is not None:
        body["counterparty"] = _counterparty(CHAIN_ALG, payee)
    record: TypedCheck = {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": DEAL, "seq": seq,
                          "at": at, "body": body}
    return _bound(record, seq, at), record


def _untyped(block_payee: str | None, body_payee: str | None = None) -> tuple[BoundCapsule, UntypedCheck]:
    """An ``x-deal-v0`` check sealing ``block_payee`` in its block and,
    when given, ``body_payee`` in its body."""
    seq = next(_SEQ)
    block: DealBlock = {"record_type": "check", "deal_id": DEAL, "seq": seq}
    if block_payee is not None:
        block["counterparty"] = _counterparty(DEAL_ALG, block_payee)
    body = _body()
    if body_payee is not None:
        body["counterparty"] = _counterparty(CHAIN_ALG, body_payee)
    record: UntypedCheck = {"body": body, "x-deal-v0": block}
    return _bound(record, seq, DAY_1), record


def _profile(payee: str) -> ProfileBlock:
    return {"fp_alg": PROFILE_ALG, "ids": {"payee": payee}}


def _companion(check: TypedCheck, payee: str) -> tuple[BoundCapsule, Companion]:
    """A bound ``counterparty_profile`` companion about ``check``."""
    seq = next(_SEQ)
    ref: AboutRef = {"rel": "about", "type": "deal-record", "digest_alg": "SHA-256", "digest": json_digest(check)}
    record: Companion = {"body": {}, "x-deal-v0": {"record_type": "counterparty_profile", "deal_id": DEAL, "seq": seq,
                                                   "refs": [ref], "counterparty_profile": _profile(payee)}}
    return _bound(record, seq, DAY_1), record


def _replay(*records: tuple[BoundCapsule, object]):
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        result = replay(
            [capsule for capsule, _ in records],
            caps_fold=resolved.caps_fold(),
            caps_minor=resolved.caps_minor(),
            manifest_digest=resolved.manifest_digest,
            disclosed={capsule["capsule_id"]: record for capsule, record in records},
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
            pack=installed.pack,
        )
    return [sourced.decision for sourced in result.decisions]


def _constraint(decision, check_id: str):
    return next(c for c in decision.constraints if c.id == check_id)


# -- the bridge ------------------------------------------------------------------


def test_a_typed_check_targets_the_payee_its_body_seals():
    action = action_for_record(*_typed(PAYEE))
    assert action.target == f"payee-fp:{CHAIN_ALG}:{PAYEE}"
    assert (action.counterparty_ids, action.counterparty_fp_alg) == ({"payee": PAYEE}, CHAIN_ALG)


def test_a_typed_and_an_untyped_check_map_their_payee_the_same_way():
    """The one difference is the ``fp_alg`` each record kind seals."""
    typed, untyped = action_for_record(*_typed(PAYEE)), action_for_record(*_untyped(PAYEE))
    assert typed.target == untyped.target.replace(DEAL_ALG, CHAIN_ALG)
    assert typed.counterparty_ids == untyped.counterparty_ids


def test_an_untyped_check_reads_only_its_block():
    """A counterparty in an ``x-deal-v0`` check's body is not where that kind
    seals it, so it is never its target."""
    assert action_for_record(*_untyped(None, body_payee=PAYEE)).target is None
    assert action_for_record(*_untyped(PAYEE, body_payee=OTHER_PAYEE)).target == f"payee-fp:{DEAL_ALG}:{PAYEE}"


def test_a_typed_check_sealing_no_counterparty_has_no_target():
    action = action_for_record(*_typed(None))
    assert (action.target, action.counterparty_ids, action.counterparty_fp_alg) == (None, None, None)


def test_a_typed_target_is_a_fingerprint_never_a_clear_value():
    capsule, record = _typed(PAYEE)
    record["body"]["counterparty"]["ids"]["name"] = "e" * 64
    capsule["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(record)
    assert action_for_record(capsule, record).target == f"payee-fp:{CHAIN_ALG}:{PAYEE}"


# -- profile-scoped when supplied ------------------------------------------------


def _entry(check: tuple[BoundCapsule, TypedCheck]) -> Entry:
    capsule, record = check
    return Entry(**capsule, agent_input=record)


def test_live_a_typed_check_with_a_profile_block_targets_the_profile_payee():
    entry = _entry(_typed(PAYEE))
    entry["counterparty_profile"] = _profile(PROFILE_PAYEE)
    action = action_for_check_input(entry)
    assert (action.target, action.ignored_inputs) == (f"payee-fp:{PROFILE_ALG}:{PROFILE_PAYEE}", ())


def test_live_a_typed_check_with_a_malformed_profile_block_keeps_its_own_payee():
    entry = _entry(_typed(PAYEE))
    entry["counterparty_profile"] = {"fp_alg": CHAIN_ALG, "ids": {"payee": PROFILE_PAYEE}}
    action = action_for_check_input(entry)
    assert (action.target, action.ignored_inputs) == (f"payee-fp:{CHAIN_ALG}:{PAYEE}", ("counterparty_profile",))


def test_replay_a_typed_check_takes_its_companions_profile_payee():
    check = _typed(PAYEE)
    (decision,) = _replay(check, _companion(check[1], PROFILE_PAYEE))
    fold_key = _constraint(decision, "counterparty_seen_before").evidence["fold_key"]
    assert fold_key["value"] == f"payee-fp:{PROFILE_ALG}:{PROFILE_PAYEE}"


# -- the payee-keyed checks ------------------------------------------------------


def test_replay_counterparty_seen_before_keys_a_typed_check_on_its_payee():
    (decision,) = _replay(_typed(PAYEE))
    out = _constraint(decision, "counterparty_seen_before")
    assert out.result != "n/a"
    assert out.evidence["fold_key"]["value"] == f"payee-fp:{CHAIN_ALG}:{PAYEE}"


def test_replay_a_typed_check_sealing_no_counterparty_fails_naming_target():
    (decision,) = _replay(_typed(None))
    out = _constraint(decision, "counterparty_seen_before")
    assert out.result == "fail"
    assert (out.evidence["seen_before"], out.evidence["missing_field"]) == (False, "target")


def test_replay_a_repeated_identical_typed_payment_is_refused():
    """Both checks are bridged the same way, payee included, so
    dedupe sees the second as the first act again, in the same deal."""
    first, second = _replay(_typed(PAYEE, DAY_1), _typed(PAYEE, DAY_2))
    assert _constraint(first, "dedupe").result == "pass"
    assert (_constraint(second, "dedupe").result, second.outcome) == ("fail", DENY)


def test_replay_identical_typed_payments_to_two_payees_are_two_acts():
    first, second = _replay(_typed(PAYEE, DAY_1), _typed(OTHER_PAYEE, DAY_2))
    assert (_constraint(first, "dedupe").result, _constraint(second, "dedupe").result) == ("pass", "pass")
