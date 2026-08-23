# SPDX-License-Identifier: Apache-2.0
"""``capsule init --pack <name>``: install a starter pack in observe mode.

Materializes the pack's constraints and folds into ``<project-dir>/.capsule/``,
writes the policy manifest fragment that cites them plus the pack itself (by
digest, mode ``observe``) -- so which pack version is in force is provable --
and, when ``--ledger`` is given, records that installation as a signed
``policy_manifest_activated`` event capsule (``packs/install.py``'s
``record_pack_activation``).

When ``--pins`` is given, every artifact this install would trust (the pack
itself, plus every fold it ships) is verified against that pins file before
anything is installed -- fail closed on a missing or mismatched pin,
nothing materialized, nothing recorded (``packs/pins.py``). This stands in
for a live ``capsule-registry`` fetch, which does not exist yet
(registry-architecture-and-namespace-2026-08-10.md §6); the verification
gate's shape does not change when that swap happens later. Without
``--pins``, this command prints each artifact's own digest so a first
install can seed a pins file for the next one.

This command does not run anything against live traffic itself -- it hands
back the manifest a caller's own integration resolves and builds a
``GuardEngine`` from (``packs.build_engine``), calling
``GuardEngine.check(..., dry_run=True)`` for as long as the pack stays in
observe mode. "Observe mode" is a property of the installed manifest
(``PackRef.mode``), not something this command enforces at runtime.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from capsule_ledger.ledger import LedgerStore

from ..envcompat import env_get
from ..guards.signing import LocalSigner
from ..packs import (
    PackDefinitionError,
    RegistryPinError,
    install_pack,
    load_pack_dir,
    load_pins_file,
    record_pack_activation,
    verify_pins,
)

__all__ = ["add_parser"]

BUILTIN_PACK_CATALOG_DIR = Path(__file__).resolve().parent.parent / "packs" / "catalog"

_KEY_ID_ENV = "CAPSULE_MCP_SIGNING_KEY_ID"
_SECRET_ENV = "CAPSULE_MCP_SIGNING_SECRET"


def _available_packs(catalog_dir: Path) -> list[str]:
    if not catalog_dir.is_dir():
        return []
    return sorted(p.name for p in catalog_dir.iterdir() if (p / "pack.yaml").is_file())


def _cmd_init(args: argparse.Namespace) -> int:
    catalog_dir = Path(args.pack_catalog_dir) if args.pack_catalog_dir else BUILTIN_PACK_CATALOG_DIR
    pack_dir = catalog_dir / args.pack

    if not (pack_dir / "pack.yaml").is_file():
        available = _available_packs(catalog_dir)
        print(f"capsule init: no pack named {args.pack!r} in {catalog_dir}", file=sys.stderr)
        print(f"  available packs: {available or '<none>'}", file=sys.stderr)
        return 2

    try:
        pack = load_pack_dir(pack_dir)
    except PackDefinitionError as exc:
        print(f"capsule init: pack {args.pack!r} failed to load ({exc.reason}): {exc}", file=sys.stderr)
        return 1

    if args.pins:
        try:
            pins = load_pins_file(args.pins)
            verify_pins(pack, pins)
        except RegistryPinError as exc:
            print(f"capsule init: registry-pin verification failed ({exc.reason}): {exc}", file=sys.stderr)
            print("nothing installed -- fail closed on a missing or mismatched pin.", file=sys.stderr)
            return 1
        print(f"registry-pin verification passed against {args.pins} ({1 + len(pack.folds)} artifact(s))")

    project_dir = Path(args.project_dir)
    installed = install_pack(pack, project_dir=project_dir, mode="observe")

    print(f"installed {pack.pack_id} in observe mode")
    print(f"  manifest:      {installed.manifest_path}")
    print(f"  manifest id:   {installed.manifest.manifest_id}")
    print(f"  manifest digest: {installed.resolved.manifest_digest}")
    print(f"  fold catalog:  {installed.fold_catalog_dir}")
    print(f"  wicket catalog: {installed.wicket_catalog_dir}")
    print(f"  obligations:   {', '.join(o.id for o in pack.obligations)}")
    if not args.pins:
        print("  artifact digests (seed a pins file with these to verify future installs with --pins):")
        print(f"    {pack.pack_id}: {pack.definition_digest()}")
        for fold in pack.folds:
            print(f"    {fold.fold_id}: {fold.definition_digest()}")
    print(
        "observe mode: every action this pack governs will be recorded with its would-be verdict "
        "(allow/deny/escalate) -- nothing is enforced until a human runs `capsule enforce --pack "
        f"{args.pack}` (not yet built)."
    )

    if not args.ledger:
        print()
        print("no --ledger given: manifest written to disk only, no activation capsule recorded")
        return 0

    key_id = args.key_id or env_get(_KEY_ID_ENV)
    secret_text = args.secret or env_get(_SECRET_ENV)
    generated_secret = secret_text is None
    if key_id is None:
        key_id = f"{args.operator}-pack-install-key"
    if secret_text is None:
        secret_text = secrets.token_hex(32)
    signer = LocalSigner(key_id=key_id, secret=secret_text.encode("utf-8"))

    ledger = LedgerStore(args.ledger)
    try:
        capsule = record_pack_activation(
            installed, ledger=ledger, operator=args.operator, developer=args.developer, signer=signer
        )
    finally:
        ledger.close()

    print()
    print(f"activation recorded: {capsule['capsule_id']}")
    if generated_secret:
        print("(signed with a freshly generated key -- not persisted anywhere; pass --key-id/--secret, or set "
              f"${_KEY_ID_ENV}/${_SECRET_ENV}, to reuse a stable key next time)")
        print(f"  export {_KEY_ID_ENV}={key_id}")
        print(f"  export {_SECRET_ENV}={secret_text}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p_init = sub.add_parser("init", help="install a starter pack in observe mode (records, enforces nothing)")
    p_init.add_argument("--pack", required=True, help="pack name, e.g. payments-safety (built-in catalog)")
    p_init.add_argument(
        "--project-dir", default=".", help="where to materialize .capsule/ (catalogs + manifest); default: cwd"
    )
    p_init.add_argument(
        "--pack-catalog-dir", default=None, help="override the built-in pack catalog directory (mainly for tests)"
    )
    p_init.add_argument(
        "--pins", default=None,
        help="pins file (artifact id -> 64-hex digest) to verify the pack and its folds against before "
        "installing; fail closed on a missing or mismatched pin (default: none, no verification -- "
        "this command still prints each artifact's digest so you can seed one)",
    )
    p_init.add_argument(
        "--ledger", default=None,
        help="ledger store directory to record this install's activation capsule into (default: none, manifest "
        "is still written to disk)",
    )
    p_init.add_argument("--operator", default="local", help="operator identity for the activation capsule (default: 'local')")
    p_init.add_argument(
        "--developer", default="capsule-init-tool", help="developer identity for the activation capsule"
    )
    p_init.add_argument("--key-id", default=None, help=f"signing key id for the activation capsule (default: ${_KEY_ID_ENV})")
    p_init.add_argument("--secret", default=None, help=f"signing key secret (default: ${_SECRET_ENV}, or freshly generated)")
    p_init.set_defaults(func=_cmd_init)
    return p_init
