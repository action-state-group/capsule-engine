# SPDX-License-Identifier: Apache-2.0
"""Tests for the interim conversation-capsule profile
(conversation capsule profile): per-turn sealing at turn time, no
unsigned window, the session-close Merkle binding, and selective-disclosure
inclusion proofs at turn granularity.
"""
from __future__ import annotations

import hashlib

import pytest
from capsule_ledger.ledger import ScanQuery

from capsule_engine.conversation import (
    EVENT_CONVERSATION_TURN,
    EVENT_SESSION_CLOSE,
    EVENT_TURN_REFERENCE,
    SPEAKER_ROLES,
    ConversationSession,
    InvalidSpeakerRole,
    SessionAlreadyClosedError,
    build_session_close_capsule,
    build_turn_capsule,
    build_turn_reference_capsule,
    find_session_close,
    find_session_turns,
    find_turn_reference,
    session_root,
    turn_inclusion_proof,
    verify_turn_inclusion,
)
from capsule_engine.guards.signing import SigningKeyUnavailable

OPERATOR = "acme-support"
DEVELOPER = "workforce-assistant@v1"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _session(store, signer, session_id="sess-1"):
    return ConversationSession(
        ledger=store,
        session_id=session_id,
        operator=OPERATOR,
        developer=DEVELOPER,
        signer_provider=lambda: signer,
    )


# -- sealing at turn time / no unsigned window -------------------------------


def test_turns_and_close_are_real_independently_verifiable_capsules(store, signer):
    sess = _session(store, signer)
    r0 = sess.record_turn(speaker_role="user", content_digest=_digest("hello"))
    r1 = sess.record_turn(speaker_role="assistant", content_digest=_digest("hi there"))
    close = sess.close()

    for record in (r0, r1, close):
        result = store.verify(record.capsule_id)
        assert result.ok, result.findings


def test_turn_zero_is_standalone_later_turns_chain_follows(store, signer):
    sess = _session(store, signer)
    r0 = sess.record_turn(speaker_role="user", content_digest=_digest("hello"))
    r1 = sess.record_turn(speaker_role="assistant", content_digest=_digest("hi"))

    assert "chain" not in r0.capsule or r0.capsule.get("chain") is None
    assert r0.capsule["asg_payload"]["detail"]["turn_index"] == 0
    assert r1.capsule["chain"] == {"parent_capsule_id": r0.capsule_id, "relation": "follows"}
    assert r1.capsule["asg_payload"]["detail"]["turn_index"] == 1
    assert r0.capsule["assurance"]["ledger_mode"] == "standalone"
    assert r1.capsule["assurance"]["ledger_mode"] == "chained"


def test_speaker_role_is_recorded_on_the_turn(store, signer):
    sess = _session(store, signer)
    r0 = sess.record_turn(speaker_role="user", content_digest=_digest("q"))
    r1 = sess.record_turn(speaker_role="assistant", content_digest=_digest("a"))
    r2 = sess.record_turn(speaker_role="human-agent", content_digest=_digest("escalated"))

    assert [r.capsule["asg_payload"]["detail"]["speaker_role"] for r in (r0, r1, r2)] == [
        "user",
        "assistant",
        "human-agent",
    ]
    assert SPEAKER_ROLES == {"user", "assistant", "human-agent"}


@pytest.mark.parametrize("bad_role", ["bot", "system", "User", ""])
def test_invalid_speaker_role_rejected(signer, bad_role):
    with pytest.raises(InvalidSpeakerRole):
        build_turn_capsule(
            session_id="sess-1",
            turn_index=0,
            speaker_role=bad_role,
            content_digest=_digest("x"),
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
        )


def test_content_never_enters_the_record_only_a_digest(signer):
    # H2 invariant: passing plaintext where a digest is required must fail
    # loud, not silently get embedded in the capsule.
    with pytest.raises(ValueError, match="content_digest"):
        build_turn_capsule(
            session_id="sess-1",
            turn_index=0,
            speaker_role="user",
            content_digest="hello, this is not a digest",
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
        )


