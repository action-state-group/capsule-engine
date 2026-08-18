# SPDX-License-Identifier: Apache-2.0
"""The MCP tool implementations: plain functions returning plain, JSON-serializable
dicts, each one a thin wrapper over an existing module -- ``LedgerAPI``, the fold
engine, or the guard engine/checks. No business logic is reimplemented here.

Kept independent of ``mcp.server.fastmcp`` (no import of it anywhere in this
module) so every tool is directly unit-testable with a plain ``LedgerStore`` and
no MCP session/transport in the loop -- ``server.py`` is the only module that
knows FastMCP exists.

Every read tool returns an ``envelope`` (or, for ``record.verify``, a
verification result) alongside its answer -- never a bare number or a bare
yes/no with nothing behind it. This is deliberate: an agent calling these tools
must never need to fall back to raw ledger rows to check a tool's own claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..cli.constraints_cmd import CHECKS, DEFAULT_CAPS_FOLD_ID
from ..cli.format import summarize_action
from ..folds.catalog import Catalog
from ..folds.engine import evaluate_one
from ..folds.errors import FoldDefinitionError, FoldDeterminismError
from ..folds.loader import load_definition_file
from ..guards import Action, GuardEngine
from ..guards.checks.dedupe import check_dedupe
from ..guards.classes import TAXONOMY, UNCLASSIFIED_DEFAULT
from ..ledger.api import LedgerAPI, ScanQuery

__all__ = [
    "ledger_query",
    "fold_list",
    "fold_get",
    "budget_remaining",
    "action_been_done",
    "constraints_list",
    "decision_explain",
    "record_get",
    "record_verify",
    "intent_declare",
]

_DEDUPE_WINDOW_DAYS = 30  # matches GuardEngine's own default (guards/engine.py)


def _error(reason: str, message: str) -> dict[str, Any]:
    return {"error": {"reason": reason, "message": message}}


def _record_dict(record) -> dict[str, Any]:
    return {
        "seq": record.seq,
        "capsule_id": record.capsule_id,
        "capsule": record.capsule,
        "consequential": record.consequential,
    }


def _shift(ts: str, *, days: int) -> str:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(text) - timedelta(days=days)
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# ledger.query
# ---------------------------------------------------------------------------


def ledger_query(
    ledger: LedgerAPI,
    *,
    agent: str | None = None,
    since: str | None = None,
    until: str | None = None,
    counterparty: str | None = None,
    verdict: str | None = None,
    action_type: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The same filtered scan `capsule log` runs: every matching record, plus the
    honest total (a filtered view is never mistaken for the whole ledger) and
    the chain-gap count `capsule log`'s own footer reports."""
    total = sum(1 for _ in ledger.scan(ScanQuery()))
    query = ScanQuery(
        agent=agent,
        since=since,
        until=until,
        counterparty=counterparty,
        verdict=verdict,
        action_type=action_type,
        limit=limit,
    )
    records = list(ledger.scan(query))
    gaps = ledger.find_gaps()
    return {
        "records": [_record_dict(r) for r in records],
        "matched": len(records),
        "total": total,
        "range": [records[0].seq, records[-1].seq] if records else [0, -1],
        "checkpoint": {"tree_size": total},
        "staleness": {"checkpoint_age_ms": 0},
        "chain_gaps": len(gaps),
    }


# ---------------------------------------------------------------------------
# fold.list / fold.get
# ---------------------------------------------------------------------------


def fold_list(catalog_dir: str | Path) -> dict[str, Any]:
    """The fold catalog: every declared fold's id, definition digest, and
    source path, matching `capsule fold list` exactly (same `Catalog`)."""
    catalog = Catalog(catalog_dir)
    entries = catalog.list_entries()
    errors = catalog.list_errors()
    return {
        "folds": [
            {"fold_id": e.definition.fold_id, "digest": e.digest, "source_path": str(e.source_path)}
            for e in entries
        ],
        "errors": [
            {"source_path": str(e.source_path), "reason": e.reason, "message": e.message} for e in errors
        ],
    }


