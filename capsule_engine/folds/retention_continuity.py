# SPDX-License-Identifier: Apache-2.0
"""Retention-continuity fold — GRC folds batch item 2 (EU-26-6/75): "logs
were retained for at least six months" as an unbroken checkpoint chain,
witness receipts present for the witnessed grade.

This is a WINDOW + RETENTION judgment layered on top of
``capsule_emit.chain_segment.verify_chain_segment`` — reused UNCHANGED, per
the batch spec's "reuse the history-card ``continuity`` property windowed."
The continuity check itself (signature verify, prev_root/prev_size chain,
consistency-proof bridging, first-broken-link reporting) is never
re-derived here; this module only adds the retention-window judgment
(earliest checkpoint reaches back far enough) and the witnessed-grade
threshold on top of the segment's own ``continuity``/``history_depth``/
``witnessed`` fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from capsule_emit.chain_segment import ChainSegment, verify_chain_segment

__all__ = ["RetentionContinuityResult", "evaluate_retention_continuity"]

# EU-26-6/75's "at least six months" has no single day count (calendar months
# vary); 183 days is the conservative (longer) approximation, so a borderline
# log is never credited early.
SIX_MONTHS_DAYS = 183


def _parse_timestamp(ts: str) -> datetime:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class RetentionContinuityResult:
    verdict: str  # "met" | "not_met" | "not_evaluable"
    continuity: str
    history_depth: int
    witnessed: str
    earliest_checkpoint_at: str | None
    window_days: int
    detail: str


def evaluate_retention_continuity(
    segment: ChainSegment,
    *,
    as_of: str,
    window_days: int = SIX_MONTHS_DAYS,
    require_witnessed: bool = False,
    trust_anchor: dict | None = None,
) -> RetentionContinuityResult:
    """``segment`` must already cover the caller's intended range (built via
    ``capsule_emit.chain_segment.chain_segment`` over the retention window's
    lower bound through the log's latest checkpoint) — this function verifies
    it, it does not select the range itself.

    ``not_met`` when the chain is broken anywhere in the segment.
    ``not_evaluable`` when the chain is sound but has not yet
    accumulated ``window_days`` of history (a young log is never graded a
    failure for its own youth). ``met`` otherwise, and additionally requires
    at least one witnessed checkpoint in the segment when
    ``require_witnessed=True``.
    """
    verify = verify_chain_segment(segment, trust_anchor=trust_anchor)
    if not verify.ok:
        return RetentionContinuityResult(
            verdict="not_met",
            continuity=verify.continuity,
            history_depth=verify.history_depth,
            witnessed=verify.witnessed,
            earliest_checkpoint_at=None,
            window_days=window_days,
            detail=f"chain continuity broken: {verify.continuity}",
        )

    earliest = segment.links[0].checkpoint.timestamp
    window_start = _parse_timestamp(as_of) - timedelta(days=window_days)

    if _parse_timestamp(earliest) > window_start:
        return RetentionContinuityResult(
            verdict="not_evaluable",
            continuity=verify.continuity,
            history_depth=verify.history_depth,
            witnessed=verify.witnessed,
            earliest_checkpoint_at=earliest,
            window_days=window_days,
            detail=(
                f"earliest retained checkpoint ({earliest}) does not yet reach back "
                f"{window_days} days from {as_of} -- the log has not accumulated the window yet"
            ),
        )

    if require_witnessed:
        witnessed_n = int(verify.witnessed.split("/", 1)[0])
        if witnessed_n == 0:
            return RetentionContinuityResult(
                verdict="not_met",
                continuity=verify.continuity,
                history_depth=verify.history_depth,
                witnessed=verify.witnessed,
                earliest_checkpoint_at=earliest,
                window_days=window_days,
                detail="witnessed grade requested but no checkpoint in the window carries a verifying witness receipt",
            )

    return RetentionContinuityResult(
        verdict="met",
        continuity=verify.continuity,
        history_depth=verify.history_depth,
        witnessed=verify.witnessed,
        earliest_checkpoint_at=earliest,
        window_days=window_days,
        detail=f"continuity unbroken over the {window_days}d window, earliest checkpoint {earliest}",
    )
