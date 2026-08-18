# SPDX-License-Identifier: Apache-2.0
"""Engine-instance-per-tenant provisioning: one physically-separate ledger
dir + policy manifest + signing-key identity per tenant, under a shared
``<tenants-root>/<tenant-id>/`` layout.

This is deliberately NOT a multi-tenant ledger process. The OSS engine
(``LedgerStore``/``GuardEngine``) is single-org by scope; the strongest
isolation story for a caller embedding it for many customers is to run one
whole engine instance per tenant -- separate files, separate keys, so a
compromise or a bug in one tenant's instance can never cross into another's
(see ``docs/tenant-provisioning.md``). This module is the shared logic
behind ``capsule tenant init``/``upgrade``/``list`` (``cli/tenant_cmds.py``):
templating that instantiation, not a new kind of engine.

Layout, under ``<tenants-root>/<tenant_id>/``::

    ledger/                    a LedgerStore root (segments/, index.sqlite3)
    .capsule/policy/manifest.yaml   this tenant's own pinned policy manifest
    .capsule/catalog/...        materialized fold/wicket defs, pack installs only
    tenant.json                 provisioning metadata -- never the signing secret

Signing secrets are never written to disk anywhere in this module, matching
every other write path in this package (``guards/signing.py``): a freshly
generated secret is handed back to the caller once, for the caller to place
in whatever secret store the tenant's own process/environment uses.
"""
from __future__ import annotations

import json
import re
import secrets as secrets_mod
from dataclasses import dataclass
from pathlib import Path

import yaml

from .guards.signing import LocalSigner, key_fingerprint
from .ledger import LedgerStore
from .packs import PackDefinition, install_pack
from .packs.install import CATALOG_DIRNAME
from .policy import (
    Manifest,
    build_manifest_activation_capsule,
    find_latest_activation,
    load_manifest_file,
    resolve_manifest,
)

__all__ = [
    "TenantProvisionError",
    "TenantLayout",
    "InitResult",
    "UpgradeResult",
    "TENANT_ID_RE",
    "tenant_layout",
    "init_tenant",
    "upgrade_tenant",
    "list_tenants",
]

_PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST_PATH = _PACKAGE_ROOT / "policy" / "catalog_defs" / "default.yaml"
DEFAULT_FOLD_DIR = _PACKAGE_ROOT / "folds" / "catalog_defs"
DEFAULT_WICKET_DIR = _PACKAGE_ROOT / "guards" / "wickets" / "catalog_defs"

LEDGER_DIRNAME = "ledger"
TENANT_METADATA_FILENAME = "tenant.json"

