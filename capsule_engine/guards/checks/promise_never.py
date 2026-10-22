# SPDX-License-Identifier: Apache-2.0
"""promise_never check: a class of statement the agent never makes.

Reads ``Action.representation_class``, one member of the closed set
``representation_classes``. A class in ``never`` fails whenever the action
makes that statement. Nothing the action carries clears it: not
``authorized_by``, not a bound approval record, not the task authority. The
only way to lift it is a change to the pinned rule set that removes the
class from ``never``. ``authorized_by`` is recorded in the evidence so a
reader sees an approval was cited and did not count.

From 1.1.0 (``attributed_never`` configured) one statement in a never class
passes: the user's own claim, said as theirs and marked unverified. A class
in ``attributed_never`` passes only when ``Action.statement_attributed_to``
is ``user``, the words are passed beside the check
(``representing_opening``, ``{nonce, text}``), they open the commitment its
check seals to them (``Action.representation_text_commitment``), and they
are one sentence in ``attributed_template``: starting with its ``prefix``,
ending with its ``suffix``, with a claim between them that is not blank and
holds none of its ``sentence_marks``. Anything less fails, the words not
given included: this is a never rule, so it does not rest on the producer's
own template check. The agent's own statement of the class fails as before.
The words are never stated in the reason or the evidence.

A replay reads the words from the bundle. capsulectl passes
``representing_opening`` to a live check; a deal bundle seals the check's
words by commitment, and its deal report carries their opening (its
``representations``) unless the copy withholds it. The replay passes the
opening whose commitment equals the check's (``report/replay.py``,
``representing_openings``), so s10 decides as it did live. When the bundle
carries none (a counterparty copy, a claim never made), it passes
``WORDS_NOT_IN_BUNDLE``, and an attributed statement in ``attributed_never``
is then not applicable, in scope, naming ``representing_opening``, and
fails closed (``CheckOutcome.fails_closed``: refused, verdict
``not_evaluable``): "the words for this attributed claim are not in the
bundle". It is never recorded as a failure, which would say the agent broke
the rule over a claim the user made. A live check given no words still
fails. 1.0.0, which configures no ``attributed_never``, reads neither the
attribution nor an opening, and decides and records as it always did.

A class outside the closed set fails closed. Applies only to the configured
``action_classes``.
"""
from __future__ import annotations

import re
from typing import TypedDict

from ..action import ATTRIBUTED_TO_USER, Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome
from .commitment import opens_commitment
from .required_disclosure import require_known_classes

__all__ = [
    "ATTRIBUTED_TO_USER",
    "AttributedTemplate",
    "RepresentingOpening",
    "WORDS_NOT_IN_BUNDLE",
    "check_promise_never",
    "fits_attributed_template",
]

_CHECK_ID = "promise_never"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_METHOD = "class_membership_v0"


class AttributedTemplate(TypedDict):
    prefix: str
    suffix: str
    sentence_marks: list[str]


class RepresentingOpening(TypedDict):
    """external-check-input/v0 ``representing_opening``, as decoded JSON.
    Nothing in it is trusted until it opens the sealed commitment."""

    nonce: str
    text: str


class NeverEvidence(TypedDict):
    representation_class: str
    recognised: bool
    never: bool
    authorized_by: str | None


class AttributedEvidence(NeverEvidence):
    """1.1.0's evidence: whose claim the statement is, whether its words were
    opened, and whether they fit the template (``None`` when not judged)."""

    attributed_to: str | None
    words_opened: bool | None
    fits_template: bool | None


def fits_attributed_template(text: str, template: AttributedTemplate) -> bool:
    """Whether ``text`` is one sentence in ``template``: it starts with the
    prefix and ends with the suffix, and the claim between them is not blank
    and holds no sentence mark."""
    prefix, suffix = template["prefix"], template["suffix"]
    if len(text) < len(prefix) + len(suffix) or not text.startswith(prefix) or not text.endswith(suffix):
        return False
    claim = text[len(prefix):len(text) - len(suffix)]
    return bool(claim.strip()) and not any(mark in claim for mark in template["sentence_marks"])


class _WordsNotInBundle:
    """The replay's stand-in for an opening it cannot have."""

    def __repr__(self) -> str:
        return "WORDS_NOT_IN_BUNDLE"


