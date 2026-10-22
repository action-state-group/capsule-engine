# SPDX-License-Identifier: Apache-2.0
"""offer_expiry/1.1.0 and the proposal an offer answers (capsule-engine#193,
capsule-cli#364): an offer sealed with ``no_proposal`` answers no proposal and
is not applicable; ``no_proposal`` beside a ``proposal_at`` fails; a missing
``proposal_at`` otherwise still fails. The bridge never carries
``no_proposal`` from a check that names a ``source`` ref. On replay an offer's
``proposal_at`` is the time of the counterparty message its ``source`` ref
names (``sent_at`` when present and valid, else the message record's ``at``
when ``sent_at`` is absent); a withheld or unbound message, or a malformed or
too-late ``sent_at``, leaves it unknown. ``offer_expiry/1.0.1`` reads none of this and
decides as before."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action
from capsule_engine.guards.checks import check_offer_expiry
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.report.replay import action_for_check_input, action_for_record, replay

WICKETS = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
V1_0_1 = load_definition_file(WICKETS / "offer_expiry.yaml")
V1_1_0 = load_definition_file(WICKETS / "offer_expiry.v1.1.yaml")
DEAL = "sale-thread-" + "a" * 16
OFFERED_AT = "2026-10-10T12:00:00Z"


def _offer(**fields) -> Action:
    fields.setdefault("action_class", "marketplace.offer")
    return Action(verb="offer", operator="seller", developer="capsulectl-deal", timestamp=OFFERED_AT, **fields)


def _check(definition, action: Action):
    return check_offer_expiry(action, **definition.config).constraint


def test_1_1_0_cites_the_check_with_the_offer_class_as_its_no_proposal_class():
    assert V1_1_0.wicket_id == "offer_expiry/1.1.0"
    assert V1_1_0.config == {**V1_0_1.config, "no_proposal_classes": ["marketplace.offer"]}
    assert V1_1_0.semantics is not None


def test_an_offer_answering_no_proposal_is_not_applicable_in_scope():
    outcome = _check(V1_1_0, _offer(no_proposal=True))
    assert outcome.result == "n/a"
    assert outcome.evidence == {"constraint_id": "offer_expiry", "in_scope": True, "missing_field": None}


def test_no_proposal_beside_a_proposal_at_fails():
    outcome = _check(V1_1_0, _offer(no_proposal=True, proposal_at="2026-10-10T11:00:00Z"))
    assert outcome.result == "fail"
    assert outcome.evidence == {"expiry_unverified": True, "no_proposal_beside": "proposal_at"}
    assert outcome.reason == ("the action says it answers no proposal (no_proposal) but also carries a "
                              "proposal_at, so the offer's expiry could not be established")


def test_a_missing_proposal_at_alone_still_fails():
    for action in (_offer(), _offer(action_class="agreement.accept", no_proposal=True)):
        outcome = _check(V1_1_0, action)
        assert outcome.result == "fail", action
        assert outcome.evidence == {"expiry_unverified": True, "missing_field": "proposal_at"}


def test_an_offer_answering_a_message_is_held_to_its_age():
    assert _check(V1_1_0, _offer(proposal_at="2026-10-10T11:00:00Z")).result == "pass"
    stale = _check(V1_1_0, _offer(proposal_at="2026-10-09T11:00:00Z"))
    assert (stale.result, stale.evidence["expired"]) == ("fail", True)


def test_1_0_1_does_not_read_no_proposal():
    assert _check(V1_0_1, _offer(no_proposal=True)).evidence == {"expiry_unverified": True,
                                                                 "missing_field": "proposal_at"}
    assert _check(V1_0_1, _offer(no_proposal=True, proposal_at="2026-10-10T11:00:00Z")).result == "pass"


# -- the bridge and the replay ------------------------------------------------------


def _bound(shown: dict, capsule_id: str, timestamp: str) -> dict:
    return {"capsule_id": capsule_id, "operator": "seller", "developer": "capsulectl-deal",
            "action_id": f"deal/{capsule_id}", "timestamp": timestamp,
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(shown)}}}


def _message(seq: int, at: str, *, sent_at: str | None = None, sender: str = "counterparty") -> dict:
    body = {"from": sender, "content_commitment": "c" * 64, **({"sent_at": sent_at} if sent_at else {})}
    return {"x-deal-v0": {"profile": "x-deal-v0", "canonicalization": "jcs", "deal_id": DEAL,
                          "record_type": "message", "seq": seq, "at": at, "channel": "app_chat"}, "body": body}


def _typed_offer(seq: int, at: str, *, source: str | None = None, no_proposal: bool = False) -> dict:
    body = {"action": "offer", "action_class": "marketplace.offer", "taxonomy_version": TAXONOMY_VERSION,
            "amount_minor": 185_000, "currency": "USD", "spend_minor": 0}
    if no_proposal:
        body["no_proposal"] = True
    record = {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": DEAL, "seq": seq, "at": at,
              "body": body}
    if source is not None:
        record["refs"] = [{"rel": "source", "type": "record", "digest_alg": "SHA-256", "digest": source}]
    return record


def test_the_bridge_carries_no_proposal_only_from_a_check_naming_no_source():
    offer = _typed_offer(2, OFFERED_AT, no_proposal=True)
    assert action_for_record(_bound(offer, "o", OFFERED_AT), offer).no_proposal is True
    both = _typed_offer(2, OFFERED_AT, source="d" * 64, no_proposal=True)
    assert action_for_record(_bound(both, "o", OFFERED_AT), both).no_proposal is None
    plain = _typed_offer(2, OFFERED_AT)
    assert action_for_record(_bound(plain, "o", OFFERED_AT), plain).no_proposal is None


def test_the_flags_are_sealed_and_read_back():
    offer = _typed_offer(2, OFFERED_AT, no_proposal=True)
    result = replay([_bound(offer, "o", OFFERED_AT)], caps_fold=None, disclosed={"o": offer}, wickets=(V1_1_0,))
    (sourced,) = result.decisions
    payload = sourced.decision.capsule["asg_payload"]
    assert payload["no_proposal"] is True
    assert Action.from_capsule(sourced.decision.capsule).no_proposal is True


def _replayed(records: list[tuple[str, dict, str]], *, withheld: frozenset[str] = frozenset(),
              unbound: frozenset[str] = frozenset()):
    """Replay (capsule id, record, timestamp) triples under 1.1.0: the offer
    check's proposal_at and its offer_expiry outcome."""
    capsules, disclosed = [], {}
    for capsule_id, shown, timestamp in records:
        capsule = _bound(shown, capsule_id, timestamp)
        if capsule_id in unbound:
            capsule["model_attestation"]["compute_attestation"]["agent_input_digest"] = "0" * 64
        capsules.append(capsule)
        if capsule_id not in withheld:
            disclosed[capsule_id] = shown
    result = replay(capsules, caps_fold=None, disclosed=disclosed, withheld=withheld, wickets=(V1_1_0,))
    (sourced,) = [d for d in result.decisions if d.record["capsule_id"] == "offer"]
    (expiry,) = [c for c in sourced.decision.constraints if c.id == "offer_expiry"]
    return sourced.action.proposal_at, expiry


