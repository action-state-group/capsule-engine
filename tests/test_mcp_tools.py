# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `capsule_ledger.mcp.tools` -- direct calls against a real
`LedgerStore`, no FastMCP/session in the loop. Each tool is checked against
the same modules the CLI uses (`store` / `caps_fold` / `signer` fixtures come
from `conftest.py`, shared with the guard-engine tests)."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_ledger.guards import GuardEngine
from capsule_ledger.mcp import tools

CATALOG_DIR = Path(__file__).parent.parent / "capsule_ledger" / "folds" / "catalog_defs"
FIXTURE_LEDGER = Path(__file__).parent / "fixtures" / "sample_ledger.jsonl"


@pytest.fixture
def guard(store, caps_fold, signer):
    return GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        caps_minor={"money.transfer": 10_000_000},
    )


# -- ledger_query -----------------------------------------------------------


def test_ledger_query_no_filter_reports_matched_and_total(store):
    store.import_jsonl(FIXTURE_LEDGER)
    result = tools.ledger_query(store)
    assert result["matched"] == 4
    assert result["total"] == 4
    assert result["range"] == [1, 4]
    assert result["checkpoint"] == {"tree_size": 4}
    assert result["chain_gaps"] == 0
    assert len(result["records"]) == 4
    assert result["records"][0]["capsule"]["action_id"].startswith("approve_purchase/")


def test_ledger_query_filter_never_hides_the_true_total(store):
    store.import_jsonl(FIXTURE_LEDGER)
    result = tools.ledger_query(store, verdict="blocked")
    assert result["matched"] == 1
    assert result["total"] == 4  # the filtered view never claims to be the whole ledger


# -- fold.list / fold.get ----------------------------------------------------


def test_fold_list_includes_built_in_catalog():
    result = tools.fold_list(CATALOG_DIR)
    fold_ids = {f["fold_id"] for f in result["folds"]}
    assert "spend.weekly/1.0.0" in fold_ids
    assert "actions.count_by_developer/1.0.0" in fold_ids
    assert result["errors"] == []


def test_fold_get_evaluates_a_real_fold_with_envelope(store):
    store.import_jsonl(FIXTURE_LEDGER)
    result = tools.fold_get(store, CATALOG_DIR, fold="actions.count_by_developer/1.0.0", key="procurement-agent@v1")
    assert result["fold_id"] == "actions.count_by_developer/1.0.0"
    assert result["result"] == 4
    assert result["range"] == [0, 3]
    assert "checkpoint" in result
    assert "staleness" in result
    assert len(result["fold"]) == 64  # a sha256 hex digest, not a name this tool invented


def test_fold_get_unknown_fold_is_an_answer_shaped_error_not_a_crash(store):
    result = tools.fold_get(store, CATALOG_DIR, fold="no.such.fold/1.0.0")
    assert "error" in result
    assert "no.such.fold/1.0.0" in result["error"]["message"]


# -- budget.remaining ---------------------------------------------------------


def test_budget_remaining_no_cap_configured_is_honest_not_fabricated(store):
    result = tools.budget_remaining(store, CATALOG_DIR, {}, agent="agent-x")
    assert result["cap_configured"] is False
    assert "remaining_minor" not in result


def test_budget_remaining_tracks_real_spend_from_intent_declare(store, guard):
    tools.intent_declare(
        store, guard, verb="transfer_funds", operator="acme", developer="agent-x",
        action_class="money.transfer", amount_minor=1_000_000, currency="USD",
    )
    result = tools.budget_remaining(store, CATALOG_DIR, {"money.transfer": 10_000_000}, agent="agent-x")
    assert result["cap_configured"] is True
    assert result["cap_minor"] == 10_000_000
    assert result["spent_minor"] == 1_000_000
    assert result["remaining_minor"] == 9_000_000
    assert result["over_cap"] is False
    assert result["envelope"]["fold"]  # a real fold envelope backs the number


