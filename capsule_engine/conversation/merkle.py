# SPDX-License-Identifier: Apache-2.0
"""The session digest: an MMR root over one session's ordered turn capsules.

Reuses ``capsule_emit.checkpoint.core`` unchanged -- the same
MMRIVER-compatible, position-committed hashing scheme the ledger's own
whole-ledger completeness certificate uses (``capsule_emit.checkpoint``'s
``MmrLedger``), just scoped to one session's turn ids instead of the whole
ledger's append order. capsule-ledger consumes this MMR/CLL core from the
neutral producer library rather than forking it (Amendment E, 2026-08-21). A
session's turns are not generally contiguous in the shared ledger (other
sessions/agents interleave), so this builds a fresh, throwaway MMR from the
explicit ordered id list the session-close capsule itself carries, rather
than indexing ledger ``seq``.

This is what makes selective disclosure at TURN granularity possible: a
holder can disclose one turn's content plus a ``turn_inclusion_proof``
against the session-close capsule's ``session_digest``, without revealing
any other turn.
"""
from __future__ import annotations

from collections.abc import Sequence

from capsule_emit.checkpoint import MemoryNodeStore, core

__all__ = ["session_root", "turn_inclusion_proof", "verify_turn_inclusion"]


def _build_store(turn_capsule_ids: Sequence[str]) -> MemoryNodeStore:
    if not turn_capsule_ids:
        raise ValueError("turn_capsule_ids must be non-empty -- a session digest requires at least one turn")
    store = MemoryNodeStore()
    for turn_id in turn_capsule_ids:
        core.add_leaf(store, core.leaf_hash(bytes.fromhex(turn_id)))
    return store


def session_root(turn_capsule_ids: Sequence[str]) -> str:
    """The session digest, hex-encoded: leaves are ``leaf_hash(capsule_id)``
    for each turn in ``turn_capsule_ids`` (append order), peak-bagged the
    same way as the ledger-wide MMR. Recomputable by any reader holding the
    same ordered id list -- the session-close capsule commits both the root
    and the list, so a reader never has to trust the root alone.
    """
    store = _build_store(turn_capsule_ids)
    pks = core.peaks(store.size())
    peak_hashes = [store.node(p) for p in pks]
    return core.root_from_peaks(peak_hashes).hex()


def turn_inclusion_proof(turn_capsule_ids: Sequence[str], turn_index: int) -> core.InclusionProof:
    """An inclusion proof that the turn at ``turn_index`` is bound into
    ``session_root(turn_capsule_ids)``. Verify with ``verify_turn_inclusion``.
    """
    store = _build_store(turn_capsule_ids)
    return core.inclusion_proof(store, turn_index, store.size())


def verify_turn_inclusion(
    *,
    session_digest: str,
    turn_count: int,
    turn_index: int,
    turn_capsule_id: str,
    proof: core.InclusionProof,
) -> bool:
    """Pure verification -- never raises, mirrors ``core.verify_inclusion``'s
    own total-function contract. ``session_digest``/``turn_count`` are the
    two fields a verifier reads straight off a session-close capsule; no
    replay of the session's own turn list is required.
    """
    try:
        root = bytes.fromhex(session_digest)
        body_digest = bytes.fromhex(turn_capsule_id)
    except (ValueError, TypeError):
        return False
    if not isinstance(turn_count, int) or turn_count < 1:
        return False
    size = core.node_count(turn_count)
    return core.verify_inclusion(root, size, turn_index, body_digest, proof)
