# SPDX-License-Identifier: Apache-2.0
"""Every enum in the outcome compiler's closed vocabulary renders (P1
acceptance line), and the reserved-verdict-word deny-list is shown RED on
a violating string before it is shown green on the real table -- a check
that has only ever passed proves nothing."""
from __future__ import annotations

import pytest

from capsule_engine.compiler.vocabulary import (
    BACKWARD_VERDICTS,
    DISPLAY_STRINGS,
    FORWARD_VERDICTS,
    RE_DERIVABILITY_GRADES,
    REFUSAL_REASON_CODES,
    RESERVED_VERDICT_WORDS,
    RESPONSE_CLASSES,
    VerdictPair,
    assert_every_value_has_a_display_string,
    check_all_display_strings,
    display_string,
)


@pytest.mark.parametrize(
    "category,values",
    [
        ("forward_verdict", FORWARD_VERDICTS),
        ("backward_verdict", BACKWARD_VERDICTS),
        ("response_class", RESPONSE_CLASSES),
        ("refusal_reason_code", REFUSAL_REASON_CODES),
        ("re_derivability_grade", RE_DERIVABILITY_GRADES),
    ],
)
def test_every_value_renders(category, values):
    for value in values:
        text = display_string(category, value)
        assert text and isinstance(text, str)


def test_completeness_check_passes_on_the_real_table():
    assert_every_value_has_a_display_string()  # must not raise


def test_completeness_check_flips_red_when_a_value_is_missing_its_display_string():
    with pytest.raises(AssertionError, match="missing display string"):
        _assert_with_missing_entry()


def _assert_with_missing_entry() -> None:
    """Same body as ``assert_every_value_has_a_display_string``, but run
    against a deliberately-corrupted copy of the table (one entry deleted)
    -- the RED-before-green proof that the completeness check can fail."""
    from capsule_engine.compiler import vocabulary as v

    mutant = {k: dict(v.DISPLAY_STRINGS[k]) for k in v.DISPLAY_STRINGS}
    del mutant["forward_verdict"]["REFUSED"]
    for category, values in v._ALL_CLOSED_SETS.items():
        table = mutant.get(category, {})
        missing = sorted(val for val in values if val not in table)
        if missing:
            raise AssertionError(f"{category}: missing display string for {missing}")


def test_deny_list_is_clean_against_the_real_shipped_table():
    assert check_all_display_strings() == []


@pytest.mark.parametrize("reserved_word", sorted(RESERVED_VERDICT_WORDS))
def test_deny_list_flips_red_on_each_reserved_word_then_recovers_green(reserved_word):
    """RED-before-green, one reserved word at a time: inject it into a
    display string, confirm the check catches it (RED), then confirm the
    real table -- which never uses the word -- is clean (GREEN)."""
    mutant = {k: dict(DISPLAY_STRINGS[k]) for k in DISPLAY_STRINGS}
    mutant["backward_verdict"]["DETERMINISTIC"] = f"this is {reserved_word}"

    violations = check_all_display_strings(mutant)
    assert violations, f"deny-list did not flip red for reserved word {reserved_word!r} -- the check cannot fail"
    assert any(reserved_word in v.reserved_words for v in violations)

    assert check_all_display_strings() == []  # green again against the real, unmutated table


def test_deny_list_flips_red_on_a_raw_mappability_label_leaking_into_its_own_display_string():
    mutant = {k: dict(DISPLAY_STRINGS[k]) for k in DISPLAY_STRINGS}
    mutant["backward_verdict"]["MODEL-ASSISTED"] = "this is MODEL-ASSISTED, not a sentence"

    violations = check_all_display_strings(mutant)
    assert violations, "deny-list did not catch a raw verdict token leaking into its own display string"
    assert any(v.value == "MODEL-ASSISTED" for v in violations)

    assert check_all_display_strings() == []


def test_deny_list_does_not_flag_response_class_restating_its_own_plain_english_value():
    """response_class values are already plain English -- ``accepted``'s
    display string legitimately contains the word "accepted"; only the
    jargon categories (verdict pair, refusal codes, re-derivability grade)
    get the self-referential raw-token check."""
    assert check_all_display_strings({"response_class": {"accepted": "the offer was accepted"}}) == []


def test_verdict_pair_rejects_a_backward_only_value_on_the_forward_side():
    with pytest.raises(ValueError, match="forward verdict"):
        VerdictPair(forward="MODEL-ASSISTED", backward="MODEL-ASSISTED")


def test_verdict_pair_rejects_a_forward_only_value_on_the_backward_side():
    with pytest.raises(ValueError, match="backward verdict"):
        VerdictPair(forward="UNAVAILABLE-MODEL-REQUIRED", backward="UNAVAILABLE-MODEL-REQUIRED")


def test_verdict_pair_accepts_the_canonical_act_in_good_faith_shape():
    # design §2.2's canonical case: "act in good faith" compiles to
    # (UNAVAILABLE-MODEL-REQUIRED, MODEL-ASSISTED).
    pair = VerdictPair(forward="UNAVAILABLE-MODEL-REQUIRED", backward="MODEL-ASSISTED")
    assert pair.forward == "UNAVAILABLE-MODEL-REQUIRED"
    assert pair.backward == "MODEL-ASSISTED"
