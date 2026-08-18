# SPDX-License-Identifier: Apache-2.0
"""Activation is a capsule: adopting or changing the policy manifest appends
a signed, passive event record (``guards/capsule.py``'s ``build_event_capsule``
-- the same mechanism this codebase already uses for degradation/recovery
events, per that module's own docstring) citing the manifest's own digest.

The activation capsule's ``chain.relation`` is ``epoch_opens`` -- the same
chain vocabulary ``cli/blame_cmd.py`` and ``cli/diff_cmd.py`` already know
about (``agent_action_capsule.history``'s registry: a legal chain-start,
never a gap). Each activation chains to the *previous* activation capsule
(if any), so the ledger carries a walkable history of policy epochs; the
very first activation in a ledger cites ``GENESIS_PARENT``, a sentinel
64-hex value that is deliberately never a real capsule_id (mirrors
``tests/test_cli_blame.py``'s own use of an out-of-scope parent for an
``epoch_opens`` boundary -- the point of this relation is exactly that its
parent need not resolve).
"""
from __future__ import annotations

from ..guards.capsule import build_event_capsule
from ..guards.signing import Signer
from ..ledger.api import LedgerAPI, ScanQuery
from ..ledger.records import LedgerRecord
from .resolve import ResolvedManifest

__all__ = ["EVENT_MANIFEST_ACTIVATED", "GENESIS_PARENT", "build_manifest_activation_capsule", "find_latest_activation"]

EVENT_MANIFEST_ACTIVATED = "policy_manifest_activated"
GENESIS_PARENT = "0" * 64


def build_manifest_activation_capsule(
    *,
    resolved: ResolvedManifest,
    operator: str,
    developer: str,
    signer: Signer,
    previous_activation_capsule_id: str | None = None,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    detail = {
        "manifest_id": resolved.manifest.manifest_id,
        "manifest_digest": resolved.manifest_digest,
        "folds": [{"fold_id": f.fold_id, "digest": f.digest} for f in resolved.manifest.folds],
        "wickets": [{"wicket_id": w.wicket_id, "digest": w.digest} for w in resolved.manifest.wickets],
    }
    # Omitted when no pack is installed -- same "byte-identical to before
    # this field existed" rule ``Manifest.canonical_dict()`` follows.
    if resolved.manifest.packs:
        detail["packs"] = [
            {"pack_id": p.pack_id, "digest": p.digest, "mode": p.mode} for p in resolved.manifest.packs
        ]
    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_MANIFEST_ACTIVATED,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id,
        chain_parent=previous_activation_capsule_id or GENESIS_PARENT,
        chain_relation="epoch_opens",
    )


def find_latest_activation(ledger: LedgerAPI) -> LedgerRecord | None:
    """The most recently appended manifest-activation record, or ``None`` if
    this ledger has never had one -- used to chain a new activation to its
    predecessor. ``scan()`` is append-ordered, so the last match wins."""
    latest: LedgerRecord | None = None
    for record in ledger.scan(ScanQuery(action_type="fyi")):
        if (record.capsule.get("asg_payload") or {}).get("event") == EVENT_MANIFEST_ACTIVATED:
            latest = record
    return latest
