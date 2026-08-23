# SPDX-License-Identifier: Apache-2.0
"""capsule-ledger MCP advisory server -- the ledger, folds, and guard checks exposed
as structured tools for any MCP-speaking harness (Claude Code, Goose, or a
custom client), stdio transport.

    python -m capsule_ledger.mcp.server        # stdio -- the default for every
                                            # MCP host that spawns a subprocess

Every tool here is a thin wrapper over the same modules the `capsule` CLI uses
(`ledger.api.LedgerAPI`, the fold engine, `guards.GuardEngine`) -- see
`tools.py`, which contains 100% of the actual logic and has no dependency on
this module or on the `mcp` package at all. This module's only job is
registering those functions as FastMCP tools with Tier-1 descriptions and
wiring up the one piece of process-lifetime state every tool needs: a single
open `LedgerAPI` binding (see `config.py` for the backend seam).

Nine tools are read-only; `intent_declare` is the only tool that writes.

Configuration is entirely environment-driven (`config.load_config()`) so a
harness's own MCP config format (Claude Code hook, Goose extension entry) can
point this at a real deployment with no code change:

    CAPSULE_LEDGER              ledger directory or JSONL fixture (required)
    CAPSULE_FOLD_DIR            fold catalog directory (default: built-in catalog)
    CAPSULE_MCP_BACKEND         "local" (default; anything else -- not yet built)
    CAPSULE_MCP_CAPS_MINOR      JSON object, e.g. {"money.transfer": 10000000}
    CAPSULE_MCP_SIGNING_KEY_ID / CAPSULE_MCP_SIGNING_SECRET   intent.declare's signer

    name isn't set.
"""
from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..cli.constraints_cmd import DEFAULT_CAPS_FOLD_ID
from ..folds.catalog import Catalog
from ..guards import GuardEngine, LocalSigner
from . import tools
from .config import ServerConfig, load_config, open_backend

__all__ = ["mcp", "main"]

mcp = FastMCP(
    "capsule-ledger",
    instructions=(
        "Read and, for one tool, write to a local capsule-ledger control plane: an "
        "append-only ledger of Agent Action Capsules, declarative fold "
        "aggregates over it, and the guard checks that gate consequential "
        "actions. Every read tool's answer carries its own verification data "
        "-- a fold envelope (definition digest, record range, checkpoint, "
        "staleness) or a re-verification result -- never a bare number or "
        "bare yes/no. Call constraints_list before assuming what's enforced; "
        "call fold_list before trying to derive a number by hand from "
        "ledger_query rows. intent_declare is the only tool that writes -- "
        "everything else only reads."
    ),
)

_config: ServerConfig | None = None
_ledger = None
_close_ledger = None
_guard: GuardEngine | None = None


def _get_config() -> ServerConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_ledger():
    global _ledger, _close_ledger
    if _ledger is None:
        _ledger, _close_ledger = open_backend(_get_config())
    return _ledger


def _get_guard() -> GuardEngine:
    global _guard
    if _guard is None:
        config = _get_config()
        signer = LocalSigner(key_id=config.signing_key_id, secret=config.signing_secret)
        entry = Catalog(config.fold_catalog_dir).get(DEFAULT_CAPS_FOLD_ID)
        if entry is None:
            raise RuntimeError(
                f"caps fold {DEFAULT_CAPS_FOLD_ID!r} not found in catalog {config.fold_catalog_dir} "
                "-- intent_declare requires it to be present (same fold GuardEngine.check "
                "always evaluates for the caps check)."
            )
        _guard = GuardEngine(
            ledger=_get_ledger(),
            caps_fold=entry.definition,
            signer_provider=lambda: signer,
            caps_minor=config.caps_minor,
        )
    return _guard


