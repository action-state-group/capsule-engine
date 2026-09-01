# SPDX-License-Identifier: Apache-2.0
"""Tests for the tau2-airline offline reference (P6b acceptance bar):
a stranger with the repo and no network sees real refusals, a real
WITH-INSTRUMENTATION item, and can verify one row offline.

Four concerns:
1. Every vendored dataset replays to real, known guard verdicts -- not
   "the script didn't crash." Counts below are a regression pin against
   the vendored data; if the vendored files or the predicates change,
   these numbers must be re-derived, not adjusted to make the test pass.
2. At least two real refusals and at least one real WITH-INSTRUMENTATION
   item exist -- the P6b acceptance line, checked mechanically.
3. The exported fixture is a real, independently offline-verifiable
   ledger (queue-protocol.md §7's "every check must fail its mutant":
   test_capsule_verify_offline_for_a_denied_call would fail if the
   ledger/signature machinery were broken).
4. The basic-economy predicate is load-bearing: neutering it collapses
   the update_reservation_flights refusal count to zero, proving this
   reference's refusals come from the predicate actually running, not
   from some other path.
"""
from __future__ import annotations

import socket

import pytest

from capsule_engine.examples import tau2_airline_reference as ref
from capsule_engine.guards import ALLOW

# LOAD-BEARING PIN: derived from the vendored data in
# examples/data/tau2_airline/ + the two predicates in this module. A
# change to either must re-derive these counts, not edit them to pass.
EXPECTED_COUNTS = {
    "pilot1-gemini-2-5-flash": {"ALLOW": 9, "DENY": 4, "MAPPABLE-WITH-INSTRUMENTATION": 0},
    "tau2-claude-3-7-sonnet": {"ALLOW": 24, "DENY": 24, "MAPPABLE-WITH-INSTRUMENTATION": 0},
    "tau2-gpt-4-1": {"ALLOW": 26, "DENY": 12, "MAPPABLE-WITH-INSTRUMENTATION": 1},
    "tau2-gpt-4-1-mini": {"ALLOW": 30, "DENY": 22, "MAPPABLE-WITH-INSTRUMENTATION": 0},
    "tau2-o4-mini": {"ALLOW": 19, "DENY": 12, "MAPPABLE-WITH-INSTRUMENTATION": 4},
}


@pytest.mark.parametrize("dataset", sorted(ref.DATASETS))
def test_dataset_replays_to_known_counts(dataset, tmp_path):
    result = ref.run_dataset(dataset, ref.DATASETS[dataset], store_dir=str(tmp_path / dataset))
    assert result.counts() == EXPECTED_COUNTS[dataset]


def test_every_dataset_file_exists_and_is_nonempty():
    for dataset, path in ref.DATASETS.items():
        assert path.is_file(), f"{dataset}: {path} missing -- vendored data must be committed"
        assert path.stat().st_size > 0


# -- module docstring claims "entirely offline, no network" -- enforce it --


def test_run_dataset_makes_no_network_connection(tmp_path):
    """The module docstring promises "entirely offline, no network". Force
    any socket connection attempt to raise, so a regression that drops
    ``witness=False`` from the ``capsule_emit.seal()`` call (which would
    silently POST to the live witness endpoint) fails CI instead of
    quietly phoning home."""

    def _no_network(*_args, **_kwargs):
        raise AssertionError("tau2_airline_reference attempted a network connection")

    original_connect = socket.socket.connect
    socket.socket.connect = _no_network  # type: ignore[method-assign]
    try:
        result = ref.run_dataset(
            "pilot1-gemini-2-5-flash", ref.DATASETS["pilot1-gemini-2-5-flash"], store_dir=str(tmp_path / "store")
        )
        assert result.calls
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]


def test_run_dataset_never_arms_witnessing(monkeypatch, tmp_path):
    """The socket-guard test above is defense in depth, but capsule-emit's
    checkpoint/witness dispatch is cadence-gated (every 100 entries or 900s
    -- see capsule_emit.witness.DEFAULT_CADENCE_ENTRIES/SECONDS) and async,
    so a low-volume replay like this reference's would pass that test even
    with witness=False silently dropped from the seal() call: the missing
    kill switch just wouldn't happen to fire within one small test run.
    This test instead asserts the kill switch itself is engaged on every
    seal() -- spying on capsule_emit.witness.maybe_checkpoint's `enabled`
    argument, which is exactly the value the `witness=` kwarg resolves to
    (see witness.witness_mode/witness_enabled) -- so it fails deterministically,
    not by chance, if witness=False is ever dropped from the call site."""
    import capsule_emit.witness as emit_witness

    seen_enabled: list[object] = []
    original_maybe_checkpoint = emit_witness.maybe_checkpoint

    def _spy_maybe_checkpoint(*args, enabled=None, **kwargs):
        seen_enabled.append(enabled)
        return original_maybe_checkpoint(*args, enabled=enabled, **kwargs)

    monkeypatch.setattr(emit_witness, "maybe_checkpoint", _spy_maybe_checkpoint)

    result = ref.run_dataset(
        "pilot1-gemini-2-5-flash", ref.DATASETS["pilot1-gemini-2-5-flash"], store_dir=str(tmp_path / "store")
    )
    assert result.calls
    assert seen_enabled, "expected at least one capsule_emit.seal() call during replay"
    assert all(v is False for v in seen_enabled), (
        f"witnessing was left enabled for at least one seal() call ({seen_enabled}) -- "
        "this reference must always pass witness=False to stay offline"
    )


