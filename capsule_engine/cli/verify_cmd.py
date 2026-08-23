# SPDX-License-Identifier: Apache-2.0
"""`capsule verify`: verify one ledger record, or an entire bundle file offline.

Exit codes (CI-friendly, and distinguished because "not found" and "found
but tampered" are different failure modes a caller may want to branch on):
  0  verified clean
  1  verification failed (digest mismatch, chain gap, etc. -- ``result.ok`` is False)
  2  usage error (no capsule_id/--bundle given, or the capsule/bundle wasn't found)
"""
from __future__ import annotations

import argparse
import json
import sys

from agent_action_capsule import verify as verify_capsule

from .format import build_echo
from .ledger_io import open_ledger, require_ledger_path

__all__ = ["add_parser", "run"]


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser("verify", help="verify one ledger record, or an entire bundle file")
    p.add_argument("capsule_id", nargs="?", help="a capsule_id or an unambiguous prefix (omit with --bundle)")
    p.add_argument("--ledger", help="ledger store directory or a JSONL fixture file (default: $CAPSULE_LEDGER)")
    p.add_argument(
        "--bundle", help="verify every record in a bundle file produced by `capsule bundle`, offline and self-contained"
    )
    p.add_argument("--json", action="store_true", help="print the raw verification result(s) as JSON")
    p.set_defaults(func=run)
    return p


def _print_result(capsule_id: str, result) -> bool:
    if result.ok:
        print(f"✓ verifies · {capsule_id}")
        return True
    print(f"✗ verification failed · {capsule_id}")
    for finding in result.findings:
        print(f"  - {finding.code}: {finding.detail}")
    return False


def run(args: argparse.Namespace) -> int:
    # ``verify`` only exists at all in the "full" packaging arm -- see
    # ``cli/main.py`` -- so any use of it is M5's "verify run" fact.
    from ..telemetry.record import record_evidence_touch

    record_evidence_touch("full")

    if args.bundle:
        return _run_bundle(args)
    if not args.capsule_id:
        print("capsule verify: capsule_id is required unless --bundle is given", file=sys.stderr)
        return 2

    ledger_path = require_ledger_path("verify", args)
    if ledger_path is None:
        return 2

    with open_ledger(ledger_path) as store:
        record = store.fetch(args.capsule_id)
        if record is None:
            print(f"no such capsule {args.capsule_id!r} in ledger {ledger_path}", file=sys.stderr)
            return 2
        result = store.verify(record.capsule_id)
        capsule_id = record.capsule_id

    if args.json:
        print(
            json.dumps(
                {
                    "capsule_id": capsule_id,
                    "ok": result.ok,
                    "findings": [{"code": f.code, "detail": f.detail, "severity": f.severity} for f in result.findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if result.ok else 1

    ok = _print_result(capsule_id, result)
    print()
    print(build_echo("verify", positional=capsule_id))
    return 0 if ok else 1


def _run_bundle(args: argparse.Namespace) -> int:
    try:
        with open(args.bundle, encoding="utf-8") as fh:
            bundle = json.load(fh)
    except OSError as exc:
        print(f"capsule verify: cannot read bundle {args.bundle!r}: {exc}", file=sys.stderr)
        return 2

    records = bundle.get("records", [])
    ids = [r.get("capsule_id") for r in records]
    all_ok = True
    for capsule in records:
        result = verify_capsule(capsule, store=ids)
        ok = _print_result(capsule.get("capsule_id", "?"), result)
        all_ok = all_ok and ok

    print()
    status = "verifies clean" if all_ok else "verification FAILED"
    print(f"bundle {args.bundle}: {len(records)} record(s), {status}")
    return 0 if all_ok else 1
