# SPDX-License-Identifier: Apache-2.0
"""YAML front door for packs: a pack directory -> a validated ``PackDefinition``.

A pack directory looks like::

    payments-safety/
      pack.yaml               # this module's own top-level shape
      wickets/                # not read directly -- constraints are inline
      folds/
        spend_weekly.yaml     # a real fold definition (folds/definition.py)
      fixtures/
        mini_ledger.jsonl
      AI-BOOTSTRAP.md

``constraints`` entries in ``pack.yaml`` are wicket-definition-shaped dicts,
parsed with the exact same ``guards.wickets.definition.parse_definition``
the core wicket catalog uses -- a malformed constraint gets the identical,
already-hardened ``unknown_check``/``invalid_wicket_id_namespace``/etc. error
a hand-written wicket file would, just wrapped with which pack and which
constraint index it came from. ``folds`` entries are file references,
resolved relative to the pack directory and parsed with
``folds.definition.parse_definition`` the same way.

Every raised error is a ``PackDefinitionError`` (``errors.py``): a reason
code plus a message that names the field, says what was expected, and shows
a correct example -- the pack.yaml author is very often an AI coding tool,
so a vague message is a real cost, not a style nit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..folds.definition import FoldDefinition
from ..folds.errors import FoldDefinitionError
from ..folds.loader import load_definition_file as load_fold_definition_file
from ..guards.classes import TAXONOMY
from ..guards.wickets.definition import WicketDefinition
from ..guards.wickets.definition import parse_definition as parse_wicket_definition
from ..guards.wickets.errors import WicketDefinitionError
from .errors import (
    DUPLICATE_ACTION_TYPE,
    DUPLICATE_CONSTRAINT_WICKET_ID,
    DUPLICATE_OBLIGATION_ID,
    FOLD_FILE_NOT_FOUND,
    INVALID_ACTION_SEMANTIC,
    INVALID_CONSTRAINT,
    INVALID_FIXTURES,
    INVALID_FOLD_REF,
    INVALID_HOLDS_INTEGRATION,
    INVALID_PACK_ID,
    INVALID_SCOPE_DIMENSION,
    MALFORMED_PACK,
    MISSING_CONSTRAINT_SCOPE,
    MISSING_REQUIRED_FIELD,
    OBLIGATION_CHECK_NOT_DECLARED,
    PACK_NOT_FOUND,
    SCOPE_MISMATCH,
    UNKNOWN_ACTION_CLASS,
    UNKNOWN_NORMALIZED_FIELD,
    PackDefinitionError,
)
from .schema import (
    HOLDS_INTEGRATION_VALUES,
    KNOWN_SCOPE_DIMENSIONS,
    NORMALIZED_ACTION_FIELDS,
    PACK_ID_RE,
    ActionSemantic,
    FixtureScenario,
    Obligation,
    PackDefinition,
    PackFixtures,
    ProposerStub,
)

__all__ = ["load_pack_dir"]

_FIXTURE_OUTCOMES = frozenset({"allow", "deny", "escalate"})
_PROPOSER_STATUSES = frozenset({"planned"})  # "active" lands with P2's thresholds propose

# The base capsule spec's own closed action_type vocabulary (§5.1: "action_type
# MUST be 'fyi' or 'decide'"). A pack's action_semantics[].action_type is a
# different, documentation-level thing entirely (an OTel-semconv-style bare
# convention name) -- but it must not collide with these reserved values, or
# a pack author could plausibly (and wrongly) believe it's meant to be written
# into a capsule's own action_type field.
RESERVED_CAPSULE_ACTION_TYPES = frozenset({"fyi", "decide"})


def _require_mapping(data: Any, what: str) -> dict:
    if not isinstance(data, dict):
        raise PackDefinitionError(MALFORMED_PACK, f"{what} must be a mapping, got {type(data).__name__}")
    return data


def _require_nonempty_str(value: Any, field_name: str, example: str) -> str:
    if not isinstance(value, str) or not value:
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD, f"{field_name!r} is required and must be a non-empty string, e.g. {example!r}"
        )
    return value


def _parse_obligations(raw: Any, *, declared_checks: set[str]) -> tuple[Obligation, ...]:
    if raw is None:
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'obligations' is required (a pack ships at least one) -- each entry needs 'id', 'statement', "
            "and 'check', e.g.:\n"
            "obligations:\n"
            "  - id: caps-per-window\n"
            "    statement: \"No payment may exceed the configured weekly cap without escalation.\"\n"
            "    check: caps",
        )
    if not isinstance(raw, list) or not raw:
        raise PackDefinitionError(MALFORMED_PACK, "'obligations' must be a non-empty list")

    obligations: list[Obligation] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"obligations[{idx}]")
        obligation_id = _require_nonempty_str(entry.get("id"), f"obligations[{idx}].id", "caps-per-window")
        if obligation_id in seen_ids:
            raise PackDefinitionError(DUPLICATE_OBLIGATION_ID, f"obligation id {obligation_id!r} declared more than once")
        seen_ids.add(obligation_id)
        statement = _require_nonempty_str(
            entry.get("statement"),
            f"obligations[{obligation_id!r}].statement",
            "No payment may exceed the configured weekly cap without escalation.",
        )
        check = _require_nonempty_str(entry.get("check"), f"obligations[{obligation_id!r}].check", "caps")
        if check not in declared_checks:
            raise PackDefinitionError(
                OBLIGATION_CHECK_NOT_DECLARED,
                f"obligations[{obligation_id!r}].check={check!r} has no matching entry in 'constraints' "
                f"(declared checks: {sorted(declared_checks) or '<none>'}) -- every obligation must map 1:1 "
                "to a constraint that actually enforces it; add a constraints[] entry with check: "
                f"{check!r}, or fix the typo",
            )
        obligations.append(Obligation(id=obligation_id, statement=statement, check=check))
    return tuple(obligations)


def _parse_action_semantics(raw: Any) -> tuple[ActionSemantic, ...]:
    if not raw:
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'action_semantics' is required (a pack ships at least one action type) -- each entry needs "
            "'action_type', 'action_class', and 'required_fields', e.g.:\n"
            "action_semantics:\n"
            "  - action_type: payment.dispatch\n"
            "    action_class: money.transfer\n"
            "    required_fields: [amount_minor, currency, target]",
        )
    if not isinstance(raw, list):
        raise PackDefinitionError(MALFORMED_PACK, "'action_semantics' must be a list")

    out: list[ActionSemantic] = []
    seen_types: set[str] = set()
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"action_semantics[{idx}]")
        action_type = _require_nonempty_str(
            entry.get("action_type"), f"action_semantics[{idx}].action_type", "payment.dispatch"
        )
        if action_type in RESERVED_CAPSULE_ACTION_TYPES:
            raise PackDefinitionError(
                INVALID_ACTION_SEMANTIC,
                f"action_semantics[{idx}].action_type={action_type!r} collides with the base capsule "
                f"spec's own reserved action_type values {sorted(RESERVED_CAPSULE_ACTION_TYPES)} (§5.1). "
                "A pack's action_type is a documentation-level convention name (how obligations/config "
                "reference this action family) -- it is never written into a capsule's own action_type "
                "field, which stays 'decide' for gate decisions. Pick a name that doesn't collide, e.g. "
                "'payment.dispatch'.",
            )
        if action_type in seen_types:
            raise PackDefinitionError(DUPLICATE_ACTION_TYPE, f"action_type {action_type!r} declared more than once")
        seen_types.add(action_type)

        action_class = _require_nonempty_str(
            entry.get("action_class"), f"action_semantics[{action_type!r}].action_class", "money.transfer"
        )
        if action_class not in TAXONOMY:
            raise PackDefinitionError(
                UNKNOWN_ACTION_CLASS,
                f"action_semantics[{action_type!r}].action_class={action_class!r} is not in the guard's "
                f"starter taxonomy (guards/classes.py): {sorted(TAXONOMY)}. A pack governs an EXISTING "
                "action class -- it does not invent new ones (that is a core-repo taxonomy change).",
            )

        required = _parse_field_list(entry.get("required_fields"), f"action_semantics[{action_type!r}].required_fields")
        optional = _parse_field_list(
            entry.get("optional_fields", []), f"action_semantics[{action_type!r}].optional_fields", allow_empty=True
        )

        aliases_raw = entry.get("field_aliases") or {}
        if not isinstance(aliases_raw, dict):
            raise PackDefinitionError(
                INVALID_ACTION_SEMANTIC, f"action_semantics[{action_type!r}].field_aliases must be a mapping"
            )
        known = set(required) | set(optional)
        for field_name, alias in aliases_raw.items():
            if field_name not in known:
                raise PackDefinitionError(
                    INVALID_ACTION_SEMANTIC,
                    f"action_semantics[{action_type!r}].field_aliases has an entry for {field_name!r}, which "
                    f"is not in this action type's required_fields or optional_fields ({sorted(known)})",
                )
            if not isinstance(alias, str) or not alias:
                raise PackDefinitionError(
                    INVALID_ACTION_SEMANTIC,
                    f"action_semantics[{action_type!r}].field_aliases[{field_name!r}] must be a non-empty string",
                )

        out.append(
            ActionSemantic(
                action_type=action_type,
                action_class=action_class,
                required_fields=required,
                optional_fields=optional,
                field_aliases=dict(aliases_raw),
            )
        )
    return tuple(out)


def _parse_field_list(raw: Any, context: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if raw is None or raw == []:
        if allow_empty:
            return ()
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            f"{context} is required and must be a non-empty list drawn from the normalized field basis "
            f"{sorted(NORMALIZED_ACTION_FIELDS)}, e.g. [amount_minor, currency, target]",
        )
    if not isinstance(raw, list):
        raise PackDefinitionError(INVALID_ACTION_SEMANTIC, f"{context} must be a list of field names")
    out: list[str] = []
    for name in raw:
        if not isinstance(name, str) or name not in NORMALIZED_ACTION_FIELDS:
            raise PackDefinitionError(
                UNKNOWN_NORMALIZED_FIELD,
                f"{context} names {name!r}, which is not in the normalized field basis a pack may bind to: "
                f"{sorted(NORMALIZED_ACTION_FIELDS)}. Packs bind to normalized capsule fields only, never "
                "framework objects -- if this action genuinely needs a new field, that is a normalization-"
                "contract change (fix the contract once), not a pack workaround.",
            )
        out.append(name)
    return tuple(out)


def _parse_scope(raw: Any, *, wicket_id: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise PackDefinitionError(
            MISSING_CONSTRAINT_SCOPE,
            f"constraints[{wicket_id!r}].scope is required for a 'caps' constraint and must be a "
            f"non-empty list drawn from {sorted(KNOWN_SCOPE_DIMENSIONS)} -- it declares which "
            "dimensions this cap is actually enforced per, e.g. scope: [developer]. This closes the "
            "class of bug where a cap is declared per-class but the fold it cites pools amounts "
            "across all classes (capsule-emit PR #54: lock/cap/aggregate scope disagreement let a "
            "cross-class race jointly admit what sequential execution would deny).",
        )
    seen: set[str] = set()
    dims: list[str] = []
    for dim in raw:
        if not isinstance(dim, str) or dim not in KNOWN_SCOPE_DIMENSIONS:
            raise PackDefinitionError(
                INVALID_SCOPE_DIMENSION,
                f"constraints[{wicket_id!r}].scope names {dim!r}, which is not in the closed set "
                f"{sorted(KNOWN_SCOPE_DIMENSIONS)}",
            )
        if dim in seen:
            raise PackDefinitionError(
                INVALID_SCOPE_DIMENSION, f"constraints[{wicket_id!r}].scope names {dim!r} more than once"
            )
        seen.add(dim)
        dims.append(dim)
    return tuple(dims)


def _parse_constraints(raw: Any) -> tuple[tuple[WicketDefinition, ...], dict[str, tuple[str, ...]]]:
    if not raw:
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'constraints' is required (a pack ships at least one) -- each entry is a wicket definition "
            "('wicket_id', 'check', 'config') plus, for 'caps', a declared 'scope', e.g.:\n"
            "constraints:\n"
            "  - wicket_id: payments_safety.caps/1.0.0\n"
            "    check: caps\n"
            "    scope: [developer]\n"
            "    config:\n"
            "      fold_id: payments_safety.spend.weekly/1.0.0\n"
            "      caps_minor:\n"
            "        money.transfer: 10000000",
        )
    if not isinstance(raw, list):
        raise PackDefinitionError(MALFORMED_PACK, "'constraints' must be a list")

    out: list[WicketDefinition] = []
    scopes: dict[str, tuple[str, ...]] = {}
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        # scope lives as a sibling key next to wicket_id/check/config -- the
        # core wicket parser below ignores unknown keys, so this is read
        # independently rather than smuggled through WicketDefinition.config.
        raw_scope = entry.get("scope") if isinstance(entry, dict) else None
        try:
            definition = parse_wicket_definition(entry)
        except WicketDefinitionError as exc:
            raise PackDefinitionError(INVALID_CONSTRAINT, f"constraints[{idx}]: {exc}") from exc
        if definition.wicket_id in seen_ids:
            raise PackDefinitionError(
                DUPLICATE_CONSTRAINT_WICKET_ID, f"wicket_id {definition.wicket_id!r} declared more than once"
            )
        seen_ids.add(definition.wicket_id)
        out.append(definition)
        if definition.check == "caps" or raw_scope is not None:
            scopes[definition.wicket_id] = _parse_scope(raw_scope, wicket_id=definition.wicket_id)
    return tuple(out), scopes


def _validate_caps_scope_against_folds(
    constraints: tuple[WicketDefinition, ...], scopes: dict[str, tuple[str, ...]], folds: tuple[FoldDefinition, ...]
) -> None:
    """Cross-checks a 'caps' constraint's declared scope against the fold it
    actually cites -- the generalized capsule-emit PR #54 check (see
    schema.py's module docstring for the incident this closes)."""
    folds_by_id = {f.fold_id: f for f in folds}
    for wicket in constraints:
        if wicket.check != "caps":
            continue
        scope = scopes[wicket.wicket_id]  # guaranteed present -- required for 'caps' in _parse_scope
        caps_minor = wicket.config.get("caps_minor") or {}
        fold_id = wicket.config.get("fold_id")
        fold = folds_by_id.get(fold_id)

        if "developer" in scope and fold is not None and fold.key != "developer":
            raise PackDefinitionError(
                SCOPE_MISMATCH,
                f"constraints[{wicket.wicket_id!r}] declares scope including 'developer', but its fold "
                f"{fold.fold_id!r} is keyed by {fold.key!r}, not 'developer' -- the declared scope and "
                "the fold's actual aggregation key disagree.",
            )

        multi_class = len(caps_minor) > 1
        if multi_class and "action_class" not in scope:
            raise PackDefinitionError(
                SCOPE_MISMATCH,
                f"constraints[{wicket.wicket_id!r}] configures caps_minor for {len(caps_minor)} action "
                f"classes ({sorted(caps_minor)}) but its declared scope {list(scope)} does not include "
                "'action_class' -- a cap declared per-class must say so, or the fold pooling amounts "
                "across those classes is silently over-broad (capsule-emit PR #54's exact bug shape: "
                "cap says per-class, aggregate says pooled).",
            )
        if "action_class" in scope and multi_class and fold is not None:
            partitions_by_class = fold.key == "action_class" or any(
                f.field.endswith("action_class") for f in fold.filter
            )
            if not partitions_by_class:
                raise PackDefinitionError(
                    SCOPE_MISMATCH,
                    f"constraints[{wicket.wicket_id!r}] declares scope including 'action_class' and "
                    f"configures {len(caps_minor)} per-class caps, but its fold {fold.fold_id!r} pools "
                    f"amounts across ALL action classes (key={fold.key!r}, no action_class filter) -- "
                    "the cap is declared per-class but the aggregate isn't. Add an action_class-scoped "
                    "key or filter to the fold, or this pack will admit combined spend across classes "
                    "that no single class's cap alone would allow.",
                )