# -- P6b acceptance: >=2 refusals, >=1 WITH-INSTRUMENTATION, across the reference set --


def test_at_least_two_refusals_and_one_with_instrumentation_overall():
    total_deny = sum(c["DENY"] for c in EXPECTED_COUNTS.values())
    total_instr = sum(c["MAPPABLE-WITH-INSTRUMENTATION"] for c in EXPECTED_COUNTS.values())
    assert total_deny >= 2
    assert total_instr >= 1


def test_task_17_basic_economy_denial_is_consistent_across_every_model(tmp_path):
    """The headline asset: the exact same basic-economy refusal recurs on
    task 17 across five different models (pilot-1's live gemini run, plus
    all four tau2-bench committed models) -- a forward check surviving
    contact with five independently-generated agent trajectories, not one."""
    for dataset in ref.DATASETS:
        result = ref.run_dataset(dataset, ref.DATASETS[dataset], store_dir=str(tmp_path / dataset))
        task_17_flight_mods = [
            c for c in result.calls if c.task_id == "17" and c.tool_name == "update_reservation_flights"
        ]
        assert task_17_flight_mods, f"{dataset}: expected an update_reservation_flights call on task 17"
        assert all(c.verdict == "DENY" for c in task_17_flight_mods), (
            f"{dataset}: task 17's update_reservation_flights call(s) should be denied (basic economy)"
        )


# -- WITH-INSTRUMENTATION is a distinct, honest verdict, not a disguised deny --


def test_with_instrumentation_calls_are_not_denied(tmp_path):
    result = ref.run_dataset("tau2-o4-mini", ref.DATASETS["tau2-o4-mini"], store_dir=str(tmp_path / "o4-mini"))
    instr_calls = [c for c in result.calls if c.verdict == "MAPPABLE-WITH-INSTRUMENTATION"]
    assert instr_calls, "tau2-o4-mini is expected to contain real WITH-INSTRUMENTATION cases"
    for call in instr_calls:
        # An undecidable predicate must not be silently turned into a deny --
        # the guard's own outcome for these must be ALLOW (verify_before_dispatch
        # is "n/a" for an uncited action, not "fail").
        assert call.outcome == ALLOW, (
            f"WITH-INSTRUMENTATION call {call.tool_name}/task {call.task_id} must not be forced to DENY"
        )


# -- offline verification: a stranger can verify one row with no network --


def test_capsule_verify_offline_for_a_denied_call(tmp_path):
    result = ref.run_dataset(
        "pilot1-gemini-2-5-flash", ref.DATASETS["pilot1-gemini-2-5-flash"], store_dir=str(tmp_path / "store")
    )
    denied = next(c for c in result.calls if c.verdict == "DENY")
    fixture_path = tmp_path / "pilot1.jsonl"
    ref._export_fixture(result.records, fixture_path)

    # Re-import the exported flat-JSONL fixture into a fresh store and verify
    # independently, matching exactly what `capsule verify --ledger <file>`
    # does for a stranger who only has the fixture, not this test's live store.
    from capsule_ledger.ledger import LedgerStore

    verify_store = LedgerStore(str(tmp_path / "verify-store"))
    try:
        import json

        with open(fixture_path, encoding="utf-8") as fh:
            for line in fh:
                verify_store.append(json.loads(line), consequential=False)
        outcome = verify_store.verify(denied.capsule_id)
        assert outcome is not None
        assert outcome.ok, f"capsule {denied.capsule_id} failed offline verification: {outcome.findings}"
    finally:
        verify_store.close()


# -- mutant: neuter the basic-economy predicate, confirm the check can fail --


def test_basic_economy_mutant_collapses_the_refusal(tmp_path, monkeypatch):
    """QUEUE_PROTOCOL.md §7: every check must fail its mutant. Force the
    basic-economy predicate to always pass; the update_reservation_flights
    refusal on pilot-1's task 17 must disappear -- proving the refusal in
    test_task_17_basic_economy_denial_is_consistent_across_every_model
    actually comes from this predicate, not from some other path."""

    def _always_allow(snapshot):
        return ref.PredicateVerdict(True, "mutant: predicate neutered for this test")

    monkeypatch.setitem(ref._PREDICATES, "update_reservation_flights", _always_allow)

    result = ref.run_dataset(
        "pilot1-gemini-2-5-flash", ref.DATASETS["pilot1-gemini-2-5-flash"], store_dir=str(tmp_path / "mutant")
    )
    flight_mod_calls = [c for c in result.calls if c.tool_name == "update_reservation_flights"]
    assert flight_mod_calls, "expected at least one update_reservation_flights call in pilot-1's data"
    assert all(c.verdict == "ALLOW" for c in flight_mod_calls), (
        "mutant should have collapsed every update_reservation_flights refusal to ALLOW"
    )