def shutdown() -> None:
    """Close the ledger binding. Call once, at process exit."""
    if _close_ledger is not None:
        _close_ledger()


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool(name="ledger_query")
def ledger_query(
    agent: Annotated[str | None, Field(description="filter: developer/agent id")] = None,
    since: Annotated[str | None, Field(description="filter: inclusive lower ISO-8601 timestamp bound")] = None,
    until: Annotated[str | None, Field(description="filter: inclusive upper ISO-8601 timestamp bound")] = None,
    counterparty: Annotated[str | None, Field(description="filter: operator id")] = None,
    verdict: Annotated[str | None, Field(description="filter: disposition.verdict_class, opaque token")] = None,
    action_type: Annotated[str | None, Field(description="filter: action_type, e.g. 'decide' or 'fyi'")] = None,
    limit: Annotated[int | None, Field(description="maximum records returned")] = None,
) -> dict[str, Any]:
    """List ledger records matching a filter -- the tool for "what did my
    agents do" questions (add `since`/`until` for a time window: "...last
    night", "...this week").

    Every field is optional and independently composable, exactly like `capsule
    log`'s flags. The response always reports both the filtered `matched`
    count and the ledger's true `total`, plus the ledger's `checkpoint` and
    any `chain_gaps` -- a filtered view is never mistaken for the whole
    ledger, and a broken chain is never silently reported as intact.
    """
    return tools.ledger_query(
        _get_ledger(),
        agent=agent,
        since=since,
        until=until,
        counterparty=counterparty,
        verdict=verdict,
        action_type=action_type,
        limit=limit,
    )


@mcp.tool(name="fold_list")
def fold_list() -> dict[str, Any]:
    """List every fold defined in the catalog -- id, content digest, and
    source file. Call this before trying to derive a number (spend, a
    count, a last-decision) by hand from `ledger_query` rows: if a fold
    already computes it, `fold_get` gives you a verifiable answer instead
    of an ad hoc one.
    """
    return tools.fold_list(_get_config().fold_catalog_dir)


