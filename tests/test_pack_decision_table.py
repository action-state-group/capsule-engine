# SPDX-License-Identifier: Apache-2.0
"""Golden decision table (fixture-shape discipline, 2026-08-11): the pack's
checked-in ``decision_table.yaml`` must match what the checked-in fixture
ledger actually contains, row for row -- and every declared check must fire
both 'pass' and 'fail' somewhere in the table ("no-dead-rules": a
constraint that's declared but never genuinely exercised both ways is a
bug class of its own, the exact gap that let ``verify_before_dispatch``
go untested on its allow side until this table caught it)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

PACK_DIR = Path(__file__).parent.parent / "capsule_ledger" / "packs" / "catalog" / "payments-safety"
DECISION_TABLE_PATH = PACK_DIR / "fixtures" / "decision_table.yaml"
FIXTURE_PATH = PACK_DIR / "fixtures" / "mini_ledger.jsonl"

_VERDICT_BY_DECISION = {"accept": "allow", "reject": "deny", "hitl_dispatched": "escalate"}


def _load_table() -> list[dict]:
    return yaml.safe_load(DECISION_TABLE_PATH.read_text())["rows"]


def _load_fixture_by_action_id() -> dict[str, dict]:
    """Maps each guard-decision capsule's exact ``action_id`` to itself --
    excludes the policy_manifest_activated event, which carries no
    ``constraints``."""
    by_action_id: dict[str, dict] = {}
    for line in FIXTURE_PATH.read_text().splitlines():
        capsule = json.loads(line)
        if "constraints" not in capsule:
            continue  # the policy_manifest_activated event, not a guard decision
        by_action_id[capsule["action_id"]] = capsule
    return by_action_id


def test_decision_table_matches_the_real_fixture_row_for_row():
    table = _load_table()
    fixture = _load_fixture_by_action_id()

    table_action_ids = {row["action_id"] for row in table}
    assert table_action_ids <= set(fixture), f"table cites action_ids not in the fixture: {table_action_ids - set(fixture)}"

    for row in table:
        capsule = fixture[row["action_id"]]
        actual_checks = {c["id"]: c["result"] for c in capsule["constraints"]}
        assert actual_checks == row["checks"], f"{row['scenario']}: table says {row['checks']}, fixture has {actual_checks}"

        actual_verdict = _VERDICT_BY_DECISION[capsule["disposition"]["decision"]]
        assert actual_verdict == row["verdict"], f"{row['scenario']}: table says {row['verdict']!r}, fixture is {actual_verdict!r}"


def test_no_dead_rules_every_check_fires_both_ways():
    table = _load_table()
    outcomes_by_check: dict[str, set[str]] = {}
    for row in table:
        for check_id, result in row["checks"].items():
            outcomes_by_check.setdefault(check_id, set()).add(result)

    for check_id, outcomes in outcomes_by_check.items():
        assert "pass" in outcomes, f"{check_id!r} never appears as 'pass' in the decision table -- dead rule"
        assert "fail" in outcomes, f"{check_id!r} never appears as 'fail' in the decision table -- dead rule"


def test_every_declared_obligation_check_appears_in_the_table():
    """Cross-check against pack.yaml itself, not just the table's own
    internal consistency -- a check the pack declares but the table never
    mentions at all is the more basic version of the same dead-rule gap."""
    from capsule_ledger.packs.loader import load_pack_dir

    pack = load_pack_dir(PACK_DIR)
    declared_checks = {o.check for o in pack.obligations}
    table_checks = {check_id for row in _load_table() for check_id in row["checks"]}
    assert declared_checks <= table_checks, f"declared but never in the decision table: {declared_checks - table_checks}"
