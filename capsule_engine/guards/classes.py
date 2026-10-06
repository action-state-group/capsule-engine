# SPDX-License-Identifier: Apache-2.0
"""Action-class taxonomy (gating decisions doc §1), the classification
default, and the measurement mapping.

The taxonomy is one versioned data table, ``action_taxonomy.json``. Each row
is a canonical action class and declares, explicitly and with no defaults:
its trigger class, ``consequential``, ``fail_open_allowed``,
``approver_role`` and its legacy aliases. ``load_taxonomy_table`` rejects a
row missing any of them, so a new action cannot silently inherit a policy.

Two axes, two jobs:

- **Gating** (``classify``): an action with no declared class -- or one not
  in this taxonomy -- is CONSEQUENTIAL, fail-closed. This is deliberate: it
  is the loophole every other rule in the failure-semantics table would
  otherwise escape through.
- **Measurement** (``trigger_class``): the canonical action classes collapse
  many-to-one onto a small set of trigger classes, which are the population
  a "should this action have been checked" count is taken over. A trigger
  class is derived from the action class, never authored per record. An
  unknown name raises ``UnmappedActionClass`` here instead of degrading,
  because the failure this guards against is an action counting toward no
  population at all.

``CHANGE`` is a trigger class no row may declare: it is a transition after a
prior approval, so it is derived from state by the caller
(``after_prior_approval=True``), not from the action's name.

Sealed records are never rewritten. A legacy class name already sealed into
a record resolves through ``legacy_aliases`` to its canonical row on read.
A decision capsule carries ``taxonomy_version`` in ``asg_payload`` only when
its ``Action.taxonomy_version`` was set (``guards/capsule.py``); nothing in
this package sets it by default yet, so existing records keep their bytes. A
record without one is read as ``unversioned_records_read_as``.

Operator config (caps, tolerances, fail-open opt-ins) is still keyed by the
raw ``action_class`` string a caller sends, not by the canonical name.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType
from typing import TypedDict

__all__ = [
    "ActionClass",
    "TAXONOMY",
    "TAXONOMY_VERSION",
    "TRIGGER_CLASSES",
    "UNCLASSIFIED_DEFAULT",
    "UNVERSIONED_TAXONOMY_VERSION",
    "TaxonomyDocument",
    "TaxonomyRow",
    "TaxonomyTable",
    "TaxonomyTableError",
    "UnmappedActionClass",
    "VersionedPayload",
    "check_connector_declaration",
    "classify",
    "is_known_action_class",
    "legacy_aliases",
    "load_taxonomy_table",
    "record_taxonomy_version",
    "resolve",
    "trigger_class",
]

_TABLE_FILE = "action_taxonomy.json"
_ROW_KEYS = frozenset(
    {"name", "trigger_class", "consequential", "fail_open_allowed", "approver_role", "legacy_aliases"}
)
_TOP_KEYS = frozenset(
    {"taxonomy_version", "unversioned_records_read_as", "trigger_classes", "state_derived_trigger_classes", "actions"}
)


@dataclass(frozen=True)
class ActionClass:
    name: str
    consequential: bool
    # Whether this class MAY be configured to fail open on a stale view or an
    # unreachable engine (gating doc §1: "fail-open permitted for low-risk
    # classes when explicitly configured"). This never makes fail-open a
    # default by itself -- a caller must still opt in per-class on the engine.
    fail_open_allowed: bool = False
    # The role that can resolve a cap-exceeded hold on this class via the
    # HITL bridge (D2, 2026-08-05). None means no approver is configured for
    # this class -- a cap-exceeded action in it hard-denies rather than
    # escalating; this is the "classes explicitly marked deny" half of D2.
    approver_role: str | None = None
    # The measurement population this class counts toward. None only for a
    # non-consequential class (outside every population) and for
    # UNCLASSIFIED_DEFAULT (which ``trigger_class`` refuses to count).
    trigger_class: str | None = None
    legacy_aliases: tuple[str, ...] = ()


class TaxonomyRow(TypedDict):
    """One row of ``action_taxonomy.json``, as authored."""

    name: str
    trigger_class: str | None
    consequential: bool
    fail_open_allowed: bool
    approver_role: str | None
    legacy_aliases: list[str]


class TaxonomyDocument(TypedDict):
    """``action_taxonomy.json``, as authored."""

    taxonomy_version: str
    unversioned_records_read_as: str
    trigger_classes: list[str]
    state_derived_trigger_classes: list[str]
    actions: list[TaxonomyRow]


class VersionedPayload(TypedDict, total=False):
    """The part of a decision capsule's ``asg_payload`` this module reads."""

    action_class: str
    taxonomy_version: str


