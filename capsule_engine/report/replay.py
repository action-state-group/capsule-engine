# SPDX-License-Identifier: Apache-2.0
"""Replay a JSONL capsule ledger through T3's ``GuardEngine`` in dry-run mode.

Every decision here comes from actually running ``GuardEngine.check(...,
dry_run=True)`` over the real fixture/ledger records -- this module never
fabricates an outcome. The guard's own replay-local view (an ephemeral
``LedgerStore``) accumulates the decision capsules this replay itself
produces, so a dedupe hit fires the same way it would in ``test_guard_dry_run.py``:
against a *prior decision in this same replay*, not against the source
ledger's original capsules (which the guard never produced and has no
decision-history relationship to).

``_bridge_deal_check`` builds the action of a capsulectl deal check from the
check's own sealed record, disclosed beside its capsule in a capsulectl deal
bundle: the record names the action's taxonomy class (``action_class``,
``taxonomy_version``) and the amount a spend cap evaluates (``spend_minor``),
and the capsule binds the record by digest
(``model_attestation.compute_attestation.agent_input_digest``). The
counterparty's keyed fingerprints the record seals (``x-deal-v0.counterparty``
beside its body, or a typed record's ``body.counterparty``) are carried as
they are, never a clear value. A record
that does not match that digest is never read.

The action's target is the payee's per-deal fingerprint unless the payee is
also keyed per profile (``hmac-sha256-profile-key``, one value for one merchant
across a profile's deals): live, from ``record.counterparty_profile`` in an
external-check-input/v0 envelope (``action_for_check_input``); in a replay,
from the check's companion, a bound ``counterparty_profile`` record whose one
``about`` ref is the check's record digest. The companion states no act. A
block that is not ``{fp_alg: "hmac-sha256-profile-key", ids: {payee: <64
lowercase hex>}}`` is ignored, named in ``Action.ignored_inputs``, and the
per-deal target stands. A decision sealed with the per-deal target is never
rewritten, so it never matches one keyed per profile.

On a check of a sale's thread, the envelope's top-level ``item_ref`` (256
random bits, lowercase hex, equal across the sale's threads) becomes
``Action.item_ref`` for ``single_commitment``. No deal record carries it, so
it is read only live, never from a deal record in a replay. A value in any
other shape is ignored and named in ``Action.ignored_inputs``. A live check
reads its history's acts through ``action_for_history_entry``
(``report/live_history.py``).

``_bridge_transfer_funds`` is the other non-default action mapping, and it
mirrors the pattern already established by ``tests/test_guard_dry_run.py``
and ``tests/test_guard_eur150k_bridge.py``: ``transfer_funds`` capsules in
these fixtures carry their amount only as free text inside
``model_attestation.compute_attestation.note`` (a field this schema version
has no structured place for), so the bridge regexes the real embedded
values out of that note rather than hardcoding them.
"""
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from ..folds.definition import FoldDefinition
from ..folds.duration import parse_duration_seconds
from ..guards import ALLOW, ESCALATE, Action, GuardDecision, GuardEngine, LocalSigner
from ..guards.checks.caps import EXECUTED_DECISION, SPEND_UNREADABLE, counts_executed_acts
from ..guards.statements import StatementRecord, make_statement_record
from ..guards.wickets.definition import WicketDefinition
from ..packs.install import engine_ask_sets
from ..packs.schema import PackDefinition

__all__ = [
    "MONEY_IN",
    "ExecutedActs",
    "bound_agent_input",
    "SourcedDecision",
    "ReplayResult",
    "load_records",
    "load_disclosed",
    "load_withheld",
    "filter_since",
    "action_for_record",
    "action_for_check_input",
    "action_for_history_entry",
    "replay",
    "statement_for_history_entry",
    "statement_record",
]

_TRANSFER_NOTE_RE = re.compile(r"amount_eur:\s*(\d+).*?target_iban:\s*([A-Z0-9]+)")


def load_records(paths: Sequence[str | Path]) -> list[dict]:
    """Load every record from one or more sources, in the given order.

    Each source is either a plain JSONL ledger file (read in file-internal
    line order), a ``LedgerStore`` directory (read via ``scan()``, its own
    append order) -- the CLI's real-deployment path, since an operator's
    actual local ledger is a store directory, not a loose JSONL file -- or a
    capsule bundle (one JSON object with ``records`` and ``disclosures``, as
    ``capsulectl bundle --deal`` writes), read in its record order.
    """
    records: list[dict] = []
    for path in paths:
        p = Path(path)
        bundle = _bundle(p)
        if bundle is not None:
            records.extend(bundle["records"])
        elif p.is_dir():
            store = LedgerStore(p)
            try:
                for rec in store.scan():
                    records.append(rec.capsule)
            finally:
                store.close()
        else:
            with p.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    return records


def _bundle(path: Path) -> dict | None:
    """``path``'s capsule bundle, or ``None`` when it is not one."""
    if path.is_dir():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if isinstance(value, dict) and isinstance(value.get("records"), list) and isinstance(value.get("disclosures"), dict):
        return value
    return None


def load_disclosed(paths: Sequence[str | Path]) -> dict[str, dict]:
    """The records a capsule bundle discloses beside its capsules, by
    ``capsule_id``: each one's ``agent_input``. Sources that are not bundles
    disclose nothing."""
    disclosed: dict[str, dict] = {}
    for path in paths:
        bundle = _bundle(Path(path))
        if bundle is None:
            continue
        for capsule_id, members in bundle["disclosures"].items():
            agent_input = members.get("agent_input") if isinstance(members, dict) else None
            if isinstance(agent_input, dict):
                disclosed[capsule_id] = agent_input
    return disclosed


def load_withheld(paths: Sequence[str | Path]) -> frozenset[str]:
    """The ``capsule_id`` of every record a capsule bundle among ``paths``
    carries without disclosing its ``agent_input``, and no other source
    discloses. Plain ledgers and store directories withhold nothing."""
    carried: set[str] = set()
    for path in paths:
        bundle = _bundle(Path(path))
        if bundle is not None:
            carried.update(r["capsule_id"] for r in bundle["records"] if isinstance(r.get("capsule_id"), str))
    return frozenset(carried - load_disclosed(paths).keys())


def _parse_ts(ts: str) -> datetime:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    return datetime.fromisoformat(text)


