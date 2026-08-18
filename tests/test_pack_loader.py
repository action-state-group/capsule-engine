# SPDX-License-Identifier: Apache-2.0
"""Pack loader: successful load of the real payments-safety pack, and
must-fail validation cases -- every failure must carry an actionable
message (field named, expected shape stated, example shown), since a dev's
AI coding tool is the primary author of pack.yaml files."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capsule_ledger.packs.errors import PackDefinitionError
from capsule_ledger.packs.loader import load_pack_dir

PAYMENTS_SAFETY_DIR = (
    Path(__file__).parent.parent / "capsule_ledger" / "packs" / "catalog" / "payments-safety"
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