def test_budget_remaining_flags_over_cap(store, guard):
    tools.intent_declare(
        store, guard, verb="transfer_funds", operator="acme", developer="agent-over",
        action_class="money.transfer", amount_minor=9_000_000, currency="USD",
    )
    result = tools.budget_remaining(store, CATALOG_DIR, {"money.transfer": 5_000_000}, agent="agent-over")
    assert result["remaining_minor"] == -4_000_000
    assert result["over_cap"] is True


# -- action.been_done ---------------------------------------------------------


def test_action_been_done_false_when_no_prior_action(store):
    result = tools.action_been_done(store, verb="approve_purchase", operator="acme", developer="agent-x")
    assert result["been_done"] is False


def test_action_been_done_true_after_a_matching_intent(store, guard):
    tools.intent_declare(store, guard, verb="approve_purchase", operator="acme", developer="agent-x", target="po-123")
    result = tools.action_been_done(store, verb="approve_purchase", operator="acme", developer="agent-x", target="po-123")
    assert result["been_done"] is True
    assert result["evidence"]["matched_capsule_id"]


# -- constraints.list ---------------------------------------------------------


def test_constraints_list_matches_registered_checks_and_taxonomy():
    result = tools.constraints_list(CATALOG_DIR)
    check_ids = {c["id"] for c in result["checks"]}
    assert check_ids == {"dedupe", "caps", "verify_before_dispatch"}
    caps_check = next(c for c in result["checks"] if c["id"] == "caps")
    assert caps_check["method"] == "spend.weekly/1.0.0"

    class_names = {c["name"] for c in result["action_classes"]}
    assert "unclassified" in class_names
    unclassified = next(c for c in result["action_classes"] if c["name"] == "unclassified")
    assert unclassified["consequential"] is True
    assert unclassified["fail_open_allowed"] is False


# -- decision.explain / record.get / record.verify -----------------------------


def test_decision_explain_record_get_record_verify_round_trip(store, guard):
    decl = tools.intent_declare(store, guard, verb="approve_purchase", operator="acme", developer="agent-x")
    cid = decl["capsule_id"]

    explained = tools.decision_explain(store, capsule_id=cid)
    assert explained["capsule_id"] == cid
    assert explained["agent"] == "agent-x"
    assert explained["constraints"], "a decision's constraints ARE the explanation -- must not be empty"

    fetched = tools.record_get(store, capsule_id=cid)
    assert fetched["capsule"]["capsule_id"] == cid

    verified = tools.record_verify(store, capsule_id=cid)
    assert verified["ok"] is True
    assert verified["findings"] == []


def test_record_get_not_found_is_answer_shaped(store):
    result = tools.record_get(store, capsule_id="deadbeef" * 8)
    assert result == {"error": {"reason": "not_found", "message": "no such capsule 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'"}}


def test_record_verify_not_found_is_answer_shaped(store):
    result = tools.record_verify(store, capsule_id="deadbeef" * 8)
    assert result["error"]["reason"] == "not_found"


# -- intent.declare: the round-trip acceptance test ----------------------------


def test_intent_declare_round_trips_to_a_real_capsule(store, guard):
    """Acceptance: call intent.declare, then confirm the capsule actually
    landed in the ledger via TWO independent read paths (record.get by id,
    and ledger.query by agent) -- not just a plausible-looking response from
    the write call itself."""
    decl = tools.intent_declare(
        store, guard,
        verb="transfer_funds", operator="acme", developer="agent-y",
        action_class="money.transfer", amount_minor=250_000, currency="USD", target="vendor-9",
    )
    assert decl["outcome"] in ("allow", "deny", "escalate")
    cid = decl["capsule_id"]
    assert cid is not None

    fetched = tools.record_get(store, capsule_id=cid)
    assert "error" not in fetched
    assert fetched["capsule"]["capsule_id"] == cid
    assert fetched["capsule"]["developer"] == "agent-y"
    assert fetched["capsule"]["asg_payload"]["amount_minor"] == 250_000

    queried = tools.ledger_query(store, agent="agent-y")
    assert any(r["capsule_id"] == cid for r in queried["records"])

    verified = tools.record_verify(store, capsule_id=cid)
    assert verified["ok"] is True
