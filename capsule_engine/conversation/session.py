# SPDX-License-Identifier: Apache-2.0
"""``ConversationSession``: records one conversation turn by turn against a
live ledger, sealing and appending each turn immediately so there is never
an unsigned window between a turn happening and the ledger holding a signed
record of it, then binds the whole session into one Merkle session digest
on ``close()``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from capsule_ledger.ledger.api import LedgerAPI
from capsule_ledger.ledger.records import LedgerRecord

from ..guards.signing import Signer
from .capsules import build_session_close_capsule, build_turn_capsule

__all__ = ["ConversationSession", "SessionAlreadyClosedError"]


class SessionAlreadyClosedError(RuntimeError):
    """A ``ConversationSession`` that has already been ``close()``d cannot
    record another turn or be closed a second time."""


@dataclass
class ConversationSession:
    """One conversation, one instance. ``signer_provider`` is called once
    per turn and once at close, never cached -- mirroring ``GuardEngine``/
    ``HoldEngine``: a signing key that becomes unavailable mid-session fails
    that one call closed (``SigningKeyUnavailable`` propagates to the
    caller), rather than silently reusing a stale ``Signer`` or swallowing
    the failure and continuing unsigned.
    """

    ledger: LedgerAPI
    session_id: str
    operator: str
    developer: str
    signer_provider: Callable[[], Signer]
    _turn_records: list[LedgerRecord] = field(default_factory=list, init=False)
    _closed: bool = field(default=False, init=False)

    def record_turn(
        self,
        *,
        speaker_role: str,
        content_digest: str,
        timestamp: str | None = None,
        action_id: str | None = None,
    ) -> LedgerRecord:
        """Seal and append the next turn. ``turn_index`` is derived from how
        many turns this session has already recorded -- callers never
        supply it directly, so it can't drift from the chain it builds."""
        if self._closed:
            raise SessionAlreadyClosedError(f"session {self.session_id!r} is already closed -- cannot record another turn")
        previous_id = self._turn_records[-1].capsule_id if self._turn_records else None
        capsule = build_turn_capsule(
            session_id=self.session_id,
            turn_index=len(self._turn_records),
            speaker_role=speaker_role,
            content_digest=content_digest,
            operator=self.operator,
            developer=self.developer,
            signer=self.signer_provider(),
            previous_turn_capsule_id=previous_id,
            timestamp=timestamp,
            action_id=action_id,
        )
        record = self.ledger.append(capsule, consequential=False)
        self._turn_records.append(record)
        return record

    def close(self, *, timestamp: str | None = None, action_id: str | None = None) -> LedgerRecord:
        """Bind every recorded turn into one session digest and append the
        session-close capsule. Requires at least one turn."""
        if self._closed:
            raise SessionAlreadyClosedError(f"session {self.session_id!r} is already closed")
        if not self._turn_records:
            raise ValueError(f"session {self.session_id!r} has no turns -- cannot close an empty session")
        capsule = build_session_close_capsule(
            session_id=self.session_id,
            turn_capsule_ids=[r.capsule_id for r in self._turn_records],
            operator=self.operator,
            developer=self.developer,
            signer=self.signer_provider(),
            timestamp=timestamp,
            action_id=action_id,
        )
        record = self.ledger.append(capsule, consequential=False)
        self._closed = True
        return record

    @property
    def turn_count(self) -> int:
        return len(self._turn_records)

    @property
    def closed(self) -> bool:
        return self._closed
