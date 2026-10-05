# SPDX-License-Identifier: Apache-2.0
"""Validator entry point for ``capsule_engine/schemas/evidence-contract-v0.json``.

This module is deliberately independent of the pack loader (``loader.py``):
it validates a *file on disk* against the Evidence Contract JSON Schema, the
same file ``capsulectl contract validate`` and any other consumer read -- there is no private compiler/planner dependency here, only the
schema plus ``jsonschema``.

``validate_requirement`` is what cross-checks
``EvidenceContract.canonical_dict()`` against the schema
(``tests/test_evidence_contract_schema.py``): a single requirement is not a
full Evidence Contract document, so it validates against the ``requirement``
union directly rather than wrapping it in a synthetic root.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

# Resolved inside the installed package, so it is present in a wheel install
# and not only in a source checkout (scripts/clean_room_wheel.sh checks this).
SCHEMA_PATH = resources.files("capsule_engine") / "schemas" / "evidence-contract-v0.json"

__all__ = [
    "SCHEMA_PATH",
    "Issue",
    "explain_evidence_contract",
    "load_schema",
    "validate_evidence_contract",
    "validate_requirement",
    "main",
]


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator_for(pointer: str) -> jsonschema.Draft202012Validator:
    schema = load_schema()
    return jsonschema.Draft202012Validator({**schema, "$ref": pointer})


def validate_evidence_contract(doc: dict[str, Any]) -> None:
    """Validate a full Evidence Contract root document. Raises
    ``jsonschema.exceptions.ValidationError`` on the first violation."""
    _validator_for("#/$defs/evidenceContract").validate(doc)


def validate_requirement(doc: dict[str, Any]) -> None:
    """Validate a single requirement object -- e.g. one entry of
    ``EvidenceContract.canonical_dict()`` -- against the ``requirement``
    union. Raises ``jsonschema.exceptions.ValidationError`` on the first
    violation."""
    _validator_for("#/$defs/requirement").validate(doc)


@dataclass(frozen=True)
class Issue:
    """One irreducible violation: where (a ``/``-joined instance path,
    ``<root>`` for the document), which schema keyword failed, and why."""

    path: str
    keyword: str
    message: str


def _path(error: jsonschema.exceptions.ValidationError) -> str:
    return "/".join(str(p) for p in error.absolute_path) or "<root>"


def _leaves(error: jsonschema.exceptions.ValidationError) -> list[jsonschema.exceptions.ValidationError]:
    if not error.context:
        return [error]
    return [leaf for sub in error.context for leaf in _leaves(sub)]


def _profile_mismatch(errors: list[jsonschema.exceptions.ValidationError]) -> bool:
    return any(
        e.absolute_path and e.absolute_path[-1] == "profile" and e.validator in ("const", "enum")
        for err in errors
        for e in _leaves(err)
    )


def _missing_property(errors: list[jsonschema.exceptions.ValidationError]) -> bool:
    return any(e.validator == "required" for err in errors for e in _leaves(err))


def _explain(error: jsonschema.exceptions.ValidationError, root: Any, out: list[Issue]) -> None:
    """The requirement union is a ``oneOf`` discriminated by ``profile``.
    Reporting it as one bare ``oneOf`` failure hides the cause, so an
    unmatched union is explained against the branch the instance's own
    ``profile`` selects -- the same walk ``capsulectl contract validate``
    does."""
    if error.validator == "oneOf" and error.context and isinstance(error.instance, dict):
        here = _path(error)
        branches: dict[int, list[jsonschema.exceptions.ValidationError]] = {}
        for sub in error.context:
            branches.setdefault(sub.relative_schema_path[0], []).append(sub)
        if "profile" not in error.instance:
            # Some branches (the native shape) do not require `profile`. When
            # one of them fails for a reason other than a missing property,
            # the requirement is in that shape and its real violation is
            # reported; otherwise the missing discriminator is.
            candidates = [errs for errs in branches.values() if not _missing_property(errs)]
            if not candidates:
                out.append(Issue(here, "required", "'profile' is a required property"))
                return
        else:
            candidates = [errs for errs in branches.values() if not _profile_mismatch(errs)]
        if not candidates:
            out.append(Issue(f"{here}/profile", "enum", f"unknown profile {error.instance.get('profile')!r}"))
            return
        best = min(candidates, key=lambda errs: sum(len(_leaves(e)) for e in errs))
        for sub in best:
            _explain(sub, root, out)
        return
    if error.context:
        for sub in error.context:
            _explain(sub, root, out)
        return
    if error.validator is None:
        # A `false` subschema (a forbidden property such as `status`). This
        # jsonschema reports it at the owning object, without the property
        # name, so recover the name from the object: the member holding the
        # rejected value.
        owner = _instance_at(root, error.absolute_path)
        if isinstance(owner, dict):
            names = sorted(k for k, v in owner.items() if v == error.instance)
            if names:
                out.append(Issue(f"{_path(error)}/{names[0]}", "false", f"{names[0]!r} is not permitted here"))
                return
        out.append(Issue(_path(error), "false", error.message))
        return
    out.append(Issue(_path(error), str(error.validator), error.message))


def _instance_at(root: Any, path: Any) -> Any:
    cur = root
    for part in path:
        try:
            cur = cur[part]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def explain_evidence_contract(doc: dict[str, Any]) -> list[Issue]:
    """Every irreducible violation in ``doc``, sorted by path; empty when
    ``doc`` is a valid Evidence Contract."""
    out: list[Issue] = []
    for error in _validator_for("#/$defs/evidenceContract").iter_errors(doc):
        _explain(error, doc, out)
    return sorted(set(out), key=lambda i: (i.path, i.keyword, i.message))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m capsule_engine.packs.contract_validate <evidence-contract.json> [...]", file=sys.stderr)
        return 2
    exit_code = 0
    for path_str in argv:
        path = Path(path_str)
        doc = json.loads(path.read_text())
        try:
            validate_evidence_contract(doc)
        except jsonschema.exceptions.ValidationError as exc:
            exit_code = 1
            at = "/".join(str(p) for p in exc.absolute_path) or "<root>"
            print(f"{path}: INVALID at {at} -- {exc.message}", file=sys.stderr)
        else:
            print(f"{path}: valid")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
