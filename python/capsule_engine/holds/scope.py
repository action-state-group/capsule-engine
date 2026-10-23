# SPDX-License-Identifier: Apache-2.0
"""Per-scope single-writer locking for evaluate-and-reserve (issue #51 point 2).

``LedgerStore.append`` (``ledger/store.py``) already serializes every write to
one ledger through its own internal lock -- that guarantees physical
correctness (no torn writes, no corrupted index) but says nothing about
*business* atomicity: two concurrent evaluate-and-reserve calls for the same
scope can each read the fold aggregate, each see room under the cap, and each
append a reservation -- over-reserving the scope even though every individual
append was itself safe.

A scope is (cap definition digest, subject identity) -- narrower than the
whole ledger, per #51: two different subjects, or two different caps, don't
contend on each other's lock. This is the *local* single-writer primitive --
one process, one ledger. A distributed sequencer across processes/nodes is
out of scope here (Dapr-side, capsule-emit's job, not this task's).
"""
from __future__ import annotations

import threading

__all__ = ["ScopeKey", "ScopeLocks"]

# (cap fold definition digest, subject identity e.g. Action.developer)
ScopeKey = tuple[str, str]


class ScopeLocks:
    """A registry of per-scope locks, created lazily on first use.

    Locks are never removed -- bounded by the number of distinct
    (fold_digest, subject) pairs ever seen by this process, which is
    acceptable for v0 (a TTL/eviction policy is a later concern, same
    category as the fold catalog's own no-caching-across-calls note).
    """

    def __init__(self) -> None:
        self._registry_lock = threading.Lock()
        self._locks: dict[ScopeKey, threading.Lock] = {}

    def get(self, scope: ScopeKey) -> threading.Lock:
        with self._registry_lock:
            lock = self._locks.get(scope)
            if lock is None:
                lock = threading.Lock()
                self._locks[scope] = lock
            return lock
