# SPDX-License-Identifier: Apache-2.0
"""``capsule guard`` verbs: dry-run replay + JSON report (moved
here, matching ``fold_cmds.py``'s per-verb-module convention, so ``main.py``
stays a thin dispatcher), plus ``guard enforce`` -- a minimal, honest local
marker command (see ``_cmd_guard_enforce``'s own docstring for exactly what
it does and doesn't do).

Every guard-dry-run invocation here checks the current packaging arm
(``capsule_engine.packaging``) and, in "guards-only", rewords/suppresses this
command's own stdout so it never prints a capsule id -- see this module's
inline comments at each print site.
"""
from __future__ import annotations

import argparse
import json
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


def _resolve_manifest(args: argparse.Namespace, *, strict: bool):
    """The policy manifest that governs this replay (``--manifest``, default
    the built-in one), resolved against the fold catalog (``_catalog_dir``)
    and the wicket catalog (``--wicket-dir``). ``strict``: one that does not
    resolve is an error (``None``, after saying why) rather than a warning."""
    from ..policy import PolicyManifestError, load_manifest_file, resolve_manifest

    manifest_path = Path(args.manifest) if getattr(args, "manifest", None) else DEFAULT_MANIFEST_PATH
    try:
        manifest = load_manifest_file(manifest_path)
        return resolve_manifest(manifest, fold_catalog_dir=_catalog_dir(args), wicket_catalog_dir=_wicket_dir(args))
    except PolicyManifestError as exc:
        if strict:
            print(f"capsule guard dry-run: policy manifest {manifest_path} did not resolve ({exc.reason}): {exc}",
                  file=sys.stderr)
        else:
            print(f"warning: policy manifest {manifest_path} did not resolve ({exc.reason}: {exc}); "
                  "proceeding without a manifest citation", file=sys.stderr)
        return None


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
    resolved = _resolve_manifest(args, strict=False)
    return resolved.manifest_digest if resolved is not None else None


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


def _report_payload(report, *, with_capsules: bool) -> dict:
    """The report as written to ``--out``. The sealed capsules in it are the
    shareable part: kept only with ``--share`` (refused earlier when one is
    local-only), otherwise the rows carry no capsule bodies."""
    from ..report import to_payload

    payload = to_payload(report)
    if not with_capsules:
        for section in payload["guards"]:
            for row in section["rows"]:
                row["capsule"] = None
                row["cited_capsule"] = None
    return payload