def test_on_replay_an_offer_reads_the_time_of_the_message_it_answers():
    message = _message(2, "2026-10-10T11:30:00Z", sent_at="2026-10-10T11:10:00Z")
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(message))
    proposal_at, expiry = _replayed([("m", message, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)])
    assert proposal_at == "2026-10-10T11:10:00Z" and expiry.result == "pass"
    noted = _message(2, "2026-10-10T11:30:00Z")
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(noted))
    proposal_at, _ = _replayed([("m", noted, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)])
    assert proposal_at == "2026-10-10T11:30:00Z", "no sent_at: when it was noted"


def test_on_replay_a_message_over_a_day_old_asks():
    message = _message(2, "2026-10-10T11:30:00Z", sent_at="2026-10-09T10:00:00Z")
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(message))
    _, expiry = _replayed([("m", message, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)])
    assert (expiry.result, expiry.evidence["expired"]) == ("fail", True)


@pytest.mark.parametrize("how", ["withheld", "unbound", "absent", "from the agent", "later"])
def test_on_replay_an_unresolved_message_leaves_the_age_unknown(how):
    sender = "agent" if how == "from the agent" else "counterparty"
    message = _message(2, "2026-10-10T11:30:00Z", sender=sender)
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(message))
    records = [("m", message, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)]
    if how == "absent":
        records = records[1:]
    if how == "later":
        records = records[::-1]
    proposal_at, expiry = _replayed(records, withheld=frozenset({"m"}) if how == "withheld" else frozenset(),
                                    unbound=frozenset({"m"}) if how == "unbound" else frozenset())
    assert proposal_at is None
    assert expiry.result == "fail" and expiry.evidence == {"expiry_unverified": True, "missing_field": "proposal_at"}