# Passed by a replay as ``representing_opening`` (module docstring): the words
# are not in the bundle, so an attributed claim cannot be confirmed either way.
WORDS_NOT_IN_BUNDLE = _WordsNotInBundle()


def _outcome(result: str, reason: str, evidence: NeverEvidence | NotApplicableEvidence, *,
             fails_closed: bool = False) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        ),
        fails_closed=fails_closed,
    )


def check_promise_never(
    action: Action,
    opening: RepresentingOpening | _WordsNotInBundle | None = None,
    *,
    representation_classes: list[str],
    never: list[str],
    action_classes: list[str],
    attributed_never: list[str] | None = None,
    attributed_template: AttributedTemplate | None = None,
) -> CheckOutcome:
    require_known_classes(never, representation_classes, what="never")
    if attributed_never is not None:
        require_known_classes(attributed_never, never, what="attributed_never")
        if attributed_template is None:
            raise ValueError("attributed_never needs an attributed_template")
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    cls = action.representation_class
    if cls is None:
        return _outcome("n/a", "the action carries no representation_class; the statement could not be classed",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="representation_class"))
    recognised = cls in representation_classes
    is_never = cls in never
    evidence = NeverEvidence(representation_class=cls, recognised=recognised, never=is_never,
                             authorized_by=action.authorized_by)
    if not recognised:
        return _outcome("fail", f"representation_class {cls!r} is not in {representation_classes}",
                        _attributed(evidence, action) if attributed_never is not None else evidence)
    if attributed_never is None:
        if is_never:
            return _outcome("fail", f"the agent never makes a {cls} statement; no approval clears it, only a "
                                    "change to the rule set", evidence)
        return _outcome("pass", f"a {cls} statement is not one the agent never makes", evidence)
    assert attributed_template is not None  # checked above
    return _decide_attributed(action, opening, cls, is_never, evidence, attributed_never, attributed_template)


def _attributed(base: NeverEvidence, action: Action, opened: bool | None = None,
                fits: bool | None = None) -> AttributedEvidence:
    return AttributedEvidence(**base, attributed_to=action.statement_attributed_to, words_opened=opened,
                              fits_template=fits)


def _decide_attributed(
    action: Action,
    opening: RepresentingOpening | _WordsNotInBundle | None,
    cls: str,
    is_never: bool,
    base: NeverEvidence,
    attributed_never: list[str],
    template: AttributedTemplate,
) -> CheckOutcome:
    """1.1.0 on a recognised class (module docstring)."""
    if not is_never:
        return _outcome("pass", f"a {cls} statement is not one the agent never makes", _attributed(base, action))
    if action.statement_attributed_to != ATTRIBUTED_TO_USER or cls not in attributed_never:
        return _outcome("fail", f"the agent never makes a {cls} statement of its own; no approval clears it, "
                                "only a change to the rule set", _attributed(base, action))
    passed = f"a {cls} statement said as the user's own claim, marked unverified, is not the agent's own"
    sealed = action.representation_text_commitment
    if not isinstance(sealed, str) or not _HEX64.fullmatch(sealed):
        # A record with no commitment to its words is malformed, not short of
        # words: nothing could ever open it.
        return _outcome("fail", "the check seals no commitment to the words of this attributed claim",
                        _attributed(base, action, opened=False))
    if opening is WORDS_NOT_IN_BUNDLE:
        return _outcome("n/a", "the words for this attributed claim are not in the bundle",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="representing_opening"),
                        fails_closed=True)
    if opening is None:
        return _outcome("fail", f"a claim attributed to the user ({cls}) passes only with its words, and none "
                                "were given with the check", _attributed(base, action))
    # Decoded JSON from the checker input: each member is checked before it is read.
    nonce, text = (opening.get("nonce"), opening.get("text")) if isinstance(opening, dict) else (None, None)
    if not opens_commitment(nonce, text, sealed):
        return _outcome("fail", "the words given with the check don't match the words it sealed",
                        _attributed(base, action, opened=False))
    assert isinstance(text, str)  # opens_commitment checked it
    if not fits_attributed_template(text, template):
        return _outcome(
            "fail",
            f'the user\'s claim must read "{template["prefix"]}… {template["suffix"].strip()}", one sentence, so '
            "the buyer sees whose claim it is and that it is unverified",
            _attributed(base, action, opened=True, fits=False),
        )
    return _outcome("pass", passed, _attributed(base, action, opened=True, fits=True))