def filter_since(records: list[dict], since: str | None) -> list[dict]:
    """Keep only records within ``since`` (e.g. ``"7d"``) of the *latest*
    timestamp in the set -- anchored to real ledger data, never the system
    wall clock (same determinism principle as the fold engine's own rolling
    windows: ``folds/engine.py`` refuses to invent an anchor from
    ``datetime.now()``)."""
    if since is None:
        return list(records)
    seconds = parse_duration_seconds(since)
    timestamps = [r["timestamp"] for r in records if r.get("timestamp")]
    if not timestamps:
        return list(records)
    anchor = max(_parse_ts(t) for t in timestamps)
    cutoff = anchor - timedelta(seconds=seconds)
    return [r for r in records if r.get("timestamp") and _parse_ts(r["timestamp"]) >= cutoff]


def _bridge_transfer_funds(record: dict) -> Action | None:
    action_id = record.get("action_id") or ""
    verb = action_id.split("/", 1)[0] if action_id else ""
    if verb != "transfer_funds":
        return None
    note = ((record.get("model_attestation") or {}).get("compute_attestation") or {}).get("note", "")
    match = _TRANSFER_NOTE_RE.search(note)
    if not match:
        return None
    amount_eur, target_iban = match.groups()
    return Action(
        verb=verb,
        operator=record.get("operator", ""),
        developer=record.get("developer", ""),
        action_class="money.transfer",
        amount_minor=int(amount_eur) * 100,
        currency="EUR",
        target=target_iban,
        timestamp=record.get("timestamp"),
        action_id=action_id or None,
    )


def _bound(record: dict, disclosed: dict) -> bool:
    """Whether ``disclosed`` is the record ``record``'s capsule sealed."""
    attestation = (record.get("model_attestation") or {}).get("compute_attestation") or {}
    bound = attestation.get("agent_input_digest")
    return isinstance(bound, str) and json_digest(disclosed) == bound


def _checked_body(disclosed: dict, *, history: bool = False) -> tuple[dict, object] | None:
    """The body of a deal check (an ``x-deal-v0`` check record, or a typed
    ``proposed-action/v0``) and the counterparty block it seals, or ``None``
    for any other record. An ``x-deal-v0`` check seals the counterparty in its
    block; capsulectl moves it into a typed record's body, the same
    fingerprints under ``fp_alg`` ``hmac-sha256-chain-key``. With ``history``,
    a typed act record (``action-record/v0``) is read the same way: it is how
    a check input's history states an act carried out."""
    block = disclosed.get("x-deal-v0")
    body = disclosed.get("body")
    if isinstance(block, dict) and block.get("record_type") == "check":
        counterparty = block.get("counterparty")
    elif disclosed.get("type") == "proposed-action/v0" or (history and disclosed.get("type") == _ACT_RECORD_TYPE):
        counterparty = body.get("counterparty") if isinstance(body, dict) else None
    else:
        return None
    return (body, counterparty) if isinstance(body, dict) else None