def _parse_folds(raw: Any, *, pack_dir: Path) -> tuple[FoldDefinition, ...]:
    if not raw:
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'folds' is required (a pack's numbers must be computable on day one) -- each entry references "
            "a fold definition file relative to the pack directory, e.g.:\n"
            "folds:\n"
            "  - file: folds/spend_weekly.yaml",
        )
    if not isinstance(raw, list):
        raise PackDefinitionError(MALFORMED_PACK, "'folds' must be a list")

    out: list[FoldDefinition] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"folds[{idx}]")
        rel_path = _require_nonempty_str(entry.get("file"), f"folds[{idx}].file", "folds/spend_weekly.yaml")
        fold_path = pack_dir / rel_path
        if not fold_path.is_file():
            raise PackDefinitionError(
                FOLD_FILE_NOT_FOUND, f"folds[{idx}].file={rel_path!r} does not exist under {pack_dir}"
            )
        try:
            definition = load_fold_definition_file(fold_path)
        except FoldDefinitionError as exc:
            raise PackDefinitionError(INVALID_FOLD_REF, f"folds[{idx}] ({rel_path}): {exc}") from exc
        if definition.fold_id in seen_ids:
            raise PackDefinitionError(
                INVALID_FOLD_REF, f"fold_id {definition.fold_id!r} (from {rel_path}) already declared by another entry"
            )
        seen_ids.add(definition.fold_id)
        out.append(definition)
    return tuple(out)


