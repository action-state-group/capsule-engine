# SPDX-License-Identifier: Apache-2.0
"""counterparty_list check: is this action's counterparty on a list the user
supplied?

The list is the user's, not the pack's. The wicket definition ships an empty
deny list (``counterparty_list.yaml``); a policy profile replaces ``mode``,
``entries`` and ``disposition`` (``policy/profile.py``), so two households
with different lists run the same wicket at the same digest.

``mode`` is ``deny`` (fail when the counterparty is listed: "never buy from
X") or ``allow`` (fail when it is not: "only ever these"). A profile that
sets a list must name its mode; neither is assumed.

What an entry matches on, and in what form, is part of the entry:

- ``{kind: target, value}``: ``Action.target``, a clear reference the
  producer declares (a payment's destination reference, for example).
- ``{kind: domain | payee, fp_alg, value}``: one of the counterparty's
  keyed fingerprints as the producer sealed them (``Action.counterparty_ids``,
  made with ``Action.counterparty_fp_alg``). A deal check seals these and
  never the clear domain or payee; the user's client computes an entry's
  fingerprint with the same key when the list is set, so the guard never
  holds the key or the clear value.

A display name is never a kind: it is not an identifier. An entry matches
only a reference of its own kind and, for a fingerprint, its own
``fp_alg``, so a clear value and a fingerprint never compare, and neither do
a domain and a payee. Matching is exact, with no case folding or trimming.
A reference is only as stable as the producer that declares it: a
re-registered domain fingerprints the same, and nothing here can tell. The
check is a pure function of the action and its config, with no I/O.

Every kind the list names must be readable on the action, or the result is
``n/a``, in scope, naming what was missing: ``target``, ``counterparty`` (no
fingerprints sealed at all), ``counterparty.fp_alg`` (sealed with a
different algorithm than an entry's), or ``counterparty.ids.<kind>``. A
list the action cannot fully be checked against never passes. With an empty
list, an action carrying no reference of any kind is ``n/a`` naming
``counterparty``.

``disposition`` is read by the engine, not here (``guards/engine.py``):
``deny`` refuses on a fail; ``ask`` sends the fail to an approver where the
action class names one and refuses where it does not, the same rule a caps
or first-time-counterparty fail follows.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict

from agent_action_capsule.canonical import json_digest

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = [
    "CLEAR_KINDS",
    "DISPOSITIONS",
    "FINGERPRINT_KINDS",
    "MODES",
    "CounterpartyListEvidence",
    "ListEntry",
    "check_counterparty_list",
    "require_disposition",
    "sorted_entries",
]

_CHECK_ID = "counterparty_list"
_METHOD = "exact_reference_match_v0"
MODES = frozenset({"deny", "allow"})
DISPOSITIONS = frozenset({"deny", "ask"})
CLEAR_KINDS = frozenset({"target"})
FINGERPRINT_KINDS = frozenset({"domain", "payee"})


class ListEntry(TypedDict):
    """One reference on the list. ``fp_alg`` is set exactly when ``kind`` is
    a fingerprint kind."""

    kind: str
    value: str
    fp_alg: NotRequired[str]


class CounterpartyListEvidence(TypedDict):
    """What a pass or a fail records: how it matched, which list (by digest
    and size), the reference of each kind the list names as read off the
    action, and the entry it matched."""

    match_rule: str
    mode: str
    list_digest: str
    list_size: int
    read: list[ListEntry]
    matched_entry: ListEntry | None


def sorted_entries(entries: list[ListEntry]) -> list[ListEntry]:
    """``entries`` in a canonical order, so neither the list digest nor the
    profile digest depends on the order a user typed them in."""
    return sorted(entries, key=json_digest)


def require_disposition(disposition: str) -> None:
    """Refuse a disposition the engine does not know, when it is built."""
    if disposition not in DISPOSITIONS:
        raise ValueError(f"counterparty_list disposition {disposition!r} must be one of {sorted(DISPOSITIONS)}")


def _outcome(result: str, reason: str, evidence: CounterpartyListEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _not_evaluable(missing_field: str) -> CheckOutcome:
    return _outcome(
        "n/a",
        f"the action carries no usable {missing_field}; the counterparty list could not be checked",
        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=missing_field),
    )


def _read(action: Action, kind: str, fp_algs: set[str]) -> tuple[ListEntry | None, str | None]:
    """The action's reference of ``kind``, in the form the list's entries of
    that kind use, or the name of the field that is missing (exactly one of
    the pair is set)."""
    if kind in CLEAR_KINDS:
        return ({"kind": kind, "value": action.target}, None) if action.target else (None, "target")
    if not action.counterparty_ids or not action.counterparty_fp_alg:
        return None, "counterparty"
    if fp_algs != {action.counterparty_fp_alg}:
        return None, "counterparty.fp_alg"
    value = action.counterparty_ids.get(kind)
    if not value:
        return None, f"counterparty.ids.{kind}"
    return {"kind": kind, "fp_alg": action.counterparty_fp_alg, "value": value}, None


def check_counterparty_list(
    action: Action, *, mode: str, entries: list[ListEntry], action_classes: list[str]
) -> CheckOutcome:
    if mode not in MODES:
        raise ValueError(f"counterparty_list mode {mode!r} must be one of {sorted(MODES)}")
    if action.action_class not in action_classes:
        return _outcome(
            "n/a",
            "the rule is not configured for this action class",
            not_applicable_evidence(_CHECK_ID, in_scope=False),
        )
    listed = sorted_entries(entries)
    if not listed and not action.target and not action.counterparty_ids:
        return _not_evaluable("counterparty")
    read: list[ListEntry] = []
    for kind in sorted({e["kind"] for e in listed}):
        fp_algs = {e["fp_alg"] for e in listed if e["kind"] == kind and "fp_alg" in e}
        reference, missing = _read(action, kind, fp_algs)
        if reference is None:
            return _not_evaluable(missing or kind)
        read.append(reference)
    matched = next((e for e in listed if e in read), None)
    evidence: CounterpartyListEvidence = {
        "match_rule": "exact",
        "mode": mode,
        "list_digest": json_digest(listed),
        "list_size": len(listed),
        "read": read,
        "matched_entry": matched,
    }
    # deny: a match fails. allow: a miss fails.
    result = "fail" if (matched is not None) == (mode == "deny") else "pass"
    verdict = "is on" if matched is not None else "is not on"
    reason = f"the counterparty {verdict} the {mode} list (consulted {len(listed)} entries)"
    return _outcome(result, reason, evidence)
