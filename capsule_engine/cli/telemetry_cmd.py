# SPDX-License-Identifier: Apache-2.0
"""`capsule telemetry`: disclosure/status, and the 6-metric funnel report
generator -- a reporting tool over already-collected events, not the
collection itself. See ``capsule_ledger/telemetry/`` for the instrumentation."""
from __future__ import annotations

import argparse
import json
import sys
from importlib import resources
from pathlib import Path

__all__ = ["add_parser"]

_SYNTHETIC_FIXTURE = "synthetic_events.jsonl"


def _cmd_status(_args: argparse.Namespace) -> int:
    from ..telemetry.consent import DISCLOSURE_TEXT, is_opted_in
    from ..telemetry.state import state_path

    print(DISCLOSURE_TEXT)
    print(f"currently: {'ON' if is_opted_in() else 'OFF (default)'}")
    if is_opted_in():
        print(f"local state file: {state_path()}")
    return 0


def _load_events(path: str | None, *, use_fixture: bool) -> list[dict]:
    if use_fixture:
        text = resources.files("capsule_engine.telemetry").joinpath("fixtures", _SYNTHETIC_FIXTURE).read_text(
            encoding="utf-8"
        )
    else:
        text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _cmd_funnel(args: argparse.Namespace) -> int:
    from ..telemetry.funnel import compute_funnel, render_funnel_report

    if not args.dry_run and not args.events:
        print("capsule telemetry funnel: --events PATH is required unless --dry-run is given", file=sys.stderr)
        return 2

    raw_events = _load_events(args.events, use_fixture=args.dry_run)
    report = compute_funnel(raw_events)
    text = render_funnel_report(report)

    if args.dry_run:
        text = "(dry run -- rendered against bundled synthetic fixture data, no real telemetry)\n\n" + text

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    telemetry = sub.add_parser("telemetry", help="telemetry disclosure/status, and the 6-metric funnel report")
    telemetry_sub = telemetry.add_subparsers(dest="telemetry_command")
    telemetry.set_defaults(telemetry_parser=telemetry)

    p_status = telemetry_sub.add_parser("status", help="show the disclosure text and current opt-in state")
    p_status.set_defaults(func=_cmd_status)

    p_funnel = telemetry_sub.add_parser(
        "funnel", help="render the 6-metric funnel report over collected telemetry (or synthetic fixture data)"
    )
    p_funnel.add_argument("--events", help="path to a JSONL file of collected telemetry events")
    p_funnel.add_argument(
        "--dry-run", action="store_true",
        help="render against bundled synthetic/fixture data instead of --events (no real telemetry required)",
    )
    p_funnel.add_argument("--out", help="write the report to this path instead of stdout")
    p_funnel.set_defaults(func=_cmd_funnel)

    return telemetry