def _minor(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sealed_counterparty(block: object) -> tuple[dict[str, str] | None, str | None]:
    """The fingerprints (kind -> hex) in a sealed counterparty block
    (``_checked_body``) and their ``fp_alg``, or ``(None, None)`` when it
    holds none usable."""
    if not isinstance(block, dict):
        return None, None
    ids, fp_alg = block.get("ids"), block.get("fp_alg")
    if not isinstance(ids, dict) or not isinstance(fp_alg, str) or not fp_alg:
        return None, None
    usable = {k: v for k, v in ids.items() if isinstance(k, str) and isinstance(v, str) and v}
    return (usable, fp_alg) if usable else (None, None)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _payee_target(counterparty_ids: dict[str, str] | None, fp_alg: str | None) -> str | None:
    """The payee's sealed fingerprint as an opaque target,
    ``payee-fp:<fp_alg>:<hex>``. Prefixed so it never compares equal to a
    clear reference or to a fingerprint made by another algorithm; ``None``
    when the record seals no payee fingerprint."""
    payee = (counterparty_ids or {}).get("payee")
    return f"payee-fp:{fp_alg}:{payee}" if payee and fp_alg else None


# The profile-scoped payee block's name, in the checker input and on the
# companion record, and the fp_alg it must carry.
_PROFILE_INPUT = "counterparty_profile"
_PROFILE_FP_ALG = "hmac-sha256-profile-key"
# The sale's item reference: top level in the checker input for the checked
# record, and on each history entry for that act (AMENDMENT 6a).
_ITEM_INPUT = "item_ref"
# The time of the accepted offer a seller's commit rests on, on the record
# entry beside its capsule (AMENDMENT 7): the offer record's sealed ``at``.
_PROPOSAL_INPUT = "proposal_at"


def _profile_target(block: object) -> str | None:
    """The payee keyed per profile as a target, from a ``counterparty_profile``
    block in its agreed shape, or ``None`` for anything else."""
    if not isinstance(block, dict) or block.get("fp_alg") != _PROFILE_FP_ALG:
        return None
    ids = block.get("ids")
    payee = ids.get("payee") if isinstance(ids, dict) else None
    if not isinstance(payee, str) or not _HEX64.fullmatch(payee):
        return None
    return _payee_target({"payee": payee}, _PROFILE_FP_ALG)


def _typed_ref_digest(value: object) -> str | None:
    """The digest of a typed record reference ``{type, digest_alg, digest}``
    when it is a SHA-256 one, or ``None``."""
    if not isinstance(value, dict) or not _text(value.get("type")) or value.get("digest_alg") != "SHA-256":
        return None
    digest = value.get("digest")
    return digest if isinstance(digest, str) and _HEX64.fullmatch(digest) else None


# The typed record a check input's history carries for each act carried out
# (capsule-cli internal/cli/rules_checker.go, rulesHistory), and the
# ``authority_basis`` entry type that names the task authority it was taken
# under (action_records.go): an act record has no ``task_authority_ref``.
_ACT_RECORD_TYPE = "action-record/v0"
_TASK_AUTHORITY_BASIS = "task_authority"


def _basis_task_authority(body: dict) -> str | None:
    """The digest of the task authority an act record's ``authority_basis``
    names: the ``ref`` of its one entry of type ``task_authority``. ``None``
    when it has none, more than one, or a ref that is not a SHA-256 typed
    reference."""
    basis = body.get("authority_basis")
    entries = [e for e in basis if isinstance(e, dict) and e.get("type") == _TASK_AUTHORITY_BASIS] if isinstance(
        basis, list) else []
    return _typed_ref_digest(entries[0].get("ref")) if len(entries) == 1 else None


# The ``direction`` a deal check states when the money moves to the user: a
# refund, or a partial cancel. Such an act is never spend: the bridge carries
# its spend as ``0`` whatever ``spend_minor`` says, so it adds nothing to the
# rolling total and cannot raise it past a cap. So an act stated in this
# direction is never a reason the spend window cannot be computed, decided or not.
MONEY_IN = "in"


# The body fields that state the amount a money-in record returns, first
# present wins: the producer's explicit ``returned_minor``, then a refund's
# ``amount_minor``, then a partial cancel's ``cancelled_amount_minor``.
_RETURNED_AMOUNT_FIELDS = ("returned_minor", "amount_minor", "cancelled_amount_minor")


def _returned_minor(body: dict) -> int | None:
    """The amount a money-in check returns (``_RETURNED_AMOUNT_FIELDS``)."""
    return next((v for v in (_minor(body.get(f)) for f in _RETURNED_AMOUNT_FIELDS) if v is not None), None)


# Where a deal record names its deal: an ``x-deal-v0`` record in its block's
# ``deal_id``, a typed record in its top-level ``chain_id``, which capsulectl
# sets to exactly that value. Read only through ``_deal_id``.
_DEAL_ID_FIELDS = ("x-deal-v0.deal_id", "chain_id")


@dataclass(frozen=True)
class _DealId:
    """The deal a record names, or ``None``; ``conflict`` when it names two."""

    value: str | None
    conflict: bool = False


def _deal_id(shown: dict) -> _DealId:
    """The deal ``shown`` is in: ``x-deal-v0.deal_id`` when the record states
    one, else its typed ``chain_id``. A record stating both, and not the same
    value, names no deal and is a conflict: neither is picked. A stated value
    that is not a non-empty string names no deal."""
    block = shown.get("x-deal-v0")
    stated = isinstance(block, dict) and "deal_id" in block
    chained = "chain_id" in shown
    if stated and chained and block["deal_id"] != shown["chain_id"]:
        return _DealId(value=None, conflict=True)
    return _DealId(value=_text(block["deal_id"]) if stated else _text(shown.get("chain_id")))


def _bridge_deal_check(
    record: dict, disclosed: dict | None, counterparty_profile: object = None, *, history: bool = False
) -> Action | None:
    """The proposed action a capsulectl deal check states, from its own
    sealed record: the class it names, and the amount a spend cap evaluates,
    which is ``spend_minor`` only: never ``amount_minor`` (on a cancel it is
    a refund) or ``cancelled_amount_minor``, and ``0`` for a record that says
    the money moved in. So a cancel is never spend. The amount the body
    states (``amount_minor``) is carried apart, as ``stated_amount_minor``, for
    ``price_floor``: on a sale it is the price, while the spend is ``0``.
    ``spend_authorized_minor``,
    the authorised maximum sealed beside it, is carried for a per-action cap
    and dropped when the money moved in. When it moved in, the amount
    returned (``_returned_minor``) and the SHA-256 digest of the reversed
    act's record (the body's typed ``reverses_ref``) are carried for
    ``dedupe`` beside the spend of ``0``, and never otherwise.
    Only a check is an action here: the step that acts on it is the same
    payment, and counting both would count it twice.
    The rail and ``refundable`` come from the body's ``recourse`` block, where
    the producer writes them (a top-level ``rail`` is read when there is no
    recourse rail). The target is the payee's sealed fingerprint
    (``_payee_target``) from the counterparty block the check seals
    (``_checked_body``), typed or not, so a check sealing none has no target;
    or ``counterparty_profile``'s when it is given in
    its agreed shape (``_profile_target``); given in any other, it is named
    in ``ignored_inputs``. The remaining body fields are carried only in the
    shape the action takes: a string, a boolean, an integer, or the SHA-256
    digest of a typed reference, and dropped otherwise.
    ``taxonomy_version`` makes it an act stated against a pinned taxonomy, so
    ``dedupe`` keys it on the act, never on this record's type or id. The
    deal is the one the record names (``_deal_id``), never the ``action_id``
    prefix. A record naming two deals carries none, and names both fields in
    ``deal_id_conflict``, so the engine refuses whatever would be allowed.
    With ``history``, a typed act record is bridged too (``_checked_body``),
    and its task authority is the one its ``authority_basis`` names
    (``_basis_task_authority``)."""
    if disclosed is None or not _bound(record, disclosed):
        return None
    checked = _checked_body(disclosed, history=history)
    if checked is None:
        return None
    body, sealed_counterparty = checked
    if not body.get("action_class") or not body.get("taxonomy_version"):
        return None
    spend = body.get("spend_minor")
    authorized = body.get("spend_authorized_minor")
    returned = reverses = None
    if body.get("direction") == MONEY_IN:
        spend = 0
        authorized = None
        returned, reverses = _returned_minor(body), _typed_ref_digest(body.get("reverses_ref"))
    deal = _deal_id(disclosed)
    counterparty_ids, fp_alg = _sealed_counterparty(sealed_counterparty)
    target, ignored = _payee_target(counterparty_ids, fp_alg), ()
    if counterparty_profile is not None:
        profile_target = _profile_target(counterparty_profile)
        if profile_target is None:
            ignored = (_PROFILE_INPUT,)
        else:
            target = profile_target
    recourse = body.get("recourse") if isinstance(body.get("recourse"), dict) else {}
    return Action(
        verb=str(body.get("action") or "unknown"),
        operator=record.get("operator", ""),
        developer=record.get("developer", ""),
        action_class=body["action_class"],
        action_id=record.get("action_id") or None,
        action_type=record.get("action_type", "decide"),
        timestamp=record.get("timestamp"),
        amount_minor=_minor(spend),
        spend_authorized_minor=_minor(authorized),
        stated_amount_minor=_minor(body.get("amount_minor")),
        currency=body.get("currency"),
        rail=_text(recourse.get("rail")) or _text(body.get("rail")),
        target=target,
        taxonomy_version=body["taxonomy_version"],
        counterparty_ids=counterparty_ids,
        counterparty_fp_alg=fp_alg,
        refundable=_flag(recourse.get("refundable")),
        recipient_role=_text(body.get("recipient_role")),
        channel=_text(body.get("channel")),
        first_contact_channel=_text(body.get("first_contact_channel")),
        upfront_amount_minor=_minor(body.get("upfront_amount_minor")),
        material_fields_changed=_minor(body.get("material_fields_changed")),
        material_fields_basis=_text(body.get("material_fields_basis")),
        offer_fields_changed=_minor(body.get("offer_fields_changed")),
        offer_fields_basis=_text(body.get("offer_fields_basis")),
        task_authority_ref=_basis_task_authority(body) if history else _typed_ref_digest(body.get("task_authority_ref")),
        returned_minor=returned,
        reverses_ref=reverses,
        deal_id=deal.value,
        deal_id_conflict=_DEAL_ID_FIELDS if deal.conflict else (),
        ignored_inputs=ignored,
    )


# The ``x-deal-v0`` record type of a statement in a deal (capsule-cli
# internal/cli/deal_claims.go), and the ``source_kind`` of one the user's
# agent made to the counterparty.
_CLAIM = "claim"
_AGENT = "agent"


def statement_record(record: dict, disclosed: dict | None) -> StatementRecord | None:
    """The record of the statement ``record`` makes to the counterparty
    (``guards/statements.py``), or ``None`` when it makes none: it must be a
    bound ``x-deal-v0`` ``claim`` whose body seals ``source_kind``
    ``agent`` and a non-empty ``class``, in the one deal it names
    (``_deal_id``; a record naming two names none). A disclosure record's
    ``body.fields[].class`` is never read: that is the user's personal data
    given out, not a statement about the deal. The same reader serves the
    replay and a live check's history."""
    if disclosed is None or not _bound(record, disclosed):
        return None
    block = disclosed.get("x-deal-v0")
    body = disclosed.get("body")
    if not isinstance(block, dict) or block.get("record_type") != _CLAIM or not isinstance(body, dict):
        return None
    sealed_class = _text(body.get("class"))
    deal = _deal_id(disclosed).value
    capsule_id = _text(record.get("capsule_id"))
    if body.get("source_kind") != _AGENT or sealed_class is None or deal is None or capsule_id is None:
        return None
    return make_statement_record(
        operator=str(record.get("operator", "")), timestamp=_text(record.get("timestamp")),
        sealed_class=sealed_class, source_kind=_AGENT, deal_id=deal, claim=capsule_id,
    )


# A capsulectl deal report's sealed ``type``: what the user asked and what was
# done, for one audience. It states no act.
_REPORT_TYPE = "deal_report"


def _gets_no_decision(record: dict, disclosed: dict | None, withheld: frozenset[str]) -> bool:
    """Whether the replay skips ``record`` before the engine sees it: a bound
    deal record other than a check (``_states_no_act``), a bound deal report,
    or a record its bundle carries but withholds (``load_withheld``), so
    nothing it states can be read. No rule fires on any of them, the gate
    included. They stay in the ledger and its completeness proof."""
    if disclosed is None:
        return record.get("capsule_id") in withheld
    if _bound(record, disclosed) and disclosed.get("type") == _REPORT_TYPE:
        return True
    return _states_no_act(record, disclosed)


# The typed records (capsule-cli internal/cli/action_records.go) that state no
# act: the task authority, an evaluation, an approval (the user's, or a
# counterparty's acceptance), the action taken, which carries out the act its
# proposed action already stated, its outcome and a report. Only a
# ``proposed-action/v0`` is a check. A type not named here still gets a
# decision.
_TYPED_NO_ACT = frozenset({
    "task-authority/v0", "action-evaluation/v0", "action-approval/v0", "action-record/v0", "action-outcome/v0",
    "action-report/v0",
})


def _states_no_act(record: dict, disclosed: dict | None) -> bool:
    """Whether ``record`` is a deal record other than a check, read from the
    ``x-deal-v0.record_type`` its capsule sealed: a baseline, verdict,
    approval or intent states no act, and the action step carries out the
    act its check already stated. A typed record, which has no ``x-deal-v0``
    block, is read from its sealed ``type`` (``_TYPED_NO_ACT``). Never read
    from ``action_id`` or ``action_type``, and never from a record the
    capsule does not bind."""
    if disclosed is None or not _bound(record, disclosed):
        return False
    block = disclosed.get("x-deal-v0")
    if not isinstance(block, dict):
        return disclosed.get("type") in _TYPED_NO_ACT
    record_type = block.get("record_type")
    return isinstance(record_type, str) and record_type != "check"


def action_for_record(record: dict, disclosed: dict | None = None, *, counterparty_profile: object = None) -> Action:
    """The action ``record`` states. ``disclosed`` is the record its capsule
    sealed, when a bundle disclosed it (``load_disclosed``).
    ``counterparty_profile`` is the payee keyed per profile for a deal check,
    from the checker input or the check's companion (module docstring)."""
    bridged = _bridge_deal_check(record, disclosed, counterparty_profile) or _bridge_transfer_funds(record)
    if bridged is not None:
        return bridged
    if _states_no_act(record, disclosed):
        return replace(Action.from_capsule(record), states_act=False)
    return Action.from_capsule(record)


def action_for_check_input(entry: dict, *, item_ref: object = None) -> Action:
    """The action an entry of an external-check-input/v0 envelope states, the
    ``record`` entry or one of ``history``: the sealed capsule, its disclosed
    ``agent_input``, and, beside them, what capsulectl computes and passes
    and the capsule does not seal: ``counterparty_profile``, ``proposal_at``
    (AMENDMENT 7) and, on a history entry, ``item_ref`` (AMENDMENT 6a).
    ``item_ref`` is the envelope's top-level ``item_ref`` for the record
    entry; when it is not passed, the entry's own is read. Either is set on
    the action when it is 64 lowercase hex and named in ``ignored_inputs``
    otherwise. ``proposal_at`` is set when it is a string (``offer_expiry``
    reads and judges it) and named in ``ignored_inputs`` otherwise. The
    envelope's other top-level members, ``party_role`` among them (a checker
    may read it to pick a pack), are not read here: an input carrying one is
    decided as one without it. The act a history entry records is read by
    ``action_for_history_entry``; here a history entry's typed act record
    states no act."""
    capsule, agent_input = _entry_parts(entry)
    action = action_for_record(capsule, agent_input, counterparty_profile=entry.get(_PROFILE_INPUT))
    return _with_inputs(action, entry, item_ref)


def action_for_history_entry(entry: dict) -> Action | None:
    """The act a ``history`` entry of an external-check-input/v0 envelope
    records: its sealed typed act record (``action-record/v0``), bridged as a
    deal check is (``_bridge_deal_check``), with the task authority its
    ``authority_basis`` names and the inputs beside it read as
    ``action_for_check_input`` reads them (the entry's own ``item_ref``).
    ``None`` when the entry is not a typed act record its capsule binds, or
    names no class or taxonomy: what it records cannot be read."""
    capsule, agent_input = _entry_parts(entry)
    action = _bridge_deal_check(capsule, agent_input, entry.get(_PROFILE_INPUT), history=True)
    return _with_inputs(action, entry, None) if action is not None else None


def bound_agent_input(entry: dict) -> dict | None:
    """An envelope entry's disclosed ``agent_input`` when its capsule binds
    it, else ``None``."""
    capsule, agent_input = _entry_parts(entry)
    return agent_input if agent_input is not None and _bound(capsule, agent_input) else None


def statement_for_history_entry(entry: dict) -> StatementRecord | None:
    """The statement a ``history`` entry of an external-check-input/v0
    envelope records (``statement_record``), read from its sealed capsule and
    disclosed ``agent_input``; ``None`` when it records none."""
    capsule, agent_input = _entry_parts(entry)
    return statement_record(capsule, agent_input)


def _entry_parts(entry: dict) -> tuple[dict, dict | None]:
    """An envelope entry's capsule, without the members capsulectl passes
    beside it, and its disclosed ``agent_input``."""
    capsule = {
        k: v for k, v in entry.items() if k not in ("agent_input", _PROFILE_INPUT, _PROPOSAL_INPUT, _ITEM_INPUT)
    }
    agent_input = entry.get("agent_input")
    return capsule, agent_input if isinstance(agent_input, dict) else None


def _with_inputs(action: Action, entry: dict, item_ref: object) -> Action:
    """``action`` with the ``item_ref`` and ``proposal_at`` passed beside
    ``entry`` (``action_for_check_input``)."""
    ignored = list(action.ignored_inputs)
    if item_ref is None:
        item_ref = entry.get(_ITEM_INPUT)
    if item_ref is not None:
        if isinstance(item_ref, str) and _HEX64.fullmatch(item_ref):
            action = replace(action, item_ref=item_ref)
        else:
            ignored.append(_ITEM_INPUT)
    proposal_at = entry.get(_PROPOSAL_INPUT)
    if proposal_at is not None:
        if isinstance(proposal_at, str):
            action = replace(action, proposal_at=proposal_at)
        else:
            ignored.append(_PROPOSAL_INPUT)
    return replace(action, ignored_inputs=tuple(ignored))


def _companion_profiles(records: list[dict], disclosed: dict[str, dict]) -> dict[str, object]:
    """Each check's ``counterparty_profile`` block, by the check's record
    digest, from the bound companions among ``records``: a
    ``counterparty_profile`` record with exactly one ref, ``rel`` ``about``,
    type ``deal-record``. A check two companions name gets their blocks as a
    list, which is no block, so it is ignored; a companion without one gets
    ``{}``, ignored the same way."""
    found: dict[str, list[object]] = {}
    for record in records:
        shown = disclosed.get(record.get("capsule_id", ""))
        if shown is None or not _bound(record, shown):
            continue
        block = shown.get("x-deal-v0")
        if not isinstance(block, dict) or block.get("record_type") != _PROFILE_INPUT:
            continue
        refs = block.get("refs")
        if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], dict):
            continue
        (ref,) = refs
        digest = _typed_ref_digest(ref)
        if ref.get("rel") != "about" or ref.get("type") != "deal-record" or digest is None:
            continue
        found.setdefault(digest, []).append(block.get(_PROFILE_INPUT) or {})
    return {digest: blocks[0] if len(blocks) == 1 else blocks for digest, blocks in found.items()}


