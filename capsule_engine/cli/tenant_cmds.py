# SPDX-License-Identifier: Apache-2.0
"""``capsule tenant`` verbs: provision/upgrade/list engine-instance-per-tenant
deployments (``capsule_ledger.tenants``) -- the "run one whole engine
instance per tenant" pattern for a caller embedding this package for many
customers. See ``docs/tenant-provisioning.md`` for the layout and the
isolation rationale.

  init    -- provision a brand-new tenant: fresh ledger dir, this tenant's
             own pinned manifest, first activation capsule. Refuses if the
             tenant already has anything on disk.
  upgrade -- re-materialize an existing tenant's manifest (e.g. after a
             pack version bump) and record a new activation epoch, only if
             the manifest actually changed. Refuses if the tenant was never
             initialized.
  list    -- print every provisioned tenant's id/manifest/key under a
             tenants-root, from their own tenant.json files.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..envcompat import env_get
from ..packs import PackDefinitionError, load_pack_dir
from ..tenants import TenantProvisionError, init_tenant, list_tenants, upgrade_tenant

__all__ = ["add_parser"]

BUILTIN_PACK_CATALOG_DIR = Path(__file__).resolve().parent.parent / "packs" / "catalog"

_KEY_ID_ENV = "CAPSULE_MCP_SIGNING_KEY_ID"
_SECRET_ENV = "CAPSULE_MCP_SIGNING_SECRET"


def _available_packs(catalog_dir: Path) -> list[str]:
    if not catalog_dir.is_dir():
        return []
    return sorted(p.name for p in catalog_dir.iterdir() if (p / "pack.yaml").is_file())


def _load_pack(args: argparse.Namespace, *, verb: str):
    """Shared ``--pack <name>`` resolution, same lookup ``capsule init``
    uses -- returns ``None`` (default manifest) when ``--pack`` is omitted,
    or a ``(pack, None)``/``(None, error_code)`` pair on failure."""
    if not args.pack:
        return None, None
    catalog_dir = Path(args.pack_catalog_dir) if args.pack_catalog_dir else BUILTIN_PACK_CATALOG_DIR
    pack_dir = catalog_dir / args.pack
    if not (pack_dir / "pack.yaml").is_file():
        available = _available_packs(catalog_dir)
        print(f"capsule tenant {verb}: no pack named {args.pack!r} in {catalog_dir}", file=sys.stderr)
        print(f"  available packs: {available or '<none>'}", file=sys.stderr)
        return None, 2
    try:
        return load_pack_dir(pack_dir), None
    except PackDefinitionError as exc:
        print(f"capsule tenant {verb}: pack {args.pack!r} failed to load ({exc.reason}): {exc}", file=sys.stderr)
        return None, 1


def _cmd_tenant_init(args: argparse.Namespace) -> int:
    pack, error_rc = _load_pack(args, verb="init")
    if error_rc is not None:
        return error_rc

    key_id = args.key_id or env_get(_KEY_ID_ENV)
    secret_text = args.secret or env_get(_SECRET_ENV)
    secret = secret_text.encode("utf-8") if secret_text is not None else None

    try:
        result = init_tenant(
            args.tenants_root,
            args.tenant_id,
            pack=pack,
            key_id=key_id,
            secret=secret,
            operator=args.operator,
            developer=args.developer,
        )
    except TenantProvisionError as exc:
        print(f"capsule tenant init: {exc.reason}: {exc}", file=sys.stderr)
        return 1

    print(f"provisioned tenant {args.tenant_id!r} at {result.layout.root}")
    print(f"  ledger:          {result.layout.ledger_dir}")
    print(f"  manifest:        {result.layout.manifest_path}")
    print(f"  manifest id:     {result.manifest_id}")
    print(f"  manifest digest: {result.manifest_digest}")
    print(f"  activation:      {result.activation_capsule_id}")
    print(f"  key id:          {result.key_id}")
    if result.generated_secret:
        print()
        print(
            "signing secret (shown once -- this command does not persist it anywhere; "
            "store it in this tenant's own secret manager, not this repo or terminal history):"
        )
        print(f"  {result.secret.decode('utf-8')}")
    return 0


def _cmd_tenant_upgrade(args: argparse.Namespace) -> int:
    pack, error_rc = _load_pack(args, verb="upgrade")
    if error_rc is not None:
        return error_rc

    key_id = args.key_id or env_get(_KEY_ID_ENV)
    secret_text = args.secret or env_get(_SECRET_ENV)
    if not key_id or not secret_text:
        print(
            f"capsule tenant upgrade: --key-id/--secret are required (or set ${_KEY_ID_ENV}/${_SECRET_ENV}) -- "
            "this must be the tenant's current live signing key, not a fresh one",
            file=sys.stderr,
        )
        return 2

    try:
        result = upgrade_tenant(
            args.tenants_root,
            args.tenant_id,
            pack=pack,
            key_id=key_id,
            secret=secret_text.encode("utf-8"),
            operator=args.operator,
            developer=args.developer,
        )
    except TenantProvisionError as exc:
        print(f"capsule tenant upgrade: {exc.reason}: {exc}", file=sys.stderr)
        return 1

    if not result.changed:
        print(f"tenant {args.tenant_id!r}: manifest unchanged ({result.manifest_digest}), nothing to activate")
        return 0

    print(f"upgraded tenant {args.tenant_id!r} at {result.layout.root}")
    print(f"  manifest id:     {result.manifest_id}")
    print(f"  manifest digest: {result.manifest_digest}")
    print(f"  activation:      {result.activation_capsule_id}")
    return 0


def _cmd_tenant_list(args: argparse.Namespace) -> int:
    tenants = list_tenants(args.tenants_root)
    if not tenants:
        print(f"no provisioned tenants under {args.tenants_root}")
        return 0
    for tenant in tenants:
        print(
            f"{tenant['tenant_id']}\t{tenant['manifest_id']}\t{tenant['manifest_digest'][:16]}…\t"
            f"key={tenant['key_id']}"
        )
    return 0


def _add_pack_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--pack", default=None, help="starter pack name to install for this tenant (default: none, built-in default manifest)"
    )
    p.add_argument(
        "--pack-catalog-dir", default=None, help="override the built-in pack catalog directory (mainly for tests)"
    )


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    tenant = sub.add_parser(
        "tenant", help="engine-instance-per-tenant provisioning: init/upgrade/list (see docs/tenant-provisioning.md)"
    )
    tenant_sub = tenant.add_subparsers(dest="tenant_command")
    tenant.set_defaults(tenant_parser=tenant)

    p_init = tenant_sub.add_parser(
        "init", help="provision a brand-new tenant instance (ledger dir + manifest + first activation)"
    )
    p_init.add_argument("--tenants-root", required=True, help="directory holding one subdirectory per tenant")
    p_init.add_argument("--tenant-id", required=True, help="this tenant's id (becomes a directory name -- lowercase, digits, hyphens)")
    _add_pack_args(p_init)
    p_init.add_argument("--operator", default="local", help="operator identity for the first activation capsule (default: 'local')")
    p_init.add_argument("--developer", default="capsule-tenant-init", help="developer identity for the first activation capsule")
    p_init.add_argument("--key-id", default=None, help=f"this tenant's signing key id (default: '<tenant-id>-signing-key', or ${_KEY_ID_ENV})")
    p_init.add_argument("--secret", default=None, help=f"this tenant's signing key secret (default: ${_SECRET_ENV}, or freshly generated)")
    p_init.set_defaults(func=_cmd_tenant_init)

    p_upgrade = tenant_sub.add_parser(
        "upgrade", help="re-materialize an existing tenant's manifest and record a new activation epoch if it changed"
    )
    p_upgrade.add_argument("--tenants-root", required=True, help="directory holding one subdirectory per tenant")
    p_upgrade.add_argument("--tenant-id", required=True, help="this tenant's id")
    _add_pack_args(p_upgrade)
    p_upgrade.add_argument("--operator", default="local", help="operator identity for the new activation capsule (default: 'local')")
    p_upgrade.add_argument("--developer", default="capsule-tenant-upgrade", help="developer identity for the new activation capsule")
    p_upgrade.add_argument("--key-id", default=None, help=f"this tenant's CURRENT live signing key id (default: ${_KEY_ID_ENV}) -- required")
    p_upgrade.add_argument("--secret", default=None, help=f"this tenant's CURRENT live signing key secret (default: ${_SECRET_ENV}) -- required")
    p_upgrade.set_defaults(func=_cmd_tenant_upgrade)

    p_list = tenant_sub.add_parser("list", help="list every provisioned tenant under a tenants-root")
    p_list.add_argument("--tenants-root", required=True, help="directory holding one subdirectory per tenant")
    p_list.set_defaults(func=_cmd_tenant_list)

    return tenant
