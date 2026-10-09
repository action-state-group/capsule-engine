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

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from capsule_ledger.ledger.api import LedgerAPI

from ..guards.checks import RUNNABLE_CHECKS
from ..guards.engine import ASK_RULE_EXCLUDED_CHECKS, GuardEngine
from ..guards.signing import Signer
from ..policy.activation import build_manifest_activation_capsule, find_latest_activation
from ..policy.limits import read_caps_limits
from ..policy.manifest import FoldRef, Manifest, PackRef, WicketRef
from ..policy.profile import PolicyProfile
from ..policy.resolve import ResolvedManifest, resolve_manifest
from .schema import PackDefinition

__all__ = [
    "InstalledPack",
    "ask_gate_selectors",
    "ask_wickets",
    "install_pack",
    "manifest_id_for_pack",
    "build_engine",
    "record_pack_activation",
]

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
    profile: PolicyProfile | None = None
    profile_path: Path | None = None


def _write_definition_yaml(path: Path, canonical: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(canonical, sort_keys=False))


def install_pack(
    pack: PackDefinition, *, project_dir: str | Path, mode: str = "observe", profile: PolicyProfile | None = None
) -> InstalledPack:
    """Materialize ``pack`` into ``<project_dir>/.capsule/`` and return the
    resolved manifest + catalog locations a ``GuardEngine`` can be built
    from. Idempotent: re-running with the same pack/mode overwrites the same
    files with byte-identical content (every definition here is written from
    its own ``canonical_dict()``, not appended to).

    ``profile`` carries the user's own values for the pack's parameters
    (``policy/profile.py``). It is written to ``.capsule/policy/profile.json``
    and pinned by digest in the manifest; the catalogs are written exactly as
    they are without it, so two installs that differ only in profile have
    byte-identical catalogs. A profile that sets anything the pack does not
    define is refused before the install returns."""
    project_dir = Path(project_dir)
    catalog_root = project_dir / CATALOG_DIRNAME
    fold_catalog_dir = catalog_root / "catalog" / "folds"
    wicket_catalog_dir = catalog_root / "catalog" / "wickets"
    manifest_path = catalog_root / "policy" / "manifest.yaml"
    profile_path = catalog_root / "policy" / "profile.json"

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
        profile_digest=profile.profile_digest() if profile is not None else None,
    )
    # Resolve before writing the manifest, so a refused profile leaves no
    # manifest on disk that pins it.
    resolved = resolve_manifest(
        manifest, fold_catalog_dir=fold_catalog_dir, wicket_catalog_dir=wicket_catalog_dir, profile=profile
    )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.safe_dump(manifest.canonical_dict(), sort_keys=False))
    if profile is not None:
        profile_path.write_text(json.dumps(profile.canonical_dict(), indent=2, sort_keys=True) + "\n")
    else:
        # A profile left by an earlier install is no longer pinned.
        profile_path.unlink(missing_ok=True)

    return InstalledPack(
        pack=pack,
        mode=mode,
        manifest=manifest,
        resolved=resolved,
        manifest_path=manifest_path,
        fold_catalog_dir=fold_catalog_dir,
        wicket_catalog_dir=wicket_catalog_dir,
        profile=profile,
        profile_path=profile_path if profile is not None else None,
    )


def build_engine(
    installed: InstalledPack,
    *,
    ledger: LedgerAPI,
    signer_provider: Callable[[], Signer | None],
    clock: Callable[[], str] | None = None,
) -> GuardEngine:
    """A ``GuardEngine`` wired from the installed pack's resolved manifest --
    the caps fold/limits and manifest_digest all come from what
    ``install_pack`` actually materialized and resolved, never re-declared
    here. The caps limits are read from ``ledger``'s activation records on
    every decision (``policy/limits.py``): a profile's value applies only once
    a signed activation binds it, a raise waits out the cooling-off, and each
    decision's caps evidence names the source of every limit it applied. A
    decision under an install whose profile no activation binds is denied
    (``policy_binding``). ``clock`` (default: the wall clock) is the engine's
    "now" for the cooling-off. Note this does NOT set ``dry_run`` -- that is a per-``check()``-call
    argument (``guards/engine.py``); a caller in ``mode="observe"`` must pass
    ``dry_run=True`` to every ``check()`` call itself (see this module's own
    docstring). An action_class_gate failure asks an approver only on the
    selectors whose obligations all declare ``default_disposition: ASK``
    (``ask_gate_selectors``); any other configured check asks on the same
    condition (``ask_wickets``)."""
    wickets = installed.resolved.configured_wickets(RUNNABLE_CHECKS)
    return GuardEngine(
        ledger=ledger,
        caps_fold=installed.resolved.caps_fold(),
        caps_limits=lambda signer: read_caps_limits(installed.resolved, ledger, signer),
        clock=clock,
        per_action_reads=installed.resolved.per_action_reads(),
        signer_provider=signer_provider,
        manifest_digest=installed.resolved.manifest_digest,
        wickets=wickets,
        ask_gate_selectors=ask_gate_selectors(installed.pack),
        ask_wickets=ask_wickets(installed.pack) & {w.check for w in wickets},
    )


def ask_gate_selectors(pack: PackDefinition) -> frozenset[str]:
    """The action_class_gate selectors bound to at least one obligation, every
    one of which declares ``default_disposition: ASK``. A selector with a
    NEVER or DO obligation, or with an obligation declaring none, is left out,
    so failing it still refuses."""
    declared: dict[str, set[str | None]] = {}
    for o in pack.obligations:
        if o.check == "action_class_gate" and o.selector is not None:
            declared.setdefault(o.selector, set()).add(o.default_disposition)
    return frozenset(sid for sid, dispositions in declared.items() if dispositions == {"ASK"})


def ask_wickets(pack: PackDefinition) -> frozenset[str]:
    """The configured checks (``RUNNABLE_CHECKS``) bound to at least one
    obligation, every one of which declares ``default_disposition: ASK``. A
    check with a NEVER or DO obligation, or with one declaring none, is left
    out, and so are the integrity checks and the two with their own ask rule
    (``ASK_RULE_EXCLUDED_CHECKS``): a declared disposition never makes any of
    them ask. ``caps`` and ``counterparty_seen_before`` are reference checks
    that ask whatever the pack declares (``guards/engine.py``
    ``_ESCALATABLE``)."""
    declared: dict[str, set[str | None]] = {}
    for o in pack.obligations:
        if o.check in RUNNABLE_CHECKS and o.check not in ASK_RULE_EXCLUDED_CHECKS:
            declared.setdefault(o.check, set()).add(o.default_disposition)
    return frozenset(check for check, dispositions in declared.items() if dispositions == {"ASK"})


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
    the starter-packs plan's manifest-fragment requirement. When the install
    pins a policy profile, the record also carries that profile's digest and
    values. Chains to the
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
