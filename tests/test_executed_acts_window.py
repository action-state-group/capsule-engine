# SPDX-License-Identifier: Apache-2.0
"""The 7-day spend window on a live check, step by step, against the Go
plugin's results on the same envelopes (``fixtures/executed-acts-window``,
README there).

Each step's envelope is decided as a live check is: a fresh engine under
everyday 0.3.6 whose ledger is the envelope's history (``history_ledger``,
with the engine's caps fold, so each history act's spend record is written).
The 7-day half of ``r05-spending-limits`` is then read off the ``caps``
constraint: its window verdict, the projected total, the limit, and the
reason the plugin's card gives. The card words a window failure as
``CARD_OVER_LIMIT`` and a pass as no reason; the engine's own reason carries
the figures, so only those two are mapped here. A not evaluable window is
the engine's own reason, unmapped.
"""
from __future__ import annotations

import json
import tempfile
from functools import cache
from pathlib import Path
from typing import TypedDict

import pytest
from capsule_ledger.ledger import LedgerStore

import capsule_engine
from capsule_engine.guards import GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ESCALATE
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.checks.caps import WINDOW_UNREADABLE_REASON
from capsule_engine.guards.engine import NOT_EVALUABLE, GuardDecision
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.live_history import history_ledger
from capsule_engine.report.replay import action_for_check_input

FIXTURE = Path(__file__).parent / "fixtures" / "executed-acts-window"
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")
R05 = "r05-spending-limits"
CARD_OVER_LIMIT = "the 7-day total with this purchase is over the 7-day limit"


class Window(TypedDict):
    r05_7d_verdict: str
    r05_7d_value: int | None
    r05_7d_limit: int
    r05_7d_reason: str


class ExpectedStep(Window):
    step: int
    history_acts: int


def _expected() -> dict[str, list[ExpectedStep]]:
    return json.loads((FIXTURE / "expected.json").read_text())["sequences"]


def _steps() -> list[tuple[str, ExpectedStep]]:
    return [(name, step) for name, steps in sorted(_expected().items()) for step in steps]


def _envelope(name: str, step: int) -> dict:
    return json.loads((FIXTURE / "inputs" / name / f"step-{step}.input.json").read_text())


@cache
def _decide(name: str, step: int) -> GuardDecision:
    return _decide_envelope(_envelope(name, step))


def _decide_envelope(envelope: dict) -> GuardDecision:
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        installed = install_pack(PACK, project_dir=Path(tmp) / "live-project", mode="observe")
        resolved = installed.resolved
        wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
        gates, asks = engine_ask_sets(installed.pack, wickets)
        signer = LocalSigner(key_id="executed-acts-window", secret=b"executed-acts-window-fixture")
        engine = GuardEngine(
            ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
            caps_minor=resolved.caps_minor() or {}, per_action_minor=resolved.per_action_minor(),
            per_action_reads=resolved.per_action_reads(), manifest_digest=resolved.manifest_digest,
            wickets=wickets, ask_gate_selectors=gates, ask_wickets=asks, evaluate_under_record_taxonomy=True,
        )
        history_ledger(envelope, store, caps_fold=resolved.caps_fold())
        return engine.check(action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref")))


def _window(decision: GuardDecision) -> Window:
    """The 7-day half of r05, as the plugin's card states it."""
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    limit = 10_000
    if caps.result == "n/a":
        return Window(r05_7d_verdict=NOT_EVALUABLE, r05_7d_value=None, r05_7d_limit=limit, r05_7d_reason=caps.reason)
    evidence = caps.evidence
    over = any(t["limit"] == "window" for t in evidence["tripped"])
    return Window(
        r05_7d_verdict="fail" if over else "pass",
        r05_7d_value=evidence["projected_minor"],
        r05_7d_limit=evidence["cap_minor"],
        r05_7d_reason=CARD_OVER_LIMIT if over else "",
    )


def _id(case: tuple[str, ExpectedStep]) -> str:
    return f"{case[0]}-{case[1]['step']}"


@pytest.mark.parametrize("case", _steps(), ids=_id)
def test_each_step_matches_the_plugin(case):
    name, expected = case
    want = Window(**{k: expected[k] for k in Window.__annotations__})
    assert _window(_decide(name, expected["step"])) == want


def test_every_not_evaluable_window_asks_and_is_never_allowed_or_refused():
    """r05 is ``n/a`` in scope with the ruled reason, and the check asks. The
    decision's verdict is ``not_evaluable`` unless another rule fails (here a
    first-time merchant fails r06, so the verdict is that ask)."""
    seen = 0
    for name, expected in _steps():
        decision = _decide(name, expected["step"])
        if _window(decision)["r05_7d_verdict"] != NOT_EVALUABLE:
            continue
        seen += 1
        assert decision.outcome == ESCALATE, (name, expected["step"])
        assert decision.asked_unevaluated_checks == ("caps",)
        failed = [c.id for c in decision.constraints if c.result == "fail"]
        assert decision.verdict == (ESCALATE if failed else NOT_EVALUABLE)
        (r05,) = [r for r in obligation_results(PACK, decision.constraints) if r.obligation_id == R05]
        assert (r05.result, r05.reason) == ("n/a", WINDOW_UNREADABLE_REASON)
        (caps,) = [c for c in decision.constraints if c.id == "caps"]
        assert caps.evidence == {"constraint_id": "caps", "in_scope": True, "missing_field": "spend_minor"}
    assert seen == 6


def test_the_fixture_covers_every_sequence_and_step():
    expected = _expected()
    assert len(expected) == 17
    for name, steps in expected.items():
        files = sorted((FIXTURE / "inputs" / name).glob("step-*.input.json"))
        assert [s["step"] for s in steps] == list(range(len(files)))


# -- beyond the recorded sequences -------------------------------------------------


def test_an_accepted_typed_act_in_the_history_counts_once():
    """The history ledger writes a readable typed act as a decision on it
    would read; under spend.weekly/3.1.0 that copy is left out, and the act
    counts once, through its executed record."""
    envelope = _envelope("fyi-commit-typed", 1)
    envelope["history"][0]["disposition"] = {"decision": "accept"}
    assert _window(_decide_envelope(envelope))["r05_7d_value"] == 4_000


def test_a_history_act_its_capsule_does_not_bind_is_unreadable():
    envelope = _envelope("fyi-commit", 1)
    envelope["history"][0]["agent_input"]["body"]["spend_minor"] = 1
    window = _window(_decide_envelope(envelope))
    assert (window["r05_7d_verdict"], window["r05_7d_reason"]) == (NOT_EVALUABLE, WINDOW_UNREADABLE_REASON)
