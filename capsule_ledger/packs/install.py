# SPDX-License-Identifier: Apache-2.0
"""Install a ``PackDefinition`` into a project: materialize its constraints
and folds into on-disk catalogs, build the policy manifest fragment that
cites them (plus the pack itself, by digest), and hand back everything a
caller needs to construct a real, pack-governed ``GuardEngine``.

This is the bridge ``capsule init --pack`` (``cli/init_cmds.py``) drives,
and what the payments-safety acceptance test exercises directly without
going through the CLI. Nothing here is pack-specific -- a pack only ever
supplies declarative data (``PackDefinition``); this module is the one,
shared path every pack installs through, which is what makes "no per-pack
logic outside the declarative layer" a real property rather than a promise.

Two catalogs, one manifest, one lifecycle mode (``PACK_MODES`` in
``policy/manifest.py``): "observe" installs the pack's constraints so every
decision is COMPUTED and RECORDED, but ``GuardEngine.check(..., dry_run=True)``
is what a caller must use for as long as the pack stays in observe mode --
this module does not itself force that; the returned ``mode`` is what a
caller (the CLI, an integration, a test) is expected to read and honor.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..guards.engine import GuardEngine
from ..guards.signing import Signer
from ..ledger.api import LedgerAPI
from ..policy.activation import build_manifest_activation_capsule, find_latest_activation
from ..policy.manifest import FoldRef, Manifest, PackRef, WicketRef
from ..policy.resolve import ResolvedManifest, resolve_manifest
from .schema import PackDefinition

__all__ = ["InstalledPack", "install_pack", "manifest_id_for_pack", "build_engine", "record_pack_activation"]

PACK_ENGINE = "pack/1"
FOLD_ENGINE = "fold/1"
WICKET_ENGINE = "wicket/1"

CATALOG_DIRNAME = ".capsule"


def manifest_id_for_pack(pack_id: str) -> str:
    """``asg/payments-safety/1.0.0`` -> ``asg.payments_safety.install/1.0.0``
    -- a manifest_id must be dot/underscore-namespaced (``MANIFEST_ID_RE``),
    while a pack_id is ``publisher/name/semver`` with a kebab-case name
    segment (registry-architecture ruling, 2026-08-10), so this is a real
    translation, not a coincidence of matching regexes."""
    publisher, name, version = pack_id.split("/")
    return f"{publisher}.{name.replace('-', '_')}.install/{version}"


@dataclass(frozen=True)
class InstalledPack:
    pack: PackDefinition
    mode: str
    manifest: Manifest
    resolved: ResolvedManifest
    manifest_path: Path
    fold_catalog_dir: Path
    wicket_catalog_dir: Path


def _write_definition_yaml(path: Path, canonical: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(canonical, sort_keys=False))


def install_pack(pack: PackDefinition, *, project_dir: str | Path, mode: str = "observe") -> InstalledPack:
    """Materialize ``pack`` into ``<project_dir>/.capsule/`` and return the
    resolved manifest + catalog locations a ``GuardEngine`` can be built
    from. Idempotent: re-running with the same pack/mode overwrites the same
    files with byte-identical content (every definition here is written from
    its own ``canonical_dict()``, not appended to)."""
    project_dir = Path(project_dir)
    catalog_root = project_dir / CATALOG_DIRNAME
    fold_catalog_dir = catalog_root / "catalog" / "folds"
    wicket_catalog_dir = catalog_root / "catalog" / "wickets"
    manifest_path = catalog_root / "policy" / "manifest.yaml"

    fold_refs: list[FoldRef] = []
    for fold in pack.folds:
        digest = fold.definition_digest()
        _write_definition_yaml(fold_catalog_dir / f"{fold.fold_id.split('/')[0]}.yaml", fold.canonical_dict())
        fold_refs.append(FoldRef(fold_id=fold.fold_id, engine=FOLD_ENGINE, digest=digest))

    wicket_refs: list[WicketRef] = []
    for wicket in pack.constraints:
        digest = wicket.definition_digest()
        _write_definition_yaml(wicket_catalog_dir / f"{wicket.wicket_id.split('/')[0]}.yaml", wicket.canonical_dict())
        wicket_refs.append(WicketRef(wicket_id=wicket.wicket_id, engine=WICKET_ENGINE, digest=digest))

    pack_ref = PackRef(pack_id=pack.pack_id, engine=PACK_ENGINE, digest=pack.definition_digest(), mode=mode)

    manifest = Manifest(
        manifest_id=manifest_id_for_pack(pack.pack_id),
        folds=tuple(fold_refs),
        wickets=tuple(wicket_refs),
        packs=(pack_ref,),
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.safe_dump(manifest.canonical_dict(), sort_keys=False))

    resolved = resolve_manifest(manifest, fold_catalog_dir=fold_catalog_dir, wicket_catalog_dir=wicket_catalog_dir)

    return InstalledPack(
        pack=pack,
        mode=mode,
        manifest=manifest,
        resolved=resolved,
        manifest_path=manifest_path,
        fold_catalog_dir=fold_catalog_dir,
        wicket_catalog_dir=wicket_catalog_dir,
    )


def build_engine(
    installed: InstalledPack, *, ledger: LedgerAPI, signer_provider: Callable[[], Signer | None]
) -> GuardEngine:
    """A ``GuardEngine`` wired from the installed pack's resolved manifest --
    the caps fold/limits and manifest_digest all come from what
    ``install_pack`` actually materialized and resolved, never re-declared
    here. Note this does NOT set ``dry_run`` -- that is a per-``check()``-call
    argument (``guards/engine.py``); a caller in ``mode="observe"`` must pass
    ``dry_run=True`` to every ``check()`` call itself (see this module's own
    docstring)."""
    return GuardEngine(
        ledger=ledger,
        caps_fold=installed.resolved.caps_fold(),
        caps_minor=installed.resolved.caps_minor(),
        signer_provider=signer_provider,
        manifest_digest=installed.resolved.manifest_digest,
    )


def record_pack_activation(
    installed: InstalledPack,
    *,
    ledger: LedgerAPI,
    operator: str,
    developer: str,
    signer: Signer,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    """Append a signed ``policy_manifest_activated`` event capsule
    (``policy/activation.py``) recording that this pack, at this digest, in
    this mode, is now in force -- the "provable what was in force" half of
    the starter-packs plan's manifest-fragment requirement. Chains to the
    ledger's own previous activation, if any, so pack installs and any other
    manifest changes share one walkable epoch history."""
    previous = find_latest_activation(ledger)
    capsule = build_manifest_activation_capsule(
        resolved=installed.resolved,
        operator=operator,
        developer=developer,
        signer=signer,
        previous_activation_capsule_id=previous.capsule_id if previous else None,
        timestamp=timestamp,
        action_id=action_id,
    )
    ledger.append(capsule, consequential=False)
    return capsule
