# SPDX-License-Identifier: Apache-2.0
"""A scripted Claude/Goose-style session: a fixture, not a live call, but every
question below goes through the real FastMCP server object end-to-end
(`mcp.call_tool`, the same dispatch path a real MCP host uses) -- proving each
answer carries real verification data, not just plausible-looking prose a
mocked tool could equally well have returned.

Note: `outcome` ("allow"/"deny"/"escalate") is the guard engine's own stable
vocabulary (`guards/capsule.py`'s `ALLOW`/`DENY`/`ESCALATE`) and is asserted on
directly below. `disposition.decision`/`verdict_class` are a separate,
in-flux vocabulary (see the workspace's `ldg-verdict-vocab` track) -- this
file deliberately asserts only that they are *present*, never their literal
value, so it stays correct regardless of which token set lands.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import capsule_ledger.mcp.server as srv


@pytest.fixture(autouse=True)
def _fresh_server(tmp_path, monkeypatch):
    """Point the server at a fresh, empty ledger directory per test, and
    reset its process-lifetime singletons -- they're module globals, cached
    across calls by design (one open `LedgerStore` per server session), so a
    test suite must reset them between tests itself."""
    monkeypatch.setenv("CAPSULE_LEDGER", str(tmp_path))
    monkeypatch.setenv("CAPSULE_MCP_CAPS_MINOR", json.dumps({"money.transfer": 10_000_000}))
    monkeypatch.setattr(srv, "_config", None)
    monkeypatch.setattr(srv, "_ledger", None)
    monkeypatch.setattr(srv, "_close_ledger", None)
    monkeypatch.setattr(srv, "_guard", None)
    yield
    if srv._close_ledger is not None:
        srv._close_ledger()


def _call(name: str, args: dict | None = None) -> dict:
    result = asyncio.run(srv.mcp.call_tool(name, args or {}))
    return json.loads(result[0][0].text)


def test_scripted_session_answers_carry_verification_data():
    now = datetime.now(timezone.utc)
    eight_hours_ago = (now - timedelta(hours=8)).isoformat()

    # -- seed a night's worth of agent activity via the one write tool.
    approve = _call(
        "intent_declare",
        {
            "verb": "approve_purchase",
            "operator": "acme-co",
            "developer": "night-shift-agent@v1",
        },
    )
    assert approve["outcome"] == "allow"

    transfer = _call(
        "intent_declare",
        {
            "verb": "transfer_funds",
            "operator": "acme-co",
            "developer": "night-shift-agent@v1",
            "action_class": "money.transfer",
            "amount_minor": 2_000_000,
            "currency": "USD",
            "target": "vendor-42",
        },
    )
    assert transfer["outcome"] == "allow"

    over_cap = _call(
        "intent_declare",
        {
            "verb": "transfer_funds",
            "operator": "acme-co",
            "developer": "night-shift-agent@v1",
            "action_class": "money.transfer",
            "amount_minor": 50_000_000,
            "currency": "USD",
            "target": "vendor-999",
        },
    )
    # money.transfer carries an approver_role by default (D2), so a lone
    # caps-constraint failure escalates to a human rather than hard-denying.
    assert over_cap["outcome"] == "escalate"

    # -- "what did my agents do last night?"
    activity = _call("ledger_query", {"agent": "night-shift-agent@v1", "since": eight_hours_ago})
    assert activity["matched"] == 3
    assert activity["total"] == 3
    assert "checkpoint" in activity and "range" in activity  # the envelope, not a bare list
    verbs = {r["capsule"]["action_id"].split("/")[0] for r in activity["records"]}
    assert verbs == {"approve_purchase", "transfer_funds"}

    # -- "how much budget do I have left?"
    budget = _call("budget_remaining", {"agent": "night-shift-agent@v1", "action_class": "money.transfer"})
    assert budget["cap_configured"] is True
    # Only the ALLOWED transfer counts toward spend -- the denied one never
    # dispatched, so it must not inflate a future cap check (spend.weekly's
    # own `disposition.decision == accept` filter, exercised for real here).
    assert budget["spent_minor"] == 2_000_000
    assert budget["remaining_minor"] == 8_000_000
    assert budget["envelope"]["fold"]  # a real fold digest backs the number, not a bare int

    # -- "why did that one need a human?"
    explanation = _call("decision_explain", {"capsule_id": over_cap["capsule_id"]})
    assert explanation["decision"] is not None  # present; literal token is a separate, in-flux vocabulary
    assert explanation["verdict_class"] is not None
    failing = [c for c in explanation["constraints"] if c["result"] == "fail"]
    assert failing, "an escalated decision must show at least one failing constraint, not an unexplained routing"
    assert failing[0]["id"] == "caps"

    # -- "is that refusal record actually intact?"
    verified = _call("record_verify", {"capsule_id": over_cap["capsule_id"]})
    assert verified["ok"] is True
    # `hitl_dispatched` (D1) is capsule-ledger's own policy vocabulary and isn't
    # yet a seeded value in AAC's REGISTRY.md -- the reference verifier
    # correctly flags that as informational (§12), not a rejection.
    assert verified["findings"] == [
        {
            "code": "unknown_registry_value",
            "detail": (
                "disposition.decision='hitl_dispatched' is not a seeded "
                "disposition.decision value; informational, not rejected (§12)"
            ),
            "severity": "info",
        }
    ]

    # -- "has this exact transfer already happened?" (would-be repeat check)
    been_done = _call(
        "action_been_done",
        {
            "verb": "transfer_funds",
            "operator": "acme-co",
            "developer": "night-shift-agent@v1",
            "target": "vendor-42",
        },
    )
    assert been_done["been_done"] is True
    assert been_done["evidence"]["matched_capsule_id"] == transfer["capsule_id"]

    # -- "what's enforced here, generally?"
    constraints = _call("constraints_list", {})
    assert {c["id"] for c in constraints["checks"]} == {"dedupe", "caps", "verify_before_dispatch"}
    assert any(c["name"] == "unclassified" for c in constraints["action_classes"])

    # -- "what folds exist, in case I need one directly?"
    folds = _call("fold_list", {})
    assert "spend.weekly/1.0.0" in {f["fold_id"] for f in folds["folds"]}