# Deliberately conservative -- a tenant_id feeds directly into a filesystem
# path (``<tenants-root>/<tenant_id>/``). No ``/``, no ``.``, no leading
# dash: rules out path traversal (``../..``) and hidden-dir surprises.
TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class TenantProvisionError(Exception):
    """Fail-closed provisioning error: nothing partial is left behind."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TenantLayout:
    tenant_id: str
    root: Path

    @property
    def ledger_dir(self) -> Path:
        return self.root / LEDGER_DIRNAME

    @property
    def catalog_root(self) -> Path:
        return self.root / CATALOG_DIRNAME

    @property
    def manifest_path(self) -> Path:
        return self.catalog_root / "policy" / "manifest.yaml"

    @property
    def metadata_path(self) -> Path:
        return self.root / TENANT_METADATA_FILENAME


def tenant_layout(tenants_root: str | Path, tenant_id: str) -> TenantLayout:
    if not TENANT_ID_RE.match(tenant_id):
        raise TenantProvisionError(
            "invalid_tenant_id",
            f"tenant_id {tenant_id!r} must match {TENANT_ID_RE.pattern} (lowercase "
            "letters/digits/hyphens, cannot start with a hyphen) -- it becomes a "
            "directory name",
        )
    return TenantLayout(tenant_id=tenant_id, root=Path(tenants_root) / tenant_id)


@dataclass(frozen=True)
class InitResult:
    layout: TenantLayout
    manifest_id: str
    manifest_digest: str
    key_id: str
    secret: bytes
    generated_secret: bool
    activation_capsule_id: str


@dataclass(frozen=True)
class UpgradeResult:
    layout: TenantLayout
    manifest_id: str
    manifest_digest: str
    changed: bool
    activation_capsule_id: str | None


def _materialize_default_manifest(
    layout: TenantLayout, *, manifest_path: Path, fold_catalog_dir: Path, wicket_catalog_dir: Path
):
    manifest: Manifest = load_manifest_file(manifest_path)
    resolved = resolve_manifest(manifest, fold_catalog_dir=fold_catalog_dir, wicket_catalog_dir=wicket_catalog_dir)
    layout.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    layout.manifest_path.write_text(yaml.safe_dump(manifest.canonical_dict(), sort_keys=False))
    return resolved


def _materialize_manifest(
    layout: TenantLayout,
    *,
    pack: PackDefinition | None,
    manifest_path: Path | None,
    fold_catalog_dir: Path | None,
    wicket_catalog_dir: Path | None,
):
    """Write this tenant's own pinned manifest to ``layout.manifest_path``
    and return its ``ResolvedManifest``. Two mutually exclusive modes: a
    starter pack (delegates to ``packs.install_pack``, which already
    materializes the pack's fold/wicket catalog alongside the manifest), or
    the built-in default manifest (no pack -- folds/wickets stay referenced
    from the installed package's own catalog, same as ``capsule manifest
    show`` does today)."""
    if pack is not None:
        installed = install_pack(pack, project_dir=layout.root, mode="observe")
        return installed.resolved
    return _materialize_default_manifest(
        layout,
        manifest_path=manifest_path or DEFAULT_MANIFEST_PATH,
        fold_catalog_dir=fold_catalog_dir or DEFAULT_FOLD_DIR,
        wicket_catalog_dir=wicket_catalog_dir or DEFAULT_WICKET_DIR,
    )


def init_tenant(
    tenants_root: str | Path,
    tenant_id: str,
    *,
    pack: PackDefinition | None = None,
    manifest_path: Path | None = None,
    fold_catalog_dir: Path | None = None,
    wicket_catalog_dir: Path | None = None,
    key_id: str | None = None,
    secret: bytes | None = None,
    operator: str = "local",
    developer: str = "capsule-tenant-init",
) -> InitResult:
    """Provision a brand-new tenant instance: a fresh ledger dir, this
    tenant's own pinned manifest, and a first ``policy_manifest_activated``
    activation capsule opening its epoch history -- signed by a fresh (or
    caller-supplied) key, so the tenant's ledger carries a real record of
    what policy governed it from record one. Refuses if anything already
    exists at this tenant's path (fail closed -- use ``upgrade_tenant`` for
    an existing tenant)."""
    layout = tenant_layout(tenants_root, tenant_id)
    if layout.root.exists() and any(layout.root.iterdir()):
        raise TenantProvisionError(
            "already_initialized",
            f"{layout.root} already exists and is not empty -- run `capsule tenant upgrade` "
            "instead, or provision a different --tenant-id",
        )

    resolved = _materialize_manifest(
        layout, pack=pack, manifest_path=manifest_path, fold_catalog_dir=fold_catalog_dir, wicket_catalog_dir=wicket_catalog_dir
    )

    resolved_key_id = key_id or f"{tenant_id}-signing-key"
    generated_secret = secret is None
    resolved_secret = secret if secret is not None else secrets_mod.token_hex(32).encode("utf-8")
    signer = LocalSigner(key_id=resolved_key_id, secret=resolved_secret)

    with LedgerStore(layout.ledger_dir) as store:
        capsule = build_manifest_activation_capsule(
            resolved=resolved,
            operator=operator,
            developer=developer,
            signer=signer,
            previous_activation_capsule_id=None,
        )
        store.append(capsule, consequential=False)

    metadata = {
        "tenant_id": tenant_id,
        "manifest_id": resolved.manifest.manifest_id,
        "manifest_digest": resolved.manifest_digest,
        "pack_id": pack.pack_id if pack is not None else None,
        "key_id": resolved_key_id,
        "key_fingerprint": key_fingerprint(resolved_key_id, resolved_secret),
        "activation_capsule_id": capsule["capsule_id"],
    }
    layout.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    return InitResult(
        layout=layout,
        manifest_id=resolved.manifest.manifest_id,
        manifest_digest=resolved.manifest_digest,
        key_id=resolved_key_id,
        secret=resolved_secret,
        generated_secret=generated_secret,
        activation_capsule_id=capsule["capsule_id"],
    )


def upgrade_tenant(
    tenants_root: str | Path,
    tenant_id: str,
    *,
    key_id: str,
    secret: bytes,
    pack: PackDefinition | None = None,
    manifest_path: Path | None = None,
    fold_catalog_dir: Path | None = None,
    wicket_catalog_dir: Path | None = None,
    operator: str = "local",
    developer: str = "capsule-tenant-upgrade",
) -> UpgradeResult:
    """Re-materialize an already-provisioned tenant's manifest against the
    currently-installed catalog (or a new pack version) and, only if the
    resulting manifest digest actually changed, record a new activation
    capsule chained to the tenant's previous one -- a new policy epoch, not
    a silent rewrite. Idempotent: running this twice with nothing changed
    upstream is a no-op on the ledger (``changed=False``), matching
    ``install_pack``'s own idempotency guarantee for the files it writes.
    Requires the tenant's *current* live signing key -- upgrading is a
    normal write to that tenant's own epoch history, not a key rotation
    (use ``capsule key rotate`` against this tenant's ledger for that)."""
    layout = tenant_layout(tenants_root, tenant_id)
    if not layout.metadata_path.is_file():
        raise TenantProvisionError(
            "not_initialized",
            f"no {TENANT_METADATA_FILENAME} under {layout.root} -- run `capsule tenant init` first",
        )
    previous_metadata = json.loads(layout.metadata_path.read_text())

    resolved = _materialize_manifest(
        layout, pack=pack, manifest_path=manifest_path, fold_catalog_dir=fold_catalog_dir, wicket_catalog_dir=wicket_catalog_dir
    )

    if resolved.manifest_digest == previous_metadata.get("manifest_digest"):
        return UpgradeResult(
            layout=layout,
            manifest_id=resolved.manifest.manifest_id,
            manifest_digest=resolved.manifest_digest,
            changed=False,
            activation_capsule_id=previous_metadata.get("activation_capsule_id"),
        )

    signer = LocalSigner(key_id=key_id, secret=secret)
    with LedgerStore(layout.ledger_dir) as store:
        previous_activation = find_latest_activation(store)
        capsule = build_manifest_activation_capsule(
            resolved=resolved,
            operator=operator,
            developer=developer,
            signer=signer,
            previous_activation_capsule_id=previous_activation.capsule_id if previous_activation is not None else None,
        )
        store.append(capsule, consequential=False)

    metadata = {
        **previous_metadata,
        "manifest_id": resolved.manifest.manifest_id,
        "manifest_digest": resolved.manifest_digest,
        "pack_id": pack.pack_id if pack is not None else previous_metadata.get("pack_id"),
        "activation_capsule_id": capsule["capsule_id"],
    }
    layout.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    return UpgradeResult(
        layout=layout,
        manifest_id=resolved.manifest.manifest_id,
        manifest_digest=resolved.manifest_digest,
        changed=True,
        activation_capsule_id=capsule["capsule_id"],
    )


def list_tenants(tenants_root: str | Path) -> list[dict]:
    """Every provisioned tenant's ``tenant.json`` under ``tenants_root``,
    sorted by tenant_id. A tenant directory with no metadata file (init
    interrupted before completion) is skipped, not guessed at."""
    root = Path(tenants_root)
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        metadata_path = child / TENANT_METADATA_FILENAME
        if metadata_path.is_file():
            out.append(json.loads(metadata_path.read_text()))
    return out
