# SPDX-License-Identifier: Apache-2.0
"""Action-class convention labels (ldg-registry-driven-viewer item 2):
where a capsule's ``asg_payload.action_class`` matches a registered
convention, every display surface shows its human label/description from
this module instead of a hardcoded string.

**Registry-of-record note (moved here from capsule-ledger,
[ldg-ledger-scope-re-extraction] RESIDUALS pass, §3.1(a)):** this module and
its two vendored snapshots (``conventions.json``, ``cpb_registry.json``) are
an INTERIM company-side vendor copy, not the canonical registry. The real
registry-of-record for the CPB tables (``cpb_registry.json``) is
``action-state-group/scitt-payload-binding``'s own ``registry.json`` +
``spec/cpb-provisional-registry.md`` -- see ``scripts/vendor_cpb_registry.py``
for the re-sync procedure and pinned-commit provenance. A future neutral
``capsule-registry`` repo (referenced throughout this module's docstrings,
not yet created as of this move) would supersede this vendor copy the same
way; until then, this copy is never to be read as the registry-of-record
itself. capsule-ledger keeps its own separate, minimal, self-contained
``describe_action_class`` shim (its own tiny embedded label data, no
dependency on this package) for its own core read/verify display surface --
that shim and this vendor copy are independent by design, not two forks of
one truth.

``action_class`` -- not the -02 spec's own closed ``action_type`` field
(restricted to ``fyi``/``decide``, see ``guards/action.py``) -- is the
namespaced convention field the "Agent Action Semantics" layer
(``payment.*``, ``comms.*``, ``authority.*``, ``hold.*``) actually governs
(registry-architecture-and-namespace-2026-08-10.md §1, Home 2, table 4).
That table's real home, ``capsule-registry``, is confirmed not to exist yet
(created "at the capsule-ledger flip", same doc §6 item 5) -- the identical
situation ``packs/pins.py`` already solved for pack/fold digest pins. This
module is the lookup GATE, deliberately decoupled from where the convention
data came from: today it's a local, vendored snapshot (``conventions.json``,
shipped as package data, no network call ever -- this package's own
no-network-calls discipline); swapping in a live ``capsule-registry`` fetch
later changes *how the table is obtained*, never this module's callers.

**The never-reject invariant** (same as the spec-level REGISTRY.md already
documents for ``verdict_class`` etc.): an ``action_class`` with no entry
here renders as-is, marked unregistered -- informational only, never an
error. A capsule carrying no ``action_class`` at all is a different,
legitimate state (most capsules predate any pack) and is never described as
"unregistered".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from agent_action_capsule import json_digest

__all__ = [
    "ActionConvention",
    "describe_action_class",
    "conventions_digest",
    "FieldConvention",
    "describe_field_value",
    "is_known_reference_field",
]


@dataclass(frozen=True)
class ActionConvention:
    action_class: str | None
    label: str
    description: str | None
    registered: bool


@dataclass(frozen=True)
class FieldConvention:
    """A resolved convention label for one AAC six-registry field value
    (``effect.type``, ``effect_attestation``, ``chain.relation``, ...).

    ``status`` is ``"provisional"`` when the value is resolved from the vendored
    CPB *provisional* payload-class snapshot (a known-with-status-provisional
    value, never a rejection -- the never-reject invariant); ``None`` when the
    value carries no convention entry here (renders as-is, unregistered).
    ``payload_class`` names the CPB provisional payload class that sets the
    value, when known."""
    field: str
    value: str
    label: str
    description: str | None
    status: str | None
    payload_class: str | None
    registered: bool


def _load_raw() -> dict[str, Any]:
    text = resources.files("capsule_engine.registry").joinpath("conventions.json").read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    return _load_raw().get("action_class_conventions", {})


@lru_cache(maxsize=1)
def _provisional_field_table() -> dict[str, Any]:
    """The vendored provisional field-value conventions, keyed by AAC registry
    field name (``effect.type`` etc.). Empty when none are vendored."""
    return _load_raw().get("provisional_field_conventions", {})


@lru_cache(maxsize=1)
def _grc_field_table() -> dict[str, Any]:
    """Company-authored (not vendored CPB) field-value conventions, keyed by
    field name -- the GRC folds batch's registry facets. Distinct from
    ``_provisional_field_table()`` so ``scripts/vendor_cpb_registry.py``'s
    drift check, scoped exactly to ``provisional_field_conventions``, is
    never touched by these additions."""
    return _load_raw().get("grc_field_conventions", {})


@lru_cache(maxsize=1)
def _reference_fields() -> dict[str, str]:
    return _load_raw().get("grc_reference_fields", {})


def is_known_reference_field(field: str) -> bool:
    """True for a bare identifier/reference field the GRC folds batch
    registered (``approver_id``, ``output.marking_ref``, ``reference_db_ref``)
    -- these carry no enumerable value set, so they are documented here
    rather than forced into ``describe_field_value``'s per-value table."""
    return field in _reference_fields()


@lru_cache(maxsize=1)
def conventions_digest() -> str:
    """The vendored snapshot's own content digest -- lets any surface state
    exactly which convention-table version it rendered a label from,
    offline, without a version number this file could drift out of sync
    with."""
    return json_digest(_load_raw())


def describe_action_class(action_class: str | None) -> ActionConvention:
    if not action_class:
        return ActionConvention(action_class=None, label="(no action class recorded)", description=None, registered=False)
    entry = _table().get(action_class)
    if entry is None:
        return ActionConvention(action_class=action_class, label=action_class, description=None, registered=False)
    return ActionConvention(
        action_class=action_class, label=entry["label"], description=entry.get("description"), registered=True
    )


def describe_field_value(field: str, value: str | None) -> FieldConvention:
    """Resolve a convention label for one AAC six-registry field value.

    A value carried by a vendored CPB *provisional* payload class resolves
    known-with-status-``provisional`` (``registered=True``, ``status=
    "provisional"``); a value from the company-authored, hand-curated GRC
    table (``_grc_field_table``, checked first since it never overlaps the
    vendored table's field/value pairs) resolves with whatever ``status`` it
    was registered under (``"registered"`` or ``"planned"``); any other value
    renders as-is, unregistered -- never an error (the never-reject invariant,
    mirroring ``describe_action_class`` and the spec-level §12 binding)."""
    if not value:
        return FieldConvention(
            field=field, value="", label="(no value recorded)", description=None,
            status=None, payload_class=None, registered=False,
        )
    entry = _grc_field_table().get(field, {}).get(value)
    if entry is not None:
        return FieldConvention(
            field=field, value=value,
            label=entry.get("label", value),
            description=entry.get("description"),
            status=entry.get("status", "registered"),
            payload_class=None,
            registered=True,
        )
    entry = _provisional_field_table().get(field, {}).get(value)
    if entry is None:
        return FieldConvention(
            field=field, value=value, label=value, description=None,
            status=None, payload_class=None, registered=False,
        )
    return FieldConvention(
        field=field, value=value,
        label=entry.get("label", value),
        description=entry.get("description"),
        status=entry.get("status", "provisional"),
        payload_class=entry.get("payload_class"),
        registered=True,
    )
