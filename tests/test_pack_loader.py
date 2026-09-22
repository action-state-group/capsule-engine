# SPDX-License-Identifier: Apache-2.0
"""Pack loader: successful load of the real payments-safety pack, and
must-fail validation cases -- every failure must carry an actionable
message (field named, expected shape stated, example shown), since a dev's
AI coding tool is the primary author of pack.yaml files."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

PAYMENTS_SAFETY_DIR = (
    Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "payments-safety"
)

BASE_PACK = {
    "pack_id": "test_pub/test-pack/1.0.0",
    "obligations": [{"id": "o1", "statement": "no dup", "check": "dedupe"}],
    "action_semantics": [
        {"action_type": "payment.dispatch", "action_class": "money.transfer", "required_fields": ["amount_minor"]}
    ],
    "constraints": [{"wicket_id": "test.dedupe/1.0.0", "check": "dedupe", "config": {}}],
    "folds": [{"file": "spend.yaml"}],
}

MINIMAL_FOLD_YAML = """
fold_id: test.spend/1.0.0
reads:
  - path: developer
    erasure_class: commitment-ok
key: developer
reduce:
  reducer: count
emit: n
"""


def _write_pack(tmp_path: Path, overrides: dict | None = None, *, omit: list[str] | None = None) -> Path:
    data = {**BASE_PACK, **(overrides or {})}
    for key in omit or []:
        data.pop(key, None)
    (tmp_path / "pack.yaml").write_text(yaml.dump(data))
    (tmp_path / "spend.yaml").write_text(MINIMAL_FOLD_YAML)
    return tmp_path


def test_loads_the_real_payments_safety_pack():
    pack = load_pack_dir(PAYMENTS_SAFETY_DIR)
    assert pack.pack_id == "asg/payments-safety/1.0.0"
    assert {o.check for o in pack.obligations} == {"caps", "dedupe", "verify_before_dispatch"}
    assert len(pack.action_semantics) == 1
    semantic = pack.action_semantics[0]
    assert semantic.action_type == "payment.dispatch"
    assert semantic.action_class == "money.transfer"
    assert set(semantic.required_fields) == {"amount_minor", "currency", "target"}
    assert semantic.field_aliases == {"target": "counterparty_ref"}
    assert {c.wicket_id for c in pack.constraints} == {
        "payments_safety.caps/1.0.0",
        "payments_safety.dedupe/1.0.0",
        "payments_safety.verify_before_dispatch/1.0.0",
    }
    assert pack.folds[0].fold_id == "payments_safety.spend.weekly/1.0.0"
    assert pack.holds_integration == "stubbed"
    assert pack.bootstrap_path == "AI-BOOTSTRAP.md"
    assert pack.fixtures is not None
    assert {s.id for s in pack.fixtures.scenarios} == {
        "caps-allow",
        "caps-escalate",
        "dedupe-deny",
        "verify-before-dispatch-refusal",
        "verify-before-dispatch-pass",
        "caps-boundary-at-cap",
    }


def test_digest_is_deterministic_and_excludes_nothing_that_should_change_it():
    a = load_pack_dir(PAYMENTS_SAFETY_DIR).definition_digest()
    b = load_pack_dir(PAYMENTS_SAFETY_DIR).definition_digest()
    assert a == b
    assert len(a) == 64


def test_missing_pack_yaml_is_pack_not_found(tmp_path):
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "pack_not_found"


def test_minimal_valid_pack_loads(tmp_path):
    _write_pack(tmp_path)
    pack = load_pack_dir(tmp_path)
    assert pack.pack_id == "test_pub/test-pack/1.0.0"


def test_backward_only_pack_needs_no_forward_declarations(tmp_path):
    """[ldg-obligations-pack-reads-trace]: a pack declaring outcomes[] may
    omit obligations/action_semantics/constraints/folds entirely -- a GRC
    obligations pack (e.g. catalog/eu-ai-act) has no forward guard
    integration to declare."""
    _write_pack(
        tmp_path,
        overrides={"outcomes": [_outcome_entry()]},
        omit=["obligations", "action_semantics", "constraints", "folds"],
    )
    pack = load_pack_dir(tmp_path)
    assert pack.obligations == ()
    assert pack.action_semantics == ()
    assert pack.constraints == ()
    assert pack.folds == ()
    assert {o.id for o in pack.outcomes} == {"outcome.test"}


def test_backward_only_pack_tolerates_explicit_empty_lists_too(tmp_path):
    """Same allowance whether the forward fields are omitted or spelled out
    as empty lists -- both are 'nothing forward to declare', not two
    different states."""
    _write_pack(
        tmp_path,
        overrides={
            "outcomes": [_outcome_entry()],
            "obligations": [],
            "action_semantics": [],
            "constraints": [],
        },
        omit=["folds"],
    )
    pack = load_pack_dir(tmp_path)
    assert pack.obligations == ()
    assert pack.action_semantics == ()
    assert pack.constraints == ()


def test_pack_with_neither_outcomes_nor_forward_declarations_still_rejected(tmp_path):
    """The backward-only allowance does not relax the base invariant: a pack
    with no outcomes[] still needs the forward triple, exactly as before."""
    _write_pack(tmp_path, omit=["obligations"])
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "missing_required_field"


def _outcome_entry(**overrides: object) -> dict:
    entry = {
        "id": "outcome.test",
        "statement": "The thing happened.",
        "evidence_rule": "some capsule pattern",
        "forward_verdict": "DETERMINISTIC",
        "backward_verdict": "DETERMINISTIC",
    }
    entry.update(overrides)
    return entry


def test_outcome_clause_ref_is_optional_and_parses_when_declared(tmp_path):
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry(clause_ref="MSA-2026 §4.2(b)")]})
    pack = load_pack_dir(tmp_path)
    outcome = pack.outcome_for_id("outcome.test")
    assert outcome.clause_ref == "MSA-2026 §4.2(b)"
    assert pack.canonical_dict()["outcomes"][0]["clause_ref"] == "MSA-2026 §4.2(b)"


def test_outcome_without_clause_ref_omits_it_from_the_canonical_form(tmp_path):
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry()]})
    pack = load_pack_dir(tmp_path)
    outcome = pack.outcome_for_id("outcome.test")
    assert outcome.clause_ref is None
    assert "clause_ref" not in pack.canonical_dict()["outcomes"][0]


def test_declaring_clause_ref_changes_the_digest_but_nothing_else_does(tmp_path):
    without = tmp_path / "without"
    with_ref = tmp_path / "with_ref"
    without.mkdir()
    with_ref.mkdir()
    _write_pack(without, overrides={"outcomes": [_outcome_entry()]})
    _write_pack(with_ref, overrides={"outcomes": [_outcome_entry(clause_ref="MSA-2026 §4.2(b)")]})
    digest_without = load_pack_dir(without).definition_digest()
    digest_with_ref = load_pack_dir(with_ref).definition_digest()
    assert digest_without != digest_with_ref


# --- clause: the structured legal anchor ([ldg-grc-clause-ref-versioning]) -


_TEXT_DIGEST = "a" * 64


def _clause_entry(**overrides: object) -> dict:
    clause = {
        "instrument": "Regulation (EU) 2024/1689",
        "article": "Article 26",
        "as_amended_by": ["Regulation (EU) 2026/1744"],
        "paragraph": "2",
        "jurisdiction": "EU",
        "text_snapshot_digest": _TEXT_DIGEST,
        "effective_from": "2026-08-02",
        "source_url": "https://eur-lex.europa.eu/eli/reg/2024/1689",
    }
    clause.update(overrides)
    return clause


def test_outcome_clause_is_optional_and_parses_when_declared(tmp_path):
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry(clause=_clause_entry())]})
    pack = load_pack_dir(tmp_path)
    outcome = pack.outcome_for_id("outcome.test")
    assert outcome.clause.instrument == "Regulation (EU) 2024/1689"
    assert outcome.clause.article == "Article 26"
    assert outcome.clause.as_amended_by == ("Regulation (EU) 2026/1744",)
    assert outcome.clause.text_snapshot_digest == _TEXT_DIGEST
    assert outcome.clause.effective_from == "2026-08-02"
    assert outcome.clause.contested is False
    assert pack.canonical_dict()["outcomes"][0]["clause"] == _clause_entry()


def test_outcome_without_clause_omits_it_from_the_canonical_form(tmp_path):
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry()]})
    pack = load_pack_dir(tmp_path)
    outcome = pack.outcome_for_id("outcome.test")
    assert outcome.clause is None
    assert "clause" not in pack.canonical_dict()["outcomes"][0]


def test_declaring_clause_changes_the_digest_but_nothing_else_does(tmp_path):
    without = tmp_path / "without"
    with_clause = tmp_path / "with_clause"
    without.mkdir()
    with_clause.mkdir()
    _write_pack(without, overrides={"outcomes": [_outcome_entry()]})
    _write_pack(with_clause, overrides={"outcomes": [_outcome_entry(clause=_clause_entry())]})
    digest_without = load_pack_dir(without).definition_digest()
    digest_with_clause = load_pack_dir(with_clause).definition_digest()
    assert digest_without != digest_with_clause


def test_clause_text_snapshot_digest_changing_moves_the_digest_too():
    """Mutant proof: one character of the confirmed clause text differing
    means a new ``text_snapshot_digest``, which must move the pack's own
    digest -- a law-text change can never be silent drift."""
    entry_a = _outcome_entry(clause=_clause_entry(text_snapshot_digest="a" * 64))
    entry_b = _outcome_entry(clause=_clause_entry(text_snapshot_digest="b" * 64))
    assert entry_a != entry_b  # sanity: the fixtures really do differ


def test_clause_contested_flag_renders_only_when_true(tmp_path):
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry(clause=_clause_entry(contested=True))]})
    pack = load_pack_dir(tmp_path)
    assert pack.outcome_for_id("outcome.test").clause.contested is True
    assert pack.canonical_dict()["outcomes"][0]["clause"]["contested"] is True


def test_clause_missing_instrument_is_rejected(tmp_path):
    bad = _clause_entry()
    del bad["instrument"]
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry(clause=bad)]})
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "missing_required_field"


def test_clause_malformed_effective_from_is_rejected(tmp_path):
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry(clause=_clause_entry(effective_from="Aug 2 2026"))]})
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "invalid_clause"


def test_clause_malformed_text_snapshot_digest_is_rejected(tmp_path):
    _write_pack(tmp_path, overrides={"outcomes": [_outcome_entry(clause=_clause_entry(text_snapshot_digest="not-hex"))]})
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == "invalid_clause"


@pytest.mark.parametrize(
    "overrides,omit,expected_reason",
    [
        ({"pack_id": "Not Valid"}, None, "invalid_pack_id_namespace"),
        ({"pack_id": "payments-safety/1.0.0"}, None, "invalid_pack_id_namespace"),  # missing publisher segment
        (None, ["obligations"], "missing_required_field"),
        ({"obligations": []}, None, "malformed_pack"),
        ({"obligations": [{"id": "o1", "statement": "s", "check": "caps"}]}, None, "obligation_check_not_declared"),
        (
            {
                "obligations": [
                    {"id": "o1", "statement": "s", "check": "dedupe"},
                    {"id": "o1", "statement": "s2", "check": "dedupe"},
                ]
            },
            None,
            "duplicate_obligation_id",
        ),
        (None, ["action_semantics"], "missing_required_field"),
        (
            {"action_semantics": [{"action_type": "x", "action_class": "not.a.class", "required_fields": ["amount_minor"]}]},
            None,
            "unknown_action_class",
        ),
        (
            {
                "action_semantics": [
                    {"action_type": "payment.dispatch", "action_class": "money.transfer", "required_fields": ["not_real"]}
                ]
            },
            None,
            "unknown_normalized_field",
        ),
        (
            {
                "action_semantics": [
                    {"action_type": "x", "action_class": "money.transfer", "required_fields": ["amount_minor"]},
                    {"action_type": "x", "action_class": "money.transfer", "required_fields": ["currency"]},
                ]
            },
            None,
            "duplicate_action_type",
        ),
        (None, ["constraints"], "missing_required_field"),
        ({"constraints": [{"wicket_id": "bad id", "check": "dedupe", "config": {}}]}, None, "invalid_constraint"),
        ({"constraints": [{"wicket_id": "test.x/1.0.0", "check": "not_a_check", "config": {}}]}, None, "invalid_constraint"),
        (
            {
                "constraints": [
                    {"wicket_id": "test.dedupe/1.0.0", "check": "dedupe", "config": {}},
                    {"wicket_id": "test.dedupe/1.0.0", "check": "dedupe", "config": {}},
                ]
            },
            None,
            "duplicate_constraint_wicket_id",
        ),
        (None, ["folds"], "missing_required_field"),
        ({"folds": [{"file": "does-not-exist.yaml"}]}, None, "fold_file_not_found"),
        ({"holds_integration": "maybe"}, None, "invalid_holds_integration"),
        ({"fixtures": {"scenarios": [{"id": "s1", "outcome": "bogus"}]}}, None, "invalid_fixtures"),
        ({"bootstrap": "MISSING.md"}, None, "malformed_pack"),
    ],
)
def test_must_fail_cases(tmp_path, overrides, omit, expected_reason):
    _write_pack(tmp_path, overrides, omit=omit)
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    assert exc_info.value.reason == expected_reason
    # Every error must be actionable: name a field/value, not just a code.
    assert len(str(exc_info.value)) > len(expected_reason) + 2


def test_error_message_shows_the_closed_action_class_set(tmp_path):
    _write_pack(
        tmp_path,
        {"action_semantics": [{"action_type": "x", "action_class": "nope", "required_fields": ["amount_minor"]}]},
    )
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(tmp_path)
    message = str(exc_info.value)
    assert "money.transfer" in message  # a real, existing class is shown as guidance
    assert "nope" in message  # the bad value is echoed back
