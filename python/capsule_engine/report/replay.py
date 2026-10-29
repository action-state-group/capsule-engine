# SPDX-License-Identifier: Apache-2.0
"""Replay a JSONL capsule ledger through T3's ``GuardEngine`` in dry-run mode.

Every decision here comes from actually running ``GuardEngine.check(...,
dry_run=True)`` over the real fixture/ledger records -- this module never
fabricates an outcome. The guard's own replay-local view (an ephemeral
``MemoryHistory``) accumulates the decision capsules this replay itself
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

An act record seals no counterparty, so a live check's history act takes the
target its check got: capsulectl gives the block that check sealed beside the
act (``counterparty``), and it is read as the check's own block is, the
profile-keyed payee still replacing it. A block that is not a check's
(``_check_counterparty``) is ignored and named in ``Action.ignored_inputs``.

On a check of a sale's thread, the envelope's top-level ``item_ref`` (256
random bits, lowercase hex, equal across the sale's threads) becomes
``Action.item_ref`` for ``single_commitment``. No deal record carries it, so
it is read only live, never from a deal record in a replay. A value in any
other shape is ignored and named in ``Action.ignored_inputs``. A live check
reads its history's acts through ``action_for_history_entry``
(``report/live_history.py``). A replay of a sale bundle keys the sale on
the digest of the sale's own task authority instead, and gives
``single_commitment`` each check's history of the sale's threads, read by the
reader a live check's history is read by (``report/sale_bundle.py``).
``single_commitment`` reads it whole; a ``release_on_acceptance`` that binds
the acceptance from the history (1.2.0) reads it cut to the live window
(``RULES_HISTORY_DAYS``). A cited ``release_on_acceptance`` (1.0.0, 1.1.0)
still reads the replay's own view, where no acceptance is sealed, so it
releases nothing. Outside a sale replay (a dry-run report of a deal's own
records, say) a 1.2.0 ``release_on_acceptance`` reads the replay's own view
too: its records carry no ``thread_counterparty``, so every share that needs
the acceptance refuses there, which fails closed. A sale replay is as close
to live as a bundle of one sale can get, not exact: a live history is
profile-wide and capped at 1,000 acts, and past the cap it reads
incomplete, so the acceptance is unknown. A share refused live for a
truncated history may therefore pass on replay of a complete bundle.

Live and replay can also differ by design under the frozen spend.weekly/3.0.0
(everyday 0.3.4/0.3.5): the live guard also counted readable undecided and
unchecked acts in the caps window, and replay follows the pinned text,
accepted acts only, so a cap failed live may pass on replay. Anything that
shows a user both must word that case as the live guard being stricter than
the pinned rule, never as a mismatch or as replay finding an error
(capsule-engine#214, Sherlock2's ruling).

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

from agent_action_capsule import json_digest
from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError

from ..folds.definition import FoldDefinition, Reversal
from ..folds.duration import parse_duration_seconds
from ..guards import ALLOW, ESCALATE, Action, GuardDecision, GuardEngine, LocalSigner
from ..guards.action import disclosed_classes_of, handover_of, listing_classes_of, repeat_disclosures_of
from ..guards.checks.caps import EXECUTED_DECISION, SPEND_UNREADABLE, counts_executed_acts
from ..guards.checks.commitment import text_commitment
from ..guards.checks.promise_never import ATTRIBUTED_TO_USER, WORDS_NOT_IN_BUNDLE
from ..guards.history import History, MemoryHistory, open_store
from ..guards.history_state import CHECK_FP_ALGS, RULES_HISTORY_DAYS, well_formed_counterparty
from ..guards.statements import StatementRecord, claim_of, make_statement_record
from ..guards.wickets.definition import WicketDefinition
from ..packs.install import engine_ask_sets, required_fields
from ..packs.schema import PackDefinition

__all__ = [
    "MONEY_IN",
    "ExecutedActs",
    "bound_agent_input",
    "SourcedDecision",
    "ReplayResult",
    "SaleCheck",
    "SaleHistory",
    "load_records",
    "load_disclosed",
    "load_representing_openings",
    "representing_openings",
    "load_withheld",
    "filter_since",
    "action_for_record",
    "action_for_check_input",
    "action_for_history_entry",
    "deal_claim_statements",
    "names_two_deals",
    "replay",
    "statement_for_history_entry",
    "statement_record",
]

_TRANSFER_NOTE_RE = re.compile(r"amount_eur:\s*(\d+).*?target_iban:\s*([A-Z0-9]+)")


def load_records(paths: Sequence[str | Path]) -> list[dict]:
    """Load every record from one or more sources, in the given order.

    Each source is either a plain JSONL file of capsules (read in file-internal
    line order), a store directory (``guards/history.py`` ``open_store``,
    read in its log order) -- the CLI's real-deployment path, since an
    operator's records are in a store directory, not a loose JSONL file -- or a
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
            with open_store(p) as store:
                records.extend(r.capsule for r in store.find())
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


# Where capsulectl puts a deal's report in a bundle (capsule-cli
# internal/cli/deal_sealed_report.go, DealSealedReport): inline as the
# x-deal-v0 extension itself, standalone in the x-deal-report/v0 extension,
# or in the log as the record the extension's sealed_report names.
_DEAL_EXTENSION = "x-deal-v0"
_REPORT_EXTENSION = "x-deal-report/v0"
_REPORT_POINTER = "sealed_report"


