# SPDX-License-Identifier: Apache-2.0
"""counterparty_seen_before/3.0.0: an act that was carried out makes its
counterparty known.

A replay decides every check as a dry run, and counterparty.seen_before/2.0.0
counts no dry run, so in a replay a first purchase that asked, was approved
and was paid never made its merchant known. When the deal's sealed records
show that chain, the replay writes the act to its own view with
``disposition.decision`` ``carried_out``; counterparty.seen_before/3.0.0
counts that beside an accepted decision. No other fold reads that value, so
it is never spend. Version 2 of the fold and the wicket, and everyday 0.3.3,
stay as they were.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from capsule_engine.folds.engine import evaluate_one
from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards.checks import seen_before_fold
from capsule_engine.guards.wickets import load_definition_file as load_wicket
from capsule_engine.guards.wickets.retired import retired_entry
from capsule_engine.packs import load_pack_dir

REPO = Path(__file__).parent.parent
ROOT = REPO / "capsule_engine"
WICKETS = ROOT / "guards" / "wickets" / "catalog_defs"
FOLDS = ROOT / "folds" / "catalog_defs"
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "everyday")
FROZEN_0_3_3 = REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.3"
FROZEN_0_3_3_FILE_SHA256 = "f9768c624b78998528620acc0258ee0878a3e49d16df7ebbe00c84ef4e11b8ca"
V2_FOLD_DIGEST = "6a1a357e4a3b950b7397c2790fe46c03f7e645d04aad1a9bb41533df9b4a4033"
V2_WICKET_DIGEST = "c82a29eae8ef72736851d275ffa4d85e511a21d202822366d3b03bf3834c8cd0"
V3_FOLD_DIGEST = "73dbf8cd8a36fadca0ac823d2b17592fdc457c24081bc44667f032601e914033"
V3_WICKET_DIGEST = "9cfab71d142ca7af8912218f4c4ea194f60fb02784d8536233cb7e18eddc24a8"
TARGET = "merchant/ref-1"


def _record(decision: str, *, dry_run: bool = False) -> dict:
    checkpoint = {"dry_run": True} if dry_run else {}
    return {"operator": "household-a", "disposition": {"decision": decision},
            "asg_payload": {"target": TARGET, "checkpoint": checkpoint}}


def _count(fold_file: str, records: list[dict]) -> int:
    return evaluate_one(load_fold(FOLDS / fold_file), records, key_value=TARGET).result or 0


def test_v3_cites_the_v3_fold_and_v2_is_untouched():
    v2 = load_wicket(WICKETS / "counterparty_seen_before.v2.yaml")
    v3 = load_wicket(WICKETS / "counterparty_seen_before.v3.yaml")
    assert (v2.definition_digest(), v3.definition_digest()) == (V2_WICKET_DIGEST, V3_WICKET_DIGEST)
    assert load_fold(FOLDS / "counterparty.seen_before.v2.yaml").definition_digest() == V2_FOLD_DIGEST
    assert v3.config == {**v2.config, "fold_id": "counterparty.seen_before/3.0.0", "fold_digest": V3_FOLD_DIGEST}
    assert v3.semantics is not None
    assert seen_before_fold(v3.config["fold_id"], v3.config["fold_digest"]).definition_digest() == V3_FOLD_DIGEST
    assert retired_entry(v2.wicket_id, V2_WICKET_DIGEST) is None


def test_v3_counts_a_carried_out_act_and_v2_does_not():
    records = [_record("carried_out")]
    assert (_count("counterparty.seen_before.v2.yaml", records), _count("counterparty.seen_before.v3.yaml", records)) == (0, 1)


def test_v3_still_counts_an_accepted_decision_and_never_a_dry_run_or_a_hold():
    records = [_record("accept"), _record("accept", dry_run=True), _record("needs_input"), _record("deny")]
    assert _count("counterparty.seen_before.v3.yaml", records) == 1


def test_no_spend_fold_counts_a_carried_out_act():
    record = {**_record("carried_out"), "timestamp": "2026-10-09T12:00:00Z"}
    record["asg_payload"]["amount_minor"] = 2_000
    for name in ("spend.weekly.v3.yaml", "spend.weekly.v2.yaml", "spend.weekly.yaml"):
        fold = load_fold(FOLDS / name)
        trace = evaluate_one(fold, [record], key_value="household-a", as_of="2026-10-09T12:00:01Z")
        assert (trace.result or 0) == 0, name


def test_everyday_0_3_4_cites_v3_and_0_3_3_is_kept_citing_v2():
    assert PACK.pack_id == "asg/everyday/0.3.4"
    cited = {c.wicket_id: c.definition_digest() for c in PACK.constraints}
    assert cited["counterparty_seen_before/3.0.0"] == V3_WICKET_DIGEST
    assert "counterparty_seen_before/2.0.0" not in cited
    assert hashlib.sha256((FROZEN_0_3_3 / "pack.yaml").read_bytes()).hexdigest() == FROZEN_0_3_3_FILE_SHA256
    frozen = load_pack_dir(FROZEN_0_3_3)
    assert {c.wicket_id: c.definition_digest() for c in frozen.constraints}["counterparty_seen_before/2.0.0"] == V2_WICKET_DIGEST


def test_0_3_4_changes_nothing_else_0_3_3_cites():
    frozen = {c.wicket_id: c.definition_digest() for c in load_pack_dir(FROZEN_0_3_3).constraints}
    cited = {c.wicket_id: c.definition_digest() for c in PACK.constraints}
    del frozen["counterparty_seen_before/2.0.0"], cited["counterparty_seen_before/3.0.0"]
    assert cited == frozen