def _parse_proposers(raw: Any) -> tuple[ProposerStub, ...]:
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise PackDefinitionError(MALFORMED_PACK, "'proposers' must be a list")
    out: list[ProposerStub] = []
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"proposers[{idx}]")
        proposer_id = _require_nonempty_str(entry.get("id"), f"proposers[{idx}].id", "weekly-cap-proposer")
        fold_id = _require_nonempty_str(entry.get("fold_id"), f"proposers[{proposer_id!r}].fold_id", "payments_safety.spend.weekly/1.0.0")
        strategy = _require_nonempty_str(entry.get("strategy"), f"proposers[{proposer_id!r}].strategy", "percentile")
        status = entry.get("status", "planned")
        if status not in _PROPOSER_STATUSES:
            raise PackDefinitionError(
                MALFORMED_PACK,
                f"proposers[{proposer_id!r}].status={status!r} must be one of {sorted(_PROPOSER_STATUSES)} -- "
                "threshold proposers are declared in P1 but not runnable until 'capsule thresholds propose' "
                "(P2) exists; 'status: active' is not yet a true claim",
            )
        out.append(ProposerStub(id=proposer_id, fold_id=fold_id, strategy=strategy, status=status))
    return tuple(out)


def _parse_fixtures(raw: Any) -> PackFixtures | None:
    if raw is None:
        return None
    raw = _require_mapping(raw, "fixtures")
    ledger = raw.get("ledger")
    if ledger is not None and not isinstance(ledger, str):
        raise PackDefinitionError(INVALID_FIXTURES, "fixtures.ledger must be a string path")
    scenarios_raw = raw.get("scenarios") or []
    if not isinstance(scenarios_raw, list):
        raise PackDefinitionError(INVALID_FIXTURES, "fixtures.scenarios must be a list")
    scenarios: list[FixtureScenario] = []
    for idx, entry in enumerate(scenarios_raw):
        entry = _require_mapping(entry, f"fixtures.scenarios[{idx}]")
        scenario_id = _require_nonempty_str(entry.get("id"), f"fixtures.scenarios[{idx}].id", "caps-escalate")
        outcome = entry.get("outcome")
        if outcome not in _FIXTURE_OUTCOMES:
            raise PackDefinitionError(
                INVALID_FIXTURES,
                f"fixtures.scenarios[{scenario_id!r}].outcome={outcome!r} must be one of {sorted(_FIXTURE_OUTCOMES)}",
            )
        scenarios.append(FixtureScenario(id=scenario_id, outcome=outcome))
    return PackFixtures(ledger=ledger, scenarios=tuple(scenarios))