def _bundle_reports(bundle: dict) -> list[dict]:
    """Every deal report ``bundle`` carries, in any of capsulectl's three
    forms; nothing about them is trusted here (``representing_openings``)."""
    extensions = bundle.get("extensions") if isinstance(bundle.get("extensions"), dict) else {}
    reports: list[object] = []
    deal = extensions.get(_DEAL_EXTENSION)
    if isinstance(deal, dict):
        reports.append(deal)
        pointer = deal.get(_REPORT_POINTER)
        disclosures = bundle.get("disclosures") if isinstance(bundle.get("disclosures"), dict) else {}
        shown = disclosures.get(pointer) if isinstance(pointer, str) else None
        agent_input = shown.get("agent_input") if isinstance(shown, dict) else None
        if isinstance(agent_input, dict):
            reports.append(agent_input.get("report"))
    standalone = extensions.get(_REPORT_EXTENSION)
    agent_input = standalone.get("agent_input") if isinstance(standalone, dict) else None
    if isinstance(agent_input, dict):
        reports.append(agent_input.get("report"))
    return [r for r in reports if isinstance(r, dict)]


def representing_openings(bundle: object) -> dict[str, dict]:
    """The openings of the agent's statements a bundle's deal report carries
    (its ``representations``, each ``{nonce, text}``), by the commitment each
    recomputes to (``commitment.text_commitment``). An opening proves itself:
    the words that recompute a represent check's sealed ``text_commitment``
    are the words that check sealed, so the report's own binding is not
    needed to read them (capsulectl seals a claim under its check's nonce,
    so a claim's opening is its check's). An entry whose nonce is not 64
    lowercase hex or whose text is not a string, or that has no JCS bytes,
    is skipped."""
    openings: dict[str, dict] = {}
    if not isinstance(bundle, dict):
        return openings
    for report in _bundle_reports(bundle):
        said = report.get("representations")
        for entry in said if isinstance(said, list) else []:
            nonce, text = (entry.get("nonce"), entry.get("text")) if isinstance(entry, dict) else (None, None)
            if not isinstance(nonce, str) or not _HEX64.fullmatch(nonce) or not isinstance(text, str):
                continue
            try:
                openings.setdefault(text_commitment(nonce, text), {"nonce": nonce, "text": text})
            except UnicodeEncodeError:
                continue
    return openings


def load_representing_openings(paths: Sequence[str | Path]) -> dict[str, dict]:
    """``representing_openings`` of every capsule bundle among ``paths``."""
    openings: dict[str, dict] = {}
    for path in paths:
        for commitment, opening in representing_openings(_bundle(Path(path))).items():
            openings.setdefault(commitment, opening)
    return openings


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
    an act carried out is read the same way, in either shape a check input's
    history states one: a typed act record (``action-record/v0``), or an
    ``x-deal-v0`` action step, which seals the counterparty in its block as a
    check does. An action step whose body states an act taken without a check
    (``body.unchecked``) is not read: it was never authorized."""
    block = disclosed.get("x-deal-v0")
    body = disclosed.get("body")
    record_type = block.get("record_type") if isinstance(block, dict) else None
    if record_type == "check" or (history and _is_action_step(disclosed) and not _taken_unchecked(body)):
        counterparty = block.get("counterparty")
    elif disclosed.get("type") == "proposed-action/v0" or (history and disclosed.get("type") == _ACT_RECORD_TYPE):
        counterparty = body.get("counterparty") if isinstance(body, dict) else None
    else:
        return None
    return (body, counterparty) if isinstance(body, dict) else None


# The sealed rule input on an offer that answers no proposal (capsule-cli
# deal_proposal.go), and the ref an offer names the message it answers by.
_NO_PROPOSAL = "no_proposal"
_SOURCE = "source"


def _is_check(disclosed: dict) -> bool:
    """Whether ``disclosed`` is a deal check: an ``x-deal-v0`` check record
    or a typed ``proposed-action/v0``."""
    block = disclosed.get("x-deal-v0")
    if isinstance(block, dict):
        return block.get("record_type") == "check"
    return disclosed.get("type") == _PROPOSED_TYPE


def _refs(disclosed: dict) -> list:
    """A deal record's refs: an ``x-deal-v0`` record's block refs, else a
    typed record's own; ``[]`` when it has none in a list."""
    block = disclosed.get("x-deal-v0")
    refs = block.get("refs") if isinstance(block, dict) else disclosed.get("refs")
    return refs if isinstance(refs, list) else []


def _names_source(disclosed: dict) -> bool:
    """Whether a deal record names any ``source`` ref, in whatever shape:
    a check that does names a proposal it answers, resolved or not."""
    return any(isinstance(r, dict) and r.get("rel") == _SOURCE for r in _refs(disclosed))


def _no_proposal(disclosed: dict, body: dict) -> bool | None:
    """``True`` when ``disclosed`` is a check whose body seals
    ``no_proposal: true`` and which names no ``source`` ref; else ``None``. A
    check naming both says it answers a message, so it is decided on that
    message's age (``_accepted_offer_at``), or fails as missing one: never
    as answering no proposal."""
    if _is_check(disclosed) and body.get(_NO_PROPOSAL) is True and not _names_source(disclosed):
        return True
    return None