def test_turn_chain_linkage_is_enforced(signer):
    # turn_index=0 must not carry a previous_turn_capsule_id...
    with pytest.raises(ValueError, match="turn_index=0"):
        build_turn_capsule(
            session_id="sess-1",
            turn_index=0,
            speaker_role="user",
            content_digest=_digest("x"),
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
            previous_turn_capsule_id="a" * 64,
        )
    # ...and turn_index>0 must carry one.
    with pytest.raises(ValueError, match="requires previous_turn_capsule_id"):
        build_turn_capsule(
            session_id="sess-1",
            turn_index=1,
            speaker_role="user",
            content_digest=_digest("x"),
            operator=OPERATOR,
            developer=DEVELOPER,
            signer=signer,
        )


def test_no_unsigned_window_signing_failure_propagates_not_silently_recorded(store):
    def _unavailable():
        raise SigningKeyUnavailable("key rotated out")

    sess = ConversationSession(
        ledger=store, session_id="sess-1", operator=OPERATOR, developer=DEVELOPER, signer_provider=_unavailable
    )
    with pytest.raises(SigningKeyUnavailable):
        sess.record_turn(speaker_role="user", content_digest=_digest("hello"))
    assert sess.turn_count == 0
    assert list(store.scan()) == []


# -- session lifecycle --------------------------------------------------------


def test_close_requires_at_least_one_turn(store, signer):
    sess = _session(store, signer)
    with pytest.raises(ValueError, match="no turns"):
        sess.close()
    with pytest.raises(ValueError, match="non-empty"):
        build_session_close_capsule(
            session_id="sess-1", turn_capsule_ids=[], operator=OPERATOR, developer=DEVELOPER, signer=signer
        )


def test_cannot_record_or_close_after_close(store, signer):
    sess = _session(store, signer)
    sess.record_turn(speaker_role="user", content_digest=_digest("hello"))
    sess.close()
    assert sess.closed
    with pytest.raises(SessionAlreadyClosedError):
        sess.record_turn(speaker_role="assistant", content_digest=_digest("too late"))
    with pytest.raises(SessionAlreadyClosedError):
        sess.close()


def test_multiple_sessions_on_one_ledger_stay_isolated(store, signer):
    sess_a = _session(store, signer, session_id="sess-a")
    sess_b = _session(store, signer, session_id="sess-b")
    sess_a.record_turn(speaker_role="user", content_digest=_digest("a0"))
    sess_b.record_turn(speaker_role="user", content_digest=_digest("b0"))
    sess_a.record_turn(speaker_role="assistant", content_digest=_digest("a1"))
    sess_a.close()
    sess_b.close()

    turns_a = find_session_turns(store, "sess-a")
    turns_b = find_session_turns(store, "sess-b")
    assert len(turns_a) == 2
    assert len(turns_b) == 1
    assert {t.capsule["asg_payload"]["detail"]["session_id"] for t in turns_a} == {"sess-a"}

    close_a = find_session_close(store, "sess-a")
    close_b = find_session_close(store, "sess-b")
    assert close_a.capsule["asg_payload"]["detail"]["turn_count"] == 2
    assert close_b.capsule["asg_payload"]["detail"]["turn_count"] == 1


def test_find_session_close_returns_none_for_an_open_session(store, signer):
    sess = _session(store, signer)
    sess.record_turn(speaker_role="user", content_digest=_digest("hello"))
    assert find_session_close(store, "sess-1") is None


def test_find_session_turns_sorted_by_turn_index_even_if_appended_out_of_order(store, signer):
    # Build all three turn capsules first (valid chain), then append them to
    # the store in reverse order -- a stand-in for an out-of-order
    # replay/import -- and confirm find_session_turns still returns them in
    # turn_index order, not append order.
    c0 = build_turn_capsule(
        session_id="sess-1", turn_index=0, speaker_role="user", content_digest=_digest("t0"),
        operator=OPERATOR, developer=DEVELOPER, signer=signer,
    )
    c1 = build_turn_capsule(
        session_id="sess-1", turn_index=1, speaker_role="assistant", content_digest=_digest("t1"),
        operator=OPERATOR, developer=DEVELOPER, signer=signer, previous_turn_capsule_id=c0["capsule_id"],
    )
    c2 = build_turn_capsule(
        session_id="sess-1", turn_index=2, speaker_role="user", content_digest=_digest("t2"),
        operator=OPERATOR, developer=DEVELOPER, signer=signer, previous_turn_capsule_id=c1["capsule_id"],
    )
    for capsule in (c2, c0, c1):
        store.append(capsule, consequential=False)

    found = find_session_turns(store, "sess-1")
    assert [f.capsule["asg_payload"]["detail"]["turn_index"] for f in found] == [0, 1, 2]
    assert [f.capsule_id for f in found] == [c0["capsule_id"], c1["capsule_id"], c2["capsule_id"]]


