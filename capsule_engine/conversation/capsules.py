# SPDX-License-Identifier: Apache-2.0
"""Builders for the conversation-capsule profile's two record types:
``conversation_turn`` (one per turn, sealed at turn time) and
``conversation_session_close`` (one per session, binding every turn's
digest into a single session digest).

Both are passive ``fyi`` records -- built via ``guards.capsule``'s
``build_event_capsule``, the same mechanism this codebase already uses for
degradation/recovery events and policy-manifest activations
(``policy/activation.py``) -- so a conversation capsule is an ordinary,
independently verifiable ledger record with no special-cased verification
path, same as every other capsule this codebase produces.

Record-type identity lives in ``asg_payload.event`` (never a repurposed
spec-defined field, matching the workspace's extension-field rule) --
``EVENT_CONVERSATION_TURN`` / ``EVENT_SESSION_CLOSE`` below, discovered via
``find_session_turns`` / ``find_session_close``, the same linear-scan-plus-
filter shape ``policy/activation.py``'s ``find_latest_activation`` uses.

"""
from __future__ import annotations

from collections.abc import Sequence

from agent_action_capsule.contracts import is_hex64
from capsule_ledger.ledger.api import LedgerAPI, ScanQuery
from capsule_ledger.ledger.records import LedgerRecord

from ..guards.capsule import build_event_capsule
from ..guards.signing import Signer
from .merkle import session_root

__all__ = [
    "SPEAKER_ROLES",
    "EVENT_CONVERSATION_TURN",
    "EVENT_SESSION_CLOSE",
    "EVENT_TURN_REFERENCE",
    "InvalidSpeakerRole",
    "build_turn_capsule",
    "build_session_close_capsule",
    "build_turn_reference_capsule",
    "find_session_turns",
    "find_session_close",
    "find_turn_reference",
]

# Interim speaker-role vocabulary (pre-VAC; migrates when the Birkholz VAC
# binding lands). Deliberately CLOSED rather than registry-open: B3's
# per-speaker judgment targeting depends on this exact set, so a typo'd
# role must fail loud at seal time, not silently land as a fourth,
# unaddressable role. Widen only alongside a B3 consumer that can target
# the new value.
SPEAKER_ROLES = frozenset({"user", "assistant", "human-agent"})

EVENT_CONVERSATION_TURN = "conversation_turn"
EVENT_SESSION_CLOSE = "conversation_session_close"
EVENT_TURN_REFERENCE = "conversation_turn_reference"


class InvalidSpeakerRole(ValueError):
    """``speaker_role`` is not one of ``SPEAKER_ROLES``."""


def _require_speaker_role(speaker_role: str) -> None:
    if speaker_role not in SPEAKER_ROLES:
        raise InvalidSpeakerRole(f"speaker_role must be one of {sorted(SPEAKER_ROLES)}; got {speaker_role!r}")


def _require_content_digest(content_digest: str) -> None:
    # The turn's own content never enters the record in any mode (H2
    # invariant) -- only a digest of it, same 64-hex-SHA-256 shape every
    # other digest field in this codebase requires.
    if not is_hex64(content_digest):
        raise ValueError(f"content_digest must be a 64-hex SHA-256 digest; got {content_digest!r}")


def build_turn_capsule(
    *,
    session_id: str,
    turn_index: int,
    speaker_role: str,
    content_digest: str,
    operator: str,
    developer: str,
    signer: Signer,
    previous_turn_capsule_id: str | None = None,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    """Seal one conversation turn as a passive ``fyi`` capsule, AT TURN TIME
    -- every turn is signed and ready to append the moment it is built, so a
    conversation never carries an unsigned window between what was said and
    what the ledger can prove was said.

    Turn 0 opens the session standalone (no chain parent); every later turn
    chains to ``previous_turn_capsule_id`` with relation ``"follows"`` --
    the same generic sequencing relation ``agent_action_capsule.emit()``'s
    own default uses for a non-adapter caller. None of the chain-relation
    registry's seeded values (``confirms``/``supersedes``/``epoch_opens``)
    describe "next turn in an ongoing conversation", so this deliberately
    does not reach for one of them.
    """
    if turn_index < 0:
        raise ValueError(f"turn_index must be >= 0; got {turn_index}")
    if turn_index == 0:
        if previous_turn_capsule_id is not None:
            raise ValueError("turn_index=0 (the session's first turn) must not chain to a previous_turn_capsule_id")
    elif previous_turn_capsule_id is None:
        raise ValueError(f"turn_index={turn_index} requires previous_turn_capsule_id (only turn 0 is standalone)")

    _require_speaker_role(speaker_role)
    _require_content_digest(content_digest)

    detail = {
        "session_id": session_id,
        "turn_index": turn_index,
        "speaker_role": speaker_role,
        "content_digest": content_digest,
    }
    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_CONVERSATION_TURN,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id or f"conversation.turn/{session_id}/{turn_index}",
        chain_parent=previous_turn_capsule_id,
        chain_relation="follows",
    )