def _is_action_step(disclosed: dict) -> bool:
    """Whether ``disclosed`` is an ``x-deal-v0`` action step: an act carried
    out, as a deal seals it."""
    block = disclosed.get("x-deal-v0")
    return isinstance(block, dict) and block.get("record_type") == _ACTION_STEP


def _taken_unchecked(body: object) -> bool:
    """Whether a deal record's ``body`` states an act taken without a check
    or without authority (``_taken``)."""
    return isinstance(body, dict) and (_UNCHECKED in body or _ATTEMPTED in body)


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


# A represent check (capsule-cli deal_representations.go), and the class the
# bridge gives one whose sealed class is not a non-empty string: no
# representation class, so promise_requires_approval and promise_never both
# fail it as not recognised. A closed token, never a stated value.
_REPRESENT = "represent"
MALFORMED_REPRESENTATION_CLASS = "malformed_representing"


def _attributed_to(value: object) -> str | None:
    """A statement's attribution in its closed set (``user``), or ``None``:
    any other value is read as the agent's own statement, which s10 refuses,
    and is never sealed."""
    return ATTRIBUTED_TO_USER if value == ATTRIBUTED_TO_USER else None


# The commitment the bridge gives a represent check whose sealed
# ``text_commitment`` is present but out of shape: not 64 lowercase hex, so
# ``promise_never`` fails it, never reading it as absent.
MALFORMED_TEXT_COMMITMENT = "malformed_text_commitment"


def _text_commitment(representing: dict) -> str | None:
    """A represent check's sealed ``text_commitment``: the digest when it is
    64 lowercase hex, ``None`` when it is absent, and
    ``MALFORMED_TEXT_COMMITMENT`` otherwise."""
    if "text_commitment" not in representing:
        return None
    return _hex64(representing["text_commitment"]) or MALFORMED_TEXT_COMMITMENT


def _hex64(value: object) -> str | None:
    return value if isinstance(value, str) and _HEX64.fullmatch(value) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _handover(body: dict) -> str | None:
    """How the item reaches the buyer, as the check's ``terms.handover``
    states it: carried only as a member of ``HANDOVERS`` (``handover_of``), and dropped
    otherwise (a rail allowed only in person then fails, as with none)."""
    terms = body.get("terms")
    return handover_of(terms.get("handover")) if isinstance(terms, dict) else None


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
# Who a history act was with, on each history entry beside its capsule: the
# counterparty block the act's check sealed, exactly as sealed, and the
# fp_algs a check seals it under (an x-deal-v0 check's, a typed check's).
_COUNTERPARTY_INPUT = "counterparty"
# The deal's earlier claims the user's agent made, on the checked record entry
# beside its capsule (AMENDMENT 9): each the sealed claim record's own values.
_CLAIMS_INPUT = "deal_claims"


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


def _check_counterparty(block: object) -> bool:
    """Whether ``block`` is a check's counterparty block in its agreed shape,
    ``{fp_alg: <a check's fp_alg>, ids: {<kind>: <64 lowercase hex>}}``, so
    ``_sealed_counterparty`` may read it as the check's own.

    Looser than ``history_state.well_formed_counterparty`` on purpose: it
    decides only whether the act gets its check's target, as it has since
    the act counterparty was added, so it does not check the kinds or refuse
    an empty block, and tightening it would move other checks' decisions.
    ``release_on_acceptance`` 1.2.0 reads the strict rule through
    ``Action.counterparty_malformed``."""
    if not isinstance(block, dict) or block.get("fp_alg") not in CHECK_FP_ALGS:
        return False
    ids = block.get("ids")
    return isinstance(ids, dict) and all(isinstance(v, str) and _HEX64.fullmatch(v) for v in ids.values())


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
_TASK_AUTHORITY = "task-authority/v0"
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


def names_two_deals(shown: dict) -> bool:
    """Whether the record ``shown`` names two deals (``_deal_id``), so it is
    in none."""
    return _deal_id(shown).conflict


