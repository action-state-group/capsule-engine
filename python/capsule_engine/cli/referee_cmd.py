# SPDX-License-Identifier: Apache-2.0
"""`capsule-engine referee judge`: the contract-named referee over one deal (``capsule_engine.referee``).

The dispute file is JSON::

    {"buyer":  {"half": <capsule>, "action": <text>, "terms": {...}, "bundles": [<path>, ...]},
     "seller": {"half": <capsule>, "action": <text>, "terms": {...}, "bundles": [<path>, ...]},
     "rail":   [<settlement leg capsule>, ...]}

``bundles`` are the party's own deal bundles, relative to the dispute file.
The referee judges under the key of ``--ledger`` (its own book), seals the
Result there, and writes the Result's JCS bytes to ``--out``. It prints one
JSON object: ``{"ok": true, "result_digest", "capsule_id"}``, or
``{"ok": false, "code", "error"}`` when it refuses.

Exit codes: 0 sealed; 1 refused (the referee's refusal, ``code``); 2 usage
error (an unreadable or malformed dispute file).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_action_capsule.canonical import jcs
from capsule_emit.signing import resolve_signer

from .. import referee
from ..report.replay import load_disclosed, load_records, load_withheld

__all__ = ["add_parser", "run"]


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser("referee", help="the contract-named referee: recompute a deal's pinned terms")
    rsub = p.add_subparsers(dest="referee_command")
    j = rsub.add_parser("judge", help="judge one deal and seal the Evidence Result in the referee's book")
    j.add_argument("--dispute", required=True, help="the dispute file (JSON)")
    j.add_argument("--ledger", required=True, help="the referee's own book; its key is the referee key")
    j.add_argument("--as-of", required=True, help="the as-of instant, RFC 3339 UTC with seconds and Z")
    j.add_argument("--out", required=True, help="where to write the Result (JCS bytes)")
    j.add_argument("--operator", default="", help="the referee's operator string")
    j.add_argument("--developer", default="", help="the referee's developer string")
    j.set_defaults(func=run)
    p.set_defaults(referee_parser=p)
    return p


def _party(entry: object, base: Path, role: str) -> referee.Party:
    if not isinstance(entry, dict) or not isinstance(entry.get("half"), dict):
        raise ValueError(f"{role}: needs a half")
    bundles = entry.get("bundles") or []
    if not isinstance(bundles, list) or not all(isinstance(b, str) for b in bundles):
        raise ValueError(f"{role}: bundles must be a list of paths")
    paths = [base / b for b in bundles]
    return referee.Party(
        half=entry["half"],
        action=str(entry.get("action", "")),
        terms=entry.get("terms") if isinstance(entry.get("terms"), dict) else {},
        records=load_records(paths) if paths else (),
        disclosed=load_disclosed(paths) if paths else {},
        withheld=load_withheld(paths) if paths else frozenset(),
    )


def _print(**members: object) -> None:
    print(json.dumps(members, sort_keys=True))


def run(args: argparse.Namespace) -> int:
    path = Path(args.dispute)
    try:
        dispute = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(dispute, dict):
            raise ValueError("the dispute file must hold a JSON object")
        buyer = _party(dispute.get("buyer"), path.parent, "buyer")
        seller = _party(dispute.get("seller"), path.parent, "seller")
        rail = dispute.get("rail") or []
        if not isinstance(rail, list):
            raise ValueError("rail must be a list of capsules")
    except (OSError, ValueError) as exc:
        print(f"capsule-engine referee judge: {exc}", file=sys.stderr)
        return 2
    key_id = resolve_signer(args.ledger).key_id
    try:
        doc = referee.judge(buyer, seller, rail, as_of=args.as_of, referee_key_id=key_id)
    except referee.RefereeError as exc:
        _print(ok=False, code=exc.code, error=str(exc))
        return 1
    sealed = referee.seal_result(doc, buyer_half=buyer.half, seller_half=seller.half, ledger=args.ledger,
                                 operator=args.operator, developer=args.developer)
    Path(args.out).write_bytes(jcs(doc))
    _print(ok=True, result_digest=referee.result_digest(doc), capsule_id=sealed.capsule_id)
    return 0
