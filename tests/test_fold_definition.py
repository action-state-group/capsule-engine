# SPDX-License-Identifier: Apache-2.0
"""Definition parsing/validation and the definition_digest (spec §2, §3)."""
from __future__ import annotations

import pytest

from capsule_ledger.folds.definition import parse_definition
from capsule_ledger.folds.errors import (
    DUPLICATE_READ_PATH,
    FLOAT_IN_DEFINITION,
    INVALID_FOLD_ID,
    UNBOUNDED_FILTER_OP,
    UNDECLARED_FIELD_READ,
    UNKNOWN_ERASURE_CLASS,
    UNKNOWN_REDUCER,
    WALL_CLOCK_REFERENCE,
)

VALID = {
    "fold_id": "spend.weekly/1.0.0",
    "reads": [
        {"path": "developer", "erasure_class": "commitment-ok"},
        {"path": "amount_minor", "erasure_class": "preimage"},
    ],
    "key": "developer",
    "reduce": {"reducer": "sum", "field": "amount_minor"},
    "emit": "money.minor_units",
}


def test_parse_valid_definition():
    definition = parse_definition(VALID)
    assert definition.fold_id == "spend.weekly/1.0.0"
    assert definition.key == "developer"
    assert definition.reduce.reducer == "sum"


def test_definition_digest_is_stable_regardless_of_dict_key_order():
    a = parse_definition(VALID)
    reordered = {
        "reduce": VALID["reduce"],
        "emit": VALID["emit"],
        "key": VALID["key"],
        "reads": VALID["reads"],
        "fold_id": VALID["fold_id"],
    }
    b = parse_definition(reordered)
    assert a.definition_digest() == b.definition_digest()


def test_definition_digest_changes_with_content():
    a = parse_definition(VALID)
    changed = dict(VALID, fold_id="spend.weekly/1.0.1")
    b = parse_definition(changed)
    assert a.definition_digest() != b.definition_digest()


@pytest.mark.parametrize(
    "fold_id",
    ["SpendWeekly", "spend.weekly", "spend.weekly/1.0", "spend.weekly/1.0.0.0", "/1.0.0", ""],
)
def test_invalid_fold_id_namespace(fold_id):
    data = dict(VALID, fold_id=fold_id)
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == INVALID_FOLD_ID


def test_unknown_erasure_class_rejected():
    data = dict(VALID, reads=[{"path": "developer", "erasure_class": "plaintext"}])
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == UNKNOWN_ERASURE_CLASS


def test_duplicate_read_path_rejected():
    data = dict(
        VALID,
        reads=[
            {"path": "developer", "erasure_class": "commitment-ok"},
            {"path": "developer", "erasure_class": "commitment-ok"},
        ],
    )
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == DUPLICATE_READ_PATH


def test_unbounded_filter_op_rejected():
    data = dict(
        VALID,
        filter=[{"field": "developer", "op": "regex", "value": ".*"}],
    )
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == UNBOUNDED_FILTER_OP


def test_filter_on_undeclared_field_rejected():
    data = dict(
        VALID,
        filter=[{"field": "not_declared", "op": "eq", "value": "x"}],
    )
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == UNDECLARED_FIELD_READ


def test_distinct_count_reducer_rejected():
    """spec §7 open question 1: distinct_count is deliberately out of the v1 set."""
    data = dict(VALID, reduce={"reducer": "distinct_count", "field": "developer"})
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == UNKNOWN_REDUCER


def test_float_filter_value_rejected():
    data = dict(
        VALID,
        filter=[{"field": "amount_minor", "op": "gte", "value": 1.5}],
    )
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == FLOAT_IN_DEFINITION


def test_window_requires_timestamp_declared():
    data = dict(VALID, window={"mode": "rolling", "duration": "7d"})
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == UNDECLARED_FIELD_READ


def test_wall_clock_pseudo_field_rejected():
    data = dict(
        VALID,
        reads=VALID["reads"] + [{"path": "now", "erasure_class": "commitment-ok"}],
    )
    with pytest.raises(Exception) as exc_info:
        parse_definition(data)
    assert exc_info.value.reason == WALL_CLOCK_REFERENCE
