# SPDX-License-Identifier: Apache-2.0
import io

from capsule_engine.setup.observe import EVENT_CONFIRMATION, EVENT_DISPATCH, ObserveRecorder


def _recorder(store, signer, **kwargs):
    kwargs.setdefault("heartbeat_stream", io.StringIO())
    return ObserveRecorder(ledger=store, signer=signer, operator="op", developer="dev", **kwargs)


def test_records_conversation_turns_and_session_close(store, signer):
    recorder = _recorder(store, signer)
    events = [
        {"kind": "turn", "session_id": "s1", "turn_index": 0, "speaker_role": "user", "content_digest": "a" * 64},
        {"kind": "turn", "session_id": "s1", "turn_index": 1, "speaker_role": "assistant", "content_digest": "b" * 64},
        {"kind": "session_close", "session_id": "s1"},
    ]
    summary = recorder.run(events)
    assert summary.turns_recorded == 2
    assert summary.unmapped == []
    assert sum(1 for _ in store.scan()) == 3


def test_dispatch_and_confirmation_chain_via_dispatch_id(store, signer):
    recorder = _recorder(store, signer)
    events = [
        {"kind": "dispatch", "dispatch_id": "d1", "action_class": "remediation", "tool": "remediate"},
        {"kind": "confirmation", "commitment_ref": "d1", "status": "confirmed"},
    ]
    summary = recorder.run(events)
    assert summary.dispatches_recorded == 1
    assert summary.confirmations_recorded == 1
    assert summary.unmapped == []

    records = [r for r in store.scan() if (r.capsule.get("asg_payload") or {}).get("event") == EVENT_CONFIRMATION]
    assert len(records) == 1
    confirmation = records[0].capsule
    dispatch_records = [r for r in store.scan() if (r.capsule.get("asg_payload") or {}).get("event") == EVENT_DISPATCH]
    assert confirmation["chain"]["parent_capsule_id"] == dispatch_records[0].capsule["capsule_id"]
    assert confirmation["chain"]["relation"] == "confirms"


def test_offer_and_response_chain_via_offer_id(store, signer):
    recorder = _recorder(store, signer)
    events = [
        {"kind": "offer", "offer_id": "advisory/1", "offer_digest": "a" * 64},
        {"kind": "response", "offer_id": "advisory/1", "response_class": "accepted"},
    ]
    summary = recorder.run(events)
    assert summary.offers_recorded == 1
    assert summary.responses_recorded == 1
    assert summary.unmapped == []


def test_unknown_kind_is_surfaced_not_dropped_trap_2(store, signer):
    """Trap 2: an observe mode that never fails teaches nothing -- an
    unrecognized event must be counted and reported, never silently
    skipped."""
    recorder = _recorder(store, signer)
    summary = recorder.run([{"kind": "totally_bogus", "foo": "bar"}])
    assert summary.total_recorded == 0
    assert len(summary.unmapped) == 1
    assert summary.unmapped[0].reason == "unknown_kind"
    assert summary.total_seen == 1


def test_malformed_event_is_surfaced_not_raised(store, signer):
    """A dispatch missing its required ``action_class`` field must be
    reported as an unmapped event, not crash the whole observe run."""
    recorder = _recorder(store, signer)
    summary = recorder.run([{"kind": "dispatch"}])
    assert summary.dispatches_recorded == 0
    assert len(summary.unmapped) == 1
    assert summary.unmapped[0].reason.startswith("malformed_event")


def test_response_citing_unknown_offer_id_is_surfaced(store, signer):
    recorder = _recorder(store, signer)
    summary = recorder.run([{"kind": "response", "offer_id": "no-such-offer", "response_class": "accepted"}])
    assert summary.responses_recorded == 0
    assert len(summary.unmapped) == 1
    assert summary.unmapped[0].reason == "response_cites_unknown_offer_id"


def test_confirmation_citing_unknown_commitment_ref_is_surfaced(store, signer):
    recorder = _recorder(store, signer)
    summary = recorder.run([{"kind": "confirmation", "commitment_ref": "no-such-dispatch", "status": "confirmed"}])
    assert summary.confirmations_recorded == 0
    assert len(summary.unmapped) == 1
    assert summary.unmapped[0].reason == "confirmation_cites_unknown_commitment_ref"


def test_observe_records_only_emit_layer_no_derived_fields(store, signer):
    """Trap 1: observe must never record a compiled artifact. A dispatch
    capsule's detail carries only what was directly observed (action_class,
    tool) -- no plan_digest, no verdict, nothing the compiler derives."""
    recorder = _recorder(store, signer)
    recorder.run([{"kind": "dispatch", "action_class": "remediation", "tool": "remediate"}])
    record = next(iter(store.scan()))
    detail = record.capsule["asg_payload"]["detail"]
    assert set(detail) <= {"action_class", "tool", "target_digest"}


def test_heartbeat_writes_progress_lines(store, signer):
    stream = io.StringIO()
    recorder = ObserveRecorder(
        ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=1, heartbeat_stream=stream
    )
    recorder.run([{"kind": "dispatch", "action_class": "remediation", "tool": "remediate"} for _ in range(3)])
    output = stream.getvalue()
    assert output.count("observe:") >= 3


def test_heartbeat_disabled_writes_only_final_line(store, signer):
    stream = io.StringIO()
    recorder = ObserveRecorder(
        ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0, heartbeat_stream=stream
    )
    recorder.run([{"kind": "dispatch", "action_class": "remediation", "tool": "remediate"}])
    assert stream.getvalue().count("observe:") == 1