def _deal_records(records: list[dict], disclosed: dict[str, dict]) -> dict[str, dict]:
    """Each bound ``x-deal-v0`` record among ``records``, by its digest."""
    found: dict[str, dict] = {}
    for record in records:
        shown = disclosed.get(record.get("capsule_id", ""))
        if shown is not None and isinstance(shown.get("x-deal-v0"), dict) and _bound(record, shown):
            found[json_digest(shown)] = shown
    return found


# How a typed deal states a seller's offer, the buyer's acceptance of it and
# the seller's commit (capsule-cli action_records.go).
_PROPOSED_TYPE = "proposed-action/v0"
_APPROVAL_TYPE = "action-approval/v0"
_ACCEPTANCE_AUTHORITY = "counterparty_acceptance"
_OFFER, _COMMIT = "offer", "commit"


def _changes_details(block: dict) -> bool:
    """Whether the ``x-deal-v0`` record ``block`` changes the deal's details,
    so an earlier acceptance no longer stands: a change, or a message or
    evidence that names the counterparty (capsule-cli deal_rules.go,
    changesDetails)."""
    record_type = block.get("record_type")
    return record_type == "change" or (record_type in ("message", "evidence") and "counterparty" in block)


def _accepted_offer_at(records: list[dict], disclosed: dict[str, dict]) -> dict[str, str]:
    """The proposal each bound typed commit rests on, as the ``at`` that
    offer's record seals, by the commit's record digest (the value capsulectl
    sends live as ``record.proposal_at``; its capsule's ``timestamp`` is the
    same instant, from one clock read, but is not read here):
    ``offer_expiry`` reads it as ``Action.proposal_at``. It is the deal's
    latest offer, when a bound acceptance (an ``action-approval/v0`` of
    authority ``counterparty_acceptance``) names that offer's record by
    ``proposed_action_ref`` and nothing after it changed the details
    (``_changes_details``), as capsule-cli's offerAccepted reads it. A later
    offer supersedes an earlier one and its acceptance. Every record is read
    within the deal it names (``_deal_id``), in ledger order. A commit with
    no such offer is left out, so its proposal's age stays unestablished."""
    latest: dict[str, tuple[str, str | None]] = {}
    accepted: dict[str, str | None] = {}
    found: dict[str, str] = {}
    for record in records:
        shown = disclosed.get(record.get("capsule_id", ""))
        if shown is None or not _bound(record, shown):
            continue
        deal = _deal_id(shown).value
        if deal is None:
            continue
        body = shown.get("body") if isinstance(shown.get("body"), dict) else {}
        block = shown.get("x-deal-v0")
        if shown.get("type") == _PROPOSED_TYPE and body.get("action") == _OFFER:
            latest[deal] = (json_digest(shown), _text(shown.get("at")))
            accepted.pop(deal, None)
        elif shown.get("type") == _PROPOSED_TYPE and body.get("action") == _COMMIT:
            at = accepted.get(deal)
            if at is not None:
                found[json_digest(shown)] = at
        elif shown.get("type") == _APPROVAL_TYPE and body.get("authority") == _ACCEPTANCE_AUTHORITY:
            offer = latest.get(deal)
            if offer is not None and _typed_ref_digest(body.get("proposed_action_ref")) == offer[0]:
                accepted[deal] = offer[1]
        elif isinstance(block, dict) and _changes_details(block):
            accepted.pop(deal, None)
    return found