# -- the session digest (Merkle binding) + selective disclosure --------------


def test_session_digest_matches_independent_recomputation(store, signer):
    sess = _session(store, signer)
    turns = [sess.record_turn(speaker_role="user", content_digest=_digest(f"turn-{i}")) for i in range(5)]
    close = sess.close()

    detail = close.capsule["asg_payload"]["detail"]
    turn_ids = [t.capsule_id for t in turns]
    assert detail["turn_capsule_ids"] == turn_ids
    assert detail["turn_count"] == 5
    assert detail["session_digest"] == session_root(turn_ids)
    assert close.capsule["chain"] == {"parent_capsule_id": turn_ids[-1], "relation": "follows"}


@pytest.mark.parametrize("n_turns", [1, 2, 3, 5, 8])
def test_turn_inclusion_proof_verifies_every_turn(store, signer, n_turns):
    sess = _session(store, signer)
    turns = [sess.record_turn(speaker_role="user", content_digest=_digest(f"t{i}")) for i in range(n_turns)]
    close = sess.close()
    detail = close.capsule["asg_payload"]["detail"]
    turn_ids = [t.capsule_id for t in turns]

    for i, turn_id in enumerate(turn_ids):
        proof = turn_inclusion_proof(turn_ids, i)
        assert verify_turn_inclusion(
            session_digest=detail["session_digest"],
            turn_count=n_turns,
            turn_index=i,
            turn_capsule_id=turn_id,
            proof=proof,
        )


def test_turn_inclusion_proof_rejects_tampered_evidence(store, signer):
    sess = _session(store, signer)
    turns = [sess.record_turn(speaker_role="user", content_digest=_digest(f"t{i}")) for i in range(4)]
    close = sess.close()
    detail = close.capsule["asg_payload"]["detail"]
    turn_ids = [t.capsule_id for t in turns]

    proof_for_turn_1 = turn_inclusion_proof(turn_ids, 1)

    # Wrong capsule id claimed at the same index -- must fail, not pass
    # through as if any turn were interchangeable.
    assert not verify_turn_inclusion(
        session_digest=detail["session_digest"],
        turn_count=4,
        turn_index=1,
        turn_capsule_id=turn_ids[0],
        proof=proof_for_turn_1,
    )
    # Right capsule id, wrong claimed index.
    assert not verify_turn_inclusion(
        session_digest=detail["session_digest"],
        turn_count=4,
        turn_index=2,
        turn_capsule_id=turn_ids[1],
        proof=proof_for_turn_1,
    )
    # Right everything, wrong session digest (a different session's root).
    assert not verify_turn_inclusion(
        session_digest="f" * 64,
        turn_count=4,
        turn_index=1,
        turn_capsule_id=turn_ids[1],
        proof=proof_for_turn_1,
    )


def test_verify_turn_inclusion_never_raises_on_garbage_input(store, signer):
    sess = _session(store, signer)
    turns = [sess.record_turn(speaker_role="user", content_digest=_digest(f"t{i}")) for i in range(2)]
    close = sess.close()
    detail = close.capsule["asg_payload"]["detail"]
    turn_ids = [t.capsule_id for t in turns]
    proof = turn_inclusion_proof(turn_ids, 0)

    assert verify_turn_inclusion(
        session_digest="not-hex", turn_count=2, turn_index=0, turn_capsule_id=turn_ids[0], proof=proof
    ) is False
    assert verify_turn_inclusion(
        session_digest=detail["session_digest"], turn_count=0, turn_index=0, turn_capsule_id=turn_ids[0], proof=proof
    ) is False
    assert verify_turn_inclusion(
        session_digest=detail["session_digest"], turn_count=2, turn_index=0, turn_capsule_id="also-not-hex", proof=proof
    ) is False


