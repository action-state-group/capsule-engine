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
counterparty's keyed fingerprints the record seals beside its body
(``x-deal-v0.counterparty``) are carried as they are, never a clear value. A record
that does not match that digest is never read.

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
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from ..folds.definition import FoldDefinition
from ..folds.duration import parse_duration_seconds
from ..guards import Action, GuardDecision, GuardEngine, LocalSigner
from ..guards.wickets.definition import WicketDefinition

__all__ = [
    "SourcedDecision",
    "ReplayResult",
    "load_records",
    "load_disclosed",
    "filter_since",
    "action_for_record",
    "replay",
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


def _checked_body(disclosed: dict) -> dict | None:
    """The body of a deal check (an ``x-deal-v0`` check record, or a typed
    ``proposed-action/v0``), or ``None`` for any other record."""
    block = disclosed.get("x-deal-v0")
    if isinstance(block, dict) and block.get("record_type") == "check":
        body = disclosed.get("body")
    elif disclosed.get("type") == "proposed-action/v0":
        body = disclosed.get("body")
    else:
        return None
    return body if isinstance(body, dict) else None


def _minor(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sealed_counterparty(block: object) -> tuple[dict[str, str] | None, str | None]:
    """The fingerprints (kind -> hex) in a sealed ``x-deal-v0.counterparty``
    block and their ``fp_alg``, or ``(None, None)`` when it holds none usable."""
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


def _typed_ref_digest(value: object) -> str | None:
    """The digest of a typed record reference ``{type, digest_alg, digest}``
    when it is a SHA-256 one, or ``None``."""
    if not isinstance(value, dict) or not _text(value.get("type")) or value.get("digest_alg") != "SHA-256":
        return None
    digest = value.get("digest")
    return digest if isinstance(digest, str) and _HEX64.match(digest) else None


def _bridge_deal_check(record: dict, disclosed: dict | None) -> Action | None:
    """The proposed action a capsulectl deal check states, from its own
    sealed record: the class it names, and the amount a spend cap evaluates,
    which is ``spend_minor`` only: never ``amount_minor`` (on a cancel it is
    a refund) or ``cancelled_amount_minor``, and ``0`` for a record that says
    the money moved in. So a cancel is never spend. ``spend_authorized_minor``,
    the authorised maximum sealed beside it, is carried for a per-action cap
    and dropped when the money moved in.
    Only a check is an action here: the step that acts on it is the same
    payment, and counting both would count it twice.
    The rail and ``refundable`` come from the body's ``recourse`` block, where
    the producer writes them (a top-level ``rail`` is read when there is no
    recourse rail). The target is the payee's sealed fingerprint
    (``_payee_target``). The remaining body fields are carried only in the
    shape the action takes: a string, a boolean, an integer, or the SHA-256
    digest of a typed reference, and dropped otherwise."""
    if disclosed is None or not _bound(record, disclosed):
        return None
    body = _checked_body(disclosed)
    if body is None or not body.get("action_class") or not body.get("taxonomy_version"):
        return None
    spend = body.get("spend_minor")
    authorized = body.get("spend_authorized_minor")
    if body.get("direction") == "in":
        spend = 0  # money arriving is never spend, whatever spend_minor says
        authorized = None
    counterparty_ids, fp_alg = _sealed_counterparty((disclosed.get("x-deal-v0") or {}).get("counterparty"))
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
        currency=body.get("currency"),
        rail=_text(recourse.get("rail")) or _text(body.get("rail")),
        target=_payee_target(counterparty_ids, fp_alg),
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
        task_authority_ref=_typed_ref_digest(body.get("task_authority_ref")),
    )


def action_for_record(record: dict, disclosed: dict | None = None) -> Action:
    """The action ``record`` states. ``disclosed`` is the record its capsule
    sealed, when a bundle disclosed it (``load_disclosed``)."""
    bridged = _bridge_deal_check(record, disclosed) or _bridge_transfer_funds(record)
    if bridged is not None:
        return bridged
    return Action.from_capsule(record)


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
    decisions: tuple[SourcedDecision, ...]
    record_range: tuple[int, int]


def replay(
    records: list[dict],
    *,
    caps_fold: FoldDefinition,
    caps_minor: dict[str, int] | None = None,
    manifest_digest: str | None = None,
    per_action_minor: dict[str, int] | None = None,
    per_action_reads: str | None = None,
    disclosed: dict[str, dict] | None = None,
    wickets: tuple[WicketDefinition, ...] = (),
) -> ReplayResult:
    """Feed every record through a fresh ``GuardEngine`` in dry-run mode, in
    order. Never blocks (``dry_run=True``) -- see ``engine.py``'s own
    guarantee that a dry-run decision still produces and appends a real,
    signed capsule. ``record_range`` on the result is this replayed set's own
    1-based position range (i.e. positions within the ``--since``-filtered
    window actually replayed, not the source ledger's absolute positions).
    ``wickets`` are configured checks run on every decision, as
    ``GuardEngine(wickets=...)`` runs them; none by default."""
    if not records:
        return ReplayResult(decisions=(), record_range=(0, -1))

    signer = LocalSigner(key_id="dry-run-report", secret=b"asg-guard-dry-run-report")

    sourced: list[SourcedDecision] = []
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
        )
        for record in records:
            action = action_for_record(record, (disclosed or {}).get(record.get("capsule_id", "")))
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

    return ReplayResult(decisions=tuple(sourced), record_range=(1, len(records)))