def _record_type(shown: dict) -> str | None:
    record_type = shown["x-deal-v0"].get("record_type")
    return record_type if isinstance(record_type, str) else None


def _only_ref(shown: dict, rel: str) -> str | None:
    """The digest of the record ``shown``'s one ``rel`` ref names, when it has
    exactly one and it is a SHA-256 ``deal-record`` ref; else ``None``."""
    refs = shown["x-deal-v0"].get("refs")
    found = [r for r in refs if isinstance(r, dict) and r.get("rel") == rel] if isinstance(refs, list) else []
    if len(found) != 1 or found[0].get("type") != "deal-record":
        return None
    return _typed_ref_digest(found[0])


# The sealed approvers whose approval carries an act out: the user, or the
# user's standing intent. Any other (``agent_card``: a click on a card the
# agent composed) certifies no one's consent, so the act stays unseen. This is
# the rule capsulectl's deal disposition applies live (capsule-cli
# internal/cli/deal.go, dealDisposition), so replay never counts as seen a
# counterparty the live path would not.
_CONSENTING_APPROVERS = frozenset({"user", "standing_intent"})


@dataclass(frozen=True)
class _CarriedOut:
    """An executed action step's chain back to its check, as digests."""

    check: str
    approval: str
    action: str


def _carried_out(action_digest: str, deal: dict[str, dict]) -> _CarriedOut | None:
    """The chain by which the bound action step ``action_digest`` carried out
    a check, read from sealed records only: its one ``authorized_by`` ref
    names an approval whose body seals ``proceed: true`` and an approver in
    ``_CONSENTING_APPROVERS``, and that approval's one ``approves`` ref names
    the check, or the verdict whose one ``checks`` ref names it. Every record
    in the chain names the action's deal (``_deal_id``). ``None`` when any
    link is missing, unbound, declined, approved by no one who consents for
    the user, of another kind, or names two deals."""
    action = deal.get(action_digest)
    if action is None or _record_type(action) != "action":
        return None
    deal_id = _deal_id(action)
    if deal_id.conflict:
        return None
    approval_digest = _only_ref(action, "authorized_by")
    approval = deal.get(approval_digest) if approval_digest is not None else None
    if approval_digest is None or approval is None or _record_type(approval) != "approval":
        return None
    body = approval.get("body")
    if _deal_id(approval) != deal_id or not isinstance(body, dict) or body.get("proceed") is not True:
        return None
    if _text(body.get("approver")) not in _CONSENTING_APPROVERS:
        return None
    check_digest = _only_ref(approval, "approves")
    approved = deal.get(check_digest) if check_digest is not None else None
    if approved is not None and _record_type(approved) == "verdict":
        check_digest = _only_ref(approved, "checks")
        approved = deal.get(check_digest) if check_digest is not None else None
    if check_digest is None or approved is None or _record_type(approved) != "check":
        return None
    if _deal_id(approved) != deal_id:
        return None
    return _CarriedOut(check=check_digest, approval=approval_digest, action=action_digest)


