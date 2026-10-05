# SPDX-License-Identifier: Apache-2.0
"""Golden decision table (fixture-shape discipline, 2026-08-11): a pack's
checked-in ``decision_table.yaml`` must match what the checked-in fixture
ledger actually contains, row for row -- and every check an obligation
declares must fire both 'pass' and 'fail' somewhere in the table
("no-dead-rules": a constraint that's declared but never genuinely exercised
both ways is a bug class of its own, the exact gap that let
``verify_before_dispatch`` go untested on its allow side until this table
caught it).

Parameterized over every catalog pack that ships
``fixtures/decision_table.yaml``, so a new pack's table cannot go unchecked.
No-dead-rules covers the checks the pack's obligations declare; a reference
check the engine always records but the pack does not declare (everyday's
``verify_before_dispatch``) may stay ``n/a`` throughout.

A row may declare ``n_a``: for every constraint that recorded ``n/a``, its
population (``a``: the rule applied and the named field was missing; ``c``:
the rule did not apply). The declaration is checked against that constraint
record's ``evidence_digest``. A table that declares ``n_a`` on any row must
declare it on every row.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from agent_action_capsule import json_digest

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog"
PACK_DIRS = sorted(p.parent.parent for p in CATALOG.glob("*/fixtures/decision_table.yaml"))

_VERDICT_BY_DECISION = {"accept": "allow", "reject": "deny", "hitl_dispatched": "escalate"}


def test_the_parameterization_finds_both_tabled_packs():
    assert {p.name for p in PACK_DIRS} >= {"payments-safety", "everyday"}


def _load_table(pack_dir: Path) -> list[dict]:
    return yaml.safe_load((pack_dir / "fixtures" / "decision_table.yaml").read_text())["rows"]


def _load_fixture_by_action_id(pack_dir: Path) -> dict[str, dict]:
    """Maps each guard-decision capsule's exact ``action_id`` to itself --
    excludes the policy_manifest_activated event, which carries no
    ``constraints``."""
    by_action_id: dict[str, dict] = {}
    for line in (pack_dir / "fixtures" / "mini_ledger.jsonl").read_text().splitlines():
        capsule = json.loads(line)
        if "constraints" not in capsule:
            continue  # the policy_manifest_activated event, not a guard decision
        by_action_id[capsule["action_id"]] = capsule
    return by_action_id


@pytest.mark.parametrize("pack_dir", PACK_DIRS, ids=lambda p: p.name)
def test_decision_table_matches_the_real_fixture_row_for_row(pack_dir):
    table = _load_table(pack_dir)
    fixture = _load_fixture_by_action_id(pack_dir)

    table_action_ids = {row["action_id"] for row in table}
    assert table_action_ids <= set(fixture), f"table cites action_ids not in the fixture: {table_action_ids - set(fixture)}"

    for row in table:
        capsule = fixture[row["action_id"]]
        actual_checks = {c["id"]: c["result"] for c in capsule["constraints"]}
        assert actual_checks == row["checks"], f"{row['scenario']}: table says {row['checks']}, fixture has {actual_checks}"

        actual_verdict = _VERDICT_BY_DECISION[capsule["disposition"]["decision"]]
        assert actual_verdict == row["verdict"], f"{row['scenario']}: table says {row['verdict']!r}, fixture is {actual_verdict!r}"


def _declared_facts(constraint_id: str, declaration: dict) -> dict:
    if declaration["population"] == "a":
        return {"constraint_id": constraint_id, "in_scope": True, "missing_field": declaration["missing_field"]}
    assert declaration["population"] == "c", declaration
    return {"constraint_id": constraint_id, "in_scope": False, "missing_field": None}


@pytest.mark.parametrize("pack_dir", PACK_DIRS, ids=lambda p: p.name)
def test_every_n_a_declares_its_population_and_matches_the_sealed_evidence_digest(pack_dir):
    table = _load_table(pack_dir)
    if not any("n_a" in row for row in table):
        pytest.skip(f"{pack_dir.name}'s table declares no n/a populations")
    fixture = _load_fixture_by_action_id(pack_dir)
    for row in table:
        records = {c["id"]: c for c in fixture[row["action_id"]]["constraints"] if c["result"] == "n/a"}
        declared = row["n_a"]
        assert set(declared) == set(records), f"{row['scenario']}: declared {sorted(declared)}, sealed n/a {sorted(records)}"
        for constraint_id, declaration in declared.items():
            assert records[constraint_id]["evidence_digest"] == json_digest(
                _declared_facts(constraint_id, declaration)
            ), f"{row['scenario']}: constraint {constraint_id!r} is not population {declaration}"


@pytest.mark.parametrize("pack_dir", PACK_DIRS, ids=lambda p: p.name)
def test_no_dead_rules_every_declared_check_fires_both_ways(pack_dir):
    from capsule_engine.packs.loader import load_pack_dir

    declared_checks = {o.check for o in load_pack_dir(pack_dir).obligations}
    outcomes_by_check: dict[str, set[str]] = {}
    for row in _load_table(pack_dir):
        for check_id, result in row["checks"].items():
            outcomes_by_check.setdefault(check_id, set()).add(result)

    for check_id in declared_checks:
        outcomes = outcomes_by_check.get(check_id, set())
        assert "pass" in outcomes, f"{check_id!r} never appears as 'pass' in the decision table -- dead rule"
        assert "fail" in outcomes, f"{check_id!r} never appears as 'fail' in the decision table -- dead rule"


@pytest.mark.parametrize("pack_dir", PACK_DIRS, ids=lambda p: p.name)
def test_every_declared_obligation_check_appears_in_the_table(pack_dir):
    """Cross-check against pack.yaml itself, not just the table's own
    internal consistency -- a check the pack declares but the table never
    mentions at all is the more basic version of the same dead-rule gap."""
    from capsule_engine.packs.loader import load_pack_dir

    pack = load_pack_dir(pack_dir)
    declared_checks = {o.check for o in pack.obligations}
    table_checks = {check_id for row in _load_table(pack_dir) for check_id in row["checks"]}
    assert declared_checks <= table_checks, f"declared but never in the decision table: {declared_checks - table_checks}"