def fold_get(
    ledger: LedgerAPI,
    catalog_dir: str | Path,
    *,
    fold: str,
    key: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Evaluate one fold over the current ledger and return its full result
    envelope -- the same replay `capsule fold test` runs, against the live
    ledger instead of a one-off `--ledger` file.

    ``fold`` is a fold_id, a definition digest, or a path to a definition
    YAML file (checked in that order). Rolling-window folds require
    ``as_of``: this tool, like the fold engine underneath it, refuses to
    invent a time anchor from the wall clock -- supply one derived from real
    data (e.g. the timestamp of the record you care about).
    """
    path = Path(fold)
    try:
        if path.exists():
            definition = load_definition_file(path)
        else:
            entry = Catalog(catalog_dir).get(fold)
            if entry is None:
                return _error("not_found", f"no such fold {fold!r} in catalog {catalog_dir}")
            definition = entry.definition
    except FoldDefinitionError as exc:
        return _error(exc.reason, str(exc))

    records = [r.capsule for r in ledger.scan(ScanQuery())]
    try:
        trace = evaluate_one(definition, records, key_value=key, as_of=as_of)
    except FoldDeterminismError as exc:
        return _error(exc.reason, str(exc))

    return {"fold_id": definition.fold_id, "key": key, **trace.to_envelope()}


# ---------------------------------------------------------------------------
# budget.remaining
# ---------------------------------------------------------------------------


def budget_remaining(
    ledger: LedgerAPI,
    catalog_dir: str | Path,
    caps_minor: dict[str, int],
    *,
    agent: str,
    action_class: str = "money.transfer",
    as_of: str | None = None,
) -> dict[str, Any]:
    """How much of ``action_class``'s configured weekly cap ``agent`` has left,
    evaluated the same way the guard's own `caps` check does (T1's
    `spend.weekly` fold, replayed for real -- never a number this tool
    computes by summing raw records itself).

    If no cap is configured for ``action_class`` (this deployment's own
    `--cap CLASS=MINOR_UNITS` / `$CAPSULE_MCP_CAPS_MINOR`), returns
    ``cap_configured: false`` rather than a fabricated remaining amount --
    matching the caps check's own `n/a` result for an unconfigured class.
    """
    cap_minor = caps_minor.get(action_class)
    if cap_minor is None:
        return {
            "agent": agent,
            "action_class": action_class,
            "cap_configured": False,
            "reason": "no cap configured for this action class",
        }

    entry = Catalog(catalog_dir).get(DEFAULT_CAPS_FOLD_ID)
    if entry is None:
        return _error("not_found", f"caps fold {DEFAULT_CAPS_FOLD_ID!r} not found in catalog {catalog_dir}")

    records = [r.capsule for r in ledger.scan(ScanQuery(agent=agent))]
    resolved_as_of = as_of or datetime.now(timezone.utc).isoformat()
    trace = evaluate_one(entry.definition, records, key_value=agent, as_of=resolved_as_of)
    spent_minor = trace.result or 0
    remaining_minor = cap_minor - spent_minor

    return {
        "agent": agent,
        "action_class": action_class,
        "cap_configured": True,
        "cap_minor": cap_minor,
        "spent_minor": spent_minor,
        "remaining_minor": remaining_minor,
        "over_cap": remaining_minor < 0,
        "envelope": trace.to_envelope(),
    }


# ---------------------------------------------------------------------------
# action.been_done
# ---------------------------------------------------------------------------


def action_been_done(
    ledger: LedgerAPI,
    *,
    verb: str,
    operator: str,
    developer: str,
    action_type: str = "decide",
    target: str | None = None,
    equivalence_key: str | None = None,
    since_days: int = _DEDUPE_WINDOW_DAYS,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Has an equivalent action already been recorded? Wraps the guard's own
    `dedupe` check (exact-match equivalence index, see `guards/checks/dedupe.py`)
    -- the same check `GuardEngine.check` runs before dispatch, run here as a
    standalone query rather than as a gate.
    """
    action = Action(
        verb=verb,
        operator=operator,
        developer=developer,
        action_type=action_type,
        target=target,
        equivalence_key=equivalence_key,
        timestamp=timestamp,
    )
    since = _shift(action.resolved_timestamp(), days=since_days)
    outcome = check_dedupe(action, ledger, since=since)
    c = outcome.constraint
    return {
        "verb": verb,
        "operator": operator,
        "developer": developer,
        "action_type": action_type,
        "target": target,
        "been_done": c.result == "fail",
        "reason": c.reason,
        "evidence": c.evidence,
        "checked_window_days": since_days,
    }


# ---------------------------------------------------------------------------
# constraints.list
# ---------------------------------------------------------------------------


def constraints_list(catalog_dir: str | Path) -> dict[str, Any]:
    """The registered guard checks and the action-class taxonomy -- the same
    static catalog `capsule constraints list` prints, structured instead of
    formatted for a terminal."""
    caps_entry = Catalog(catalog_dir).get(DEFAULT_CAPS_FOLD_ID)
    caps_method = caps_entry.definition.fold_id if caps_entry is not None else None

    checks = [
        {
            "id": check_id,
            "type": check_type,
            "method": caps_method if check_id == "caps" else method,
            "description": description,
        }
        for check_id, check_type, method, description in CHECKS
    ]
    action_classes = [
        {"name": ac.name, "consequential": ac.consequential, "fail_open_allowed": ac.fail_open_allowed}
        for ac in (*TAXONOMY.values(), UNCLASSIFIED_DEFAULT)
    ]
    return {"checks": checks, "action_classes": action_classes}


