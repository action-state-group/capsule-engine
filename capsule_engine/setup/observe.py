# SPDX-License-Identifier: Apache-2.0
"""``capsule setup observe`` (design §3.3/§6b): dry-run mode. Wraps an
adapter's raw event stream and records everything at the EMIT LAYER ONLY --
conversation turns, tool dispatches, offer/response, and external
confirmations -- enforcing nothing, declaring nothing.

**Trap 1 (schema drift eats your traces).** Every capsule this module
appends is a passive ``fyi`` record of something that already happened;
none of them is a compiled artifact (a ``PlanDefinition``, a fold result).
``propose`` derives candidates from this corpus later, as many times as the
compiler's own schema changes, without ever re-observing.

**Trap 2 (an observe mode that never fails teaches nothing).** A raw event
this module cannot map to a known emit-layer shape is never silently
dropped -- it is counted and surfaced in ``ObserveSummary.unmapped`` at
least as loudly as a successful record, and the live heartbeat prints as
events are processed so a hung/misconfigured adapter reads as a hang, not
as quiet success.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

from capsule_compiler.compiler.offer_response import build_offer_capsule, build_response_capsule
from capsule_ledger.conversation.capsules import build_session_close_capsule, build_turn_capsule
from capsule_ledger.ledger.api import LedgerAPI

from ..guards.capsule import build_event_capsule
from ..guards.signing import Signer

__all__ = [
    "EVENT_DISPATCH",
    "EVENT_CONFIRMATION",
    "KNOWN_KINDS",
    "UnmappedEvent",
    "ObserveSummary",
    "ObserveRecorder",
]

# Emit-layer event names this module mints itself (conversation turns and
# offer/response reuse their own home modules' event names -- this module
# adds only the two record kinds neither of those already covers: a tool
# dispatch, and an external system's confirmation of one).
EVENT_DISPATCH = "setup.observe.dispatch"
EVENT_CONFIRMATION = "setup.observe.confirmation"

KNOWN_KINDS = frozenset({"turn", "session_close", "dispatch", "offer", "response", "confirmation"})


@dataclass(frozen=True)
class UnmappedEvent:
    """A raw event this observe run could NOT map to an emit-layer record --
    Trap 2's "surface what could not be mapped at least as loudly as what
    could" made concrete. ``reason`` is a short, stable code (never prose),
    same "zero free prose" discipline as ``compiler.refusal``'s labels."""

    index: int
    kind: str | None
    reason: str
    raw: dict[str, Any]


@dataclass
class ObserveSummary:
    session_id: str | None = None
    turns_recorded: int = 0
    dispatches_recorded: int = 0
    offers_recorded: int = 0
    responses_recorded: int = 0
    confirmations_recorded: int = 0
    unmapped: list[UnmappedEvent] = field(default_factory=list)

    @property
    def total_recorded(self) -> int:
        return (
            self.turns_recorded
            + self.dispatches_recorded
            + self.offers_recorded
            + self.responses_recorded
            + self.confirmations_recorded
        )

    @property
    def total_seen(self) -> int:
        return self.total_recorded + len(self.unmapped)


