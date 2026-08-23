# SPDX-License-Identifier: Apache-2.0
"""Closed vocabulary for the outcome compiler, and its display strings.

Five closed sets, each shipping its plain-language rendering in this same
module (design §3.6: "an enum without a display string is developer
vocabulary leaking onto the auditor's desk"):

- ``FORWARD_VERDICTS`` / ``BACKWARD_VERDICTS`` -- the verdict PAIR every
  compiled statement carries (design §2.2). This is a schema *shape*
  decision: two enums per statement, not one annotation. The judge is never
  in the enforcement path, so ``MODEL-ASSISTED`` is a backward-only value --
  it has no forward counterpart.
- ``RESPONSE_CLASSES`` -- the offer/response denominator primitive (design
  §4b gap 2): an offer was made, a response occurred, and the response is
  recorded whether or not it was the one wanted. Named ``response_class``,
  never ``consent`` -- that word imports one partner's domain vocabulary
  into a shared format.
- ``REFUSAL_REASON_CODES`` -- v0, closed, extended deliberately (same
  "unregistered is a typo" doctrine as ``packs/schema.py``'s other closed
  sets). Seeded with exactly the two refusals this wave's design work
  demonstrated: an effect claim that assumes the agent caused a resolution
  it can only be shown to have recommended, and a goal too unbounded to
  decompose into windowed proxies.
- ``RE_DERIVABILITY_GRADES`` -- design §2.3: whether a constraint's inputs
  are sealed and its verdict independently re-derivable from a compiled
  plan alone (``pure_replay`` -- e.g. containment), or whether re-deriving
  it means possessing the ledger and adopting this repo's own fold
  semantics over ledger state no capsule carries (``ledger_state_dependent``
  -- e.g. caps). Labelled rather than hidden: "an asymmetry named is an
  honesty feature; an asymmetry discovered is an embarrassment."

**The reserved-verdict-word deny-list.** Per licensing law and design §3.6:
publisher vocabulary may name things; only the verifier states verdicts.
Seven words are reserved outright (they assert soundness properties this
format does not make): *verified, certified, compliant, guaranteed, pass,
approved, tamper-proof*. On top of that fixed list, ``check_display_string`` also refuses any
display string that contains the raw enum token it is describing
(case-insensitively) -- e.g. ``MODEL-ASSISTED``'s own rendering may not
contain the substring "model-assisted". That second rule is what "the
mappability labels" (build plan Phase 1 item 6) cashes out to
structurally: self-referential, so it covers the verdict-pair and
refusal/grade jargon without a second, driftable list to keep in sync.
It does not apply to ``response_class`` -- ``accepted``/``declined``/
``deferred``/``no_response`` are already plain English, and restating one
is not the "developer vocabulary leaking onto the auditor's desk" failure
this rule exists to catch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "FORWARD_VERDICTS",
    "BACKWARD_VERDICTS",
    "RESPONSE_CLASSES",
    "REFUSAL_REASON_CODES",
    "RE_DERIVABILITY_GRADES",
    "RESERVED_VERDICT_WORDS",
    "VerdictPair",
    "DisplayViolation",
    "display_string",
    "check_display_string",
    "check_all_display_strings",
    "assert_every_value_has_a_display_string",
]

# --- Verdict pair (design §2.2) --------------------------------------------
FORWARD_VERDICTS = frozenset(
    {"DETERMINISTIC", "UNAVAILABLE-MODEL-REQUIRED", "UNAVAILABLE-STATE-REQUIRED", "REFUSED"}
)
BACKWARD_VERDICTS = frozenset({"DETERMINISTIC", "MODEL-ASSISTED", "MANUAL", "WITH-INSTRUMENTATION", "REFUSED"})

# --- offer/response denominator primitive (design §4b gap 2) ---------------
RESPONSE_CLASSES = frozenset({"accepted", "declined", "deferred", "no_response"})

# --- refusal reason codes, v0 -----------------------------------------------
REFUSAL_REASON_CODES = frozenset(
    {
        # design §4b gap 1: the format is incoherent without this refusal --
        # a causal claim about an advisory-only agent has no evidence that
        # could confirm it, only correlation.
        "agent_caused_resolution_undecomposable",
        # design §3.4 / build plan Phase 1: an "eventually X" goal with no
        # window cannot be compiled into a windowed proxy and must refuse
        # rather than ship as an opaque score.
        "unbounded_goal_unmonitorable",
    }
)

# --- re-derivability grade (design §2.3) ------------------------------------
RE_DERIVABILITY_GRADES = frozenset({"pure_replay", "ledger_state_dependent"})

RESERVED_VERDICT_WORDS = frozenset(
    {"verified", "certified", "compliant", "guaranteed", "pass", "approved", "tamper-proof"}
)

_ALL_CLOSED_SETS: dict[str, frozenset[str]] = {
    "forward_verdict": FORWARD_VERDICTS,
    "backward_verdict": BACKWARD_VERDICTS,
    "response_class": RESPONSE_CLASSES,
    "refusal_reason_code": REFUSAL_REASON_CODES,
    "re_derivability_grade": RE_DERIVABILITY_GRADES,
}

# ``response_class`` values (accepted/declined/deferred/no_response) are
# already plain English -- restating one in its own display string is not
# "developer vocabulary leaking onto the auditor's desk", the failure mode
# the self-referential check exists to catch. That failure mode is real for
# the SCREAMING-KEBAB and snake_case categories below (a verifier stating
# raw "MODEL-ASSISTED" instead of a sentence), so the self-check applies
# only to those.
_SELF_TOKEN_CHECK_CATEGORIES = frozenset(
    {"forward_verdict", "backward_verdict", "refusal_reason_code", "re_derivability_grade"}
)


@dataclass(frozen=True)
class VerdictPair:
    """A compiled statement's two verdicts. Both must come from their own
    closed set -- ``MODEL-ASSISTED`` forward or ``UNAVAILABLE-MODEL-REQUIRED``
    backward are both typos, not values, because the judge is never in the
    enforcement path (design §2.2)."""

    forward: str
    backward: str

    def __post_init__(self) -> None:
        if self.forward not in FORWARD_VERDICTS:
            raise ValueError(f"forward verdict must be one of {sorted(FORWARD_VERDICTS)}; got {self.forward!r}")
        if self.backward not in BACKWARD_VERDICTS:
            raise ValueError(f"backward verdict must be one of {sorted(BACKWARD_VERDICTS)}; got {self.backward!r}")


# category -> {value -> display string}. Every value in every closed set
# above MUST have an entry here -- enforced by
# ``assert_every_value_has_a_display_string`` (run at import time, below,
# and exercised directly by tests/test_compiler_vocabulary.py).
DISPLAY_STRINGS: dict[str, dict[str, str]] = {
    "forward_verdict": {
        "DETERMINISTIC": "checked automatically before the action ran",
        "UNAVAILABLE-MODEL-REQUIRED": "not checked before the action ran -- would need a live judgment call",
        "UNAVAILABLE-STATE-REQUIRED": "not checked before the action ran -- the needed record did not exist yet",
        "REFUSED": "no automatic check exists for this claim",
    },
    "backward_verdict": {
        "DETERMINISTIC": "provable from the record alone",
        "MODEL-ASSISTED": "provable with a reviewer's judgment over the record",
        "MANUAL": "provable only by a person attesting to it directly",
        "WITH-INSTRUMENTATION": "provable once a missing record is captured; not claimed today",
        "REFUSED": "this claim cannot be decomposed into evidence that would prove it",
    },
    "response_class": {
        "accepted": "the offer was accepted",
        "declined": "the offer was declined",
        "deferred": "a decision on the offer was put off",
        "no_response": "no response to the offer was recorded",
    },
    "refusal_reason_code": {
        "agent_caused_resolution_undecomposable": (
            "the record can show a recommendation was made and a person acted, "
            "but not that the recommendation is what made them act"
        ),
        "unbounded_goal_unmonitorable": "this goal has no time window, so no record could ever settle it",
    },
    "re_derivability_grade": {
        "pure_replay": "a stranger can recompute this verdict from the compiled plan alone",
        "ledger_state_dependent": "recomputing this verdict requires the ledger, not just the compiled plan",
    },
}


@dataclass(frozen=True)
class DisplayViolation:
    category: str
    value: str
    display_string: str
    reserved_words: tuple[str, ...]


def display_string(category: str, value: str) -> str:
    """The plain-language rendering for ``value`` in the named closed set.
    Raises ``KeyError`` for anything outside the closed set or missing a
    rendering -- these are curated, closed vocabularies (unlike the open,
    registry-resolved ``action_class`` conventions), so an unregistered
    value here is a typo, not a legitimate degraded state."""
    table = DISPLAY_STRINGS[category]
    return table[value]


def check_display_string(category: str, value: str, text: str) -> DisplayViolation | None:
    """Return a ``DisplayViolation`` if ``text`` (the display string for
    ``category``/``value``) carries a reserved verdict word, or the raw
    token it is itself describing. ``None`` means clean."""
    lowered = text.lower()
    hits: list[str] = []
    for word in RESERVED_VERDICT_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            hits.append(word)
    if category in _SELF_TOKEN_CHECK_CATEGORIES and value.lower() in lowered:
        hits.append(value)
    if hits:
        return DisplayViolation(category=category, value=value, display_string=text, reserved_words=tuple(hits))
    return None


def check_all_display_strings(
    table: dict[str, dict[str, str]] | None = None,
) -> list[DisplayViolation]:
    """Scan every entry in ``table`` (defaults to the real, shipped
    ``DISPLAY_STRINGS``) and return every violation found. Used both as the
    CI gate (``tests/test_compiler_vocabulary.py`` fails the build if this
    is non-empty against the real table) and, with a deliberately-corrupted
    ``table`` argument, as the RED-before-green mutant proof that the check
    can fail at all."""
    violations: list[DisplayViolation] = []
    for category, entries in (table or DISPLAY_STRINGS).items():
        for value, text in entries.items():
            v = check_display_string(category, value, text)
            if v is not None:
                violations.append(v)
    return violations


def assert_every_value_has_a_display_string() -> None:
    """Every value in every closed set above must render -- the P1
    acceptance line ("every enum renders"). Raises ``AssertionError`` naming
    exactly what's missing; run at import time so a schema addition that
    forgets its display string fails immediately, not at first render."""
    for category, values in _ALL_CLOSED_SETS.items():
        table = DISPLAY_STRINGS.get(category, {})
        missing = sorted(v for v in values if v not in table)
        if missing:
            raise AssertionError(f"{category}: missing display string for {missing}")
        extra = sorted(v for v in table if v not in values)
        if extra:
            raise AssertionError(f"{category}: display string table carries unregistered value(s) {extra}")


assert_every_value_has_a_display_string()