# ---------------------------------------------------------------------------
# decision.explain / record.get / record.verify
# ---------------------------------------------------------------------------


def decision_explain(ledger: LedgerAPI, *, capsule_id: str) -> dict[str, Any]:
    """Why a decision came out the way it did: the capsule's own constraints
    breakdown (`id`/`result`/`method` per check that ran) plus its verdict --
    the same information `capsule show` prints, never a separately-generated
    explanation. A capsule's constraints ARE the explanation; there is no
    other reasoning to fetch."""
    record = ledger.fetch(capsule_id)
    if record is None:
        return _error("not_found", f"no such capsule {capsule_id!r}")

    capsule = record.capsule
    disposition = capsule.get("disposition") or {}
    return {
        "capsule_id": record.capsule_id,
        "agent": capsule.get("developer"),
        "operator": capsule.get("operator"),
        "action": summarize_action(capsule),
        "action_type": capsule.get("action_type"),
        "timestamp": capsule.get("timestamp"),
        "decision": disposition.get("decision"),
        "verdict_class": disposition.get("verdict_class"),
        "constraints": [
            {
                "id": c.get("id"),
                "result": c.get("result"),
                "check_type": c.get("check_type"),
                "method": c.get("method"),
            }
            for c in (capsule.get("constraints") or [])
        ],
        "chain": capsule.get("chain"),
    }


def record_get(ledger: LedgerAPI, *, capsule_id: str) -> dict[str, Any]:
    """The full raw capsule by id or unambiguous prefix -- `capsule show --json`'s
    own output, unchanged."""
    record = ledger.fetch(capsule_id)
    if record is None:
        return _error("not_found", f"no such capsule {capsule_id!r}")
    return _record_dict(record)


def record_verify(ledger: LedgerAPI, *, capsule_id: str) -> dict[str, Any]:
    """Independently re-verify one capsule's digest and chain -- `capsule verify`'s
    own check, run against the live ledger. Returns `ok` plus every finding
    (never just a bare boolean) so a `False` always comes with a reason."""
    record = ledger.fetch(capsule_id)
    if record is None:
        return _error("not_found", f"no such capsule {capsule_id!r}")
    result = ledger.verify(capsule_id)
    if result is None:
        return _error("not_found", f"no such capsule {capsule_id!r}")
    return {
        "capsule_id": record.capsule_id,
        "ok": result.ok,
        "findings": [
            {"code": f.code, "detail": f.detail, "severity": f.severity} for f in result.findings
        ],
    }


# ---------------------------------------------------------------------------
# intent.declare -- the only write tool
# ---------------------------------------------------------------------------


def intent_declare(
    ledger: LedgerAPI,
    guard: GuardEngine,
    *,
    verb: str,
    operator: str,
    developer: str,
    action_class: str | None = None,
    amount_minor: int | None = None,
    currency: str | None = None,
    target: str | None = None,
    cited_mandate_capsule_id: str | None = None,
    equivalence_key: str | None = None,
) -> dict[str, Any]:
    """Declare an intended action and run it through the guard for a real
    decision -- allow, deny, or escalate. THE ONLY WRITE TOOL on this server:
    every other tool only reads. On anything but an infra-level failure, the
    decision is appended to the ledger as a signed capsule (`GuardEngine.check`,
    same as a live `capsule guard` call would do) -- `capsule_id` in the response
    is directly queryable afterward via `record.get` or `ledger.query`.

    ``action_class`` is looked up in the starter taxonomy
    (`capsule constraints list` shows it); absent or unrecognized both resolve to
    the consequential, fail-closed default -- there is no silent bypass for
    an unclassified action.
    """
    action = Action(
        verb=verb,
        operator=operator,
        developer=developer,
        action_class=action_class,
        amount_minor=amount_minor,
        currency=currency,
        target=target,
        cited_mandate_capsule_id=cited_mandate_capsule_id,
        equivalence_key=equivalence_key,
    )
    decision = guard.check(action)
    return {
        "outcome": decision.outcome,
        "capsule_id": (decision.capsule or {}).get("capsule_id"),
        "dry_run": decision.dry_run,
        "degraded": decision.degraded,
        "degradation_kind": decision.degradation_kind,
        "reason": decision.reason,
        "checkpoint": decision.checkpoint,
        "constraints": [
            {"id": c.id, "result": c.result, "reason": c.reason} for c in decision.constraints
        ],
    }