# The ``disposition.decision`` of the record a replay writes to its own view
# for an act carried out (counterparty.seen_before/3.0.0 counts it). No guard
# decision carries it, so no fold that reads accepted decisions counts it.
_CARRIED_OUT = "carried_out"


class _CarriedOutCitation(TypedDict):
    decision: str
    check: str
    approval: str
    action: str


class _CarriedOutPayload(TypedDict):
    target: str | None
    carried_out: _CarriedOutCitation


class _Disposition(TypedDict):
    decision: str


class _CarriedOutBody(TypedDict):
    operator: str
    timestamp: str | None
    disposition: _Disposition
    asg_payload: _CarriedOutPayload


class _CarriedOutRecord(_CarriedOutBody):
    capsule_id: str


def _carried_out_record(sourced: SourcedDecision, chain: _CarriedOut, at: str | None) -> _CarriedOutRecord:
    """The record of ``sourced``'s act as carried out, citing its decision and
    the sealed chain by digest; its ``capsule_id`` is the digest of the rest.
    It is written to the replay's own view only."""
    citation = _CarriedOutCitation(
        decision=sourced.decision.capsule["capsule_id"], check=chain.check, approval=chain.approval, action=chain.action
    )
    body = _CarriedOutBody(
        operator=sourced.action.operator,
        timestamp=at,
        disposition=_Disposition(decision=_CARRIED_OUT),
        asg_payload=_CarriedOutPayload(target=sourced.action.target, carried_out=citation),
    )
    return _CarriedOutRecord(**body, capsule_id=json_digest(body))


# How a deal seals an act it executed: an ``x-deal-v0`` action step, or an
# outcome stating an act taken without a check (``body.unchecked``); a typed
# deal seals them as an ``action-record/v0`` and an ``action-outcome/v0``
# (``body.attempted``) (capsule-cli deal_profile.go, action_records.go). A
# typed action record of a disclosure (``body.disclosed``) moves no money.
_ACTION_STEP, _OUTCOME_STEP = "action", "outcome"
_UNCHECKED, _ATTEMPTED, _DISCLOSED = "unchecked", "attempted", "disclosed"
_ACCEPT = "accept"


