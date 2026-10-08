# SPDX-License-Identifier: Apache-2.0
"""everyday pack 0.3.1 carries all 27 rules. Each one either cites a check
the pack declares (measured) or is declared not measured and names the
evidence instrument the fixture corpus does not carry. None is left out.

The other catalog packs digest exactly as they did before obligations could
declare measurability."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_engine.packs.corpus_verify import (
    CorpusVerificationError,
    resolves_instrument,
    verify_declared_not_measured,
)
from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.packs.schema import EvidenceInstrument

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog"
PACK_DIR = CATALOG / "everyday"

# Rule -> the check that measures it.
MEASURED = {
    "r01-research-and-prepare": "action_class_gate",
    "r02-ordinary-purchase": "counterparty_seen_before",
    "r03-shipping-address-to-fulfilling-merchant": "recipient_role",
    "r04-prepare-without-committing": "action_class_gate",
    "r05-spending-limits": "caps",
    "r06-new-merchant": "counterparty_seen_before",
    "r07-subscription-or-free-trial": "recurring_charge",
    "r08-non-refundable-or-hard-to-undo": "refundability",
    "r09-material-terms-changed": "material_fields_changed",
    "r10-materially-different-offer": "offer_fields_changed",
    "r11-home-address-to-an-individual": "action_class_gate",
    "r12-personal-contact-to-a-new-party": "action_class_gate",
    "r13-message-to-a-new-recipient": "recipient_seen_before",
    "r15-public-posting": "action_class_gate",
    "r16-booking-with-a-commitment": "action_class_gate",
    "r17-cancellation": "action_class_gate",
    "r18-delete-persistent-data": "action_class_gate",
    "r19-counterparty-identity-changed": "counterparty_identity_change",
    "r20-payment-method-or-destination-changed": "destination_rail",
    "r21-unexpected-channel-change": "channel_change",
    "r22-unusual-counterparty-behaviour": "upfront_amount",
    "r23-secrets-and-authentication": "credential_pattern",
    "r26-stay-within-task-bounds": "task_authority",
    "r27-no-commitment-beyond-task-bounds": "dedupe",
}
# Rule -> the structured field its missing input would arrive in.
DECLARED_NOT_MEASURED = {
    "r14-message-that-commits-the-user": "commitment_classification",
    "r24-outside-instructions-are-not-the-users": "instruction_source",
    "r25-no-bypassing-safeguards": "user_control_state",
}
# Rule -> the action field (or fold key) its check reads, for each rule 0.3.1
# moved from declared to measured. Each is one number, one member of a closed
# set, or one opaque reference.
FLIPPED_INPUTS = {
    "r03-shipping-address-to-fulfilling-merchant": ("recipient_role",),
    "r08-non-refundable-or-hard-to-undo": ("refundable",),
    "r09-material-terms-changed": ("material_fields_changed", "material_fields_basis"),
    "r10-materially-different-offer": ("offer_fields_changed", "offer_fields_basis"),
    "r13-message-to-a-new-recipient": ("target",),
    "r21-unexpected-channel-change": ("channel", "first_contact_channel"),
    "r22-unusual-counterparty-behaviour": ("upfront_amount_minor",),
    "r26-stay-within-task-bounds": ("task_authority_ref",),
}
DISPOSITION = {n: "DO" for n in (1, 2, 3, 4)} | {n: "NEVER" for n in (23, 24, 25)}

# Every other catalog pack's digest before obligations could declare
# measurability (origin/main 0526e2d834f3b5f0aa2c76fadd4478ae8d70c357).
OTHER_PACK_DIGESTS = {
    "airline-engagement": "ea74a099524b5f7a82047cc883e211a25ef230a29c34438e68bfac22c9169277",
    "eu-ai-act": "9d3197935b6b002ccd4682c7cfa2bacf01d9dbc2b3b04811e94dd50e6a2b9cfa",
    "eu-ai-act-deterministic": "b6f6b62454380c6ab4fc1c11fd4418045cc310b95f6a7cbbf2ea23bf54ca5609",
    "payments-safety": "81278051db5ca3755956b7adeaea727d333d12a0c14c51c7e030f15ea8b9666f",
    "standard-vendor": "9ae420d59ebfb1f87ecd740c948be4d0df16cefac97d8640da24934302359ae2",
}

PACK = load_pack_dir(PACK_DIR)


def _rule_number(obligation_id: str) -> int:
    return int(obligation_id[1:3])


def test_the_pack_is_version_0_3_1_with_exactly_27_rules_numbered_1_to_27():
    assert PACK.pack_id == "asg/everyday/0.3.1"
    assert len(PACK.obligations) == 27
    assert sorted(_rule_number(o.id) for o in PACK.obligations) == list(range(1, 28))


def test_every_rule_is_either_measured_by_a_declared_check_or_declared_not_measured():
    assert len(MEASURED) + len(DECLARED_NOT_MEASURED) == 27
    declared_checks = {c.check for c in PACK.constraints}
    for o in PACK.obligations:
        if o.id in MEASURED:
            assert (o.measurability, o.check, o.evidence_instrument) == ("measured", MEASURED[o.id], None), o.id
            assert o.check in declared_checks, o.id
        else:
            assert o.measurability == "declared_not_measured", o.id
            assert o.check is None, o.id
            assert o.evidence_instrument.to_dict() == {
                "kind": "structured_field",
                "field": DECLARED_NOT_MEASURED[o.id],
            }, o.id


def test_the_measured_subset_is_named_and_counted():
    measured = sorted(o.id for o in PACK.obligations if o.measurability == "measured")
    assert measured == sorted(MEASURED)
    assert len(measured) == 24


def test_every_rule_carries_its_disposition():
    for o in PACK.obligations:
        assert o.default_disposition == DISPOSITION.get(_rule_number(o.id), "ASK"), o.id


def test_every_declared_check_measures_at_least_one_rule():
    assert {c.check for c in PACK.constraints} == set(MEASURED.values())


# Reads raw capsule JSON: the test's decoding boundary.
def _fixture_corpus() -> list[dict]:
    lines = (PACK_DIR / "fixtures" / "mini_ledger.jsonl").read_text().splitlines()
    capsules = [json.loads(line) for line in lines]
    return [{"messages": [c, c.get("asg_payload") or {}]} for c in capsules]


def test_no_declared_instrument_resolves_on_the_fixture_corpus():
    verify_declared_not_measured(PACK, _fixture_corpus())


def test_the_oracle_refuses_a_declared_rule_whose_instrument_the_corpus_carries():
    corpus = _fixture_corpus() + [{"messages": [{"user_control_state": "paused"}]}]
    with pytest.raises(CorpusVerificationError, match="r25-no-bypassing-safeguards"):
        verify_declared_not_measured(PACK, corpus)


@pytest.mark.parametrize(("rule", "fields"), sorted(FLIPPED_INPUTS.items()))
def test_every_flipped_rules_input_resolves_on_the_fixture_corpus(rule, fields):
    corpus = _fixture_corpus()
    for field in fields:
        instrument = EvidenceInstrument(kind="structured_field", field=field)
        assert any(resolves_instrument(instrument, unit["messages"]) for unit in corpus), (rule, field)


def test_the_resolve_check_reds_on_a_field_the_corpus_does_not_carry():
    instrument = EvidenceInstrument(kind="structured_field", field="user_control_state")
    assert not any(resolves_instrument(instrument, unit["messages"]) for unit in _fixture_corpus())


def test_every_flipped_rule_is_measured_by_its_check():
    by_id = {o.id: o for o in PACK.obligations}
    assert set(FLIPPED_INPUTS) <= set(MEASURED)
    for rule in FLIPPED_INPUTS:
        assert (by_id[rule].measurability, by_id[rule].check) == ("measured", MEASURED[rule]), rule


@pytest.mark.parametrize(("name", "digest"), sorted(OTHER_PACK_DIGESTS.items()))
def test_every_other_catalog_pack_digests_as_before(name, digest):
    assert load_pack_dir(CATALOG / name).definition_digest() == digest


def test_the_pinned_set_is_every_other_catalog_pack():
    assert {p.name for p in CATALOG.iterdir() if (p / "pack.yaml").exists()} - {"everyday"} == set(OTHER_PACK_DIGESTS)