class TaxonomyTableError(ValueError):
    """The taxonomy table is malformed or under-declared."""


class UnmappedActionClass(LookupError):
    """A name has no row in the taxonomy, so it belongs to no population."""


@dataclass(frozen=True)
class TaxonomyTable:
    version: str
    unversioned_read_as: str
    trigger_classes: tuple[str, ...]
    classes: Mapping[str, ActionClass]
    aliases: Mapping[str, str]


def _require_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise TaxonomyTableError(f"{where} must be true or false, got {value!r}")
    return value


def _require_optional_str(value: object, where: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, f"{where} (or null)")


def _require_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise TaxonomyTableError(f"{where} must be a non-empty string, got {value!r}")
    return value


def _require_str_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TaxonomyTableError(f"{where} must be a list, got {value!r}")
    return tuple(_require_str(item, f"{where}[{i}]") for i, item in enumerate(value))


def _parse_row(row: object, idx: int, declarable: frozenset[str]) -> ActionClass:
    where = f"actions[{idx}]"
    if not isinstance(row, dict):
        raise TaxonomyTableError(f"{where} must be an object")
    missing = _ROW_KEYS - row.keys()
    if missing:
        raise TaxonomyTableError(f"{where} does not declare {sorted(missing)}; every row declares every key")
    extra = row.keys() - _ROW_KEYS
    if extra:
        raise TaxonomyTableError(f"{where} carries unknown keys {sorted(extra)}")
    name = _require_str(row["name"], f"{where}.name")
    where = f"actions[{name!r}]"
    consequential = _require_bool(row["consequential"], f"{where}.consequential")
    fail_open_allowed = _require_bool(row["fail_open_allowed"], f"{where}.fail_open_allowed")
    approver_role = _require_optional_str(row["approver_role"], f"{where}.approver_role")
    trigger = _require_optional_str(row["trigger_class"], f"{where}.trigger_class")
    if consequential and fail_open_allowed:
        raise TaxonomyTableError(f"{where} is consequential and sets fail_open_allowed; only a non-consequential class may")
    if consequential and trigger is None:
        raise TaxonomyTableError(f"{where} is consequential and has no trigger_class")
    if not consequential and trigger is not None:
        raise TaxonomyTableError(f"{where} is non-consequential but declares trigger_class {trigger!r}")
    if trigger is not None and trigger not in declarable:
        raise TaxonomyTableError(f"{where}.trigger_class={trigger!r} is not a declarable trigger class {sorted(declarable)}")
    return ActionClass(
        name=name,
        consequential=consequential,
        fail_open_allowed=fail_open_allowed,
        approver_role=approver_role,
        trigger_class=trigger,
        legacy_aliases=_require_str_list(row["legacy_aliases"], f"{where}.legacy_aliases"),
    )


def load_taxonomy_table(raw: object) -> TaxonomyTable:
    """Validate a parsed taxonomy table (a ``TaxonomyDocument`` once it passes). Raises ``TaxonomyTableError`` on any
    under-declared row, duplicate name, or alias collision."""
    if not isinstance(raw, dict):
        raise TaxonomyTableError("taxonomy table must be an object")
    missing = _TOP_KEYS - raw.keys()
    if missing:
        raise TaxonomyTableError(f"taxonomy table does not declare {sorted(missing)}")
    version = _require_str(raw["taxonomy_version"], "taxonomy_version")
    unversioned = _require_str(raw["unversioned_records_read_as"], "unversioned_records_read_as")
    triggers = _require_str_list(raw["trigger_classes"], "trigger_classes")
    derived = _require_str_list(raw["state_derived_trigger_classes"], "state_derived_trigger_classes")
    if not set(derived) <= set(triggers):
        raise TaxonomyTableError(f"state_derived_trigger_classes {list(derived)} must be trigger classes")
    declarable = frozenset(triggers) - frozenset(derived)
    rows = raw["actions"]
    if not isinstance(rows, list) or not rows:
        raise TaxonomyTableError("actions must be a non-empty list")

    classes: dict[str, ActionClass] = {}
    for idx, row in enumerate(rows):
        ac = _parse_row(row, idx, declarable)
        if ac.name in classes:
            raise TaxonomyTableError(f"action class {ac.name!r} declared more than once")
        classes[ac.name] = ac
    aliases: dict[str, str] = {}
    for ac in classes.values():
        for alias in ac.legacy_aliases:
            if alias in classes or alias in aliases:
                raise TaxonomyTableError(f"legacy alias {alias!r} on {ac.name!r} collides with another name")
            aliases[alias] = ac.name
    if not any(not ac.consequential for ac in classes.values()):
        raise TaxonomyTableError("taxonomy has no non-consequential class; the negative set would vanish")
    return TaxonomyTable(
        version=version,
        unversioned_read_as=unversioned,
        trigger_classes=triggers,
        classes=MappingProxyType(classes),
        aliases=MappingProxyType(aliases),
    )


