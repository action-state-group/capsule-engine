# SPDX-License-Identifier: Apache-2.0
"""One-off migration: regenerate the checked-in ``tests/fixtures/*.jsonl``
capsule fixtures from ``format_version`` "2" to "4" via the reference
producer (``agent_action_capsule.emit()``), no hand edits to capsule bytes.

Each capsule's declared fields (action_id, action_type, operator, developer,
timestamp, model_attestation, effect, disposition, constraints, chain) are
read from the checked-in v2 capsule and replayed through ``emit()``, which
always produces format_version "4" (JCS canonicalization, spec -04). Chain
links are re-pointed at the newly computed parent capsule_id, since every
capsule's capsule_id changes when the format changes.

Run once per fixture file:

    python3 tools/migrate_fixture_format_v4.py tests/fixtures/sample_ledger.jsonl
    python3 tools/migrate_fixture_format_v4.py tests/fixtures/amaury_sample_ledger.jsonl
    python3 tools/migrate_fixture_format_v4.py tests/fixtures/nanda_transaction_ledger.jsonl

Prints a before/after capsule_id per line so a digest change is visible, not
silent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TypedDict

from agent_action_capsule import ConstraintRecord, Disposition, EffectRecord, emit


class _ModelAttestationDict(TypedDict, total=False):
    model_id: str
    provider: str
    # compute_attestation is best-effort free-form inference metadata by
    # design (agent_action_capsule.emit()'s own compute_attestation param is
    # typed dict[str, Any] for the same reason) -- there is no fixed shape to
    # declare narrower than the upstream contract.
    compute_attestation: dict[str, Any]


class _EffectDict(TypedDict, total=False):
    """Mirrors ``agent_action_capsule.EffectRecord``'s constructor fields."""

    status: str
    type: str
    request_digest: str
    response_digest: str
    external_ref: str
    irreversibility_class: str
    effect_attestation: str


class _DispositionDict(TypedDict, total=False):
    """Mirrors ``agent_action_capsule.Disposition``'s constructor fields
    (``expiry_policy`` excepted: no fixture in scope uses it)."""

    decision: str
    approver: str
    human_disposed: bool
    authority: str
    verdict_class: str
    reason_digest: str


class _ConstraintDict(TypedDict, total=False):
    """Mirrors ``agent_action_capsule.ConstraintRecord``'s constructor fields."""

    id: str
    result: str
    severity: str
    blocking: bool
    check_type: str
    method: str
    evidence_digest: str


class _ChainDict(TypedDict):
    parent_capsule_id: str
    relation: str


class _CapsuleDict(TypedDict, total=False):
    """The subset of a checked-in v2 fixture capsule this migration reads."""

    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: _ModelAttestationDict
    effect: _EffectDict
    disposition: _DispositionDict
    constraints: list[_ConstraintDict]
    chain: _ChainDict


# emit()'s own return type is the bare `dict` (agent_action_capsule/emit.py) --
# the sealed capsule shape is exactly _CapsuleDict plus whatever optional
# blocks were supplied, so there is no narrower type to declare on top of the
# library's own untyped return here.
def _migrate_capsule(old: _CapsuleDict, id_map: dict[str, str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "action_id": old["action_id"],
        "action_type": old["action_type"],
        "operator": old["operator"],
        "developer": old["developer"],
        "timestamp": old["timestamp"],
    }

    ma = old.get("model_attestation")
    if ma is not None:
        if "model_id" in ma:
            kwargs["model_id"] = ma["model_id"]
        if "provider" in ma:
            kwargs["provider"] = ma["provider"]
        if "compute_attestation" in ma:
            kwargs["compute_attestation"] = ma["compute_attestation"]

    if "effect" in old:
        kwargs["effect"] = EffectRecord(**old["effect"])

    if "disposition" in old:
        kwargs["disposition"] = Disposition(**old["disposition"])

    if "constraints" in old:
        kwargs["constraints"] = tuple(ConstraintRecord(**c) for c in old["constraints"])

    if "chain" in old:
        parent = old["chain"]["parent_capsule_id"]
        if parent not in id_map:
            raise ValueError(
                f"chain parent {parent!r} not yet migrated -- fixture capsules "
                "must be in chain order (parent before child) in the source file"
            )
        kwargs["prior_capsule_id"] = id_map[parent]
        kwargs["chain_relation"] = old["chain"]["relation"]

    return emit(**kwargs)


def migrate_file(path: Path) -> None:
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    id_map: dict[str, str] = {}
    out_lines = []
    for line in lines:
        old = json.loads(line)
        new = _migrate_capsule(old, id_map)
        id_map[old["capsule_id"]] = new["capsule_id"]
        changed = "changed" if new["capsule_id"] != old["capsule_id"] else "unchanged"
        print(f"{path.name}: {old['action_id']}: {old['capsule_id']} -> {new['capsule_id']} ({changed})")
        out_lines.append(json.dumps(new, separators=(",", ":")))
    path.write_text("\n".join(out_lines) + "\n")


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: migrate_fixture_format_v4.py <fixture.jsonl> [...]", file=sys.stderr)
        return 2
    for arg in argv:
        migrate_file(Path(arg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
