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

**Backward-only packs**: a pack that
declares at least one ``outcomes[]`` entry is not required to also declare
``obligations``/``action_semantics``/``constraints`` -- a GRC obligations
pack with no forward guard integration at all (e.g. ``catalog/eu-ai-act``)
makes its claims entirely through outcomes, and requiring an unused forward
triple just to satisfy this loader would be a fake declaration, not a real
one. A pack declaring NEITHER outcomes nor the forward triple is still
rejected -- see ``load_pack_dir``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ..compiler.effect_model import EFFECT_CLAIMS, UnknownEffectClaim, compile_effect_claim
from ..compiler.vocabulary import (
    BACKWARD_VERDICTS,
    FORWARD_VERDICTS,
    RE_DERIVABILITY_GRADES,
    REFUSAL_REASON_CODES,
)
from ..folds.catalog import Catalog as FoldCatalog
from ..folds.definition import FoldDefinition
from ..folds.errors import FoldDefinitionError
from ..folds.loader import load_definition_file as load_fold_definition_file
from ..guards.classes import (
    TAXONOMY,
    TAXONOMY_DIGEST,
    TAXONOMY_VERSION,
    is_known_action_class,
    legacy_aliases,
)
from ..guards.wickets.catalog import Catalog as WicketCatalog
from ..guards.wickets.definition import WicketDefinition
from ..guards.wickets.definition import parse_definition as parse_wicket_definition
from ..guards.wickets.errors import WicketDefinitionError
from ..guards.wickets.retired import RetiredDefinition, retired_entry
from .errors import (
    CATALOG_REF_DIGEST_MISMATCH,
    DUPLICATE_ACTION_TYPE,
    DUPLICATE_CONSTRAINT_WICKET_ID,
    DUPLICATE_OBLIGATION_ID,
    DUPLICATE_OUTCOME_ID,
    DUPLICATE_PROFILE_ID,
    DUPLICATE_PROFILE_OVERRIDE_OUTCOME_ID,
    EFFECT_CLAIM_NOT_REFUSED,
    FOLD_FILE_NOT_FOUND,
    INVALID_ACTION_SEMANTIC,
    INVALID_CLAUSE,
    INVALID_CONSTRAINT,
    INVALID_COUNTERPARTY_BINDING,
    INVALID_DEFAULT_DISPOSITION,
    INVALID_EPISTEMIC_TYPE,
    INVALID_EVIDENCE_INSTRUMENT,
    INVALID_EVIDENCE_PROFILE,
    INVALID_FIXTURES,
    INVALID_FOLD_REF,
    INVALID_HOLDS_INTEGRATION,
    INVALID_JUDGE_PIN,
    INVALID_MEASURABILITY,
    INVALID_MODE,
    INVALID_OBLIGATION_SELECTOR,
    INVALID_OUTCOME,
    INVALID_PACK_ID,
    INVALID_PROFILE_ID,
    INVALID_RE_DERIVABILITY_GRADE,
    INVALID_SCOPE_CENSUS,
    INVALID_SCOPE_DIMENSION,
    INVALID_TIER,
    INVALID_VERDICT,
    JUDGED_DISPOSITION_NEVER,
    MALFORMED_PACK,
    MISSING_CONSTRAINT_SCOPE,
    MISSING_EVIDENCE_INSTRUMENT,
    MISSING_EVIDENCE_RULE,
    MISSING_JUDGE_PIN,
    MISSING_OBLIGATION_CLAUSE,
    MISSING_OBLIGATION_SELECTOR,
    MISSING_REFUSAL_REASON,
    MISSING_REQUIRED_FIELD,
    OBLIGATION_CHECK_NOT_DECLARED,
    PACK_NOT_FOUND,
    PROMPT_TEXT_IN_PACK,
    RETIRED_CATALOG_REF,
    SCOPE_MISMATCH,
    TAXONOMY_PIN_MISMATCH,
    TOPOLOGY_INVARIANT_OVERRIDE,
    UNKNOWN_ACTION_CLASS,
    UNKNOWN_CATALOG_REF,
    UNKNOWN_EFFECT_CLAIM,
    UNKNOWN_NORMALIZED_FIELD,
    UNKNOWN_OBLIGATION_SELECTOR,
    UNKNOWN_OUTCOME_IN_PROFILE_OVERRIDE,
    PackDefinitionError,
)
from .schema import (
    DEFAULT_DISPOSITION_VALUES,
    EPISTEMIC_TYPE_VALUES,
    EVIDENCE_INSTRUMENT_KINDS,
    EVIDENCE_PROFILE_VALUES,
    HOLDS_INTEGRATION_VALUES,
    JUDGE_PIN_HOSTING_VALUES,
    KNOWN_SCOPE_DIMENSIONS,
    MEASURABILITY_VALUES,
    MODE_VALUES,
    NORMALIZED_ACTION_FIELDS,
    PACK_ID_RE,
    PROFILE_ID_VALUES,
    TIER_VALUES,
    TOPOLOGY_INVARIANT_MODES,
    ActionSemantic,
    ClauseSpec,
    CounterpartyBinding,
    EvidenceContract,
    EvidenceInstrument,
    FixtureScenario,
    JudgePin,
    Obligation,
    OutcomeOverride,
    PackDefinition,
    PackFixtures,
    ProposerStub,
    ScopeCensus,
    TaxonomyPin,
    TopologyProfile,
    WindowSpec,
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

# clause.effective_from / clause.text_snapshot_digest -- ISO-8601 calendar
# date and SHA-256 hex respectively. Both are compared/looked up as plain
# strings downstream (compiler.terms_desk.compute_binding_status does a
# lexicographic YYYY-MM-DD comparison against a report period), so a
# malformed value here would silently miscompare rather than raise -- these
# are validated at load time for the same reason every other closed shape
# in this module is.
# The engine's own built-in catalogs. A pack may cite a definition from one
# of these by id and digest (``wicket_ref``/``fold_ref`` + ``digest``)
# instead of copying it: the loader resolves the id, refuses a digest that
# does not match the catalog's current definition, and the pack's canonical
# form is identical to what an inline copy of the same definition produces.
CORE_WICKET_CATALOG_DIR = Path(__file__).resolve().parent.parent / "guards" / "wickets" / "catalog_defs"
CORE_FOLD_CATALOG_DIR = Path(__file__).resolve().parent.parent / "folds" / "catalog_defs"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_JUDGE_PIN_KEYS = frozenset({"model_id", "prompt_template_hash", "schema_hash", "input_refs", "model_hosting"})


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


def _parse_obligations(
    raw: Any,
    *,
    declared_checks: set[str],
    check_selectors: dict[str, frozenset[str]],
    allow_empty: bool = False,
) -> tuple[Obligation, ...]:
    if raw is None:
        if allow_empty:
            return ()  # see _parse_constraints' allow_empty docstring note
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'obligations' is required (a pack ships at least one, unless it declares 'outcomes' instead) -- "
            "each entry needs 'id', 'statement', and 'check', e.g.:\n"
            "obligations:\n"
            "  - id: caps-per-window\n"
            "    statement: \"No payment may exceed the configured weekly cap without escalation.\"\n"
            "    check: caps",
        )
    if not isinstance(raw, list) or not raw:
        if allow_empty and isinstance(raw, list):
            return ()
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
        _refuse_prompt_text(entry, what=f"obligations[{obligation_id!r}]")
        measurability = entry.get("measurability", "measured")
        if measurability not in MEASURABILITY_VALUES:
            raise PackDefinitionError(
                INVALID_MEASURABILITY,
                f"obligations[{obligation_id!r}].measurability={measurability!r} must be one of "
                f"{sorted(MEASURABILITY_VALUES)}, or omitted (defaults to 'measured')",
            )
        mode = entry.get("mode", "structural")
        if mode not in MODE_VALUES:
            raise PackDefinitionError(
                INVALID_MODE,
                f"obligations[{obligation_id!r}].mode={mode!r} must be one of {sorted(MODE_VALUES)}, or omitted "
                "(defaults to 'structural')",
            )
        if "selector" in entry and (mode == "judged" or measurability == "declared_not_measured"):
            raise PackDefinitionError(
                INVALID_OBLIGATION_SELECTOR,
                f"obligations[{obligation_id!r}] names a selector but cites no check; a selector belongs "
                "only to an obligation measured by a check that has selectors",
            )
        if mode == "judged":
            obligations.append(_judged_obligation(entry, obligation_id=obligation_id, statement=statement))
            continue
        if "judge_pin" in entry:
            raise PackDefinitionError(
                INVALID_JUDGE_PIN,
                f"obligations[{obligation_id!r}] carries a judge_pin but mode={mode!r}; a judge_pin belongs "
                "only to a mode: judged obligation",
            )
        if measurability == "declared_not_measured":
            obligations.append(
                _declared_not_measured_obligation(
                    obligation_id=obligation_id,
                    statement=statement,
                    check=entry.get("check"),
                    instrument=entry.get("evidence_instrument"),
                    grades=_obligation_grades(
                        entry.get("re_derivability_grade"), entry.get("default_disposition"), obligation_id=obligation_id
                    ),
                    mode=mode,
                )
            )
            continue
        if "evidence_instrument" in entry:
            raise PackDefinitionError(
                INVALID_MEASURABILITY,
                f"obligations[{obligation_id!r}] is measured by its check and also names an "
                "evidence_instrument; an instrument belongs only to a declared_not_measured obligation",
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
        selector = _obligation_selector(
            entry.get("selector"), check=check, selectors=check_selectors.get(check), obligation_id=obligation_id
        )
        re_derivability_grade, default_disposition = _obligation_grades(
            entry.get("re_derivability_grade"), entry.get("default_disposition"), obligation_id=obligation_id
        )
        obligations.append(
            Obligation(
                id=obligation_id,
                statement=statement,
                check=check,
                selector=selector,
                re_derivability_grade=re_derivability_grade,
                default_disposition=default_disposition,
                mode=mode,
            )
        )
    return tuple(obligations)


# Reads one obligation's raw YAML value: the loader's decoding boundary.
def _obligation_selector(
    raw: object, *, check: str, selectors: frozenset[str] | None, obligation_id: str
) -> str | None:
    """The selector an obligation is measured by, or ``None`` for a check without selectors.

    A check with selectors produces one result for the whole action, so an
    obligation citing it with no selector would fail whenever any selector
    fails -- the reason it is required here.
    """
    what = f"obligations[{obligation_id!r}]"
    if selectors is None:
        if raw is not None:
            raise PackDefinitionError(
                INVALID_OBLIGATION_SELECTOR,
                f"{what}.selector={raw!r}, but check {check!r} has no selectors; drop the selector",
            )
        return None
    if raw is None:
        raise PackDefinitionError(
            MISSING_OBLIGATION_SELECTOR,
            f"{what} cites check {check!r}, which has selectors {sorted(selectors)}; name the one this "
            f"obligation is measured by, e.g. selector: {sorted(selectors)[0]!r}",
        )
    if not isinstance(raw, str) or raw not in selectors:
        raise PackDefinitionError(
            UNKNOWN_OBLIGATION_SELECTOR,
            f"{what}.selector={raw!r} is not a selector of check {check!r} (selectors: {sorted(selectors)})",
        )
    return raw


# Reads one obligation's raw YAML mapping: the loader's decoding boundary.
def _refuse_prompt_text(entry: dict, *, what: str) -> None:
    """Refuse any prompt-text key on an obligation or its judge_pin.

    An interpolated prompt carries the action's content, which can be the
    user's identity or a secret, and neither may enter a capsule in clear or
    as a digest. A pack names the template by hash and the fields it reads;
    the only prompt-named key it may carry is ``judge_pin.prompt_template_hash``."""
    pin = entry.get("judge_pin")
    keys = [(what, key) for key in entry]
    if isinstance(pin, dict):
        keys += [(f"{what}.judge_pin", key) for key in pin if key != "prompt_template_hash"]
    for where, key in keys:
        if isinstance(key, str) and "prompt" in key.lower():
            raise PackDefinitionError(
                PROMPT_TEXT_IN_PACK,
                f"{where} carries {key!r}; prompt text never enters a pack. Name the template by "
                "judge_pin.prompt_template_hash and the fields it reads by judge_pin.input_refs",
            )


# Reads one obligation's raw YAML mapping: the loader's decoding boundary.
def _judged_obligation(entry: dict, *, obligation_id: str, statement: str) -> Obligation:
    """A ``mode: judged`` obligation: measured by the judge its pin names, never by a check."""
    what = f"obligations[{obligation_id!r}]"
    if entry.get("measurability", "measured") != "measured" or "evidence_instrument" in entry:
        raise PackDefinitionError(
            INVALID_MEASURABILITY,
            f"{what} is mode: judged, so the judge its judge_pin names measures it; it cannot also be "
            "declared_not_measured or name an evidence_instrument",
        )
    if "check" in entry:
        raise PackDefinitionError(
            INVALID_JUDGE_PIN,
            f"{what} is mode: judged and also cites check={entry['check']!r}; a judged obligation is "
            "measured by its judge_pin, not by a check -- drop one of the two",
        )
    if "judge_pin" not in entry:
        raise PackDefinitionError(
            MISSING_JUDGE_PIN,
            f"{what} is mode: judged but carries no judge_pin -- name the judge that answers it, e.g.:\n"
            "judge_pin:\n"
            "  model_id: <model>\n"
            "  prompt_template_hash: <sha256 hex of the prompt template>\n"
            "  schema_hash: <sha256 hex of the answer schema>\n"
            "  input_refs: [outgoing_content]\n"
            "  model_hosting: hosted",
        )
    pin = _parse_judge_pin(entry["judge_pin"], what=what)
    re_derivability_grade, default_disposition = _obligation_grades(
        entry.get("re_derivability_grade"), entry.get("default_disposition"), obligation_id=obligation_id
    )
    if default_disposition == "NEVER":
        raise PackDefinitionError(
            JUDGED_DISPOSITION_NEVER,
            f"{what} is mode: judged with default_disposition: NEVER; a judged verdict cannot be "
            "re-derived by the person it stops, so a judged obligation may ASK but never NEVER",
        )
    if pin.model_hosting == "hosted" and re_derivability_grade == "pure_replay":
        raise PackDefinitionError(
            INVALID_RE_DERIVABILITY_GRADE,
            f"{what} is judged by a hosted model and declares re_derivability_grade: pure_replay; a "
            "hosted model's verdict is attributable, not re-derivable",
        )
    return Obligation(
        id=obligation_id,
        statement=statement,
        re_derivability_grade=re_derivability_grade,
        default_disposition=default_disposition,
        mode="judged",
        judge_pin=pin,
    )


def _parse_judge_pin(raw: Any, *, what: str) -> JudgePin:
    raw = _require_mapping(raw, f"{what}.judge_pin")
    unknown = sorted(str(key) for key in raw if key not in _JUDGE_PIN_KEYS)
    if unknown:
        raise PackDefinitionError(
            INVALID_JUDGE_PIN,
            f"{what}.judge_pin carries unknown keys {unknown}; the pin is exactly {sorted(_JUDGE_PIN_KEYS)}",
        )
    model_id = raw.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise PackDefinitionError(INVALID_JUDGE_PIN, f"{what}.judge_pin.model_id must be a non-empty string")
    for key in ("prompt_template_hash", "schema_hash"):
        value = raw.get(key)
        if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
            raise PackDefinitionError(
                INVALID_JUDGE_PIN, f"{what}.judge_pin.{key} must be 64 lowercase hex characters (SHA-256)"
            )
    input_refs = raw.get("input_refs")
    if not isinstance(input_refs, list) or not input_refs:
        raise PackDefinitionError(
            INVALID_JUDGE_PIN, f"{what}.judge_pin.input_refs must be a non-empty list of normalized action fields"
        )
    for ref in input_refs:
        if not isinstance(ref, str) or ref not in NORMALIZED_ACTION_FIELDS:
            raise PackDefinitionError(
                INVALID_JUDGE_PIN,
                f"{what}.judge_pin.input_refs entry {ref!r} must be one of {sorted(NORMALIZED_ACTION_FIELDS)}",
            )
    if len(set(input_refs)) != len(input_refs):
        raise PackDefinitionError(INVALID_JUDGE_PIN, f"{what}.judge_pin.input_refs lists a field more than once")
    model_hosting = raw.get("model_hosting")
    if model_hosting not in JUDGE_PIN_HOSTING_VALUES:
        raise PackDefinitionError(
            INVALID_JUDGE_PIN,
            f"{what}.judge_pin.model_hosting={model_hosting!r} must be one of {sorted(JUDGE_PIN_HOSTING_VALUES)}",
        )
    return JudgePin(
        model_id=model_id,
        prompt_template_hash=raw["prompt_template_hash"],
        schema_hash=raw["schema_hash"],
        input_refs=tuple(input_refs),
        model_hosting=model_hosting,
    )


def _obligation_grades(
    re_derivability_grade: object, default_disposition: object, *, obligation_id: str
) -> tuple[str | None, str | None]:
    """The optional ``re_derivability_grade`` and ``default_disposition``, each checked against its closed set."""
    if re_derivability_grade is not None and re_derivability_grade not in RE_DERIVABILITY_GRADES:
        raise PackDefinitionError(
            INVALID_RE_DERIVABILITY_GRADE,
            f"obligations[{obligation_id!r}].re_derivability_grade={re_derivability_grade!r} must be one of "
            f"{sorted(RE_DERIVABILITY_GRADES)}, or omitted",
        )
    if default_disposition is not None and default_disposition not in DEFAULT_DISPOSITION_VALUES:
        raise PackDefinitionError(
            INVALID_DEFAULT_DISPOSITION,
            f"obligations[{obligation_id!r}].default_disposition={default_disposition!r} must be one of "
            f"{sorted(DEFAULT_DISPOSITION_VALUES)}, or omitted",
        )
    return re_derivability_grade, default_disposition


def _declared_not_measured_obligation(
    *,
    obligation_id: str,
    statement: str,
    check: object,
    instrument: object,
    grades: tuple[str | None, str | None],
    mode: str,
) -> Obligation:
    """An obligation no check measures: it cites none and names the evidence instrument its input would arrive in."""
    what = f"obligations[{obligation_id!r}]"
    if check is not None:
        raise PackDefinitionError(
            INVALID_MEASURABILITY,
            f"{what} declares measurability=declared_not_measured and also cites check={check!r}; "
            "a rule a check measures is measured -- drop one of the two",
        )
    if instrument is None:
        raise PackDefinitionError(
            MISSING_EVIDENCE_INSTRUMENT,
            f"{what} declares measurability=declared_not_measured but no evidence_instrument -- name the "
            "signal its missing input would arrive in, so corpus_verify.py can check the claim, e.g.:\n"
            "evidence_instrument:\n"
            "  kind: structured_field\n"
            "  field: task_authority_ref",
        )
    re_derivability_grade, default_disposition = grades
    return Obligation(
        id=obligation_id,
        statement=statement,
        re_derivability_grade=re_derivability_grade,
        default_disposition=default_disposition,
        measurability="declared_not_measured",
        evidence_instrument=_parse_evidence_instrument(instrument, what=what),
        mode=mode,
    )


def _parse_action_semantics(raw: Any, *, allow_empty: bool = False) -> tuple[ActionSemantic, ...]:
    if not raw:
        if allow_empty:
            return ()  # see _parse_constraints' allow_empty docstring note
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'action_semantics' is required (a pack ships at least one action type, unless it declares "
            "'outcomes' instead) -- each entry needs 'action_type', 'action_class', and 'required_fields', "
            "e.g.:\n"
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
        if not is_known_action_class(action_class):
            raise PackDefinitionError(
                UNKNOWN_ACTION_CLASS,
                f"action_semantics[{action_type!r}].action_class={action_class!r} is not in the guard's "
                f"taxonomy (guards/action_taxonomy.json): {sorted(TAXONOMY)} (legacy names also accepted: "
                f"{sorted(legacy_aliases())}). A pack governs an EXISTING "
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


# `entry` is one raw pack.yaml mapping, decoded here at the loader boundary.
def _resolve_catalog_ref(
    entry: dict,
    *,
    ref_key: str,
    catalog,
    what: str,
    retired: Callable[[str, str], RetiredDefinition | None] | None = None,
):
    ref = _require_nonempty_str(entry.get(ref_key), f"{what}.{ref_key}", "caps/1.0.0")
    digest = _require_nonempty_str(entry.get("digest"), f"{what}.digest", "<64-char sha-256 hex>")
    # A retired pair is gone from the catalog; say so, rather than report an
    # unknown ref, so the pack author is told what replaced it.
    row = retired(ref, digest) if retired is not None else None
    if row is not None:
        raise PackDefinitionError(
            RETIRED_CATALOG_REF,
            f"{what} cites {ref!r} at digest {digest}, which is retired: {row.reason}; cite {row.replaced_by} instead",
        )
    found = catalog.get(ref)
    if found is None:
        raise PackDefinitionError(
            UNKNOWN_CATALOG_REF, f"{what}.{ref_key}={ref!r} is not in the built-in catalog {catalog.directory}"
        )
    if found.digest != digest:
        raise PackDefinitionError(
            CATALOG_REF_DIGEST_MISMATCH,
            f"{what} cites {ref!r} at digest {digest}, but the built-in definition digests to {found.digest} -- "
            "the cited definition has changed; re-pin the digest only after reviewing the change",
        )
    return found.definition


def _parse_constraints(
    raw: Any, *, allow_empty: bool = False
) -> tuple[tuple[WicketDefinition, ...], dict[str, tuple[str, ...]]]:
    if not raw:
        if allow_empty:
            # A pack declaring at least one outcome (backward-only, e.g. a GRC
            # obligations pack with no forward guard integration at all)
            # has somewhere else to make
            # its claims; the forward obligations/action_semantics/constraints
            # triple is then genuinely optional, not merely omitted.
            return (), {}
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'constraints' is required (a pack ships at least one, unless it declares 'outcomes' instead) -- "
            "each entry is a wicket definition ('wicket_id', 'check', 'config') plus, for 'caps', a declared "
            "'scope', e.g.:\n"
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
        if isinstance(entry, dict) and "wicket_ref" in entry:
            definition = _resolve_catalog_ref(
                entry,
                ref_key="wicket_ref",
                catalog=WicketCatalog(CORE_WICKET_CATALOG_DIR),
                what=f"constraints[{idx}]",
                retired=retired_entry,
            )
        else:
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

        for dim in ("developer", "operator"):
            if dim in scope and fold is not None and fold.key != dim:
                raise PackDefinitionError(
                    SCOPE_MISMATCH,
                    f"constraints[{wicket.wicket_id!r}] declares scope including {dim!r}, but its fold "
                    f"{fold.fold_id!r} is keyed by {fold.key!r}, not {dim!r} -- the declared scope and "
                    "the fold's actual aggregation key disagree.",
                )

        # One value across every class is one pooled limit over the pooled
        # total, which is what a fold with no class partition enforces.
        multi_class = len(set(caps_minor.values())) > 1
        if multi_class and "action_class" not in scope:
            raise PackDefinitionError(
                SCOPE_MISMATCH,
                f"constraints[{wicket.wicket_id!r}] configures different caps_minor limits for {len(caps_minor)} "
                f"action classes ({sorted(caps_minor)}) but its declared scope {list(scope)} does not include "
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
                    f"configures different limits for {len(caps_minor)} classes, but its fold {fold.fold_id!r} pools "
                    f"amounts across ALL action classes (key={fold.key!r}, no action_class filter) -- "
                    "the cap is declared per-class but the aggregate isn't. Add an action_class-scoped "
                    "key or filter to the fold, or this pack will admit combined spend across classes "
                    "that no single class's cap alone would allow.",
                )


def _parse_folds(raw: Any, *, pack_dir: Path, allow_empty: bool = False) -> tuple[FoldDefinition, ...]:
    if not raw:
        if allow_empty:
            return ()  # see _parse_constraints' allow_empty docstring note
        raise PackDefinitionError(
            MISSING_REQUIRED_FIELD,
            "'folds' is required (a pack's numbers must be computable on day one, unless it declares "
            "'outcomes' instead) -- each entry references a fold definition file relative to the pack "
            "directory, e.g.:\n"
            "folds:\n"
            "  - file: folds/spend_weekly.yaml",
        )
    if not isinstance(raw, list):
        raise PackDefinitionError(MALFORMED_PACK, "'folds' must be a list")

    out: list[FoldDefinition] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"folds[{idx}]")
        if "fold_ref" in entry:
            definition = _resolve_catalog_ref(
                entry, ref_key="fold_ref", catalog=FoldCatalog(CORE_FOLD_CATALOG_DIR), what=f"folds[{idx}]"
            )
            if definition.fold_id in seen_ids:
                raise PackDefinitionError(INVALID_FOLD_REF, f"fold_id {definition.fold_id!r} already declared")
            seen_ids.add(definition.fold_id)
            out.append(definition)
            continue
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


def _parse_window(raw: Any, *, what: str) -> WindowSpec | None:
    if raw is None:
        return None
    raw = _require_mapping(raw, what)
    duration = _require_nonempty_str(raw.get("duration"), f"{what}.duration", "P30D")
    cure = raw.get("cure")
    if cure is not None and not isinstance(cure, str):
        raise PackDefinitionError(INVALID_OUTCOME, f"{what}.cure must be a string duration or omitted")
    grace = raw.get("grace")
    if grace is not None and not isinstance(grace, str):
        raise PackDefinitionError(INVALID_OUTCOME, f"{what}.grace must be a string duration or omitted")
    return WindowSpec(duration=duration, cure=cure, grace=grace)


def _parse_evidence_instrument(raw: Any, *, what: str) -> EvidenceInstrument:
    """``what`` names the entry, e.g. ``outcomes['x']`` or ``obligations['y']``."""
    raw = _require_mapping(raw, f"{what}.evidence_instrument")
    kind = raw.get("kind")
    if kind not in EVIDENCE_INSTRUMENT_KINDS:
        raise PackDefinitionError(
            INVALID_EVIDENCE_INSTRUMENT,
            f"{what}.evidence_instrument.kind={kind!r} must be one of "
            f"{sorted(EVIDENCE_INSTRUMENT_KINDS)}",
        )
    if kind == "structured_field":
        field = raw.get("field")
        if not isinstance(field, str) or not field:
            raise PackDefinitionError(
                INVALID_EVIDENCE_INSTRUMENT,
                f"{what}.evidence_instrument.field is required and must be a non-empty "
                "string for kind: structured_field, e.g. field: restriction_reason_cited",
            )
        return EvidenceInstrument(kind=kind, field=field)
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise PackDefinitionError(
            INVALID_EVIDENCE_INSTRUMENT,
            f"{what}.evidence_instrument.name is required and must be a non-empty string "
            "for kind: tool_call_name, e.g. name: issue_refund",
        )
    return EvidenceInstrument(kind=kind, name=name)


def _parse_clause_spec(raw: Any, *, outcome_id: str) -> ClauseSpec:
    raw = _require_mapping(raw, f"outcomes[{outcome_id!r}].clause")
    instrument = _require_nonempty_str(
        raw.get("instrument"), f"outcomes[{outcome_id!r}].clause.instrument", "Regulation (EU) 2024/1689"
    )
    article = _require_nonempty_str(raw.get("article"), f"outcomes[{outcome_id!r}].clause.article", "Article 26")

    as_amended_by_raw = raw.get("as_amended_by", [])
    if not isinstance(as_amended_by_raw, list) or not all(isinstance(v, str) for v in as_amended_by_raw):
        raise PackDefinitionError(
            INVALID_CLAUSE,
            f"outcomes[{outcome_id!r}].clause.as_amended_by must be a list of strings or omitted, "
            "e.g. as_amended_by: [\"Regulation (EU) 2026/1744\"]",
        )

    for field_name, example in (("paragraph", "2"), ("jurisdiction", "EU"), ("source_url", "https://eur-lex.europa.eu/...")):
        value = raw.get(field_name)
        if value is not None and not isinstance(value, str):
            raise PackDefinitionError(
                INVALID_CLAUSE, f"outcomes[{outcome_id!r}].clause.{field_name} must be a string or omitted, e.g. {example!r}"
            )

    text_snapshot_digest = raw.get("text_snapshot_digest")
    if text_snapshot_digest is not None and not _SHA256_HEX_RE.match(text_snapshot_digest):
        raise PackDefinitionError(
            INVALID_CLAUSE,
            f"outcomes[{outcome_id!r}].clause.text_snapshot_digest must be a 64-char lowercase hex SHA-256 "
            "digest or omitted",
        )

    effective_from = raw.get("effective_from")
    if effective_from is not None and not _ISO_DATE_RE.match(effective_from):
        raise PackDefinitionError(
            INVALID_CLAUSE,
            f"outcomes[{outcome_id!r}].clause.effective_from must be an ISO-8601 date (YYYY-MM-DD) or omitted, "
            "e.g. effective_from: \"2026-08-02\"",
        )

    contested = raw.get("contested", False)
    if not isinstance(contested, bool):
        raise PackDefinitionError(
            INVALID_CLAUSE, f"outcomes[{outcome_id!r}].clause.contested must be a boolean or omitted"
        )

    return ClauseSpec(
        instrument=instrument,
        article=article,
        as_amended_by=tuple(as_amended_by_raw),
        paragraph=raw.get("paragraph"),
        jurisdiction=raw.get("jurisdiction"),
        text_snapshot_digest=text_snapshot_digest,
        effective_from=effective_from,
        source_url=raw.get("source_url"),
        contested=contested,
    )


def _parse_outcomes(raw: Any) -> tuple[EvidenceContract, ...]:
    """``outcomes[]``, the sister table to ``obligations[]`` (design of
    record 2026-08-19). Every entry needs a confirming-evidence rule and a
    verdict pair; an ``effect_claim`` of ``agent.caused_resolution`` MUST
    compile REFUSED -- this is where "REFUSED at compile time" becomes a
    load-time error rather than a convention someone could forget.

    Every entry here is an ``EvidenceContract`` -- the fields validated below
    are the **outcome profile's** field set (the 2026-09-21 Evidence-
    Contract reframe ruling), still required regardless of a declared
    ``profile``/``epistemic_type`` because the non-outcome profiles are typed
    stubs only (their own field-level validation is still to be specified
    -- see ``schema.EVIDENCE_PROFILE_VALUES``)."""
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise PackDefinitionError(MALFORMED_PACK, "'outcomes' must be a list")

    outcomes: list[EvidenceContract] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"outcomes[{idx}]")
        outcome_id = _require_nonempty_str(entry.get("id"), f"outcomes[{idx}].id", "outcome.remediation_confirmed")
        if outcome_id in seen_ids:
            raise PackDefinitionError(DUPLICATE_OUTCOME_ID, f"outcome id {outcome_id!r} declared more than once")
        seen_ids.add(outcome_id)
        statement = _require_nonempty_str(
            entry.get("statement"), f"outcomes[{outcome_id!r}].statement", "The remediation was confirmed."
        )
        evidence_rule = entry.get("evidence_rule")
        if not isinstance(evidence_rule, str) or not evidence_rule:
            raise PackDefinitionError(
                MISSING_EVIDENCE_RULE,
                f"outcomes[{outcome_id!r}].evidence_rule is required -- a declared outcome with no confirming-"
                "evidence rule is a schema error, e.g. evidence_rule: \"fulfill capsule chained to intent, "
                'effect_attestation=counterparty_confirmed"',
            )
        forward_verdict = entry.get("forward_verdict")
        if forward_verdict not in FORWARD_VERDICTS:
            raise PackDefinitionError(
                INVALID_VERDICT,
                f"outcomes[{outcome_id!r}].forward_verdict={forward_verdict!r} must be one of {sorted(FORWARD_VERDICTS)}",
            )
        backward_verdict = entry.get("backward_verdict")
        if backward_verdict not in BACKWARD_VERDICTS:
            raise PackDefinitionError(
                INVALID_VERDICT,
                f"outcomes[{outcome_id!r}].backward_verdict={backward_verdict!r} must be one of "
                f"{sorted(BACKWARD_VERDICTS)}",
            )
        window = _parse_window(entry.get("window"), what=f"outcomes[{outcome_id!r}].window")

        effect_claim = entry.get("effect_claim")
        refusal_reason_code = entry.get("refusal_reason_code")
        if effect_claim is not None:
            if effect_claim not in EFFECT_CLAIMS:
                raise PackDefinitionError(
                    UNKNOWN_EFFECT_CLAIM,
                    f"outcomes[{outcome_id!r}].effect_claim={effect_claim!r} must be one of {sorted(EFFECT_CLAIMS)} "
                    "-- the advisory effect model is a closed vocabulary (design §4b gap 1)",
                )
            try:
                compiled = compile_effect_claim(effect_claim)
            except UnknownEffectClaim as exc:  # pragma: no cover -- EFFECT_CLAIMS check above already excludes this
                raise PackDefinitionError(UNKNOWN_EFFECT_CLAIM, str(exc)) from exc
            if compiled.refusal_reason_code is not None:
                # agent.caused_resolution: the format is incoherent without this refusal (design §4b gap 1).
                # An outcome MAY declare it, but only compiled exactly as compile_effect_claim says --
                # never claiming provability for an undecomposable causal claim.
                if (forward_verdict, backward_verdict) != (compiled.verdict.forward, compiled.verdict.backward):
                    raise PackDefinitionError(
                        EFFECT_CLAIM_NOT_REFUSED,
                        f"outcomes[{outcome_id!r}] declares effect_claim={effect_claim!r}, which MUST compile to "
                        f"forward_verdict={compiled.verdict.forward!r}/backward_verdict={compiled.verdict.backward!r} "
                        f"(got forward_verdict={forward_verdict!r}/backward_verdict={backward_verdict!r}) -- a "
                        "record can show a recommendation was made and a person acted, never that the agent "
                        "caused the resolution; use recommendation.acted_on or resolution.followed_action for "
                        "the admissible near-miss instead",
                    )
                if refusal_reason_code is None:
                    refusal_reason_code = compiled.refusal_reason_code

        if "REFUSED" in (forward_verdict, backward_verdict):
            if refusal_reason_code is None:
                raise PackDefinitionError(
                    MISSING_REFUSAL_REASON,
                    f"outcomes[{outcome_id!r}] has forward_verdict={forward_verdict!r}/"
                    f"backward_verdict={backward_verdict!r} but no refusal_reason_code -- every refusal must name "
                    f"why, one of {sorted(REFUSAL_REASON_CODES)}",
                )
            if refusal_reason_code not in REFUSAL_REASON_CODES:
                raise PackDefinitionError(
                    MISSING_REFUSAL_REASON,
                    f"outcomes[{outcome_id!r}].refusal_reason_code={refusal_reason_code!r} must be one of "
                    f"{sorted(REFUSAL_REASON_CODES)}",
                )

        re_derivability_grade = entry.get("re_derivability_grade")
        if re_derivability_grade is not None and re_derivability_grade not in RE_DERIVABILITY_GRADES:
            raise PackDefinitionError(
                INVALID_RE_DERIVABILITY_GRADE,
                f"outcomes[{outcome_id!r}].re_derivability_grade={re_derivability_grade!r} must be one of "
                f"{sorted(RE_DERIVABILITY_GRADES)}, or omitted",
            )

        measurability = entry.get("measurability", "measured")
        if measurability not in MEASURABILITY_VALUES:
            raise PackDefinitionError(
                INVALID_MEASURABILITY,
                f"outcomes[{outcome_id!r}].measurability={measurability!r} must be one of "
                f"{sorted(MEASURABILITY_VALUES)}, or omitted (defaults to 'measured')",
            )
        evidence_instrument_raw = entry.get("evidence_instrument")
        if measurability == "declared_not_measured" and evidence_instrument_raw is None:
            raise PackDefinitionError(
                MISSING_EVIDENCE_INSTRUMENT,
                f"outcomes[{outcome_id!r}] declares measurability=declared_not_measured but no "
                "evidence_instrument -- a declared-not-measured claim must name the specific signal "
                "this pack's corpus never carries, so corpus_verify.py can actually check the claim "
                "rather than merely trust it, e.g.:\n"
                "evidence_instrument:\n"
                "  kind: structured_field\n"
                "  field: restriction_reason_cited",
            )
        evidence_instrument = (
            _parse_evidence_instrument(evidence_instrument_raw, what=f"outcomes[{outcome_id!r}]")
            if evidence_instrument_raw is not None
            else None
        )

        tier = entry.get("tier", "informational")
        if tier not in TIER_VALUES:
            raise PackDefinitionError(
                INVALID_TIER,
                f"outcomes[{outcome_id!r}].tier={tier!r} must be one of {sorted(TIER_VALUES)}, or omitted "
                "(defaults to 'informational')",
            )

        mode = entry.get("mode", "structural")
        if mode not in MODE_VALUES:
            raise PackDefinitionError(
                INVALID_MODE,
                f"outcomes[{outcome_id!r}].mode={mode!r} must be one of {sorted(MODE_VALUES)}, or omitted "
                "(defaults to 'structural')",
            )

        clause_raw = entry.get("clause")
        clause = _parse_clause_spec(clause_raw, outcome_id=outcome_id) if clause_raw is not None else None

        profile = entry.get("profile", "outcome")
        if profile not in EVIDENCE_PROFILE_VALUES:
            raise PackDefinitionError(
                INVALID_EVIDENCE_PROFILE,
                f"outcomes[{outcome_id!r}].profile={profile!r} must be one of {sorted(EVIDENCE_PROFILE_VALUES)}, "
                "or omitted (defaults to 'outcome')",
            )
        if profile == "obligation" and clause is None:
            raise PackDefinitionError(
                MISSING_OBLIGATION_CLAUSE,
                f"outcomes[{outcome_id!r}].profile=='obligation' requires a clause -- an obligation-profile "
                "entry is a register row anchored to a specific legal/contractual clause, so one with no "
                "clause at all is not yet a real obligation, e.g.:\n"
                "clause:\n"
                "  instrument: Regulation (EU) 2024/1689\n"
                "  article: Article 26\n"
                '  paragraph: "6"',
            )

        epistemic_type = entry.get("epistemic_type")
        if epistemic_type is not None and epistemic_type not in EPISTEMIC_TYPE_VALUES:
            raise PackDefinitionError(
                INVALID_EPISTEMIC_TYPE,
                f"outcomes[{outcome_id!r}].epistemic_type={epistemic_type!r} must be one of "
                f"{sorted(EPISTEMIC_TYPE_VALUES)}, or omitted",
            )

        outcomes.append(
            EvidenceContract(
                id=outcome_id,
                statement=statement,
                evidence_rule=evidence_rule,
                forward_verdict=forward_verdict,
                backward_verdict=backward_verdict,
                profile=profile,
                epistemic_type=epistemic_type,
                window=window,
                effect_claim=effect_claim,
                refusal_reason_code=refusal_reason_code,
                re_derivability_grade=re_derivability_grade,
                declared_by=entry.get("declared_by"),
                evidence_mapping_by=entry.get("evidence_mapping_by"),
                required_assurance_grade=entry.get("required_assurance_grade"),
                exposure_denominator_ref=entry.get("exposure_denominator_ref"),
                retention_check=entry.get("retention_check"),
                measurability=measurability,
                evidence_instrument=evidence_instrument,
                tier=tier,
                mode=mode,
                clause_ref=entry.get("clause_ref"),
                clause=clause,
            )
        )
    return tuple(outcomes)


def _parse_scope_census(raw: Any) -> ScopeCensus | None:
    if raw is None:
        return None
    raw = _require_mapping(raw, "scope_census")
    document_digest = _require_nonempty_str(raw.get("document_digest"), "scope_census.document_digest", "<sha256>")
    n = raw.get("n")
    m = raw.get("m")
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise PackDefinitionError(INVALID_SCOPE_CENSUS, f"scope_census.n must be a non-negative integer; got {n!r}")
    if not isinstance(m, int) or isinstance(m, bool) or m < 1:
        raise PackDefinitionError(
            INVALID_SCOPE_CENSUS, f"scope_census.m must be a positive integer (M is the document's statement count); got {m!r}"
        )
    if n > m:
        raise PackDefinitionError(INVALID_SCOPE_CENSUS, f"scope_census.n ({n}) must not exceed scope_census.m ({m})")
    review_by = _require_nonempty_str(raw.get("review_by"), "scope_census.review_by", "2027-01-01")
    return ScopeCensus(document_digest=document_digest, n=n, m=m, review_by=review_by)


def _parse_taxonomy_pin(raw: Any) -> TaxonomyPin | None:
    """The optional ``taxonomy`` pin, refused unless it names the taxonomy this
    engine ships, by version and by digest."""
    if raw is None:
        return None
    raw = _require_mapping(raw, "taxonomy")
    version = _require_nonempty_str(raw.get("taxonomy_version"), "taxonomy.taxonomy_version", TAXONOMY_VERSION)
    digest = _require_nonempty_str(raw.get("digest"), "taxonomy.digest", "<sha256 over the taxonomy's JCS bytes>")
    if (version, digest) != (TAXONOMY_VERSION, TAXONOMY_DIGEST):
        raise PackDefinitionError(
            TAXONOMY_PIN_MISMATCH,
            f"taxonomy pins version {version!r} digest {digest!r}; this engine ships version "
            f"{TAXONOMY_VERSION!r} digest {TAXONOMY_DIGEST!r}",
        )
    return TaxonomyPin(taxonomy_version=version, digest=digest)


def _parse_counterparty_binding(raw: Any, *, profile_id: str) -> CounterpartyBinding:
    raw = _require_mapping(raw, f"profiles[{profile_id!r}].counterparty_binding")
    direct = _require_nonempty_str(
        raw.get("direct"), f"profiles[{profile_id!r}].counterparty_binding.direct", "employee"
    )
    ultimate = raw.get("ultimate", direct)
    if not isinstance(ultimate, str) or not ultimate:
        raise PackDefinitionError(
            INVALID_COUNTERPARTY_BINDING,
            f"profiles[{profile_id!r}].counterparty_binding.ultimate must be a non-empty string, or omitted "
            "(defaults to 'direct') -- it names the ultimate beneficiary a fold_rollup (job-success) outcome "
            "binds to, e.g. 'downstream' for a mediated profile",
        )
    return CounterpartyBinding(direct=direct, ultimate=ultimate)


def _parse_profile_overrides(
    raw: Any, *, profile_id: str, outcomes_by_id: dict[str, EvidenceContract]
) -> tuple[OutcomeOverride, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PackDefinitionError(INVALID_PROFILE_ID, f"profiles[{profile_id!r}].overrides must be a list")

    out: list[OutcomeOverride] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"profiles[{profile_id!r}].overrides[{idx}]")
        outcome_id = _require_nonempty_str(
            entry.get("outcome_id"), f"profiles[{profile_id!r}].overrides[{idx}].outcome_id", "C1"
        )
        if outcome_id in seen_ids:
            raise PackDefinitionError(
                DUPLICATE_PROFILE_OVERRIDE_OUTCOME_ID,
                f"profiles[{profile_id!r}] declares an override for outcome_id {outcome_id!r} more than once",
            )
        seen_ids.add(outcome_id)
        outcome = outcomes_by_id.get(outcome_id)
        if outcome is None:
            raise PackDefinitionError(
                UNKNOWN_OUTCOME_IN_PROFILE_OVERRIDE,
                f"profiles[{profile_id!r}].overrides names outcome_id {outcome_id!r}, which this pack's own "
                f"'outcomes' does not declare (known ids: {sorted(outcomes_by_id)})",
            )
        if outcome.mode in TOPOLOGY_INVARIANT_MODES:
            raise PackDefinitionError(
                TOPOLOGY_INVARIANT_OVERRIDE,
                f"profiles[{profile_id!r}] declares an override for {outcome_id!r}, whose mode={outcome.mode!r} "
                f"is topology-invariant ({sorted(TOPOLOGY_INVARIANT_MODES)}) -- the agent-integrity core (design "
                "§6b) is the same in every profile by construction; only judged conduct (mode: judged) and "
                "counterparty-change value-props (mode: fold_counterparty) vary by topology",
            )
        applies = entry.get("applies", True)
        if not isinstance(applies, bool):
            raise PackDefinitionError(
                INVALID_PROFILE_ID, f"profiles[{profile_id!r}].overrides[{outcome_id!r}].applies must be a boolean"
            )
        tier = entry.get("tier")
        if tier is not None and tier not in TIER_VALUES:
            raise PackDefinitionError(
                INVALID_TIER,
                f"profiles[{profile_id!r}].overrides[{outcome_id!r}].tier={tier!r} must be one of "
                f"{sorted(TIER_VALUES)}, or omitted (keeps the pack's own declared tier)",
            )
        out.append(OutcomeOverride(outcome_id=outcome_id, applies=applies, tier=tier))
    return tuple(out)


def _parse_profiles(raw: Any, *, outcomes: tuple[EvidenceContract, ...]) -> tuple[TopologyProfile, ...]:
    """``profiles[]`` -- relationship-topology profiles over this pack's own
    outcomes. Optional: a pack
    with no ``profiles`` key ships zero profiles, same additive convention as
    ``proposers``/``outcomes``."""
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise PackDefinitionError(MALFORMED_PACK, "'profiles' must be a list")

    outcomes_by_id = {o.id: o for o in outcomes}
    out: list[TopologyProfile] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        entry = _require_mapping(entry, f"profiles[{idx}]")
        profile_id = _require_nonempty_str(entry.get("profile_id"), f"profiles[{idx}].profile_id", "p1_external_serve")
        if profile_id not in PROFILE_ID_VALUES:
            raise PackDefinitionError(
                INVALID_PROFILE_ID,
                f"profiles[{idx}].profile_id={profile_id!r} must be one of {sorted(PROFILE_ID_VALUES)} "
                "(P5 autonomous is deferred per design §6b -- not yet a declarable profile)",
            )
        if profile_id in seen_ids:
            raise PackDefinitionError(DUPLICATE_PROFILE_ID, f"profile_id {profile_id!r} declared more than once")
        seen_ids.add(profile_id)

        binding = _parse_counterparty_binding(entry.get("counterparty_binding"), profile_id=profile_id)
        overrides = _parse_profile_overrides(entry.get("overrides"), profile_id=profile_id, outcomes_by_id=outcomes_by_id)
        out.append(TopologyProfile(profile_id=profile_id, counterparty_binding=binding, overrides=overrides))
    return tuple(out)


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

    # outcomes[] parsed before the forward obligations/action_semantics/constraints
    # triple: a pack declaring at least one outcome is backward-only-eligible,
    # so whether that triple may be empty depends on outcomes, not the other
    # way around (see _parse_constraints' allow_empty docstring note).
    outcomes = _parse_outcomes(data.get("outcomes"))
    allow_empty_forward = bool(outcomes)

    constraints, constraint_scopes = _parse_constraints(data.get("constraints"), allow_empty=allow_empty_forward)
    declared_checks = {c.check for c in constraints}
    check_selectors = {c.check: frozenset(c.config["selectors"]) for c in constraints if "selectors" in c.config}
    obligations = _parse_obligations(
        data.get("obligations"),
        declared_checks=declared_checks,
        check_selectors=check_selectors,
        allow_empty=allow_empty_forward,
    )
    action_semantics = _parse_action_semantics(data.get("action_semantics"), allow_empty=allow_empty_forward)
    folds = _parse_folds(data.get("folds"), pack_dir=pack_dir, allow_empty=allow_empty_forward)
    _validate_caps_scope_against_folds(constraints, constraint_scopes, folds)
    proposers = _parse_proposers(data.get("proposers"))
    fixtures = _parse_fixtures(data.get("fixtures"))
    scope_census = _parse_scope_census(data.get("scope_census"))
    profiles = _parse_profiles(data.get("profiles"), outcomes=outcomes)
    taxonomy = _parse_taxonomy_pin(data.get("taxonomy"))

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
        outcomes=outcomes,
        scope_census=scope_census,
        profiles=profiles,
        taxonomy=taxonomy,
    )
