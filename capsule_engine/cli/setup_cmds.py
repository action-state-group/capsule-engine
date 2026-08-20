# SPDX-License-Identifier: Apache-2.0
"""``capsule setup {init,observe,propose,confirm,enforce,status}`` (design
§3.2/§6b): the onboarding journey. Distinct from the pre-existing
``capsule init --pack``/``capsule thresholds``/``capsule enforce --pack``
verbs (``init_cmds.py``/``thresholds_cmds.py``/``enforce_cmds.py``), which
install and promote a starter PACK (a bundle of wickets/folds/caps) --
these verbs onboard a COMPILER DECLARATION (``capsule_ledger.compiler`` +
``capsule_ledger.setup``), a different object with no pack involved.
Nested under ``setup`` so neither command group has to rename around the
other.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..envcompat import env_get
from ..guards.action import Action
from ..guards.signing import LocalSigner
from capsule_ledger.ledger import LedgerStore
from ..setup import adapters as setup_adapters
from ..setup import confirm as setup_confirm
from ..setup import enforce as setup_enforce
from ..setup import init as setup_init_mod
from ..setup import observe as setup_observe
from ..setup import propose as setup_propose
from ..setup.declarations import DeclarationStore

__all__ = ["add_parser"]

_KEY_ID_ENV = setup_init_mod.KEY_ID_ENV
_SECRET_ENV = setup_init_mod.SECRET_ENV


def _setup_dir(args: argparse.Namespace) -> Path:
    return Path(args.project_dir) / setup_init_mod.SETUP_DIRNAME


def _ledger_path(args: argparse.Namespace) -> Path:
    return Path(args.ledger) if args.ledger else _setup_dir(args) / setup_init_mod.LEDGER_DIRNAME


def _signer(args: argparse.Namespace) -> LocalSigner:
    key_id = args.key_id or env_get(_KEY_ID_ENV)
    secret_text = args.secret or env_get(_SECRET_ENV)
    if key_id is None or secret_text is None:
        print(
            f"capsule setup {args.setup_command}: signing key required -- pass --key-id/--secret or set "
            f"${_KEY_ID_ENV}/${_SECRET_ENV} (run `capsule setup init` first and export the printed key)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return LocalSigner(key_id=key_id, secret=secret_text.encode("utf-8"))


def _add_common_args(p: argparse.ArgumentParser, *, needs_signer: bool = False) -> None:
    p.add_argument("--project-dir", default=".", help="project directory containing .capsule-setup/ (default: cwd)")
    p.add_argument("--ledger", default=None, help="override the ledger path (default: <project-dir>/.capsule-setup/ledger)")
    if needs_signer:
        p.add_argument("--key-id", default=None, help=f"signing key id (default: ${_KEY_ID_ENV})")
        p.add_argument("--secret", default=None, help=f"signing key secret (default: ${_SECRET_ENV})")
        p.add_argument("--operator", default="local", help="operator identity for recorded capsules (default: 'local')")
        p.add_argument("--developer", default="capsule-setup", help="developer identity for recorded capsules")


# --- init --------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    if args.tenant_id is not None and args.tenants_root is None:
        print("capsule setup init: --tenants-root is required with --tenant-id", file=sys.stderr)
        return 2
    secret = args.secret.encode("utf-8") if args.secret else None
    result = setup_init_mod.setup_init(
        args.project_dir,
        tenant_id=args.tenant_id,
        tenants_root=args.tenants_root,
        key_id=args.key_id,
        secret=secret,
        operator=args.operator,
        developer=args.developer,
    )
    print(f"instance ready: {result.setup_dir}")
    print(f"  ledger:        {result.ledger_dir}")
    print(f"  declarations:  {result.declarations_dir}")
    print(f"  key_id:        {result.key_id}")
    print(f"  key fingerprint: {result.key_fingerprint}")
    if result.generated_secret:
        print("  (signed with a freshly generated key -- not persisted anywhere; export these to reuse it)")
        print(f"    export {_KEY_ID_ENV}={result.key_id}")
        print(f"    export {_SECRET_ENV}={result.secret.decode('utf-8')}")
    print()
    print("next: `capsule setup observe --input <trace.jsonl>` to start recording (zero enforcement, zero declarations yet).")
    return 0


# --- observe -------------------------------------------------------------


def _cmd_observe(args: argparse.Namespace) -> int:
    try:
        info = setup_adapters.adapter_info(args.attach)
    except setup_adapters.UnknownAdapter as exc:
        print(f"capsule setup observe: {exc}", file=sys.stderr)
        print(setup_adapters.describe_adapters(), file=sys.stderr)
        return 2
    if not info.wired:
        print(f"capsule setup observe: adapter {args.attach!r} is not built yet -- {info.note}", file=sys.stderr)
        return 2

    try:
        with open(args.input, encoding="utf-8") as fh:
            raw_events = [json.loads(line) for line in fh if line.strip()]
    except OSError as exc:
        print(f"capsule setup observe: cannot read --input {args.input!r}: {exc}", file=sys.stderr)
        return 2

    signer = _signer(args)
    with LedgerStore(_ledger_path(args)) as ledger:
        recorder = setup_observe.ObserveRecorder(
            ledger=ledger,
            signer=signer,
            operator=args.operator,
            developer=args.developer,
            heartbeat_every=args.heartbeat_every,
        )
        summary = recorder.run(raw_events)

    print()
    print(f"observe: {summary.total_recorded} record(s) written (turns={summary.turns_recorded}, "
          f"dispatches={summary.dispatches_recorded}, offers={summary.offers_recorded}, "
          f"responses={summary.responses_recorded}, confirmations={summary.confirmations_recorded})")
    if summary.unmapped:
        print(f"COULD NOT MAP {len(summary.unmapped)} event(s):", file=sys.stderr)
        for u in summary.unmapped:
            print(f"  [{u.index}] kind={u.kind!r}: {u.reason}", file=sys.stderr)
        return 1
    return 0


# --- propose ---------------------------------------------------------------


def _cmd_propose(args: argparse.Namespace) -> int:
    store = DeclarationStore(_setup_dir(args))
    with LedgerStore(_ledger_path(args)) as ledger:
        proposal_set = setup_propose.propose_from_ledger(ledger)
        drift = setup_propose.diff_against_stored(proposal_set, store) if args.diff else []
        setup_propose.persist_proposals(proposal_set, store)

    print(setup_propose.render_terminal(proposal_set), end="")
    if args.out:
        setup_propose.write_proposals_yaml(args.out, proposal_set)
        print(f"wrote {args.out}")

    exit_code = 0
    if args.diff:
        drifted = [d for d in drift if d.drifted]
        if drifted:
            print(f"DRIFT DETECTED in {len(drifted)} accepted declaration(s):", file=sys.stderr)
            for d in drifted:
                print(f"  {d.outcome_id}: stored={d.stored_digest[:12]}... current={d.current_digest[:12]}...", file=sys.stderr)
            exit_code = 1
        else:
            print(f"propose --diff: {len(drift)} previously-stored outcome(s) re-checked, clean")
    return exit_code


# --- confirm -----------------------------------------------------------


def _cmd_confirm_accept(args: argparse.Namespace) -> int:
    store = DeclarationStore(_setup_dir(args))
    signer = _signer(args)
    with LedgerStore(_ledger_path(args)) as ledger:
        try:
            capsule = setup_confirm.confirm_accept(
                args.outcome_id,
                store=store,
                ledger=ledger,
                signer=signer,
                operator=args.operator,
                developer=args.developer,
                d_prev_digest=args.d_prev_digest,
                replay_report_digest=args.replay_report_digest,
            )
        except (setup_confirm.ConfirmError, KeyError) as exc:
            print(f"capsule setup confirm accept: {exc}", file=sys.stderr)
            return 1
    print(f"T1 accepted {args.outcome_id}: {capsule['capsule_id']}")
    return 0


def _cmd_confirm_census(args: argparse.Namespace) -> int:
    signer = _signer(args)
    with LedgerStore(_ledger_path(args)) as ledger:
        try:
            capsule = setup_confirm.confirm_scope_census(
                document_digest=args.document_digest,
                n=args.n,
                m=args.m,
                review_by=args.review_by,
                ledger=ledger,
                signer=signer,
                operator=args.operator,
                developer=args.developer,
                chain_parent=args.chain_parent,
            )
        except ValueError as exc:
            print(f"capsule setup confirm census: {exc}", file=sys.stderr)
            return 1
    print(f"T2 census recorded: {capsule['capsule_id']} ({args.n} of {args.m}, review by {args.review_by})")
    return 0


def _cmd_confirm_acknowledge_refusal(args: argparse.Namespace) -> int:
    store = DeclarationStore(_setup_dir(args))
    signer = _signer(args)
    with LedgerStore(_ledger_path(args)) as ledger:
        try:
            refusal_capsule, ack_capsule = setup_confirm.confirm_acknowledge_refusal(
                args.outcome_id,
                store=store,
                ledger=ledger,
                signer=signer,
                operator=args.operator,
                developer=args.developer,
                acknowledged_by=args.acknowledged_by,
            )
        except (setup_confirm.ConfirmError, KeyError) as exc:
            print(f"capsule setup confirm acknowledge-refusal: {exc}", file=sys.stderr)
            return 1
    print(f"T4 refusal acknowledged for {args.outcome_id}: refusal={refusal_capsule['capsule_id']} ack={ack_capsule['capsule_id']}")
    return 0


# --- enforce -----------------------------------------------------------


def _cmd_enforce_shadow(args: argparse.Namespace) -> int:
    store = DeclarationStore(_setup_dir(args))
    with LedgerStore(_ledger_path(args)) as ledger:
        try:
            stored = store.load(args.outcome_id)
        except KeyError:
            print(f"capsule setup enforce shadow: no such outcome {args.outcome_id!r}", file=sys.stderr)
            return 1
        if not isinstance(stored.candidate, setup_propose.AttainmentCandidate):
            print(f"capsule setup enforce shadow: {args.outcome_id!r} is not forward-checkable (kind={stored.candidate.kind!r})", file=sys.stderr)
            return 1
        actions = setup_enforce.historical_actions_for(ledger, stored.candidate.action_class)
        try:
            report = setup_enforce.run_shadow_report(args.outcome_id, actions, store=store)
        except setup_enforce.EnforceError as exc:
            print(f"capsule setup enforce shadow: {exc}", file=sys.stderr)
            return 1
    print(f"shadow report for {args.outcome_id}: {report.total} historical action(s), {report.would_fail_count} would have been refused")
    for r in report.results:
        if not r.would_pass:
            print(f"  WOULD REFUSE: verb={r.action.verb!r} -- {r.outcome.constraint.reason}")
    if args.out:
        payload = {
            "outcome_id": report.outcome_id,
            "plan_digest": report.plan_digest,
            "total": report.total,
            "would_fail": report.would_fail_count,
        }
        Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.out}")
    return 0


def _cmd_enforce_promote(args: argparse.Namespace) -> int:
    store = DeclarationStore(_setup_dir(args))
    enforce_state = setup_enforce.EnforceStateStore(_setup_dir(args))
    signer = _signer(args)
    with LedgerStore(_ledger_path(args)) as ledger:
        try:
            stored = store.load(args.outcome_id)
            actions = setup_enforce.historical_actions_for(ledger, stored.candidate.action_class)
            report = setup_enforce.run_shadow_report(args.outcome_id, actions, store=store)
            capsule = setup_enforce.promote(
                args.outcome_id,
                shadow_report=report,
                store=store,
                enforce_state=enforce_state,
                ledger=ledger,
                signer=signer,
                operator=args.operator,
                developer=args.developer,
            )
        except (setup_enforce.EnforceError, KeyError) as exc:
            print(f"capsule setup enforce promote: {exc}", file=sys.stderr)
            return 1
    print(f"promoted {args.outcome_id} to enforce (shadow: {report.total} total, {report.would_fail_count} would-fail): {capsule['capsule_id']}")
    return 0


def _cmd_enforce_dispatch(args: argparse.Namespace) -> int:
    store = DeclarationStore(_setup_dir(args))
    enforce_state = setup_enforce.EnforceStateStore(_setup_dir(args))
    signer = _signer(args)
    action = Action(verb=args.verb, operator=args.operator, developer=args.developer, target=args.target)
    with LedgerStore(_ledger_path(args)) as ledger:
        try:
            result = setup_enforce.dispatch(
                args.outcome_id,
                action,
                store=store,
                enforce_state=enforce_state,
                ledger=ledger,
                signer=signer,
                setup_dir=_setup_dir(args),
            )
        except (setup_enforce.EnforceError, KeyError) as exc:
            print(f"capsule setup enforce dispatch: {exc}", file=sys.stderr)
            return 1
    if result.passed:
        print(f"ALLOW {result.capsule['capsule_id']}")
        return 0
    print(f"DENY {result.capsule['capsule_id']}")
    print(f"reproduce: {result.reproduction_command}")
    return 1


# --- status --------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> int:
    setup_dir = _setup_dir(args)
    store = DeclarationStore(setup_dir)
    enforce_state = setup_enforce.EnforceStateStore(setup_dir)
    ledger_path = _ledger_path(args)
    if not ledger_path.exists():
        print(f"no instance at {setup_dir} -- run `capsule setup init` first")
        return 1
    with LedgerStore(ledger_path) as ledger:
        record_count = sum(1 for _ in ledger.scan())
    print(f"instance: {setup_dir}")
    print(f"  ledger records: {record_count}")
    outcome_ids = store.list_ids()
    if not outcome_ids:
        print("  no declarations proposed yet")
        return 0
    print("  declarations:")
    for outcome_id in outcome_ids:
        stored = store.load(outcome_id)
        mode = enforce_state.mode(outcome_id) if stored.acceptance_state == "accepted" else "-"
        print(f"    {outcome_id}: {stored.acceptance_state} (forward={stored.forward_verdict}, backward={stored.backward_verdict}, enforce={mode})")
    return 0


# --- wiring --------------------------------------------------------------


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    setup = sub.add_parser("setup", help="onboard a compiler declaration: init/observe/propose/confirm/enforce")
    setup_sub = setup.add_subparsers(dest="setup_command")
    setup.set_defaults(setup_parser=setup)

    p_init = setup_sub.add_parser("init", help="stand up an instance (ledger + signing key); zero decisions")
    _add_common_args(p_init)
    p_init.add_argument("--tenant-id", default=None, help="provision this instance per-tenant (Pattern A) instead of a single local instance")
    p_init.add_argument("--tenants-root", default=None, help="tenants root directory (required with --tenant-id)")
    p_init.add_argument("--key-id", default=None, help=f"signing key id (default: ${_KEY_ID_ENV})")
    p_init.add_argument("--secret", default=None, help=f"signing key secret (default: ${_SECRET_ENV}, or freshly generated)")
    p_init.add_argument("--operator", default="local", help="operator identity (default: 'local')")
    p_init.add_argument("--developer", default="capsule-setup-init", help="developer identity")
    p_init.set_defaults(func=_cmd_init)

    p_observe = setup_sub.add_parser("observe", help="dry-run mode: record everything at the emit layer, enforce/declare nothing")
    _add_common_args(p_observe, needs_signer=True)
    p_observe.add_argument("--input", required=True, help="a JSONL raw-event trace file (one event dict per line)")
    p_observe.add_argument(
        "--attach", default="conversation-log", choices=sorted(setup_adapters.ADAPTER_KINDS),
        help="which onboarding path this run is wired to (default: conversation-log)",
    )
    p_observe.add_argument("--heartbeat-every", type=int, default=10, help="print a progress line every N events (0 disables)")
    p_observe.set_defaults(func=_cmd_observe)

    p_propose = setup_sub.add_parser("propose", help="draft candidate declarations from observed traces")
    _add_common_args(p_propose)
    p_propose.add_argument("--out", default=None, help="write the diffable proposals.yaml artifact here")
    p_propose.add_argument("--diff", action="store_true", help="also diff every accepted outcome_id against a fresh recompile (drift check)")
    p_propose.set_defaults(func=_cmd_propose)

    p_confirm = setup_sub.add_parser("confirm", help="the human touchpoints: T1 accept, T2 census, T4 refusal acknowledgment")
    confirm_sub = p_confirm.add_subparsers(dest="setup_confirm_command", required=True)
    p_confirm.set_defaults(setup_confirm_parser=p_confirm)

    p_accept = confirm_sub.add_parser("accept", help="T1: freeze a proposed declaration's compilation record")
    _add_common_args(p_accept, needs_signer=True)
    p_accept.add_argument("--outcome-id", required=True)
    p_accept.add_argument("--d-prev-digest", default=None, help="the declaration this one replaces, if any (design lineage)")
    p_accept.add_argument("--replay-report-digest", default=None, help="the replay-before-merge report that justified this change, if any")
    p_accept.set_defaults(func=_cmd_confirm_accept)

    p_census = confirm_sub.add_parser("census", help="T2: sign off on N of M outcomes in a document")
    _add_common_args(p_census, needs_signer=True)
    p_census.add_argument("--document-digest", required=True)
    p_census.add_argument("--n", type=int, required=True)
    p_census.add_argument("--m", type=int, required=True)
    p_census.add_argument("--review-by", required=True, help="ISO-8601 date/datetime this census must be re-run by")
    p_census.add_argument("--chain-parent", default=None, help="the prior census this one supersedes, if any")
    p_census.set_defaults(func=_cmd_confirm_census)

    p_ack = confirm_sub.add_parser("acknowledge-refusal", help="T4: a human sees and accepts a REFUSED verdict")
    _add_common_args(p_ack, needs_signer=True)
    p_ack.add_argument("--outcome-id", required=True)
    p_ack.add_argument("--acknowledged-by", required=True, help="the human's own identity")
    p_ack.set_defaults(func=_cmd_confirm_acknowledge_refusal)

    p_enforce = setup_sub.add_parser("enforce", help="per-check promotion: shadow-first, never bulk")
    enforce_sub = p_enforce.add_subparsers(dest="setup_enforce_command", required=True)
    p_enforce.set_defaults(setup_enforce_parser=p_enforce)

    p_shadow = enforce_sub.add_parser("shadow", help="replay-before-merge: what would this outcome's plan have refused")
    _add_common_args(p_shadow)
    p_shadow.add_argument("--outcome-id", required=True)
    p_shadow.add_argument("--out", default=None, help="write a JSON summary here (consumed by the guard-check composite action)")
    p_shadow.set_defaults(func=_cmd_enforce_shadow)

    p_promote = enforce_sub.add_parser("promote", help="T3: promote one accepted outcome from shadow to enforce, after a shadow report")
    _add_common_args(p_promote, needs_signer=True)
    p_promote.add_argument("--outcome-id", required=True)
    p_promote.set_defaults(func=_cmd_enforce_promote)

    p_dispatch = enforce_sub.add_parser("dispatch", help="check one live action against a promoted outcome's plan")
    _add_common_args(p_dispatch, needs_signer=True)
    p_dispatch.add_argument("--outcome-id", required=True)
    p_dispatch.add_argument("--verb", required=True)
    p_dispatch.add_argument("--target", default=None)
    p_dispatch.set_defaults(func=_cmd_enforce_dispatch)

    p_status = setup_sub.add_parser("status", help="instance + declaration summary (also serves observe's live-heartbeat visibility)")
    _add_common_args(p_status)
    p_status.set_defaults(func=_cmd_status)

    return setup