def _cmd_guard_dry_run(args: argparse.Namespace) -> int:
    from agent_action_capsule import compute_capsule_id

    from ..folds.loader import load_definition_file
    from ..guards.capsule import local_only_refusal
    from ..packs import PackDefinitionError, load_proposals_file
    from ..report import build_dry_run_report, build_dry_run_report_with_proposal
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
    per_action_minor = None
    per_action_reads = None
    if getattr(args, "caps_from_manifest", False):
        # The resolved manifest's own caps wicket: its window and per-action
        # limits, and the fold it names. A --cap still sets a window limit.
        if getattr(args, "no_manifest", False):
            print("--caps-from-manifest needs a manifest: drop --no-manifest", file=sys.stderr)
            return 1
        resolved = _resolve_manifest(args, strict=True)
        if resolved is None:
            return 1
        if not resolved.caps_minor():
            print("capsule guard dry-run: --caps-from-manifest: the manifest configures no caps limits", file=sys.stderr)
            return 1
        caps_minor = {**resolved.caps_minor(), **caps_minor}
        per_action_minor = resolved.per_action_minor() or None
        per_action_reads = resolved.per_action_reads()
        if not args.fold_file and resolved.caps_fold() is not None:
            caps_fold = resolved.caps_fold()
        manifest_digest = resolved.manifest_digest
    else:
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
                args.store,
                caps_fold=caps_fold,
                proposed_caps_minor=proposed_caps_minor,
                since=since,
                caps_minor=caps_minor,
                proposal_rationale=proposal_rationale,
                operator=args.operator,
                model_note=args.model_note,
                model_id=args.model_id,
                manifest_digest=manifest_digest,
                per_action_minor=per_action_minor,
                per_action_reads=per_action_reads,
            )
        return build_dry_run_report(
            args.store,
            caps_fold=caps_fold,
            since=since,
            caps_minor=caps_minor,
            operator=args.operator,
            model_note=args.model_note,
            model_id=args.model_id,
            manifest_digest=manifest_digest,
            per_action_minor=per_action_minor,
            per_action_reads=per_action_reads,
        )

    report = _build()

    # The printed link carries every row's decision capsule and the capsule
    # it cites; one holding a local-only field must not leave this machine.
    if args.share and evidence_visible:
        shared = [c for section in report.guards for row in section.rows for c in (row.capsule, row.cited_capsule) if c]
        refusal = local_only_refusal(shared)
        if refusal is not None:
            print(f"capsule guard dry-run --share: refused: {refusal}; rerun without --share", file=sys.stderr)
            return 1

    with_capsules = bool(args.share and evidence_visible)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(_report_payload(report, with_capsules=with_capsules), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {out_path}")
    if report.manifest_digest is not None:
        print(f"evaluated under manifest {report.manifest_digest}")

    if caps_minor:
        record_guard_configured(arm)
    if report.actions_replayed:
        record_guard_evaluated(arm)
    if evidence_visible and (args.share or args.verify):
        record_evidence_touch(arm)

    if args.verify:
        # --verify attests the file itself: it is read back and compared to a
        # rebuild. generated_at is excluded: the one field that legitimately
        # differs between two otherwise-identical replays (a wall-clock stamp).
        written = json.loads(out_path.read_text(encoding="utf-8"))
        lhs = {k: v for k, v in written.items() if k != "generated_at"}
        rebuilt = _report_payload(_build(), with_capsules=with_capsules)
        rhs = {k: v for k, v in rebuilt.items() if k != "generated_at"}
        if lhs != rhs:
            print("FAIL: replaying the same records did not reproduce the report written", file=sys.stderr)
            return 1

        mismatches = []
        for section in written["guards"]:
            for row in section["rows"]:
                for capsule in (row["capsule"], row["cited_capsule"]):
                    if not capsule:
                        continue
                    if compute_capsule_id(capsule) != capsule.get("capsule_id"):
                        mismatches.append(capsule.get("capsule_id"))
        if mismatches:
            # Arm A: "cited record(s)", never the word "capsule" in output.
            noun = "cited capsule(s)" if evidence_visible else "cited record(s)"
            print(f"FAIL: {len(mismatches)} {noun} do not re-verify", file=sys.stderr)
            return 1
        tail = " and every cited capsule verifies" if with_capsules else ""
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
    guard = sub.add_parser("guard", help="guard API: dry-run replay + report")
    guard_sub = guard.add_subparsers(dest="guard_command")
    guard.set_defaults(guard_parser=guard)

    p_dry_run = guard_sub.add_parser(
        "dry-run", help="replay stored records through the guard checks and write a JSON report"
    )
    p_dry_run.add_argument(
        "--store", required=True, action="append",
        help="store directory, JSONL file of capsules or capsule bundle (repeatable)",
    )
    p_dry_run.add_argument(
        "--since", default="7d",
        help="rolling window, e.g. '7d' (anchored to the records' own latest); 'all' for no filter",
    )
    p_dry_run.add_argument(
        "--share", action="store_true",
        help="include the sealed capsules in the report file (refused when one is local-only; no-op in the guards-only packaging arm)",
    )
    p_dry_run.add_argument(
        "--verify", action="store_true", help="re-replay and re-verify every cited capsule before exiting"
    )
    p_dry_run.add_argument("--out", default="dry-run-report.json", help="output JSON path (default: dry-run-report.json)")
    p_dry_run.add_argument(
        "--operator", default=None, help="override the displayed operator label (default: the records' own)"
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
        "--caps-from-manifest", dest="caps_from_manifest", action="store_true",
        help="apply the resolved manifest's caps wicket to the replay: its window and per-action limits "
        "(e.g. caps/3.0.0) and the fold it names; --cap still sets a window limit (default: off, only --cap limits apply)",
    )
    p_dry_run.add_argument(
        "--no-manifest", dest="no_manifest", action="store_true",
        help="skip policy-manifest resolution entirely (report carries no manifest_digest)",
    )
    p_dry_run.set_defaults(func=_cmd_guard_dry_run)

    p_enforce = guard_sub.add_parser(
        "enforce", help="record locally that this install has moved from dry-run to enforce (telemetry marker only)"
    )
    p_enforce.set_defaults(func=_cmd_guard_enforce)

    return guard
