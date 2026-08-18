# SPDX-License-Identifier: Apache-2.0
"""`capsule bundle`: a self-contained, independently verifiable slice of the
ledger, plus a verify-surface permalink.

"Self-contained" is load-bearing: the bundle transitively pulls in any
``chain.parent_capsule_id`` a selected record cites (walking the real
ledger) so the slice verifies on its own, without needing the rest of the
ledger present -- verification below runs with ``store=`` restricted to
exactly the bundle's own capsule ids, never the full ledger, to prove that
property rather than assume it.

The permalink is fragment-carried (``#...``, after the URL's ``#``): per
the workspace's other verify surfaces (`agentactioncapsule-site` /
`scitt-cose`'s hosted verifier), capsule data that goes after ``#`` is never
sent to a server -- only the browser-side JS reads it. ``verify.
agentactioncapsule.org`` is this workspace's existing public verify domain
(see CLAUDE.md / STATUS.md T7 notes); the base URL is still overridable
(``--verify-base-url`` / ``$CAPSULE_VERIFY_BASE_URL``) since no bundle-specific
route exists there yet -- this is this package's own convention until one
does.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_action_capsule import verify as verify_capsule

from ..envcompat import env_get
from ..ledger.api import ScanQuery
from ..mmr import core as mmr_core
from ..mmr.index import MmrLedger
from .format import build_echo, format_staleness
from .ledger_io import add_scan_query_args, build_scan_query, echo_parts, open_ledger, require_ledger_path

__all__ = ["add_parser", "run"]

DEFAULT_VERIFY_BASE_URL = "https://verify.agentactioncapsule.org/bundle"


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser("bundle", help="produce a self-contained verifiable slice of the ledger")
    add_scan_query_args(p)
    p.add_argument("--out", default="bundle.json", help="output path for the bundle file (default: %(default)s)")
    p.add_argument(
        "--verify-base-url",
        dest="verify_base_url",
        default=env_get("CAPSULE_VERIFY_BASE_URL", DEFAULT_VERIFY_BASE_URL),
        help="base URL the permalink's fragment is appended to (default: %(default)s)",
    )
    p.add_argument(
        "--with-viewer",
        dest="with_viewer",
        action="store_true",
        help=(
            "also write a self-contained offline HTML viewer next to --out, so the "
            "bundle verifies on a machine with no network (default viewer path: "
            "--out with a .html extension)"
        ),
    )
    p.add_argument(
        "--viewer-out",
        dest="viewer_out",
        default=None,
        help="output path for the offline viewer HTML (implies --with-viewer; default: derived from --out)",
    )
    p.set_defaults(func=run)
    return p


def _default_viewer_out(out: str) -> str:
    """``bundle.json`` -> ``bundle.html`` alongside it; falls back to a
    ``-viewer.html`` suffix on the rare path where ``--out`` already ends in
    ``.html`` (so the two outputs never collide)."""
    p = Path(out)
    candidate = p.with_suffix(".html")
    if str(candidate) == out:
        candidate = p.with_name(p.stem + "-viewer.html")
    return str(candidate)


def _collect_with_parents(store, matched):
    """Pull in any cited chain parent not already selected, so the bundle
    verifies standalone without needing the rest of the ledger. A parent
    that genuinely isn't in the ledger (a real gap) is left out -- it
    surfaces honestly as a finding on the citing record, never hidden."""
    by_id = {r.capsule_id: r for r in matched}
    frontier = list(matched)
    missing: set[str] = set()
    while frontier:
        rec = frontier.pop()
        parent_id = (rec.capsule.get("chain") or {}).get("parent_capsule_id")
        if not parent_id or parent_id in by_id or parent_id in missing:
            continue
        parent = store.fetch(parent_id)
        if parent is None:
            missing.add(parent_id)
            continue
        by_id[parent.capsule_id] = parent
        frontier.append(parent)
    return sorted(by_id.values(), key=lambda r: r.seq)


def _build_completeness_certificate(store, records, tree_size: int) -> dict | None:
    """MMR range proof (plus a consistency proof bridging to the full
    ledger's checkpoint, when the bundle's range doesn't already reach it)
    over the bundle's own record range.

    Shape matches scitt-cose's viewer (``MMR_JS`` / ``checkCompleteness`` in
    ``hosted_profiles/hosted.py``) field for field -- that module ports this
    package's MMR core to JS and already checks a certificate in this shape;
    this is the CLI side of that contract, not a new one.
    """
    if not records:
        return None

    mmr = MmrLedger(store)
    mmr.sync()

    from_seq, to_seq = records[0].seq, records[-1].seq
    proof = mmr.range_proof(from_seq, to_seq)
    range_root = mmr.root_at(proof.size).hex()

    checkpoint_size = mmr_core.node_count(tree_size)
    if checkpoint_size == proof.size:
        checkpoint_root = range_root
        consistency = None
    else:
        checkpoint_root = mmr.root_at(checkpoint_size).hex()
        consistency = mmr.consistency_proof(proof.size, checkpoint_size)

    return {
        "v": 1,
        "range_proof": {
            "from_seq": proof.from_seq,
            "to_seq": proof.to_seq,
            "size": proof.size,
            "inclusion_from": dataclasses.asdict(proof.inclusion_from),
            "inclusion_to": dataclasses.asdict(proof.inclusion_to),
        },
        "range_root": range_root,
        "checkpoint_size": checkpoint_size,
        "checkpoint_root": checkpoint_root,
        "consistency_proof": dataclasses.asdict(consistency) if consistency is not None else None,
    }


def run(args: argparse.Namespace) -> int:
    # ``bundle`` only exists at all in the "full" packaging arm -- see
    # ``cli/main.py`` -- so any use of it is M5's "bundle/share created" fact.
    from ..telemetry.record import record_evidence_touch

    record_evidence_touch("full")

    ledger_path = require_ledger_path("bundle", args)
    if ledger_path is None:
        return 2

    query = build_scan_query(args)
    with open_ledger(ledger_path) as store:
        matched = list(store.scan(query))
        records = _collect_with_parents(store, matched)
        capsules = [r.capsule for r in records]
        ids = [c["capsule_id"] for c in capsules]

        verification: dict[str, dict] = {}
        all_ok = True
        for capsule in capsules:
            result = verify_capsule(capsule, store=ids)
            verification[capsule["capsule_id"]] = {
                "ok": result.ok,
                "findings": [{"code": f.code, "detail": f.detail, "severity": f.severity} for f in result.findings],
            }
            all_ok = all_ok and result.ok

        tree_size = sum(1 for _ in store.scan(ScanQuery()))
        completeness_certificate = _build_completeness_certificate(store, records, tree_size)

    echo = build_echo("bundle", flags=[*echo_parts(args), ("--out", args.out)])
    bundle = {
        "bundle_version": "1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "query": {
            k: v
            for k, v in {
                "agent": args.agent,
                "since": args.since,
                "until": args.until,
                "counterparty": args.counterparty,
                "verdict": args.verdict,
                "action_type": args.action_type,
                "limit": args.limit,
            }.items()
            if v is not None
        },
        "cli_echo": echo,
        "records": capsules,
        "range": [records[0].seq, records[-1].seq] if records else [0, -1],
        "checkpoint": {"tree_size": tree_size},
        "verification": verification,
        "completeness_certificate": completeness_certificate,
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2, sort_keys=True)

    payload = json.dumps(bundle, separators=(",", ":"), sort_keys=True).encode("utf-8")
    fragment = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    permalink = f"{args.verify_base_url}#{fragment}"

    r0, r1 = bundle["range"]
    status = "all verify" if all_ok else "VERIFICATION FAILURE in this slice"
    print(f"wrote {args.out} ({len(records)} record(s), records {r0}–{r1}, {status})")
    print(f"checkpoint #{tree_size} · as of {format_staleness(0)}")
    print(f"verify: {permalink}")

    if args.with_viewer or args.viewer_out is not None:
        from ..bundle_viewer import render_offline_viewer_html

        viewer_out = args.viewer_out or _default_viewer_out(args.out)
        viewer_html = render_offline_viewer_html(fragment)
        with open(viewer_out, "w", encoding="utf-8") as fh:
            fh.write(viewer_html)
        print(f"wrote {viewer_out} (self-contained, opens with no network)")

    print()
    print(echo)
    return 0 if all_ok else 1