@dataclass(frozen=True)
class _ExecutedAct:
    """An act a deal executed, as its record seals it: the spend it took
    (``_executed_spend``), ``None`` when that cannot be read, and its class."""

    spend_minor: int | None
    action_class: str | None


def _taken(shown: dict, *, every_record_is_an_act: bool) -> tuple[dict, bool] | None:
    """The body stating the act the bound record ``shown`` records as
    executed, and whether it was taken without a check; ``None`` for a
    record that states no executed act. A check input's history holds acts
    only (``every_record_is_an_act``), so any other record there is read as
    a checked act's body."""
    body = shown.get("body")
    if not isinstance(body, dict):
        return None
    block = shown.get("x-deal-v0")
    record_type = block.get("record_type") if isinstance(block, dict) else shown.get("type")
    if record_type in (_OUTCOME_STEP, "action-outcome/v0") or _UNCHECKED in body or _ATTEMPTED in body:
        taken = body.get(_UNCHECKED, body.get(_ATTEMPTED))
        return (taken, True) if isinstance(taken, dict) else None
    if record_type in (_ACTION_STEP, _ACT_RECORD_TYPE) or every_record_is_an_act:
        return body, False
    return None


def _executed_spend(body: dict, *, unchecked: bool, accepted: bool) -> int | None:
    """The spend the act ``body`` states took, or ``None`` when it cannot be
    read. Money moved in (``MONEY_IN``) is ``0``, decided or not, and a
    disclosure moves none. An accepted act is counted at its
    ``amount_minor``, as the fold counts an accepted decision. An act taken
    without a check is sealed before its spend is classed, so its
    ``amount_minor`` stands in for a missing ``spend_minor``. Any other act
    is counted at its ``spend_minor``, which must be an integer of at least
    0."""
    if body.get("direction") == MONEY_IN or _DISCLOSED in body:
        return 0
    if accepted:
        return _minor(body.get("amount_minor"))
    spend = _minor(body.get("spend_minor"))
    if unchecked:
        return spend if spend is not None else _minor(body.get("amount_minor"))
    return spend if spend is not None and spend >= 0 else None


def _executed_act(capsule: dict, shown: dict | None, *, every_record_is_an_act: bool = False) -> _ExecutedAct | None:
    """The act the record ``shown``, bound by ``capsule``, states was
    executed (``_taken``), or ``None`` for one that states none. An act whose
    record is not given cannot be read: its spend is ``None``."""
    if shown is None:
        return _ExecutedAct(spend_minor=None, action_class=None)
    taken = _taken(shown, every_record_is_an_act=every_record_is_an_act)
    if taken is None:
        return None
    body, unchecked = taken
    spend = _executed_spend(body, unchecked=unchecked, accepted=_sealed_accept(capsule))
    return _ExecutedAct(spend_minor=spend, action_class=_text(body.get("action_class")))


def _sealed_accept(capsule: dict) -> bool:
    disposition = capsule.get("disposition")
    return isinstance(disposition, dict) and disposition.get("decision") == _ACCEPT


def _act_ids(capsule: dict) -> frozenset[str]:
    """The identities an act's capsule carries: its ``capsule_id`` and
    ``action_id``."""
    return frozenset(v for v in (capsule.get("capsule_id"), capsule.get("action_id")) if isinstance(v, str) and v)


class ExecutedActs:
    """Each executed act counted once in the spend window. An accepted act
    always counts. Any other act that shares a ``capsule_id`` or an
    ``action_id`` with an accepted one, or with an act already counted, is the
    same act and adds nothing. ``capsules`` are every act's capsule the
    window may hold, so the accepted ones are known whatever their order."""

    def __init__(self, capsules: Sequence[dict]) -> None:
        self._accepted = frozenset().union(*(_act_ids(c) for c in capsules if _sealed_accept(c)))
        self._counted: set[str] = set()

    def record(self, capsule: dict, shown: dict | None, act_digest: str, *,
               every_record_is_an_act: bool = False) -> _ExecutedRecord | None:
        """The spend window's record of the act ``capsule`` binds
        (``_executed_record``), or ``None`` when it states no executed act
        or was counted already."""
        act = _executed_act(capsule, shown, every_record_is_an_act=every_record_is_an_act)
        if act is None:
            return None
        ids = _act_ids(capsule)
        if not _sealed_accept(capsule):
            if ids & (self._accepted | self._counted):
                return None
            self._counted |= ids
        return _executed_record(capsule, act, act_digest)


class _ExecutedCitation(TypedDict):
    act: str


class _ExecutedPayload(TypedDict, total=False):
    amount_minor: int
    action_class: str
    spend_unreadable: bool
    executed: _ExecutedCitation


class _ExecutedBody(TypedDict):
    operator: str | None
    timestamp: str | None
    disposition: _Disposition
    asg_payload: _ExecutedPayload


class _ExecutedRecord(_ExecutedBody):
    capsule_id: str


def _executed_record(capsule: dict, act: _ExecutedAct, act_digest: str) -> _ExecutedRecord:
    """The record of an executed act for the spend window
    (``spend.weekly/3.1.0``), under its capsule's operator and time, citing
    the act's record by digest; its ``capsule_id`` is the digest of the rest.
    It carries the act's spend as ``amount_minor``, or ``spend_unreadable``
    and no amount when the spend cannot be read (``_executed_spend``), so
    the window counts the act or is known not to. It is never a decision
    and names no target, so it is never seen, and never the earlier act a
    repeat duplicates."""
    payload = _ExecutedPayload(executed=_ExecutedCitation(act=act_digest))
    if act.spend_minor is None:
        payload[SPEND_UNREADABLE] = True
    else:
        payload["amount_minor"] = act.spend_minor
    if act.action_class is not None:
        payload["action_class"] = act.action_class
    body = _ExecutedBody(
        operator=_text(capsule.get("operator")),
        timestamp=_text(capsule.get("timestamp")),
        disposition=_Disposition(decision=EXECUTED_DECISION),
        asg_payload=payload,
    )
    return _ExecutedRecord(**body, capsule_id=json_digest(body))


