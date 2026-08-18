# SPDX-License-Identifier: Apache-2.0
"""Constraint scope declaration + the lock/cap/aggregate consistency
validator (generalizes capsule-emit PR #54's cross-class TOCTOU finding:
a cap declared per-class, enforced by a fold that pools across classes,
lets combined spend through that no single class's cap alone would allow).

``schema.py``'s module docstring carries the full incident writeup; these
tests hold the validator to it with concrete must-fail cases."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capsule_ledger.packs.errors import PackDefinitionError
from capsule_ledger.packs.loader import load_pack_dir

DEVELOPER_KEYED_FOLD = """
fold_id: test.spend.by_developer/1.0.0
reads:
  - path: developer
    erasure_class: commitment-ok
  - path: timestamp
    erasure_class: commitment-ok
  - path: asg_payload.amount_minor
    erasure_class: commitment-ok
key: developer
window:
  mode: rolling
  duration: 7d
reduce:
  reducer: sum
  field: asg_payload.amount_minor
emit: spend_minor
"""

# A fold keyed by operator instead of developer -- for the "scope declares
# developer, fold doesn't agree" must-fail case.
OPERATOR_KEYED_FOLD = """
fold_id: test.spend.by_operator/1.0.0
reads:
  - path: operator
    erasure_class: commitment-ok
  - path: timestamp
    erasure_class: commitment-ok
  - path: asg_payload.amount_minor
    erasure_class: commitment-ok
key: operator
window:
  mode: rolling
  duration: 7d
reduce:
  reducer: sum
  field: asg_payload.amount_minor
emit: spend_minor
"""

# A fold that also filters (thus genuinely partitions) by action_class.
CLASS_PARTITIONED_FOLD = """
fold_id: test.spend.by_developer_and_class/1.0.0
reads:
  - path: developer
    erasure_class: commitment-ok
  - path: timestamp
    erasure_class: commitment-ok
  - path: asg_payload.amount_minor
    erasure_class: commitment-ok
  - path: asg_payload.action_class
    erasure_class: commitment-ok
filter:
  - field: asg_payload.action_class
    op: eq
    value: money.transfer
key: developer
window:
  mode: rolling
  duration: 7d
reduce:
  reducer: sum
  field: asg_payload.amount_minor
