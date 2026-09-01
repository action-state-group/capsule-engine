# SPDX-License-Identifier: Apache-2.0
"""Check this repo's vendored ``registry/conventions.json`` against
capsule-ledger's independent minimal shim of the same file.

capsule-ledger's ``capsule_ledger/registry/conventions.py`` module docstring
documents its ``action_class_conventions`` table as a deliberately
independent, hand-maintained copy -- "this module never imports
capsule-engine and carries its own tiny label table, so the two are
independent, not two forks of one truth" -- so this is not a vendor/--check
pair like ``vendor_cpb_registry.py`` or ``vendor_envcompat.py`` (there is no
canonical source to re-derive from and no "run this to fix it" write mode).
It exists to catch exactly the failure mode that produced this fix pass:
the two tables silently drifting apart with nothing to notice. A real
product change to one side must still be applied to the other by hand.

Usage:
    python scripts/check_registry_conventions_drift.py [path-to-capsule-ledger-checkout]

If no path is given, tries ``$LEDGER_REPO_PATH``, else a sibling checkout at
``../capsule-ledger`` next to this repo.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_CONVENTIONS = REPO_ROOT / "capsule_engine" / "registry" / "conventions.json"
LEDGER_CONVENTIONS_RELPATH = Path("capsule_ledger") / "registry" / "conventions.json"


def _find_capsule_ledger(explicit: str | None) -> Path:
    candidates = [Path(explicit)] if explicit else []
    env = os.environ.get("LEDGER_REPO_PATH")
    if env:
        candidates.append(Path(env))
    candidates.append(REPO_ROOT.parent / "capsule-ledger")
    for c in candidates:
        if c and (c / LEDGER_CONVENTIONS_RELPATH).exists():
            return c.resolve()
    raise SystemExit(
        "no capsule-ledger checkout found -- pass a path, set $LEDGER_REPO_PATH, "
        "or place a checkout at ../capsule-ledger next to this repo"
    )


def main(argv: list[str]) -> int:
    capsule_ledger = _find_capsule_ledger(argv[1] if len(argv) > 1 else None)
    ledger_path = capsule_ledger / LEDGER_CONVENTIONS_RELPATH

    engine_data = json.loads(ENGINE_CONVENTIONS.read_text(encoding="utf-8"))
    ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))

    engine_block = engine_data.get("action_class_conventions", {})
    ledger_block = ledger_data.get("action_class_conventions", {})

    if engine_block != ledger_block:
        raise SystemExit(
            f"{ENGINE_CONVENTIONS}'s 'action_class_conventions' block is out of sync with "
            f"{ledger_path}'s -- these are two independent hand-maintained copies (see "
            "capsule_ledger/registry/conventions.py's module docstring), so a real change "
            "on one side must be applied to the other by hand; this check only catches the "
            "drift, it does not resolve it"
        )
    print(f"{ENGINE_CONVENTIONS} and {ledger_path} agree on action_class_conventions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