# -- determinism (fixture-hygiene discipline) ---------------------------------


def test_build_turn_capsule_is_deterministic_given_explicit_inputs(signer):
    kwargs = dict(
        session_id="sess-fixed",
        turn_index=0,
        speaker_role="user",
        content_digest=_digest("hello"),
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
        timestamp="2026-08-12T09:00:00Z",
        action_id="conversation.turn/sess-fixed/0",
    )
    c1 = build_turn_capsule(**kwargs)
    c2 = build_turn_capsule(**kwargs)
    assert c1 == c2
    assert c1["capsule_id"] == c2["capsule_id"]

    c3 = build_turn_capsule(**{**kwargs, "content_digest": _digest("goodbye")})
    assert c3["capsule_id"] != c1["capsule_id"]


def test_event_type_and_scan_filter_shape(store, signer):
    sess = _session(store, signer)
    sess.record_turn(speaker_role="user", content_digest=_digest("hello"))
    sess.close()

    fyi_records = list(store.scan(ScanQuery(action_type="fyi")))
    events = [r.capsule["asg_payload"]["event"] for r in fyi_records]
    assert events.count(EVENT_CONVERSATION_TURN) == 1
    assert events.count(EVENT_SESSION_CLOSE) == 1


# -- turn -> external-capsule cross-reference ---------------------------------


def test_turn_reference_resolves_a_referenced_capsule_back_to_its_turn(store, signer):
    sess = _session(store, signer)
    turn = sess.record_turn(speaker_role="assistant", content_digest=_digest("book it"))
    sess.close()

    # Stand-in for a tool-call capsule some other pipeline recorded, built
    # and appended independently of the conversation profile.
    tool_capsule_id = "b" * 64
    reference = build_turn_reference_capsule(
        turn_capsule_id=turn.capsule_id,
        referenced_capsule_ids=[tool_capsule_id],
        operator=OPERATOR,
        developer=DEVELOPER,
        signer=signer,
    )
    store.append(reference, consequential=False)

    found = find_turn_reference(store, tool_capsule_id)
    assert found is not None
    assert found.capsule["asg_payload"]["event"] == EVENT_TURN_REFERENCE
    assert found.capsule["asg_payload"]["detail"]["turn_capsule_id"] == turn.capsule_id
    assert found.capsule["chain"] == {"parent_capsule_id": turn.capsule_id, "relation": "confirms"}
    result = store.verify(found.capsule_id)
    assert result.ok, result.findings


def test_turn_reference_supports_multiple_referenced_capsules(store, signer):
    sess = _session(store, signer)
    turn = sess.record_turn(speaker_role="assistant", content_digest=_digest("book it"))
    sess.close()

    ids = ["c" * 64, "d" * 64]
    reference = build_turn_reference_capsule(
        turn_capsule_id=turn.capsule_id, referenced_capsule_ids=ids, operator=OPERATOR, developer=DEVELOPER, signer=signer,
    )
    store.append(reference, consequential=False)

    for capsule_id in ids:
        found = find_turn_reference(store, capsule_id)
        assert found is not None
        assert found.capsule["asg_payload"]["detail"]["referenced_capsule_ids"] == ids


def test_find_turn_reference_returns_none_when_unreferenced(store, signer):
    sess = _session(store, signer)
    sess.record_turn(speaker_role="user", content_digest=_digest("hello"))
    sess.close()
    assert find_turn_reference(store, "e" * 64) is None


def test_turn_reference_requires_at_least_one_referenced_capsule(signer):
    with pytest.raises(ValueError, match="non-empty"):
        build_turn_reference_capsule(
            turn_capsule_id="a" * 64, referenced_capsule_ids=[], operator=OPERATOR, developer=DEVELOPER, signer=signer,
        )