def _bridge_deal_check(
    record: dict,
    disclosed: dict | None,
    counterparty_profile: object = None,
    *,
    history: bool = False,
    act_counterparty: object = None,
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
    recourse rail). The handover comes from the body's ``terms`` block
    (``_handover``), as a seller's commit or sale states it. The target is the payee's sealed fingerprint
    (``_payee_target``) from the counterparty block the check seals
    (``_checked_body``), typed or not, so a check sealing none has no target;
    or ``counterparty_profile``'s when it is given in
    its agreed shape (``_profile_target``); given in any other, it is named
    in ``ignored_inputs``. The remaining body fields are carried only in the
    shape the action takes: a string, a boolean, an integer, or the SHA-256
    digest of a typed reference, and dropped otherwise. A share's
    ``disclosing``, the classes of data it gives, is carried as
    ``disclosed_classes`` only as a non-empty list of known class names
    (``DISCLOSED_CLASSES``); with any other member it is dropped whole.
    ``repeat_disclosures`` is carried the same way, possibly empty, and
    ``recipient_told_before`` only as a boolean (capsule-engine#188); neither
    is sealed on the decision. A sale thread's ``listing_classes`` (the
    classes every live listing stated when the check was made) is carried as
    one only as a list of class names, and dropped whole otherwise; it is read
    here and never sealed on the decision.
    A check's sealed ``no_proposal`` is carried only when it is ``true`` and
    the check names no ``source`` ref (``_no_proposal``); it is never read
    off an act a history entry records.
    A represent check's ``representing`` gives the class of the statement
    (``representation_class``), whose claim it is (``attributed_to``, as
    ``statement_attributed_to``, its closed set only) and the commitment to its words
    (``text_commitment``, as ``representation_text_commitment``: the digest
    when it is 64 lowercase hex, ``MALFORMED_TEXT_COMMITMENT`` when it is out
    of shape, ``None`` when absent), so ``promise_requires_approval``
    and ``promise_never`` decide it (capsule-engine#180).
    ``taxonomy_version`` makes it an act stated against a pinned taxonomy, so
    ``dedupe`` keys it on the act, never on this record's type or id. The
    deal is the one the record names (``_deal_id``), never the ``action_id``
    prefix. A record naming two deals carries none, and names both fields in
    ``deal_id_conflict``, so the engine refuses whatever would be allowed.
    With ``history``, a typed act record is bridged too (``_checked_body``),
    and its task authority is the one its ``authority_basis`` names
    (``_basis_task_authority``). An act record seals no counterparty, so
    ``act_counterparty``, the block its check sealed, given beside it, is
    read in its place, as the check's own (``_check_counterparty``): the act
    gets the target its check got, keyed per deal. Given in any other shape,
    it is named in ``ignored_inputs`` and the act has no target from it."""
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
        if reverses is None and history and _is_action_step(disclosed):
            # An action step names the act it reverses in its refs, not its body.
            reverses = _only_ref(disclosed, "reverses")
    deal = _deal_id(disclosed)
    ignored: list[str] = []
    if act_counterparty is not None and sealed_counterparty is None:
        if _check_counterparty(act_counterparty):
            sealed_counterparty = act_counterparty
        else:
            ignored.append(_COUNTERPARTY_INPUT)
    counterparty_ids, fp_alg = _sealed_counterparty(sealed_counterparty)
    malformed = sealed_counterparty is not None and not (
        isinstance(sealed_counterparty, dict)
        and well_formed_counterparty(sealed_counterparty.get("fp_alg"), sealed_counterparty.get("ids")))
    target = _payee_target(counterparty_ids, fp_alg)
    if counterparty_profile is not None:
        profile_target = _profile_target(counterparty_profile)
        if profile_target is None:
            ignored.insert(0, _PROFILE_INPUT)
        else:
            target = profile_target
    recourse = body.get("recourse") if isinstance(body.get("recourse"), dict) else {}
    representing = body.get("representing") if isinstance(body.get("representing"), dict) else {}
    stated_class = _text(representing.get("class"))
    if stated_class is None and body.get("action") == _REPRESENT:
        # A represent check whose class cannot be read (no representing, or
        # one out of shape): a class no rule recognises, so s04 and s10 fail
        # it rather than read it as n/a.
        stated_class = MALFORMED_REPRESENTATION_CLASS
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
        handover=_handover(body),
        recipient_role=_text(body.get("recipient_role")),
        disclosed_classes=disclosed_classes_of(body.get("disclosing")),
        repeat_disclosures=repeat_disclosures_of(body.get("repeat_disclosures")),
        recipient_told_before=_flag(body.get("recipient_told_before")),
        listing_classes=listing_classes_of(body.get("listing_classes")),
        representation_class=stated_class,
        statement_attributed_to=_attributed_to(representing.get("attributed_to")),
        representation_text_commitment=_text_commitment(representing),
        channel=_text(body.get("channel")),
        first_contact_channel=_text(body.get("first_contact_channel")),
        upfront_amount_minor=_minor(body.get("upfront_amount_minor")),
        material_fields_changed=_minor(body.get("material_fields_changed")),
        material_fields_basis=_text(body.get("material_fields_basis")),
        offer_fields_changed=_minor(body.get("offer_fields_changed")),
        offer_fields_basis=_text(body.get("offer_fields_basis")),
        task_authority_ref=_basis_task_authority(body) if history else _typed_ref_digest(body.get("task_authority_ref")),
        no_proposal=_no_proposal(disclosed, body),
        returned_minor=returned,
        reverses_ref=reverses,
        deal_id=deal.value,
        deal_id_conflict=_DEAL_ID_FIELDS if deal.conflict else (),
        counterparty_malformed=malformed,
        ignored_inputs=tuple(ignored),
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
    # The claim's own sealed ``at``, never the capsule's ``timestamp``: the same
    # instant (one clock read in capsulectl), but only ``at`` is the string a
    # live check receives in ``deal_claims``, so the two never diverge.
    return make_statement_record(
        operator=str(record.get("operator", "")), timestamp=_text(block.get("at")),
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
    reads and judges it) and named in ``ignored_inputs`` otherwise.
    ``deal_claims`` is read by ``deal_claim_statements``, and named in
    ``ignored_inputs`` when any of it is ignored. The
    envelope's other top-level members, ``party_role`` among them (a checker
    may read it to pick a pack), are not read here: an input carrying one is
    decided as one without it. The act a history entry records is read by
    ``action_for_history_entry``; here a history entry's typed act record
    states no act."""
    capsule, agent_input = _entry_parts(entry)
    action = action_for_record(capsule, agent_input, counterparty_profile=entry.get(_PROFILE_INPUT))
    action = _with_inputs(action, entry, item_ref)
    if _CLAIMS_INPUT in entry and deal_claim_statements(entry).ignored:
        action = replace(action, ignored_inputs=(*action.ignored_inputs, _CLAIMS_INPUT))
    return action


@dataclass(frozen=True)
class DealClaims:
    """The statements a checked record entry's ``deal_claims`` makes, and
    whether any of it was ignored."""

    statements: tuple[StatementRecord, ...]
    ignored: bool


def deal_claim_statements(entry: dict) -> DealClaims:
    """The statements the ``deal_claims`` beside a checked record entry make
    (AMENDMENT 9), as ``statement_record`` writes them for the same claims in
    a bundle: the claim's ``capsule_id``, its class (``delivery_promise`` read
    as ``delivery_date``), ``source_kind`` ``agent``, the checked record's
    deal and operator, and the claim's sealed ``at`` as its time. An entry
    counts only in its agreed shape: ``capsule_id``, ``record_digest`` and
    ``text_commitment`` 64 lowercase hex, a non-empty ``class``,
    ``source_kind`` ``agent``, and ``at`` an RFC 3339 time with a zone at or
    before the checked capsule's ``timestamp``. Any other entry, a
    ``deal_claims`` that is not a list, or a checked record whose deal cannot
    be read (unbound, or naming two deals) is ignored, and ``ignored`` says
    so; nothing it holds is echoed. An empty list is no claims. The same
    claim (one ``capsule_id``) given twice is one statement.

    The entries are capsulectl's word, on the user's own device: an entry
    names no deal of its own, so it is taken as the checked record's, and
    its ``record_digest`` is checked for shape only, since a live check has
    no claim record to recompute it from. A replay of the bundle reads the
    sealed claims themselves. The time rule is "at or before", as for a
    claim in the history: capsule times are whole seconds, and capsulectl
    seals a claim and the step after it in one second."""
    claims = entry.get(_CLAIMS_INPUT)
    if claims is None:
        return DealClaims(statements=(), ignored=False)
    capsule, agent_input = _entry_parts(entry)
    deal = _deal_id(agent_input).value if agent_input is not None and _bound(capsule, agent_input) else None
    checked_at = _aware(capsule.get("timestamp"))
    if not isinstance(claims, list) or deal is None or checked_at is None:
        return DealClaims(statements=(), ignored=True)
    statements: dict[str, StatementRecord] = {}
    ignored = False
    for claim in claims:
        at = _aware(claim.get("at")) if isinstance(claim, dict) else None
        if at is None or at > checked_at or not _claim_in_shape(claim):
            ignored = True
            continue
        stated = make_statement_record(
            operator=str(capsule.get("operator", "")), timestamp=claim["at"], sealed_class=claim["class"],
            source_kind=_AGENT, deal_id=deal, claim=claim["capsule_id"],
        )
        statements.setdefault(claim_of(stated), stated)
    return DealClaims(statements=tuple(statements.values()), ignored=ignored)


def _claim_in_shape(claim: dict) -> bool:
    """Whether a ``deal_claims`` entry has its agreed members, each in shape
    (``deal_claim_statements``); its ``at`` is checked by the caller."""
    hexes = (claim.get(k) for k in ("capsule_id", "record_digest", "text_commitment"))
    return (all(isinstance(v, str) and _HEX64.fullmatch(v) for v in hexes)
            and _text(claim.get("class")) is not None and claim.get("source_kind") == _AGENT)


def _aware(value: object) -> datetime | None:
    """``value`` as an aware time, when it is an RFC 3339 string with a zone."""
    if not isinstance(value, str):
        return None
    try:
        at = _parse_ts(value)
    except ValueError:
        # Not a time: the caller ignores what it dates.
        return None
    return at if at.tzinfo is not None else None


def action_for_history_entry(entry: dict) -> Action | None:
    """The act a ``history`` entry of an external-check-input/v0 envelope
    records: its sealed act, a typed act record (``action-record/v0``) or an
    ``x-deal-v0`` action step, bridged as a deal check is
    (``_bridge_deal_check``), with the task authority a typed record's
    ``authority_basis`` names, the act an action step reverses read from its
    ``refs`` (``rel`` ``reverses``), and the inputs beside it read as
    ``action_for_check_input`` reads them (the entry's own ``item_ref``),
    plus the entry's ``counterparty``: who the act was with, its target as
    its check's (``_bridge_deal_check``). ``counterparty_profile`` still
    replaces that target, as it does a check's. ``None`` when the entry is
    not an act its capsule binds, or
    names no class or taxonomy, or states an act taken without a check:
    what it records cannot be read as a checked act."""
    capsule, agent_input = _entry_parts(entry)
    action = _bridge_deal_check(
        capsule, agent_input, entry.get(_PROFILE_INPUT), history=True, act_counterparty=entry.get(_COUNTERPARTY_INPUT)
    )
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
        k: v
        for k, v in entry.items()
        if k not in ("agent_input", _PROFILE_INPUT, _PROPOSAL_INPUT, _ITEM_INPUT, _COUNTERPARTY_INPUT, _CLAIMS_INPUT)
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
    no such offer is left out, so its proposal's age stays unestablished.

    A typed offer that names the counterparty's message it answers by its one
    ``source`` ref gets that message's time (the value capsulectl sends live
    on the offer): the message record's sealed ``sent_at``, else its sealed
    ``at``, only when ``sent_at`` is absent (``_message_time``). The message
    must be a bound ``x-deal-v0`` ``message`` from the
    counterparty, earlier in ledger order and in the same deal. A withheld,
    unbound or unresolved message, or more than one ``source`` ref, leaves
    the offer out, so its proposal's age stays unestablished."""
    latest: dict[str, tuple[str, str | None]] = {}
    accepted: dict[str, str | None] = {}
    messages: dict[str, tuple[str, str | None]] = {}
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
        if isinstance(block, dict) and block.get("record_type") == _MESSAGE and body.get("from") == _COUNTERPARTY:
            messages[json_digest(shown)] = (deal, _message_time(block, body))
        if shown.get("type") == _PROPOSED_TYPE and body.get("action") == _OFFER:
            digest = json_digest(shown)
            latest[deal] = (digest, _text(shown.get("at")))
            accepted.pop(deal, None)
            answered = messages.get(_answered_message(shown) or "")
            if answered is not None and answered[0] == deal and answered[1] is not None:
                found[digest] = answered[1]
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


