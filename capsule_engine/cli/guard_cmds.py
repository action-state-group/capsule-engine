# SPDX-License-Identifier: Apache-2.0
"""``capsule guard`` verbs: dry-run replay + self-contained HTML report (moved
here, matching ``fold_cmds.py``'s per-verb-module convention, so ``main.py``
stays a thin dispatcher), plus ``guard enforce`` -- a minimal, honest local
marker command (see ``_cmd_guard_enforce``'s own docstring for exactly what
it does and doesn't do).

Every guard-dry-run invocation here checks the current packaging arm
(``capsule_ledger.packaging``) and, in "guards-only", both suppresses the
evidence chrome in the rendered HTML report (``render_report_html``'s own
``arm`` param) and rewords/suppresses this command's own stdout so it never
prints a capsule id or a share/verify link -- see this module's inline
comments at each print site.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import packaging
from ..envcompat import env_get

__all__ = ["add_parser"]

DEFAULT_CATALOG_DIR = Path(__file__).resolve().parent.parent / "folds" / "catalog_defs"
DEFAULT_WICKET_DIR = Path(__file__).resolve().parent.parent / "guards" / "wickets" / "catalog_defs"
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "policy" / "catalog_defs" / "default.yaml"


def _catalog_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "dir", None):
        return Path(args.dir)
    env = env_get("CAPSULE_FOLD_DIR")
    if env:
        return Path(env)
    return DEFAULT_CATALOG_DIR


def _wicket_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "wicket_dir", None):
        return Path(args.wicket_dir)
    env = env_get("CAPSULE_WICKET_DIR")
    return Path(env) if env else DEFAULT_WICKET_DIR


def _resolve_manifest_digest(args: argparse.Namespace) -> str | None:
    """Resolve the policy manifest that governs this replay (``--manifest``,
    default the built-in one) against the same fold catalog this command is
    already using (``_catalog_dir``) and the wicket catalog (``--wicket-dir``).

    Best-effort: a caller pointing ``--dir``/``--wicket-dir`` at a catalog the
    configured manifest doesn't (yet) pin correctly gets a warning, not a
    hard failure -- the replay report itself is still real and useful without
    a resolvable manifest citation. ``--no-manifest`` skips this entirely."""
    if getattr(args, "no_manifest", False):
        return None

    from ..policy import PolicyManifestError, load_manifest_file, resolve_manifest

    manifest_path = Path(args.manifest) if getattr(args, "manifest", None) else DEFAULT_MANIFEST_PATH
    try:
        manifest = load_manifest_file(manifest_path)
        resolved = resolve_manifest(
            manifest, fold_catalog_dir=_catalog_dir(args), wicket_catalog_dir=_wicket_dir(args)
        )
    except PolicyManifestError as exc:
        print(f"warning: policy manifest {manifest_path} did not resolve ({exc.reason}: {exc}); "
              "proceeding without a manifest citation", file=sys.stderr)
        return None
    return resolved.manifest_digest


def _parse_cap_args(items: list[str]) -> dict[str, int] | None:
    caps_minor: dict[str, int] = {}
    for item in items or []:
        cls, sep, value = item.partition("=")
        if not sep:
            print(f"--cap must be CLASS=MINOR_UNITS, got {item!r}", file=sys.stderr)
            return None
        try:
            caps_minor[cls] = int(value)
        except ValueError:
            print(f"--cap value must be an integer (minor units), got {item!r}", file=sys.stderr)
            return None
    return caps_minor


def _cmd_guard_dry_run(args: argparse.Namespace) -> int:
    from agent_action_capsule import compute_capsule_id

    from ..folds.loader import load_definition_file
    from ..packs import PackDefinitionError, load_proposals_file
    from ..report import build_dry_run_report, build_dry_run_report_with_proposal, render_report_html
    from ..report.render import TelemetryConfig, decode_fragment, to_fragment_payload
    from ..telemetry.record import record_evidence_touch, record_guard_configured, record_guard_evaluated

    if bool(args.model_note) != bool(args.model_id):
        print("--model-note and --model-id must be given together (or neither)", file=sys.stderr)
        return 1

    caps_minor = _parse_cap_args(args.cap)
    if caps_minor is None:
        return 1

    arm = packaging.current_arm()
    evidence_visible = packaging.evidence_visible(arm)

    since = None if args.since in (None, "all") else args.since
    fold_path = Path(args.fold_file) if args.fold_file else _catalog_dir(args) / "spend.weekly.yaml"
    caps_fold = load_definition_file(fold_path)
    manifest_digest = _resolve_manifest_digest(args)

    proposed_caps_minor = None
    proposal_rationale = None
    if args.proposals:
        try:
            _, proposals = load_proposals_file(args.proposals)
        except PackDefinitionError as exc:
            print(f"capsule guard dry-run: --proposals failed to load ({exc.reason}): {exc}", file=sys.stderr)
            return 1
        proposed_caps_minor = {p["action_class"]: p["proposed_cap_minor"] for p in proposals}
        parts = [f"{cls}={cap}" for cls, cap in sorted(proposed_caps_minor.items())]
        proposal_rationale = f"proposed: {', '.join(parts)}"

    def _build():
        if proposed_caps_minor is not None:
            return build_dry_run_report_with_proposal(
                args.ledger,
                caps_fold=caps_fold,
                proposed_caps_minor=proposed_caps_minor,
                since=since,
                caps_minor=caps_minor,
                proposal_rationale=proposal_rationale,
                operator=args.operator,
                model_note=args.model_note,
                model_id=args.model_id,
                manifest_digest=manifest_digest,
            )
        return build_dry_run_report(
            args.ledger,
            caps_fold=caps_fold,
            since=since,
            caps_minor=caps_minor,
            operator=args.operator,
            model_note=args.model_note,
            model_id=args.model_id,
            manifest_digest=manifest_digest,
        )

    report = _build()

    telemetry = None
    if args.telemetry_opt_in and args.telemetry_endpoint:
        telemetry = TelemetryConfig(opted_in=True, endpoint=args.telemetry_endpoint)

    html, fragment = render_report_html(report, arm=arm, telemetry=telemetry)
    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    url = f"file://{out_path.resolve()}#{fragment}"

    print(f"wrote {out_path}")
    if report.manifest_digest is not None:
        print(f"evaluated under manifest {report.manifest_digest}")
    # Arm A ("guards-only"): the share link is a verify-link suggestion --
    # not printed, matching the report itself never surfacing its evidence
    # chrome. The file above still exists and is still openable; this CLI
    # just doesn't advertise the fragment-carried permalink.
    if args.share and evidence_visible:
        print(url)

    if caps_minor:
        record_guard_configured(arm)
    if report.actions_replayed:
        record_guard_evaluated(arm)
    if evidence_visible and (args.share or args.verify):
        record_evidence_touch(arm)

    if args.verify:
        written_payload = decode_fragment(fragment)
        rebuilt_payload = to_fragment_payload(_build())
        # exclude generated_at: the one field that legitimately differs
        # between two otherwise-identical replays (it's a wall-clock
        # generation timestamp, not derived from the ledger).
        lhs = {k: v for k, v in written_payload.items() if k != "generated_at"}
        rhs = {k: v for k, v in rebuilt_payload.items() if k != "generated_at"}
        if lhs != rhs:
            print("FAIL: replaying the same ledger did not reproduce the same report", file=sys.stderr)
            return 1

        mismatches = []
        for section in written_payload["guards"]:
            for row in section["rows"]:
                for capsule in (row.get("capsule"), row.get("cited_capsule")):
                    if not capsule:
                        continue
                    if compute_capsule_id(capsule) != capsule.get("capsule_id"):
                        mismatches.append(capsule.get("capsule_id"))
        if mismatches:
            # Arm A: "cited record(s)", never the word "capsule" in output.
            noun = "cited capsule(s)" if evidence_visible else "cited record(s)"
            print(f"FAIL: {len(mismatches)} {noun} do not re-verify", file=sys.stderr)
            return 1
        tail = " and every cited capsule verifies" if evidence_visible else ""
        print(f"OK: report is reproducible{tail}")

    return 0


def _cmd_guard_enforce(args: argparse.Namespace) -> int:
    """Records, locally, that this install has moved from dry-run to
    enforce -- nothing more. ``GuardEngine.check(..., dry_run=...)`` is
    already a real parameter your own integration code controls (this CLI
    only ever drives it in dry-run mode via ``guard dry-run``); this
    command does not gate anything itself. It exists so there is a real,
    minimal, honest action to record the M2 ("enforcement-on") telemetry
    fact against, matching the report's own "$ capsule guard enforce" callout.
    """
    from ..telemetry.record import record_enforcement_flip

    arm = packaging.current_arm()
    record_enforcement_flip(arm)
    print(
        "recorded: this install has moved from dry-run to enforce (locally, opt-in telemetry only).\n"
        "This command does not itself gate actions -- wire GuardEngine.check(..., dry_run=False) into "
        "your own integration to actually enforce."
    )
    return 0


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    guard = sub.add_parser("guard", help="guard API: dry-run replay + report (full CLI lands in a later task)")
    guard_sub = guard.add_subparsers(dest="guard_command")
    guard.set_defaults(guard_parser=guard)

    p_dry_run = guard_sub.add_parser(
        "dry-run", help="replay a ledger through the guard checks and emit a self-contained HTML report"
    )
    p_dry_run.add_argument(
        "--ledger", required=True, action="append", help="ledger JSONL file or LedgerStore directory (repeatable)"
    )
    p_dry_run.add_argument(
        "--since", default="7d",
        help="rolling window, e.g. '7d' (anchored to the ledger's own latest record); 'all' for no filter",
    )
    p_dry_run.add_argument(
        "--share", action="store_true",
        help="print the full shareable file://...#<fragment> URL (no-op in the guards-only packaging arm)",
    )
    p_dry_run.add_argument(
        "--verify", action="store_true", help="re-replay and re-verify every cited capsule before exiting"
    )
    p_dry_run.add_argument("--out", default="dry-run-report.html", help="output HTML path (default: dry-run-report.html)")
    p_dry_run.add_argument(
        "--operator", default=None, help="override the displayed operator label (default: the ledger's own)"
    )
    p_dry_run.add_argument(
        "--cap", action="append", default=[], metavar="CLASS=MINOR_UNITS",
        help="per-action-class cap for the caps check, e.g. money.transfer=10000000 (repeatable; omit an action "
        "class to leave it unconfigured -- it will never trigger the caps guard, matching GuardEngine's own default)",
    )
    p_dry_run.add_argument(
        "--model-note", default=None, help="optional pre-written model commentary quote (never generated by this CLI)"
    )
    p_dry_run.add_argument(
        "--model-id", default=None, help="model id the note above was drafted by (required together with --model-note)"
    )
    p_dry_run.add_argument(
        "--fold-file", default=None,
        help="fold definition YAML the caps check evaluates against (default: the built-in "
        "spend.weekly.yaml) -- point this at a pack's own materialized fold, e.g. "
        ".capsule/catalog/folds/payments_safety.yaml, to replay under that pack's caps",
    )
    p_dry_run.add_argument(
        "--proposals", default=None,
        help="proposals YAML file (from `capsule thresholds propose --out`) -- when given, adds a "
        "section showing what would ALSO have been held under the proposed caps, on top of what "
        "--cap already configures (the P2 'conversion moment': what tightening to the proposal "
        "would additionally catch)",
    )
    p_dry_run.add_argument(
        "--dir", help="fold catalog directory the caps check's fold definition lives in (default: built-in catalog, or $CAPSULE_FOLD_DIR)"
    )
    p_dry_run.add_argument(
        "--manifest", help="policy manifest YAML resolved for this replay (default: built-in manifest)"
    )
    p_dry_run.add_argument(
        "--wicket-dir", dest="wicket_dir",
        help="wicket catalog directory the manifest's wicket refs resolve against (default: built-in catalog, or $CAPSULE_WICKET_DIR)",
    )
    p_dry_run.add_argument(
        "--no-manifest", dest="no_manifest", action="store_true",
        help="skip policy-manifest resolution entirely (report carries no manifest_digest)",
    )
    p_dry_run.add_argument(
        "--telemetry-opt-in", action="store_true",
        help="embed a disclosed, anonymous open-beacon in the report for the M6 (viral unit) metric -- "
        "requires --telemetry-endpoint too, and is off by default; see `capsule telemetry status`",
    )
    p_dry_run.add_argument(
        "--telemetry-endpoint", default=env_get("CAPSULE_LEDGER_TELEMETRY_ENDPOINT"),
        help="where the open-beacon above (if enabled) sends its single anonymous event (default: "
        "$CAPSULE_LEDGER_TELEMETRY_ENDPOINT, or none -- with no endpoint the beacon is never embedded)",
    )
    p_dry_run.set_defaults(func=_cmd_guard_dry_run)

    p_enforce = guard_sub.add_parser(
        "enforce", help="record locally that this install has moved from dry-run to enforce (telemetry marker only)"
    )
    p_enforce.set_defaults(func=_cmd_guard_enforce)

    return guard