@mcp.tool(name="fold_get")
def fold_get(
    fold: Annotated[str, Field(description="fold_id (e.g. 'spend.weekly/1.0.0'), a definition digest, or a path to a definition YAML file")],
    key: Annotated[str | None, Field(description="group key value to evaluate (omit for key-less folds)")] = None,
    as_of: Annotated[
        str | None,
        Field(
            description=(
                "reference timestamp for a rolling window, derived from real data (e.g. the "
                "timestamp of the record you care about) -- required for rolling-window folds; "
                "this tool never invents one from the wall clock"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Evaluate one fold over the live ledger and return its full result
    envelope: `fold` (the definition's own content digest), `range`,
    `checkpoint`, `staleness`, and `result` -- the number this answer is
    built on always comes with exactly what it was computed against.
    """
    return tools.fold_get(_get_ledger(), _get_config().fold_catalog_dir, fold=fold, key=key, as_of=as_of)


@mcp.tool(name="budget_remaining")
def budget_remaining(
    agent: Annotated[str, Field(description="developer/agent id")],
    action_class: Annotated[
        str, Field(description="action class the cap applies to, e.g. 'money.transfer' (see constraints_list)")
    ] = "money.transfer",
    as_of: Annotated[
        str | None, Field(description="reference timestamp for the rolling window (default: now)")
    ] = None,
) -> dict[str, Any]:
    """How much of `action_class`'s configured weekly cap `agent` has left --
    the tool for "budget left" questions. Evaluated by replaying the real
    `spend.weekly` fold (the same one the guard's own `caps` check uses),
    never by summing raw records. If no cap is configured for this action
    class on this deployment, returns `cap_configured: false` rather than a
    fabricated number.
    """
    return tools.budget_remaining(
        _get_ledger(),
        _get_config().fold_catalog_dir,
        _get_config().caps_minor,
        agent=agent,
        action_class=action_class,
        as_of=as_of,
    )


@mcp.tool(name="action_been_done")
def action_been_done(
    verb: Annotated[str, Field(description="the action verb, e.g. 'transfer_funds'")],
    operator: Annotated[str, Field(description="the operator/tenant id")],
    developer: Annotated[str, Field(description="the developer/agent id")],
    action_type: Annotated[str, Field(description="'decide' (default) or 'fyi'")] = "decide",
    target: Annotated[str | None, Field(description="dedupe discriminator, e.g. a recipient/counterparty reference")] = None,
    equivalence_key: Annotated[
        str | None, Field(description="override the default equivalence formula for this lookup")
    ] = None,
    since_days: Annotated[int, Field(description="lookback window in days (default: 30, matching the guard's own dedupe window)")] = 30,
) -> dict[str, Any]:
    """Has an equivalent action already been recorded? Wraps the guard's own
    `dedupe` check (exact-match equivalence, no fuzzy matching) run as a
    standalone lookup rather than as a gate -- use this before declaring an
    intent you suspect might be a repeat.
    """
    return tools.action_been_done(
        _get_ledger(),
        verb=verb,
        operator=operator,
        developer=developer,
        action_type=action_type,
        target=target,
        equivalence_key=equivalence_key,
        since_days=since_days,
    )


@mcp.tool(name="constraints_list")
def constraints_list() -> dict[str, Any]:
    """What's actually enforced: every registered guard check (id, type,
    method) and the action-class taxonomy (consequential /
    fail-open-allowed flags per class, including the fail-closed
    `unclassified` default). Call this before assuming a guard exists, a
    class is fail-open, or a cap applies -- it is the ground truth, not an
    inference from source you haven't read.
    """
    return tools.constraints_list(_get_config().fold_catalog_dir)


@mcp.tool(name="decision_explain")
def decision_explain(
    capsule_id: Annotated[str, Field(description="a capsule_id, or an unambiguous prefix (minimum 8 hex chars)")],
) -> dict[str, Any]:
    """Why a decision came out the way it did -- the tool for "why was this
    refused" questions. Returns the capsule's own constraints breakdown
    (`id`/`result`/`method` per check that ran) plus its verdict; the
    constraints ARE the explanation, there is no separately-generated
    reasoning behind them.
    """
    return tools.decision_explain(_get_ledger(), capsule_id=capsule_id)


@mcp.tool(name="record_get")
def record_get(
    capsule_id: Annotated[str, Field(description="a capsule_id, or an unambiguous prefix (minimum 8 hex chars)")],
) -> dict[str, Any]:
    """Fetch one ledger record in full -- the raw capsule, unchanged. Use this
    when you need the actual signed bytes (e.g. to hand off, or to inspect a
    field `decision_explain` doesn't surface), not just a summary.
    """
    return tools.record_get(_get_ledger(), capsule_id=capsule_id)


@mcp.tool(name="record_verify")
def record_verify(
    capsule_id: Annotated[str, Field(description="a capsule_id, or an unambiguous prefix (minimum 8 hex chars)")],
) -> dict[str, Any]:
    """Independently re-verify one capsule's digest and chain against the live
    ledger -- the tool for "is this record intact" questions. Returns `ok`
    plus every finding; a `false` always comes with a reason, never a bare
    boolean.
    """
    return tools.record_verify(_get_ledger(), capsule_id=capsule_id)


# ---------------------------------------------------------------------------
# The one write tool
# ---------------------------------------------------------------------------


@mcp.tool(name="intent_declare")
def intent_declare(
    verb: Annotated[str, Field(description="the action verb, e.g. 'transfer_funds'")],
    operator: Annotated[str, Field(description="the operator/tenant id")],
    developer: Annotated[str, Field(description="the developer/agent id")],
    action_class: Annotated[
        str | None,
        Field(description="e.g. 'money.transfer'; absent or unrecognized both resolve to the consequential, fail-closed default"),
    ] = None,
    amount_minor: Annotated[int | None, Field(description="integer minor units (never a float) -- required for the caps check to apply")] = None,
    currency: Annotated[str | None, Field(description="ISO currency code, paired with amount_minor")] = None,
    target: Annotated[str | None, Field(description="dedupe discriminator, e.g. a recipient/counterparty reference")] = None,
    cited_mandate_capsule_id: Annotated[
        str | None, Field(description="a prior capsule this action claims authorization from")
    ] = None,
    equivalence_key: Annotated[
        str | None, Field(description="override the default dedupe equivalence formula for this action")
    ] = None,
) -> dict[str, Any]:
    """Declare an intended action and get a real guard decision: allow, deny,
    or escalate. THE ONLY WRITE TOOL on this server -- every other tool only
    reads. The decision is appended to the ledger as a signed capsule (same
    as a live `capsule guard` call); the returned `capsule_id` is immediately
    queryable via `record_get` or `ledger_query` -- this tool never reports
    success without the record actually landing.
    """
    return tools.intent_declare(
        _get_ledger(),
        _get_guard(),
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


def main() -> None:
    try:
        mcp.run()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