# A deal's message record, and who sent it, as capsulectl seals it.
_MESSAGE, _COUNTERPARTY = "message", "counterparty"


# A sealed time as capsulectl writes it: RFC 3339 UTC, whole seconds.
_SEALED_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _sealed_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not _SEALED_TIME.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # The shape matched but the date does not exist: no time.
        return None


def _message_time(block: dict, body: dict) -> str | None:
    """When a counterparty's message was sent: its sealed ``sent_at`` when the
    member is present, else its sealed ``at`` (when it was noted). A present
    ``sent_at`` that is not a real UTC time, or that is later than the
    message's own ``at``, gives no time: it is never replaced by ``at``."""
    at = _sealed_time(block.get("at"))
    if "sent_at" not in body:
        return block["at"] if at is not None else None
    sent = _sealed_time(body["sent_at"])
    if sent is None or at is None or sent > at:
        return None
    return body["sent_at"]


def _answered_message(shown: dict) -> str | None:
    """The digest a typed offer's one ``source`` ref names, when it has
    exactly one and it is a SHA-256 typed ref; else ``None``."""
    refs = shown.get("refs")
    found = [r for r in refs if isinstance(r, dict) and r.get("rel") == _SOURCE] if isinstance(refs, list) else []
    return _typed_ref_digest(found[0]) if len(found) == 1 else None


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
    disclosure moves none. An accepted act is counted at the larger of its
    stated ``amount_minor`` and its ``spend_minor``, and at its
    ``amount_minor``, never below 0, when it seals no ``spend_minor``
    (capsule-engine#214 hold A, ruling A): a partial charge keeps its stated
    amount, and a spend above it is never left out. An act taken
    without a check is sealed before its spend is classed, so its
    ``amount_minor`` stands in for a missing ``spend_minor``. Any other act
    is counted at its ``spend_minor``, which must be an integer of at least
    0."""
    if body.get("direction") == MONEY_IN or _DISCLOSED in body:
        return 0
    spend = _minor(body.get("spend_minor"))
    if accepted:
        amount = _minor(body.get("amount_minor"))
        if amount is None:
            return None
        return max(amount, 0) if spend is None else max(amount, spend)
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
    window may hold, so the accepted ones are known whatever their order.
    ``reversal`` and ``checked_at``, given for a live check's history, lower
    an accepted partial charge a cancel or refund in ``capsules`` cites
    (``_history_freed``); without them (a replay) every act counts as it is
    sealed."""

    def __init__(self, capsules: Sequence[dict], *, reversal: Reversal | None = None,
                 checked_at: datetime | None = None) -> None:
        self._accepted = frozenset().union(*(_act_ids(c) for c in capsules if _sealed_accept(c)))
        self._counted: set[str] = set()
        self._freed = _history_freed(capsules, reversal, checked_at) if reversal is not None else {}

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
        freed = self._freed.get(capsule.get("capsule_id"), 0) if act.spend_minor is not None else 0
        if freed:
            act = replace(act, spend_minor=act.spend_minor - freed)
        return _executed_record(capsule, act, act_digest)


@dataclass(frozen=True)
class _Charge:
    """An accepted charge in a history whose counted amount a reversal may
    lower: what it counts at, the least it may count at (its sealed
    ``spend_minor``), and the key it is counted under."""

    counted: int
    floor: int
    key: tuple[object, object]
    at: datetime


def _history_freed(capsules: Sequence[dict], reversal: Reversal, checked_at: datetime | None) -> dict[str, int]:
    """How much each accepted charge in a live check's history counts less,
    by its ``capsule_id`` (capsule-engine#234, ruling A on #214 hold A: a
    partial charge counts at its stated amount "until a cancel or refund is
    on record"). A history act's record carries no chain (the fold's
    ``reversal`` clause cannot link it), so the history's reversals are
    applied here, to the record of the charge they reverse.

    A reversal is an act of a ``reversal.classes`` class stated as money in
    (``MONEY_IN``) whose capsule's chain cites, under ``reversal.relation``,
    the ``capsule_id`` of an accepted charge in this history, and nothing
    else: never matched on amount, merchant or time alone. It must be sealed
    no earlier than its charge and no later than the check (``checked_at``,
    unread: none link), and carry the charge's operator and developer. It
    takes back the amount it returns (``_returned_minor``), once per
    ``capsule_id`` or ``action_id``, and the charge never counts below its
    sealed ``spend_minor``: a charge that seals none, or whose act cannot be
    read, is lowered by nothing, so an unlinked, malformed or unreadable
    reversal frees nothing, as before. ``capsules`` are in the order the
    history ledger writes them, oldest first."""
    if checked_at is None:
        return {}
    charges: dict[str, _Charge] = {}
    seen_charges: set[str] = set()
    reversals: list[tuple[dict, dict, datetime]] = []
    for capsule in capsules:
        if statement_for_history_entry(capsule) is not None:
            continue
        shown = bound_agent_input(capsule)
        taken = _taken(shown, every_record_is_an_act=True) if shown is not None else None
        at = parse_aware_time(capsule.get("timestamp"))
        capsule_id = capsule.get("capsule_id")
        if taken is None or at is None or not isinstance(capsule_id, str) or not capsule_id:
            continue
        body, unchecked = taken
        if _text(body.get("action_class")) in reversal.classes:
            reversals.append((capsule, body, at))
            continue
        # Only the first copy of a capsule_id is written, so only it can be the charge.
        if capsule_id in seen_charges:
            continue
        seen_charges.add(capsule_id)
        counted = _executed_spend(body, unchecked=unchecked, accepted=True)
        floor = _minor(body.get("spend_minor"))
        if (_sealed_accept(capsule) and not unchecked and counted is not None and floor is not None
                and 0 <= floor < counted):
            charges[capsule_id] = _Charge(counted=counted, floor=floor, key=_charge_key(capsule), at=at)
    freed: dict[str, int] = {}
    seen: set[str] = set()
    for capsule, body, at in reversals:
        chain = capsule.get("chain")
        parent = chain.get("parent_capsule_id") if isinstance(chain, dict) else None
        returned = _returned_minor(body) if body.get("direction") == MONEY_IN else None
        charge = charges.get(parent) if isinstance(parent, str) else None
        ids = _act_ids(capsule)
        if (charge is None or chain.get("relation") != reversal.relation or returned is None or returned <= 0
                or ids & seen or charge.key != _charge_key(capsule) or not charge.at <= at <= checked_at):
            continue
        seen |= ids
        freed[parent] = freed.get(parent, 0) + min(returned, charge.counted - charge.floor - freed.get(parent, 0))
    return freed


def _charge_key(capsule: dict) -> tuple[object, object]:
    """The operator and developer a charge and its reversal must share."""
    return capsule.get("operator"), capsule.get("developer")


def parse_aware_time(value: object) -> datetime | None:
    """``value`` as an aware time when it is an RFC 3339 string with a zone,
    else ``None``: how a history timestamp is read."""
    try:
        at = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return at if at is not None and at.tzinfo is not None else None


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
class SaleCheck:
    """A replayed check is one of a sale's (``report/sale_bundle.py``):
    ``item_ref`` is the sale's key, ``None`` when it cannot be read."""

    item_ref: str | None


# Writes the history of the sale a replayed record is a check of into the
# ledger it is given, and says so; ``None`` for a record of no such sale. The
# third argument, when not ``None``, cuts the history to the acts sealed in
# that many days before the check, as a live check's history is cut.
SaleHistory = Callable[[str, History, "int | None"], "SaleCheck | None"]


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
    sale_history: SaleHistory | None = None,
    representing_openings: Mapping[str, dict] | None = None,
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
    later check in the same deal finds it made first. A claim the sources
    give more than once is written once.
    ``representing_openings`` are the openings of represent checks' words
    the bundles' deal reports carry, by commitment
    (``load_representing_openings``); a check whose sealed commitment one
    recomputes is decided with it, as a live check is with its words.
    A check citing a task authority (``task_authority_ref``) is decided with
    the task-authority record the bundle discloses under that digest, when
    it discloses one (``_decide``), as a live check gets it beside its input.
    A seller's typed commit is decided with ``proposal_at`` the sealed time
    of the accepted offer it rests on, and a typed offer with the time of the
    counterparty's message it answers by its ``source`` ref
    (``_accepted_offer_at``), so ``offer_expiry`` reads that proposal's age.
    Each record is evaluated under the action taxonomy it was sealed with,
    where the engine carries that version, so history at a carried version is
    never refused for its version. A record naming one it does not carry
    still gets a decision: its class-keyed checks are held ``n/a`` with
    evidence naming both versions, and when nothing else fails it is refused
    (``GuardEngine``), so no later check counts it as seen or as spend.
    ``sale_history`` is given for a replay of a sale bundle
    (``report/sale_bundle.py``, ``replay_sale``). A check it says is the
    sale's is decided with the sale's key as ``item_ref``, and
    ``single_commitment`` reads the sale's acceptance from a fresh ledger it
    writes for that check alone (``GuardEngine.check``'s ``sale_history``),
    never from the replay's own view. A ``release_on_acceptance`` that binds
    the acceptance from the history (1.2.0) reads it too, cut to the acts of
    the last ``RULES_HISTORY_DAYS`` days before the check, as live
    (``GuardEngine.check``'s ``release_history``); within that window the
    same completeness rule holds."""
    if not records:
        return ReplayResult(decisions=(), record_range=(0, -1))

    signer = LocalSigner(key_id="dry-run-report", secret=b"asg-guard-dry-run-report")

    gate_selectors, ask_checks = engine_ask_sets(pack, wickets) if pack is not None else (frozenset(), frozenset())
    sourced: list[SourcedDecision] = []
    undecided: list[dict] = []
    store = MemoryHistory()
    engine = GuardEngine(
        history=store,
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
        required_fields=required_fields(pack) if pack is not None else None,
    )
    profiles = _companion_profiles(records, disclosed or {})
    deal = _deal_records(records, disclosed or {})
    offered_at = _accepted_offer_at(records, disclosed or {})
    authorities = _task_authorities(disclosed or {})
    decided_checks: dict[str, SourcedDecision] = {}
    counts_spend = counts_executed_acts(caps_fold)
    executed_acts = ExecutedActs(records)
    written_statements: set[str] = set()
    for record in records:
        shown = (disclosed or {}).get(record.get("capsule_id", ""))
        digest = json_digest(shown) if shown is not None else None
        if _gets_no_decision(record, shown, withheld):
            undecided.append(record)
            stated = statement_record(record, shown)
            if stated is not None and claim_of(stated) not in written_statements:
                # A claim given twice (overlapping sources) is one statement.
                written_statements.add(claim_of(stated))
                store.append(dict(stated))
            chain = _carried_out(digest, deal) if digest is not None else None
            checked = decided_checks.pop(chain.check, None) if chain is not None else None
            if chain is not None and checked is not None and checked.decision.outcome in (ALLOW, ESCALATE):
                store.append(dict(_carried_out_record(checked, chain, _text(record.get("timestamp")))))
            if counts_spend and digest is not None and _bound(record, shown):
                executed = executed_acts.record(record, shown, digest)
                if executed is not None:
                    store.append(dict(executed))
            continue
        profile = profiles.get(digest) if profiles and digest is not None else None
        action = action_for_record(record, shown, counterparty_profile=profile)
        if digest in offered_at:
            action = replace(action, proposal_at=offered_at[digest])
        authority = authorities.get(action.task_authority_ref) if action.task_authority_ref else None
        opening = (representing_openings or {}).get(action.representation_text_commitment or "")
        action, decision = _decide(engine, action, record, sale_history, authority, opening)
        cited_capsule = None
        for constraint in decision.constraints:
            cited_id = (constraint.evidence or {}).get("matched_capsule_id") or (
                constraint.evidence or {}
            ).get("cited_capsule_id")
            if cited_id:
                found = store.get(cited_id)
                if found is not None:
                    cited_capsule = found.capsule
                    break
        sourced.append(SourcedDecision(record=record, action=action, decision=decision, cited_capsule=cited_capsule))
        if digest in deal and decision.capsule is not None:
            decided_checks[digest] = sourced[-1]

    return ReplayResult(decisions=tuple(sourced), record_range=(1, len(records)), undecided=tuple(undecided))


def _task_authorities(disclosed: dict[str, dict]) -> dict[str, dict]:
    """The task-authority records a bundle discloses (``task-authority/v0``),
    by the SHA-256 digest of each, the digest a check's
    ``task_authority_ref`` names. A record with no digest (a float, an
    unsafe integer) is left out: no reference can name it."""
    found: dict[str, dict] = {}
    for shown in disclosed.values():
        if isinstance(shown, dict) and shown.get("type") == _TASK_AUTHORITY:
            try:
                found[json_digest(shown)] = shown
            except (FloatInDigestError, UnsafeIntegerError, UnicodeEncodeError):
                continue
    return found


def _decide(engine: GuardEngine, action: Action, record: dict, sale_history: SaleHistory | None,
            task_authority: dict | None = None, opening: dict | None = None) -> tuple[Action, GuardDecision]:
    """The dry-run decision on ``action``, and the action decided: with the
    sale's key and the sale's history for a check of a replayed sale
    (``replay``), as given otherwise. ``task_authority`` is the record the
    bundle discloses under the digest the action's ``task_authority_ref``
    names (``_task_authorities``); the engine reads it only once its own
    digest of it equals that reference, as live, so a missing record leaves
    it unread (capsule-engine#180). ``opening`` is the opening of the
    represent check's words the bundle's deal report carries
    (``representing_openings``), passed as ``representing_opening`` so
    ``promise_never`` judges them as live; with none,
    ``WORDS_NOT_IN_BUNDLE``: a claim attributed to the user is then not
    evaluable ("the words for this attributed claim are not in the bundle"),
    never a failure (``guards/checks/promise_never.py``)."""
    capsule_id = _text(record.get("capsule_id"))

    def check(decided: Action, **ledgers: History) -> GuardDecision:
        return engine.check(decided, dry_run=True, task_authority_record=task_authority,
                            representing_opening=opening if opening is not None else WORDS_NOT_IN_BUNDLE,
                            **ledgers)

    if sale_history is None or capsule_id is None:
        return action, check(action)
    sale_ledger = MemoryHistory()
    sale = sale_history(capsule_id, sale_ledger, None)
    if sale is None:
        return action, check(action)
    action = replace(action, item_ref=sale.item_ref)
    if not engine.uses_release_history:
        return action, check(action, sale_history=sale_ledger)
    window_ledger = MemoryHistory()
    sale_history(capsule_id, window_ledger, RULES_HISTORY_DAYS)
    return action, check(action, sale_history=sale_ledger, release_history=window_ledger)