def build_session_close_capsule(
    *,
    session_id: str,
    turn_capsule_ids: Sequence[str],
    operator: str,
    developer: str,
    signer: Signer,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    """Bind every turn in ``turn_capsule_ids`` (append order) into one
    session digest -- an MMR root over the ordered turn capsule ids
    (``merkle.session_root``) -- and seal that binding as one more ``fyi``
    capsule chained to the last turn. This is what makes selective
    disclosure at TURN granularity possible: a holder can later disclose
    one turn's content plus a ``merkle.turn_inclusion_proof`` against this
    capsule's ``session_digest``, without ever revealing the rest of the
    session.

    Requires at least one turn -- there is no such thing as an empty
    session's completeness certificate.
    """
    if not turn_capsule_ids:
        raise ValueError("turn_capsule_ids must be non-empty -- cannot close a session with zero turns")

    digest = session_root(turn_capsule_ids)
    detail = {
        "session_id": session_id,
        "turn_count": len(turn_capsule_ids),
        "session_digest": digest,
        "turn_capsule_ids": list(turn_capsule_ids),
    }
    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_SESSION_CLOSE,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id or f"conversation.session_close/{session_id}",
        chain_parent=turn_capsule_ids[-1],
        chain_relation="follows",
    )


def build_turn_reference_capsule(
    *,
    turn_capsule_id: str,
    referenced_capsule_ids: Sequence[str],
    operator: str,
    developer: str,
    signer: Signer,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    """A typed cross-reference from one turn to the capsule(s) it gave rise
    to (e.g. tool-call capsules a caller's own pipeline recorded for that
    turn) -- built via the same ``build_event_capsule`` every other passive
    record in this module uses, not a new mechanism.

    Chains to the turn with relation ``"confirms"`` (the reference only
    makes sense once the turn it names already exists), so a caller that
    already has ``turn_capsule_id`` from ``ConversationSession.record_turn``
    can call this once per turn that produced one or more referenced
    capsules -- letting a caller resolve any of those capsules back to the
    turn that prompted it via ``find_turn_reference``, without requiring the
    referenced capsule itself to carry the link (which is often built and
    appended before the turn is even known -- e.g. a tool call dispatched
    live, while the turn is only reconstructable after the fact from a full
    trajectory).
    """
    if not referenced_capsule_ids:
        raise ValueError("referenced_capsule_ids must be non-empty")

    detail = {
        "turn_capsule_id": turn_capsule_id,
        "referenced_capsule_ids": list(referenced_capsule_ids),
    }
    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_TURN_REFERENCE,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id or f"conversation.turn_reference/{turn_capsule_id}",
        chain_parent=turn_capsule_id,
        chain_relation="confirms",
    )


def _matches(record: LedgerRecord, event: str, session_id: str) -> bool:
    payload = record.capsule.get("asg_payload") or {}
    if payload.get("event") != event:
        return False
    return (payload.get("detail") or {}).get("session_id") == session_id


def find_session_turns(ledger: LedgerAPI, session_id: str) -> list[LedgerRecord]:
    """Every turn capsule recorded for ``session_id``, sorted by
    ``turn_index`` (not append order -- defensive against an out-of-order
    replay/import; a live ``ConversationSession`` always appends in order).
    """
    matches = [r for r in ledger.scan(ScanQuery(action_type="fyi")) if _matches(r, EVENT_CONVERSATION_TURN, session_id)]
    matches.sort(key=lambda r: (r.capsule.get("asg_payload") or {}).get("detail", {}).get("turn_index", 0))
    return matches


def find_session_close(ledger: LedgerAPI, session_id: str) -> LedgerRecord | None:
    """The session-close capsule for ``session_id``, or ``None`` if the
    session is still open (or never existed). ``scan()`` is append-ordered
    and a session closes at most once, so the last match wins if more than
    one is ever found (defensive; not an expected shape).
    """
    latest: LedgerRecord | None = None
    for record in ledger.scan(ScanQuery(action_type="fyi")):
        if _matches(record, EVENT_SESSION_CLOSE, session_id):
            latest = record
    return latest


def find_turn_reference(ledger: LedgerAPI, capsule_id: str) -> LedgerRecord | None:
    """The turn-reference capsule (if any) that names ``capsule_id`` among
    its ``referenced_capsule_ids`` -- lets a caller resolve any capsule
    (e.g. a tool-call capsule from an unrelated pipeline) back to the
    conversation turn that gave rise to it. ``None`` if no such reference
    was ever recorded.
    """
    for record in ledger.scan(ScanQuery(action_type="fyi")):
        payload = record.capsule.get("asg_payload") or {}
        if payload.get("event") != EVENT_TURN_REFERENCE:
            continue
        detail = payload.get("detail") or {}
        if capsule_id in (detail.get("referenced_capsule_ids") or []):
            return record
    return None
