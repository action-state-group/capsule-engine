"""Replay reads x-deal-v1 records beside x-deal-v0 (capsule-engine#301).

The Go guard reads the same records the same way (go/guard/profile_block_test.go);
both read evidencebook's released Appendix A fixtures (v0.18.1)."""
import json
from pathlib import Path

import pytest

from agent_action_capsule import json_digest

from capsule_engine.report.replay import (
    _PROFILE_V0,
    _PROFILE_V1,
    _block,
    _deal_id,
    act_view,
    action_for_record,
    unreadable_profile,
)

FIXTURES = Path(__file__).resolve().parents[2] / "go" / "guard" / "testdata" / "x-deal-v1-fixtures"
V1_TO_V0 = {"perform": "action", "settle": "action", "detail_change": "change"}
DEAL = "deal-6a1f0c93b27d4e58"
HEX = "a" * 64


def _records():
    paths = sorted(FIXTURES.glob("*/*.json"))
    assert len(paths) >= 30
    return [(p, json.loads(p.read_text())["record"]) for p in paths]


@pytest.mark.parametrize("path,record", _records(), ids=lambda v: v.name if isinstance(v, Path) else "")
def test_every_appendix_a_record_is_read(path, record):
    block = _block(record)
    original = record[_PROFILE_V1]["record_type"]
    assert isinstance(block, dict)
    assert block["record_type"] == V1_TO_V0.get(original, original)
    assert unreadable_profile(record) is None
    if path.parent.name.endswith("-book"):
        assert _deal_id(record).value == DEAL


@pytest.mark.parametrize(
    "record,block,unreadable",
    [
        ({"body": {}}, None, False),
        ({_PROFILE_V0: {"deal_id": "d"}}, {"deal_id": "d"}, False),
        ({_PROFILE_V1: {"profile": _PROFILE_V1, "record_type": "check"}}, {"profile": _PROFILE_V1, "record_type": "check"}, False),
        ({_PROFILE_V1: {"profile": _PROFILE_V0}}, None, True),
        ({_PROFILE_V1: {"record_type": "check"}}, None, True),
        ({_PROFILE_V1: "x"}, None, True),
        ({_PROFILE_V0: {}, _PROFILE_V1: {"profile": _PROFILE_V1}}, None, True),
        ({"x-deal-v2": {"profile": "x-deal-v2"}}, None, True),
        ({"x-deal-vx": {}}, None, False),
    ],
)
def test_versions(record, block, unreadable):
    assert _block(record) == block
    assert (unreadable_profile(record) is not None) is unreadable


def _settle(leg="payer", **body):
    return {_PROFILE_V1: {"profile": _PROFILE_V1, "record_type": "settle", "deal_id": DEAL}, "body": {"leg": leg, **body}}


def _capsule(value="2000", code="USD"):
    return {"settlement": {"version": "0", "leg": "payer", "amount": {"value": value, "assetCode": code, "assetScale": 2}}}


def test_a_payer_settle_is_money_out_at_its_settlement_amount():
    view = act_view(_capsule(), _settle())
    assert view["body"] == {"action": "settle", "amount_minor": 2000, "spend_minor": 2000, "currency": "USD", "direction": "out"}


def test_a_payee_leg_is_money_in_and_names_what_it_reverses():
    ref = {"type": "deal-record", "digest_alg": "SHA-256", "digest": HEX}
    view = act_view(_capsule(), _settle("payee", reverses=ref))
    assert view["body"]["direction"] == "in"
    assert view["body"]["reverses_ref"] == ref


@pytest.mark.parametrize("capsule", [{}, _capsule("1.5"), _capsule("-1"), _capsule("007"), _capsule(code=""), _capsule("9007199254740992")])
def test_a_payer_settle_with_no_readable_settlement_is_unread(capsule):
    assert act_view(capsule, _settle()) is None


def test_the_largest_readable_amount_is_two_to_the_53_minus_1():
    assert act_view(_capsule("9007199254740991"), _settle())["body"]["amount_minor"] == 9007199254740991


@pytest.mark.parametrize("mutate", [
    lambda r: r.update({"x-deal-v2": {"profile": "x-deal-v2"}}),
    lambda r: r.update({_PROFILE_V0: {"profile": _PROFILE_V0, "record_type": "check"}}),
    lambda r: r[_PROFILE_V1].update({"profile": _PROFILE_V0}),
])
def test_a_checked_record_whose_profile_is_unreadable_is_refused_not_read_as_no_deal(mutate):
    record = {_PROFILE_V1: {"profile": _PROFILE_V1, "record_type": "check", "deal_id": DEAL}, "body": {"action": "pay"}}
    mutate(record)
    capsule = {"capsule_id": HEX, "action_id": DEAL + "/2", "action_type": "fyi", "operator": "o", "developer": "d",
               "timestamp": "2026-10-07T12:00:00Z",
               "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}
    action = action_for_record(capsule, record)
    assert action.deal_id_conflict


def test_a_perform_states_no_amount_and_other_records_pass_through():
    perform = {_PROFILE_V1: {"profile": _PROFILE_V1, "record_type": "perform", "deal_id": DEAL}, "body": {}}
    assert act_view({}, perform)["body"] == {"action": "perform"}
    check = {_PROFILE_V1: {"profile": _PROFILE_V1, "record_type": "check"}, "body": {"action": "pay"}}
    assert act_view({}, check) is check
    v0 = {_PROFILE_V0: {"record_type": "action"}, "body": {"spend_minor": 1}}
    assert act_view({}, v0) is v0