emit: spend_minor
"""


def _pack(tmp_path: Path, *, fold_yaml: str, fold_id: str, scope, caps_minor: dict) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "spend.yaml").write_text(fold_yaml)
    data = {
        "pack_id": "test_pub/scope-test-pack/1.0.0",
        "obligations": [{"id": "o1", "statement": "cap enforced", "check": "caps"}],
        "action_semantics": [
            {
                "action_type": "payment.dispatch",
                "action_class": "money.transfer",
                "required_fields": ["amount_minor"],
            }
        ],
        "constraints": [
            {
                "wicket_id": "test.caps/1.0.0",
                "check": "caps",
                **({"scope": scope} if scope is not None else {}),
                "config": {"fold_id": fold_id, "caps_minor": caps_minor},
            }
        ],
        "folds": [{"file": "spend.yaml"}],
    }
    (tmp_path / "pack.yaml").write_text(yaml.dump(data))
    return tmp_path


def test_scope_declared_and_agreeing_loads_cleanly(tmp_path):
    pack = _pack(
        tmp_path,
        fold_yaml=DEVELOPER_KEYED_FOLD,
        fold_id="test.spend.by_developer/1.0.0",
        scope=["developer"],
        caps_minor={"money.transfer": 100},
    )
    loaded = load_pack_dir(pack)
    assert loaded.constraint_scopes["test.caps/1.0.0"] == ("developer",)


def test_missing_scope_on_caps_constraint_fails_closed(tmp_path):
    pack = _pack(
        tmp_path,
        fold_yaml=DEVELOPER_KEYED_FOLD,
        fold_id="test.spend.by_developer/1.0.0",
        scope=None,
        caps_minor={"money.transfer": 100},
    )
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(pack)
    assert exc_info.value.reason == "missing_constraint_scope"


def test_unknown_scope_dimension_rejected(tmp_path):
    pack = _pack(
        tmp_path,
        fold_yaml=DEVELOPER_KEYED_FOLD,
        fold_id="test.spend.by_developer/1.0.0",
        scope=["not_a_real_dimension"],
        caps_minor={"money.transfer": 100},
    )
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(pack)
    assert exc_info.value.reason == "invalid_scope_dimension"


def test_duplicate_scope_dimension_rejected(tmp_path):
    pack = _pack(
        tmp_path,
        fold_yaml=DEVELOPER_KEYED_FOLD,
        fold_id="test.spend.by_developer/1.0.0",
        scope=["developer", "developer"],
        caps_minor={"money.transfer": 100},
    )
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(pack)
    assert exc_info.value.reason == "invalid_scope_dimension"


def test_declared_developer_scope_disagreeing_with_fold_key_fails_closed(tmp_path):
    pack = _pack(
        tmp_path,
        fold_yaml=OPERATOR_KEYED_FOLD,
        fold_id="test.spend.by_operator/1.0.0",
        scope=["developer"],
        caps_minor={"money.transfer": 100},
    )
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(pack)
    assert exc_info.value.reason == "scope_mismatch"
    assert "developer" in str(exc_info.value)
    assert "operator" in str(exc_info.value)


def test_multi_class_caps_minor_without_action_class_scope_fails_closed(tmp_path):
    """The exact capsule-emit PR #54 shape: a cap declared per-class, but
    nothing says so, and (as the next test shows) the fold doesn't
    partition by class either -- this is the case that must be caught."""
    pack = _pack(
        tmp_path,
        fold_yaml=DEVELOPER_KEYED_FOLD,
        fold_id="test.spend.by_developer/1.0.0",
        scope=["developer"],
        caps_minor={"money.transfer": 100, "money.refund": 50},
    )
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(pack)
    assert exc_info.value.reason == "scope_mismatch"
    assert "action_class" in str(exc_info.value)


def test_multi_class_scope_declared_but_fold_pools_across_classes_fails_closed(tmp_path):
    """Declaring 'action_class' in scope isn't enough on its own -- the fold
    must actually partition by it. This is the precise bug PR #54 found:
    the declaration and the enforcement disagreed."""
    pack = _pack(
        tmp_path,
        fold_yaml=DEVELOPER_KEYED_FOLD,  # no action_class filter/key
        fold_id="test.spend.by_developer/1.0.0",
        scope=["developer", "action_class"],
        caps_minor={"money.transfer": 100, "money.refund": 50},
    )
    with pytest.raises(PackDefinitionError) as exc_info:
        load_pack_dir(pack)
    assert exc_info.value.reason == "scope_mismatch"
    assert "pools amounts across ALL action classes" in str(exc_info.value)


def test_multi_class_scope_declared_and_fold_genuinely_partitions_loads_cleanly(tmp_path):
    pack = _pack(
        tmp_path,
        fold_yaml=CLASS_PARTITIONED_FOLD,
        fold_id="test.spend.by_developer_and_class/1.0.0",
        scope=["developer", "action_class"],
        caps_minor={"money.transfer": 100, "money.refund": 50},
    )
    loaded = load_pack_dir(pack)
    assert loaded.constraint_scopes["test.caps/1.0.0"] == ("developer", "action_class")


def test_single_class_caps_minor_does_not_require_action_class_scope(tmp_path):
    """A pack with only one action class configured has nothing to pool
    across -- declaring just ['developer'] is honest and sufficient."""
    pack = _pack(
        tmp_path,
        fold_yaml=DEVELOPER_KEYED_FOLD,
        fold_id="test.spend.by_developer/1.0.0",
        scope=["developer"],
        caps_minor={"money.transfer": 100},
    )
    loaded = load_pack_dir(pack)
    assert loaded.constraint_scopes["test.caps/1.0.0"] == ("developer",)


def test_scope_participates_in_the_pack_digest(tmp_path):
    """A declared scope is policy, digest-committed like everything else --
    two packs identical except for their declared scope must digest
    differently (using the class-partitioned fold + two classes so both
    ['developer'] and ['developer', 'action_class'] are independently
    valid declarations to compare)."""
    narrow = _pack(
        tmp_path / "narrow",
        fold_yaml=CLASS_PARTITIONED_FOLD,
        fold_id="test.spend.by_developer_and_class/1.0.0",
        scope=["developer", "action_class"],
        caps_minor={"money.transfer": 100},
    )
    wide = _pack(
        tmp_path / "wide",
        fold_yaml=CLASS_PARTITIONED_FOLD,
        fold_id="test.spend.by_developer_and_class/1.0.0",
        scope=["developer"],
        caps_minor={"money.transfer": 100},
    )
    assert load_pack_dir(narrow).definition_digest() != load_pack_dir(wide).definition_digest()