class ObserveRecorder:
    """Consumes an iterable of raw event dicts (the ``conversation-log``
    adapter's own wire shape -- see ``adapters.py``; every other adapter
    normalizes into the same shape before handing events here, so this
    class is the one place emit-layer recording logic lives) and appends
    one emit-layer capsule per recognized event, live, in order.

    Raw event shape, one dict per line of a JSONL trace::

        {"kind": "turn", "session_id": ..., "turn_index": ..., "speaker_role": ..., "content_digest": ...}
        {"kind": "session_close", "session_id": ...}
        {"kind": "dispatch", "action_class": ..., "tool": ..., "dispatch_id": ...(optional), "target_digest": ...(optional), "chain_parent": ...(optional)}
        {"kind": "offer", "offer_id": ..., "offer_digest": ...}
        {"kind": "response", "offer_id": ..., "response_class": ..., "response_digest": ...(optional)}
        {"kind": "confirmation", "commitment_ref": ...(a prior dispatch_id) | "commitment_capsule_id": ..., "status": ..., "external_ref": ...(optional)}

    ``heartbeat_every`` controls how often (in events processed) a progress
    line is written to ``heartbeat_stream`` (default ``sys.stderr``) --
    Trap 2's other half: a silent dry run reads as a hang and gets killed
    (design §6b), so this defaults ON rather than opt-in.
    """

    def __init__(
        self,
        *,
        ledger: LedgerAPI,
        signer: Signer,
        operator: str,
        developer: str,
        heartbeat_every: int = 10,
        heartbeat_stream: TextIO | None = None,
    ) -> None:
        self._ledger = ledger
        self._signer = signer
        self._operator = operator
        self._developer = developer
        self._heartbeat_every = heartbeat_every
        self._heartbeat_stream = heartbeat_stream if heartbeat_stream is not None else sys.stderr
        self._turn_capsule_ids: list[str] = []
        self._offer_capsule_ids: dict[str, str] = {}
        self._dispatch_capsule_ids: dict[str, str] = {}
        self.summary = ObserveSummary()

    def _heartbeat(self, index: int) -> None:
        if self._heartbeat_every <= 0:
            return
        if index % self._heartbeat_every == 0:
            print(
                f"observe: {self.summary.total_seen} event(s) seen, {self.summary.total_recorded} recorded, "
                f"{len(self.summary.unmapped)} unmapped",
                file=self._heartbeat_stream,
            )

    def _unmap(self, index: int, kind: str | None, reason: str, raw: dict[str, Any]) -> None:
        self.summary.unmapped.append(UnmappedEvent(index=index, kind=kind, reason=reason, raw=raw))

    def _record_turn(self, raw: dict[str, Any]) -> dict | None:
        session_id = raw.get("session_id")
        if self.summary.session_id is None:
            self.summary.session_id = session_id
        capsule = build_turn_capsule(
            session_id=session_id,
            turn_index=raw["turn_index"],
            speaker_role=raw["speaker_role"],
            content_digest=raw["content_digest"],
            operator=self._operator,
            developer=self._developer,
            signer=self._signer,
            previous_turn_capsule_id=self._turn_capsule_ids[-1] if self._turn_capsule_ids else None,
        )
        self._ledger.append(capsule, consequential=False)
        self._turn_capsule_ids.append(capsule["capsule_id"])
        self.summary.turns_recorded += 1
        return capsule

    def _record_session_close(self, raw: dict[str, Any]) -> dict | None:
        if not self._turn_capsule_ids:
            return None
        capsule = build_session_close_capsule(
            session_id=raw["session_id"],
            turn_capsule_ids=self._turn_capsule_ids,
            operator=self._operator,
            developer=self._developer,
            signer=self._signer,
        )
        self._ledger.append(capsule, consequential=False)
        return capsule

    def _record_dispatch(self, raw: dict[str, Any]) -> dict:
        detail = {
            "action_class": raw["action_class"],
            "tool": raw.get("tool", raw["action_class"]),
        }
        if raw.get("target_digest") is not None:
            detail["target_digest"] = raw["target_digest"]
        capsule = build_event_capsule(
            operator=self._operator,
            developer=self._developer,
            signer=self._signer,
            event=EVENT_DISPATCH,
            detail=detail,
            chain_parent=raw.get("chain_parent"),
            chain_relation="follows" if raw.get("chain_parent") else None,
        )
        self._ledger.append(capsule, consequential=False)
        # ``dispatch_id`` is a trace-author-chosen correlation key (unlike a
        # capsule_id, knowable before this capsule is sealed) -- the same
        # role ``offer_id`` plays for offer/response, so a later
        # "confirmation" event in the same trace can cite this dispatch by
        # a stable name instead of a digest nobody could have written down
        # in advance.
        dispatch_id = raw.get("dispatch_id")
        if dispatch_id is not None:
            self._dispatch_capsule_ids[dispatch_id] = capsule["capsule_id"]
        self.summary.dispatches_recorded += 1
        return capsule

    def _record_offer(self, raw: dict[str, Any]) -> dict:
        # ``option_digests`` postdates this trace format -- an un-instrumented
        # trace naming only a single, undifferentiated offer is the one-option
        # case: the offer's own digest doubles as its sole option's digest.
        capsule = build_offer_capsule(
            offer_id=raw["offer_id"],
            offer_digest=raw["offer_digest"],
            option_digests=raw.get("option_digests") or [raw["offer_digest"]],
            operator=self._operator,
            developer=self._developer,
            signer=self._signer,
        )
        self._ledger.append(capsule, consequential=False)
        self._offer_capsule_ids[raw["offer_id"]] = capsule["capsule_id"]
        self.summary.offers_recorded += 1
        return capsule

    def _record_response(self, raw: dict[str, Any], index: int) -> dict | None:
        offer_capsule_id = self._offer_capsule_ids.get(raw["offer_id"])
        if offer_capsule_id is None:
            self._unmap(index, "response", "response_cites_unknown_offer_id", raw)
            return None
        capsule = build_response_capsule(
            offer_id=raw["offer_id"],
            offer_capsule_id=offer_capsule_id,
            response_class=raw["response_class"],
            response_digest=raw.get("response_digest"),
            operator=self._operator,
            developer=self._developer,
            signer=self._signer,
        )
        self._ledger.append(capsule, consequential=False)
        self.summary.responses_recorded += 1
        return capsule

    def _record_confirmation(self, raw: dict[str, Any], index: int) -> dict | None:
        detail = {"status": raw["status"]}
        if raw.get("external_ref") is not None:
            detail["external_ref"] = raw["external_ref"]
        # ``commitment_capsule_id`` is for a live/programmatic caller that
        # already holds the real capsule_id it is confirming (e.g. an
        # in-process adapter chaining immediately after dispatch);
        # ``commitment_ref`` is the trace-file form, resolved against
        # ``dispatch_id``s already seen -- same two-sided shape as
        # ``offer_id``/``offer_capsule_id`` for response.
        commitment_id = raw.get("commitment_capsule_id")
        commitment_ref = raw.get("commitment_ref")
        if commitment_id is None and commitment_ref is not None:
            commitment_id = self._dispatch_capsule_ids.get(commitment_ref)
            if commitment_id is None:
                self._unmap(index, "confirmation", "confirmation_cites_unknown_commitment_ref", raw)
                return None
        capsule = build_event_capsule(
            operator=self._operator,
            developer=self._developer,
            signer=self._signer,
            event=EVENT_CONFIRMATION,
            detail=detail,
            chain_parent=commitment_id,
            chain_relation="confirms" if commitment_id else None,
        )
        self._ledger.append(capsule, consequential=False)
        self.summary.confirmations_recorded += 1
        return capsule

    def record_one(self, raw: dict[str, Any], *, index: int) -> None:
        kind = raw.get("kind")
        try:
            if kind == "turn":
                self._record_turn(raw)
            elif kind == "session_close":
                self._record_session_close(raw)
            elif kind == "dispatch":
                self._record_dispatch(raw)
            elif kind == "offer":
                self._record_offer(raw)
            elif kind == "response":
                self._record_response(raw, index)
            elif kind == "confirmation":
                self._record_confirmation(raw, index)
            elif kind not in KNOWN_KINDS:
                self._unmap(index, kind, "unknown_kind", raw)
        except (KeyError, ValueError) as exc:
            self._unmap(index, kind, f"malformed_event:{exc}", raw)
        self._heartbeat(index)

    def run(self, raw_events: list[dict[str, Any]]) -> ObserveSummary:
        for index, raw in enumerate(raw_events, start=1):
            self.record_one(raw, index=index)
        # Final heartbeat line even when heartbeat_every doesn't land on the
        # last index -- Trap 2 again: the run's own end must never be silent.
        print(
            f"observe: done -- {self.summary.total_seen} event(s) seen, {self.summary.total_recorded} recorded, "
            f"{len(self.summary.unmapped)} unmapped",
            file=self._heartbeat_stream,
        )
        return self.summary
