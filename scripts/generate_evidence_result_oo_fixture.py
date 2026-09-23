#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Regenerate ``tests/fixtures/evidence-result/oo-claims-result.json`` --
the synthetic OO Evidence Result [batch4-result-emission-from-engine] emits.

Every claim is a REAL ``evaluate_retention_continuity`` verdict over a REAL
signed ``capsule_emit`` chain (three checkpoints, ``CAPSULE_WITNESS=stub`` --
the same fixture shape ``tests/test_fold_retention_continuity.py`` uses),
projected through ``result_from_folds.claim_from_retention_continuity`` --
nothing here is invented data; see that module's docstring. The one
NOT_APPLICABLE exclusion is a real ``evaluate_record_type_coverage`` call
with an empty ``registered_kinds`` declaration (that fold's own honest
"nothing was declared to check coverage against" case).

Not run by the test suite -- ``tests/test_report_result_from_folds.py``
reads this committed, static file, the same "commit static bytes, don't
re-sign every run" convention ``scripts/generate_eu_ai_act_trace_fixtures.
py`` already follows. [batch4-capsule-viewer-three-buckets] (neutral lane)
is the intended second consumer of this exact file.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("CAPSULE_WITNESS", "stub")

from capsule_emit import seal, witness  # noqa: E402
from capsule_emit.chain_segment import ChainSegment, chain_segment  # noqa: E402
from capsule_emit.ledger import read_ledger_entries  # noqa: E402

from capsule_engine.folds.record_type_coverage import evaluate_record_type_coverage  # noqa: E402
from capsule_engine.folds.retention_continuity import evaluate_retention_continuity  # noqa: E402
from capsule_engine.report.result import View, build_result  # noqa: E402
from capsule_engine.report.result_from_folds import (  # noqa: E402
    claim_from_retention_continuity,
    is_excluded_not_applicable,
)

FIXTURE_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "evidence-result" / "oo-claims-result.json"
CONTRACT_REF = "ec:oo-retention-continuity:2026-09-22@1"


def _signed_segment(ledger_path: Path) -> ChainSegment:
    checkpoints = []
    for batch in range(3):
        for i in range(2):
            seal(None, action=f"batch{batch}-{i}", operator="OO", anchor=False, ledger=ledger_path)
        cp = witness.push(str(ledger_path))
        assert cp is not None
        checkpoints.append(cp)
    entries = list(read_ledger_entries(ledger_path))
    return chain_segment(entries, from_size=0, to_size=checkpoints[-1].mmr_size)


def build_oo_result():
    with tempfile.TemporaryDirectory() as tmp:
        segment = _signed_segment(Path(tmp) / "ledger.jsonl")

    as_of = datetime.now(timezone.utc).isoformat()

    met_result = evaluate_retention_continuity(segment, as_of=as_of, window_days=0)
    met_claim = claim_from_retention_continuity(
        met_result,
        segment,
        claim_id="claim-1",
        contract_ref=CONTRACT_REF,
        requirement_ref="req-retention-immediate",
    )

    tampered = ChainSegment(v=segment.v, links=(segment.links[0], segment.links[2]))
    not_met_result = evaluate_retention_continuity(tampered, as_of=as_of, window_days=0)
    not_met_claim = claim_from_retention_continuity(
        not_met_result,
        tampered,
        claim_id="claim-2",
        contract_ref=CONTRACT_REF,
        requirement_ref="req-retention-continuity-unbroken",
    )

    insufficient_result = evaluate_retention_continuity(segment, as_of=as_of, window_days=183)
    insufficient_claim = claim_from_retention_continuity(
        insufficient_result,
        segment,
        claim_id="claim-3",
        contract_ref=CONTRACT_REF,
        requirement_ref="req-retention-6mo",
    )

    excluded_result = evaluate_record_type_coverage([], registered_kinds=frozenset())
    assert is_excluded_not_applicable(excluded_result)

    return build_result(
        [met_claim, not_met_claim, insufficient_claim],
        generated_at=as_of,
        excluded_not_applicable=1,
        view=View(producer_name="OO", title="OO Retention Continuity -- Result v0"),
    )


def main() -> None:
    result = build_oo_result()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE_PATH}")


if __name__ == "__main__":
    main()