def _load_packaged() -> TaxonomyTable:
    text = resources.files("capsule_engine.guards").joinpath(_TABLE_FILE).read_text(encoding="utf-8")
    return load_taxonomy_table(json.loads(text))


_TABLE = _load_packaged()

TAXONOMY_VERSION: str = _TABLE.version
UNVERSIONED_TAXONOMY_VERSION: str = _TABLE.unversioned_read_as
TRIGGER_CLASSES: tuple[str, ...] = _TABLE.trigger_classes
# Canonical rows only; legacy names resolve through ``resolve``/``classify``.
TAXONOMY: Mapping[str, ActionClass] = MappingProxyType(dict(_TABLE.classes))

MONEY_TRANSFER = TAXONOMY["money.transfer"]
DATA_DELETE = TAXONOMY["data.delete"]
COMMS_EXTERNAL = TAXONOMY["communication.send"]
# The one low-risk class, so "fail-open only where declared" is a real,
# exercised path rather than a theoretical one.
INFO_QUERY = TAXONOMY["info.query"]

UNCLASSIFIED_DEFAULT = ActionClass("unclassified", consequential=True)


def resolve(action_class: str) -> ActionClass | None:
    """The canonical row for a canonical or legacy name, or None."""
    canonical = _TABLE.aliases.get(action_class, action_class)
    return TAXONOMY.get(canonical)


def is_known_action_class(action_class: str) -> bool:
    return resolve(action_class) is not None


def legacy_aliases() -> Mapping[str, str]:
    """``{legacy name: canonical name}`` for every alias in the table."""
    return _TABLE.aliases


def classify(action_class: str | None) -> ActionClass:
    """Resolve a declared class name to its policy.

    Absent, or not present in the taxonomy, both resolve to the
    consequential/fail-closed default -- the classification default is not
    conditioned on the name being *recognized*, only on it being *declared
    and known*. A legacy name resolves to its canonical row.
    """
    if action_class is None:
        return UNCLASSIFIED_DEFAULT
    return resolve(action_class) or UNCLASSIFIED_DEFAULT


def trigger_class(action_class: str, *, after_prior_approval: bool = False) -> str | None:
    """The measurement population ``action_class`` counts toward.

    ``None`` means the class is non-consequential and outside every
    population. ``after_prior_approval`` is the caller's state: a
    consequential action that amends something already approved counts as
    ``CHANGE``. Raises ``UnmappedActionClass`` for a name with no row.
    """
    ac = resolve(action_class)
    if ac is None:
        raise UnmappedActionClass(
            f"action class {action_class!r} has no row in taxonomy version {TAXONOMY_VERSION}; "
            "add it to guards/action_taxonomy.json before it can be counted"
        )
    if not ac.consequential:
        return None
    if after_prior_approval:
        return "CHANGE"
    return ac.trigger_class


def check_connector_declaration(exposed: Mapping[str, str]) -> dict[str, ActionClass]:
    """Validate a connector's ``{tool name: canonical action class}``
    declaration. Every target must be a canonical row (not a legacy alias);
    raises ``UnmappedActionClass`` naming every tool that is not."""
    bad = sorted(tool for tool, name in exposed.items() if name not in TAXONOMY)
    if bad:
        raise UnmappedActionClass(f"connector tools {bad} declare no canonical action class in taxonomy version {TAXONOMY_VERSION}")
    return {tool: TAXONOMY[name] for tool, name in exposed.items()}


def record_taxonomy_version(asg_payload: VersionedPayload) -> str:
    """The taxonomy version a sealed decision capsule's ``action_class`` was
    written under; records that predate the table carry none."""
    if "taxonomy_version" not in asg_payload:
        return UNVERSIONED_TAXONOMY_VERSION
    return _require_str(asg_payload["taxonomy_version"], "asg_payload.taxonomy_version")
