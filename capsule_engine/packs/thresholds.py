# SPDX-License-Identifier: Apache-2.0
"""Threshold proposers: fold a pack's own proposer over observed traffic,
propose a cap with rationale (percentile, sample size, max-seen). Never
auto-enforced -- a proposal is data a human reviews (``capsule report
--dry-run --proposals``) and accepts (``capsule enforce --pack``); nothing
here writes to a manifest or changes what any check enforces.

Deterministic given the ledger, same discipline the fold engine itself
holds to (``folds/engine.py``'s module docstring): every sample is derived
from record timestamps already in the ledger, never a wall clock, and
percentile is nearest-rank over sorted integers -- no float interpolation
(the repo-wide no-float rule for anything that becomes part of a policy
decision).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..folds.definition import FoldDefinition
from ..folds.engine import evaluate_all
from .errors import MALFORMED_PACK, PackDefinitionError
from .schema import PackDefinition

__all__ = ["ThresholdProposal", "propose_thresholds", "write_proposals_file", "load_proposals_file"]


@dataclass(frozen=True)
class ThresholdProposal:
    pack_id: str
    proposer_id: str
    fold_id: str
    action_class: str
    proposed_cap_minor: int
    rationale: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "action_class": self.action_class,
            "fold_id": self.fold_id,
            "proposed_cap_minor": self.proposed_cap_minor,
            "rationale": self.rationale,
        }


def _nearest_rank_percentile(sorted_values: list[int], pct: int) -> int:
    """Nearest-rank percentile over a sorted list of non-negative integers.
    ``pct=100`` returns the max; ``pct<=0`` returns the min. No interpolation
    (never a float) -- the picked value is always one that was actually
    observed."""
    if not sorted_values:
        return 0
    rank = max(1, min(len(sorted_values), math.ceil(pct / 100 * len(sorted_values))))
    return sorted_values[rank - 1]


def propose_thresholds(
    pack: PackDefinition,
    fold: FoldDefinition,
    records: list[dict],
    *,
    action_class: str,
    percentile: int = 95,
) -> ThresholdProposal:
    """One proposal for ``action_class``, folding ``fold`` over ``records``.

    The sample set is one observation per record that carries a usable
    timestamp: that record's group (``fold.key``) evaluated as-of that
    record's own timestamp -- "what this group's rolling value was right
    after this record posted". ``records`` should already be the pack's own
    accepted/observed traffic (e.g. everything a ``capsule init --pack``
    install recorded); this function does not itself filter by pack or
    action class -- the fold's own ``filter``/``key`` already scope it.
    """
    proposer = next((p for p in pack.proposers if p.fold_id == fold.fold_id), None)
    if proposer is None:
        raise PackDefinitionError(
            MALFORMED_PACK,
            f"pack {pack.pack_id!r} declares no proposer for fold_id {fold.fold_id!r} in 'proposers'",
        )

    samples: list[int] = []
    for record in records:
        ts = record.get("timestamp")
        if not isinstance(ts, str):
            continue
        trace = evaluate_all(fold, records, as_of=ts)
        for value in trace.values():
            if isinstance(value.result, int):
                samples.append(value.result)
    samples.sort()

    proposed = _nearest_rank_percentile(samples, percentile)

    return ThresholdProposal(
        pack_id=pack.pack_id,
        proposer_id=proposer.id,
        fold_id=fold.fold_id,
        action_class=action_class,
        proposed_cap_minor=proposed,
        rationale={
            "strategy": proposer.strategy,
            "percentile": percentile,
            "sample_size": len(samples),
            "max_seen_minor": samples[-1] if samples else 0,
        },
    )


def write_proposals_file(path: str | Path, pack: PackDefinition, proposals: list[ThresholdProposal]) -> None:
    data = {
        "pack_id": pack.pack_id,
        "proposals": [p.to_dict() for p in proposals],
    }
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False))


def load_proposals_file(path: str | Path) -> tuple[str, list[dict]]:
    """Returns ``(pack_id, proposals)`` -- ``proposals`` is the plain-dict
    form (``ThresholdProposal.to_dict()``'s shape), not re-hydrated into
    ``ThresholdProposal`` (callers need only ``action_class`` and
    ``proposed_cap_minor``, both plain data by design)."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise PackDefinitionError(MALFORMED_PACK, f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or "pack_id" not in data or "proposals" not in data:
        raise PackDefinitionError(MALFORMED_PACK, f"{path} must have 'pack_id' and 'proposals'")
    proposals = data["proposals"]
    if not isinstance(proposals, list):
        raise PackDefinitionError(MALFORMED_PACK, f"{path}: 'proposals' must be a list")
    for entry in proposals:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("action_class"), str)
            or not isinstance(entry.get("proposed_cap_minor"), int)
        ):
            raise PackDefinitionError(
                MALFORMED_PACK, f"{path}: each proposals[] entry needs a string 'action_class' and integer 'proposed_cap_minor'"
            )
    return data["pack_id"], proposals
