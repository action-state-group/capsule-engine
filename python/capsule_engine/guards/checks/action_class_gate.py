# SPDX-License-Identifier: Apache-2.0
"""action_class_gate check: named selectors over the action taxonomy.

Each selector matches on the canonical ``action_class`` and/or its
``trigger_class`` and/or ``consequential`` (``guards/action_taxonomy.json``),
and declares the result a match produces (``on_match``: ``fail`` or
``pass``). Every key a selector sets must match. The check reads
``Action.action_class``, resolved through the taxonomy table so a
legacy alias is its canonical class; it reads no fold. A selector may also
set ``except_recipient_roles``: it then does not match an action whose
``recipient_role`` is one of them (action_class_gate/1.1.0 leaves a
disclosure to a ``platform`` to ``recipient_role``, which measures it). That
is the one other field it reads, and only for such a selector.
action_class_gate/1.2.0 adds two narrowing keys, each ``true`` or absent
(capsule-engine#188): ``except_repeat_disclosures`` does not match an action
every one of whose ``disclosed_classes`` is in its ``repeat_disclosures``,
the classes already told to this recipient; ``except_recipient_told_before``
does not match an action whose ``recipient_told_before`` is ``True``. A
missing input is no exception, so such a selector matches as it would
without the key. Neither input is sealed on the decision, and neither reason
nor evidence repeats them.
A replay passes the table the record was sealed under (``table``), and the
evidence names that table's version; otherwise it is the engine's.

Result: ``fail`` when any ``fail`` selector matches, else ``pass`` when any
``pass`` selector matches, else ``n/a`` out of scope. A class with no row in
the taxonomy (or no declared class) fails closed, because the taxonomy is
the only input this check has. A selector naming something the taxonomy
cannot resolve raises ``ValueError`` instead of matching nothing; the
selectors are parsed on every decision, so a bad wicket raises from
``GuardEngine.check``, not when the definition is loaded.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from ..classes import ENGINE_TAXONOMY, TAXONOMY, TAXONOMY_VERSION, TRIGGER_CLASSES, TaxonomyTable
from .base import CheckOutcome

__all__ = ["Selector", "check_action_class_gate", "parse_selectors"]

_CHECK_ID = "action_class_gate"
_METHOD = "taxonomy_selector_v0"
_MATCH_KEYS = frozenset({"action_classes", "trigger_classes", "consequential"})
# Narrows a selector; it selects nothing on its own.
_EXCEPT_ROLES = "except_recipient_roles"
# Narrow a selector by what the producer's telling memory says (1.2.0); each
# is ``true`` when set.
_EXCEPT_REPEAT = "except_repeat_disclosures"
_EXCEPT_TOLD = "except_recipient_told_before"
_EXCEPT_FLAGS = (_EXCEPT_REPEAT, _EXCEPT_TOLD)
_ON_MATCH = frozenset({"fail", "pass"})
# Trigger classes a taxonomy row may declare; CHANGE is derived from state.
_DECLARED_TRIGGERS = frozenset(ac.trigger_class for ac in TAXONOMY.values() if ac.trigger_class is not None)


class Selector(TypedDict, total=False):
    """One selector as authored in the wicket's ``config.selectors``."""

    action_classes: list[str]
    trigger_classes: list[str]
    consequential: bool
    except_recipient_roles: list[str]
    except_repeat_disclosures: bool
    except_recipient_told_before: bool
    on_match: str


class GateEvidence(TypedDict):
    action_class: str
    trigger_class: str | None
    consequential: bool
    taxonomy_version: str
    matched_selectors: list[str]


class UnmappedClassEvidence(TypedDict):
    action_class: str | None
    in_taxonomy: bool
    taxonomy_version: str


def _outcome(
    result: str, reason: str, evidence: GateEvidence | UnmappedClassEvidence | NotApplicableEvidence
) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