def load_pack_dir(pack_dir: str | Path) -> PackDefinition:
    """Load and fully validate a pack directory's ``pack.yaml`` (plus every
    fold file and inline constraint it references) into a ``PackDefinition``."""
    pack_dir = Path(pack_dir)
    pack_yaml_path = pack_dir / "pack.yaml"
    if not pack_yaml_path.is_file():
        raise PackDefinitionError(PACK_NOT_FOUND, f"no pack.yaml found in {pack_dir} -- every pack directory must have one")

    try:
        data = yaml.safe_load(pack_yaml_path.read_text())
    except yaml.YAMLError as exc:
        raise PackDefinitionError(MALFORMED_PACK, f"{pack_yaml_path} is not valid YAML: {exc}") from exc
    data = _require_mapping(data, str(pack_yaml_path))

    pack_id = _require_nonempty_str(data.get("pack_id"), "pack_id", "asg/payments-safety/1.0.0")
    if not PACK_ID_RE.match(pack_id):
        raise PackDefinitionError(
            INVALID_PACK_ID,
            f"pack_id {pack_id!r} must match '<publisher>/<kebab-name>/<major>.<minor>.<patch>' "
            "(e.g. 'asg/payments-safety/1.0.0') -- the publisher segment is a registered namespace "
            "prefix (registry-architecture ruling, 2026-08-10), not a display name",
        )

    constraints, constraint_scopes = _parse_constraints(data.get("constraints"))
    declared_checks = {c.check for c in constraints}
    obligations = _parse_obligations(data.get("obligations"), declared_checks=declared_checks)
    action_semantics = _parse_action_semantics(data.get("action_semantics"))
    folds = _parse_folds(data.get("folds"), pack_dir=pack_dir)
    _validate_caps_scope_against_folds(constraints, constraint_scopes, folds)
    proposers = _parse_proposers(data.get("proposers"))
    fixtures = _parse_fixtures(data.get("fixtures"))

    holds_integration = data.get("holds_integration", "none")
    if holds_integration not in HOLDS_INTEGRATION_VALUES:
        raise PackDefinitionError(
            INVALID_HOLDS_INTEGRATION,
            f"holds_integration={holds_integration!r} must be one of {sorted(HOLDS_INTEGRATION_VALUES)}",
        )

    bootstrap = data.get("bootstrap")
    bootstrap_path: str | None = None
    if bootstrap is not None:
        bootstrap = _require_nonempty_str(bootstrap, "bootstrap", "AI-BOOTSTRAP.md")
        if not (pack_dir / bootstrap).is_file():
            raise PackDefinitionError(MALFORMED_PACK, f"bootstrap={bootstrap!r} does not exist under {pack_dir}")
        bootstrap_path = bootstrap

    return PackDefinition(
        pack_id=pack_id,
        obligations=obligations,
        action_semantics=action_semantics,
        constraints=constraints,
        folds=folds,
        proposers=proposers,
        holds_integration=holds_integration,
        fixtures=fixtures,
        bootstrap_path=bootstrap_path,
        source_dir=pack_dir,
        constraint_scopes=constraint_scopes,
    )