def test_on_replay_a_first_offer_answering_no_proposal_is_not_applicable():
    offer = _typed_offer(2, OFFERED_AT, no_proposal=True)
    proposal_at, expiry = _replayed([("offer", offer, OFFERED_AT)])
    assert proposal_at is None and expiry.result == "n/a"


@pytest.mark.parametrize("how", ["resolved", "stale", "withheld", "unbound"])
def test_on_replay_no_proposal_beside_a_source_is_never_read_as_no_proposal(how):
    """A check naming a source message and no_proposal is decided on the
    message: held to its age when it resolves, failing as missing a proposal
    time when it does not. Never not applicable."""
    message = _message(2, "2026-10-10T11:30:00Z", sent_at="2026-10-09T10:00:00Z" if how == "stale" else None)
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(message), no_proposal=True)
    proposal_at, expiry = _replayed([("m", message, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)],
                                    withheld=frozenset({"m"}) if how == "withheld" else frozenset(),
                                    unbound=frozenset({"m"}) if how == "unbound" else frozenset())
    if how == "resolved":
        assert (proposal_at, expiry.result) == ("2026-10-10T11:30:00Z", "pass")
    elif how == "stale":
        assert (expiry.result, expiry.evidence["expired"]) == ("fail", True)
    else:
        assert proposal_at is None
        assert expiry.result == "fail" and expiry.evidence == {"expiry_unverified": True,
                                                               "missing_field": "proposal_at"}


@pytest.mark.parametrize("sent_at", [42, "", "yesterday", "2026-10-10 11:10:00", "2026-02-30T11:10:00Z",
                                     "2026-10-10T11:10:00+00:00", None])
def test_on_replay_a_malformed_sent_at_never_falls_back_to_the_noted_time(sent_at):
    message = _message(2, "2026-10-10T11:30:00Z")
    message["body"]["sent_at"] = sent_at
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(message))
    proposal_at, expiry = _replayed([("m", message, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)])
    assert proposal_at is None
    assert expiry.evidence == {"expiry_unverified": True, "missing_field": "proposal_at"}


def test_on_replay_a_sent_at_later_than_the_noted_time_leaves_the_age_unknown():
    """Noted 11:30, sent_at 11:45, answered 12:00: a message cannot be sent
    after it was noted."""
    message = _message(2, "2026-10-10T11:30:00Z", sent_at="2026-10-10T11:45:00Z")
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(message))
    proposal_at, expiry = _replayed([("m", message, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)])
    assert proposal_at is None and expiry.result == "fail"
    same = _message(2, "2026-10-10T11:30:00Z", sent_at="2026-10-10T11:30:00Z")
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(same))
    assert _replayed([("m", same, "2026-10-10T11:30:00Z"), ("offer", offer, OFFERED_AT)])[0] == "2026-10-10T11:30:00Z"


def test_a_live_offer_whose_proposal_at_was_dropped_asks():
    """capsulectl sends record.proposal_at beside an offer that answers a
    message; an envelope that dropped it is decided with no proposal time."""
    message = _message(2, "2026-10-10T11:30:00Z")
    offer = _typed_offer(3, OFFERED_AT, source=json_digest(message))
    entry = {**_bound(offer, "offer", OFFERED_AT), "agent_input": offer}
    action = action_for_check_input(entry)
    assert (action.proposal_at, action.no_proposal) == (None, None)
    assert _check(V1_1_0, action).evidence == {"expiry_unverified": True, "missing_field": "proposal_at"}
    given = action_for_check_input({**entry, "proposal_at": "2026-10-10T11:30:00Z"})
    assert _check(V1_1_0, given).result == "pass"
    assert _check(V1_1_0, replace(given, no_proposal=True)).result == "fail"