# Reads the wicket's YAML config: the check's decoding boundary.
def parse_selectors(selectors: object) -> dict[str, Selector]:
    """The selectors, or ``ValueError`` unless every one resolves against the taxonomy."""
    if not isinstance(selectors, dict) or not selectors:
        raise ValueError("selectors must be a non-empty mapping of selector id to selector")
    for sid, sel in selectors.items():
        if not isinstance(sid, str) or not sid:
            raise ValueError(f"selector id {sid!r} must be a non-empty string")
        if not isinstance(sel, dict):
            raise ValueError(f"selector {sid!r} must be a mapping")
        unknown = sel.keys() - _MATCH_KEYS - {"on_match", _EXCEPT_ROLES, *_EXCEPT_FLAGS}
        if unknown:
            raise ValueError(f"selector {sid!r} carries unknown keys {sorted(unknown)}")
        if not sel.keys() & _MATCH_KEYS:
            raise ValueError(f"selector {sid!r} selects nothing; set one of {sorted(_MATCH_KEYS)}")
        if sel.get("on_match") not in _ON_MATCH:
            raise ValueError(f"selector {sid!r} on_match must be one of {sorted(_ON_MATCH)}")
        for key in ("action_classes", "trigger_classes", _EXCEPT_ROLES):
            if key in sel and (
                not isinstance(sel[key], list) or not sel[key] or not all(isinstance(n, str) for n in sel[key])
            ):
                raise ValueError(f"selector {sid!r} {key} must be a non-empty list of names")
        for name in sel.get("action_classes", []):
            if name not in TAXONOMY:
                raise ValueError(f"selector {sid!r} names {name!r}, not a canonical class in taxonomy {TAXONOMY_VERSION}")
        for name in sel.get("trigger_classes", []):
            if name not in _DECLARED_TRIGGERS:
                raise ValueError(
                    f"selector {sid!r} names trigger class {name!r}; no taxonomy row declares it "
                    f"(declared: {sorted(_DECLARED_TRIGGERS)}, all: {list(TRIGGER_CLASSES)})"
                )
        if "consequential" in sel and not isinstance(sel["consequential"], bool):
            raise ValueError(f"selector {sid!r} consequential must be true or false")
        for key in _EXCEPT_FLAGS:
            if key in sel and sel[key] is not True:
                raise ValueError(f"selector {sid!r} {key} must be true when set")
    return selectors


def _matches(sel: Selector, action_class: str, trigger_class: str | None, consequential: bool) -> bool:
    """Whether ``sel``'s class keys match; ``except_recipient_roles`` is read apart (``_excepted``)."""
    if "action_classes" in sel and action_class not in sel["action_classes"]:
        return False
    if "trigger_classes" in sel and trigger_class not in sel["trigger_classes"]:
        return False
    return "consequential" not in sel or sel["consequential"] == consequential


def _excepted(sel: Selector, action: Action) -> str | None:
    """Why ``sel`` does not match ``action`` although its class keys do, or
    ``None``: a fixed phrase, never a value the action carries."""
    if action.recipient_role in sel.get(_EXCEPT_ROLES, ()):
        return "role"
    if sel.get(_EXCEPT_REPEAT) and _all_repeated(action):
        return "every class it discloses was told to this recipient before"
    if sel.get(_EXCEPT_TOLD) and action.recipient_told_before is True:
        return "this recipient was told something before"
    return None


def _all_repeated(action: Action) -> bool:
    """Every class the action discloses is one already told to its
    recipient. An action naming no class, or carrying no repeat list, is not
    a repeat."""
    disclosed, repeated = action.disclosed_classes, action.repeat_disclosures
    if not disclosed or repeated is None:
        return False
    return set(disclosed) <= set(repeated)


def check_action_class_gate(
    action: Action, *, selectors: Mapping[str, Selector], table: TaxonomyTable = ENGINE_TAXONOMY
) -> CheckOutcome:
    selectors = parse_selectors(selectors)
    ac = table.resolve(action.action_class) if action.action_class is not None else None
    if ac is None:
        return _outcome(
            "fail",
            f"action class {action.action_class!r} has no row in taxonomy {table.version}; fail closed",
            UnmappedClassEvidence(action_class=action.action_class, in_taxonomy=False, taxonomy_version=table.version),
        )
    named = [sid for sid, sel in selectors.items() if _matches(sel, ac.name, ac.trigger_class, ac.consequential)]
    why = {sid: _excepted(selectors[sid], action) for sid in named}
    matched = sorted(sid for sid in named if why[sid] is None)
    if not matched:
        reason = "no selector names this action class"
        if named and all(w == "role" for w in why.values()):
            # 1.1.0's reason, byte for byte.
            reason = (f"the selectors naming action class {ac.name!r} leave out recipient_role "
                      f"{action.recipient_role!r}")
        elif named:
            phrases = sorted({f"recipient_role {action.recipient_role!r}" if w == "role" else w for w in why.values()})
            reason = f"the selectors naming action class {ac.name!r} leave this action out: {'; '.join(phrases)}"
        return _outcome("n/a", reason, not_applicable_evidence(_CHECK_ID, in_scope=False))
    evidence = GateEvidence(
        action_class=ac.name,
        trigger_class=ac.trigger_class,
        consequential=ac.consequential,
        taxonomy_version=table.version,
        matched_selectors=matched,
    )
    if any(selectors[sid]["on_match"] == "fail" for sid in matched):
        return _outcome("fail", f"action class {ac.name!r} matched {matched}", evidence)
    return _outcome("pass", f"action class {ac.name!r} matched {matched}", evidence)