@dataclass(frozen=True)
class SourcedDecision:
    """One replayed record, its resulting ``Action``, its real
    ``GuardDecision``, and (when a check matched a prior record) that prior
    record's full capsule -- fetched from the replay's own ephemeral store
    while it is still open, so the report can carry it for re-verification."""

    record: dict
    action: Action
    decision: GuardDecision
    cited_capsule: dict | None


@dataclass(frozen=True)
class ReplayResult:
    """``decisions`` are the records that state an act, in order;
    ``undecided`` the records the replay gave no decision
    (``_gets_no_decision``). ``record_range`` spans both."""

    decisions: tuple[SourcedDecision, ...]
    record_range: tuple[int, int]
    undecided: tuple[dict, ...] = ()


def replay(
    records: list[dict],
    *,
    caps_fold: FoldDefinition | None,
    caps_minor: dict[str, int] | None = None,
    manifest_digest: str | None = None,
    per_action_minor: dict[str, int] | None = None,
    per_action_reads: str | None = None,
    disclosed: dict[str, dict] | None = None,
    withheld: frozenset[str] = frozenset(),
    wickets: tuple[WicketDefinition, ...] = (),
    pack: PackDefinition | None = None,
) -> ReplayResult:
    """Feed every record through a fresh ``GuardEngine`` in dry-run mode, in
    order. Never blocks (``dry_run=True``) -- see ``engine.py``'s own
    guarantee that a dry-run decision still produces and appends a real,
    signed capsule. ``record_range`` on the result is this replayed set's own
    1-based position range (i.e. positions within the ``--since``-filtered
    window actually replayed, not the source ledger's absolute positions).
    ``wickets`` are configured checks run on every decision, as
    ``GuardEngine(wickets=...)`` runs them; none by default. ``pack`` is the
    pack those wickets were installed from: given, the engine asks an approver
    on the same failures ``packs.build_engine`` does (``engine_ask_sets``);
    without it every gate or wicket failure refuses.
    ``withheld`` are the records a bundle carries without disclosing
    (``load_withheld``); like every record that states no act, each gets no
    decision.
    When a deal's sealed records show that an act whose check this replay
    decided (allowed, or asked) was approved to proceed, by the user or under
    the user's standing intent, and then executed (``_carried_out``), the replay writes that act to its own view as
    carried out (``_carried_out_record``), once, when it reaches the action
    step, so a later check counts it as an earlier act with that
    counterparty. A refused check is never counted.
    Under a caps fold that counts executed acts (``spend.weekly/3.1.0``),
    every act a deal executed is also written to the view when the replay
    reaches it (``_executed_record``), whoever approved it and whether or not
    it was checked, so a later check's spend window counts it once. The
    replay's own decisions are dry runs and never count, so no act counts
    twice. Under any other fold nothing more is written, and a replay keeps
    the bytes it had.
    A claim the user's agent made to the counterparty is written to that view
    as a statement (``statement_record``) when the replay reaches it, so a
    later check in the same deal finds it made first.
    A seller's typed commit is decided with ``proposal_at`` the sealed time
    of the accepted offer it rests on (``_accepted_offer_at``), so
    ``offer_expiry`` reads that offer's age.
    Each record is evaluated under the action taxonomy it was sealed with,
    where the engine carries that version, so history at a carried version is
    never refused for its version. A record naming one it does not carry
    still gets a decision: its class-keyed checks are held ``n/a`` with
    evidence naming both versions, and when nothing else fails it is refused
    (``GuardEngine``), so no later check counts it as seen or as spend."""
    if not records:
        return ReplayResult(decisions=(), record_range=(0, -1))

    signer = LocalSigner(key_id="dry-run-report", secret=b"asg-guard-dry-run-report")

    gate_selectors, ask_checks = engine_ask_sets(pack, wickets) if pack is not None else (frozenset(), frozenset())
    sourced: list[SourcedDecision] = []
    undecided: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(tmp) as store:
        engine = GuardEngine(
            ledger=store,
            caps_fold=caps_fold,
            signer_provider=lambda: signer,
            caps_minor=caps_minor or {},
            per_action_minor=per_action_minor,
            per_action_reads=per_action_reads,
            manifest_digest=manifest_digest,
            wickets=wickets,
            ask_gate_selectors=gate_selectors,
            ask_wickets=ask_checks,
            evaluate_under_record_taxonomy=True,
        )
        profiles = _companion_profiles(records, disclosed or {})
        deal = _deal_records(records, disclosed or {})
        offered_at = _accepted_offer_at(records, disclosed or {})
        decided_checks: dict[str, SourcedDecision] = {}
        counts_spend = counts_executed_acts(caps_fold)
        executed_acts = ExecutedActs(records)
        for record in records:
            shown = (disclosed or {}).get(record.get("capsule_id", ""))
            digest = json_digest(shown) if shown is not None else None
            if _gets_no_decision(record, shown, withheld):
                undecided.append(record)
                stated = statement_record(record, shown)
                if stated is not None:
                    store.append(dict(stated), consequential=False)
                chain = _carried_out(digest, deal) if digest is not None else None
                checked = decided_checks.pop(chain.check, None) if chain is not None else None
                if chain is not None and checked is not None and checked.decision.outcome in (ALLOW, ESCALATE):
                    store.append(dict(_carried_out_record(checked, chain, _text(record.get("timestamp")))), consequential=False)
                if counts_spend and digest is not None and _bound(record, shown):
                    executed = executed_acts.record(record, shown, digest)
                    if executed is not None:
                        store.append(dict(executed), consequential=False)
                continue
            profile = profiles.get(digest) if profiles and digest is not None else None
            action = action_for_record(record, shown, counterparty_profile=profile)
            if digest in offered_at:
                action = replace(action, proposal_at=offered_at[digest])
            decision = engine.check(action, dry_run=True)
            cited_capsule = None
            for constraint in decision.constraints:
                cited_id = (constraint.evidence or {}).get("matched_capsule_id") or (
                    constraint.evidence or {}
                ).get("cited_capsule_id")
                if cited_id:
                    found = store.fetch(cited_id)
                    if found is not None:
                        cited_capsule = found.capsule
                        break
            sourced.append(SourcedDecision(record=record, action=action, decision=decision, cited_capsule=cited_capsule))
            if digest in deal and decision.capsule is not None:
                decided_checks[digest] = sourced[-1]

    return ReplayResult(decisions=tuple(sourced), record_range=(1, len(records)), undecided=tuple(undecided))
