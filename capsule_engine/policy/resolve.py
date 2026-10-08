# SPDX-License-Identifier: Apache-2.0
"""Resolve a parsed ``Manifest`` against the real fold/wicket catalogs it
cites, cross-checking every pinned digest.

A manifest that fails to resolve (a cited fold_id/wicket_id no longer
exists, its current catalog digest no longer matches what the manifest
pinned, it names an evaluation ``engine`` this build doesn't recognize, or
the policy profile supplied is not the one it pins)
is not "real, loadable" -- every caller in this task (``manifest show``,
``manifest activate``, ``guard dry-run``) treats a resolve failure as
fail-closed, never a best-effort partial load.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..folds.catalog import Catalog as FoldCatalog
from ..folds.definition import FoldDefinition
from ..guards.plan import PlanDefinition, parse_plan_definition
from ..guards.wickets.catalog import Catalog as WicketCatalog
from ..guards.wickets.definition import WicketDefinition
from .errors import (
    FOLD_DIGEST_DRIFT,
    PROFILE_DIGEST_DRIFT,
    PROFILE_MISSING,
    PROFILE_UNKNOWN_PACK,
    PROFILE_UNKNOWN_PARAMETER,
    PROFILE_UNPINNED,
    UNKNOWN_ENGINE,
    UNKNOWN_FOLD_ID,
    UNKNOWN_WICKET_ID,
    WICKET_DIGEST_DRIFT,
    PolicyManifestError,
)
from .manifest import Manifest
from .profile import CONFIGURABLE, PolicyProfile, pack_name

__all__ = ["SUPPORTED_FOLD_ENGINES", "SUPPORTED_WICKET_ENGINES", "ResolvedManifest", "resolve_manifest"]

# The only evaluation engines this build knows how to run a fold/wicket
# entry through -- this repo's own built-in evaluator, one engine per entry
# kind. A manifest naming anything else is rejected fail-closed rather than
# silently ignored (see the ``engine`` design note in ``manifest.py``).
SUPPORTED_FOLD_ENGINES = frozenset({"fold/1"})
SUPPORTED_WICKET_ENGINES = frozenset({"wicket/1"})


@dataclass(frozen=True)
class ResolvedManifest:
    """A manifest plus the real, digest-verified definitions it cites."""

    manifest: Manifest
    manifest_digest: str
    folds: dict[str, FoldDefinition]
    wickets: dict[str, WicketDefinition]
    # The profile the manifest pins, verified against its digest and against
    # the wickets above. ``None`` when the manifest pins none.
    profile: PolicyProfile | None = None

    def wicket_config(self, check: str) -> dict:
        """The declarative ``config`` of the (first) resolved wicket
        configuring the given check, or ``{}`` if none is active. These are
        the pack's defaults; ``effective_values`` overlays the profile."""
        for wicket in self.wickets.values():
            if wicket.check == check:
                return wicket.config
        return {}

    def effective_values(self, check: str, key: str) -> dict[str, int]:
        """The wicket's default ``config[key]`` with every value the profile
        sets for that check laid over it -- what the check actually enforces."""
        out = dict(self.wicket_config(check).get(key) or {})
        if self.profile is not None:
            for entry in self.profile.packs:
                out.update(entry.parameters.get(check, {}).get(key, {}))
        return out

    def caps_minor(self) -> dict[str, int]:
        return self.effective_values("caps", "caps_minor")

    def per_action_minor(self) -> dict[str, int]:
        return self.effective_values("caps", "per_action_minor")

    def per_action_reads(self) -> str | None:
        return self.wicket_config("caps").get("per_action_reads")

    def dedupe_window_days(self) -> int | None:
        return self.wicket_config("dedupe").get("window_days")

    def caps_fold(self) -> FoldDefinition | None:
        fold_id = self.wicket_config("caps").get("fold_id")
        return self.folds.get(fold_id) if fold_id else None

    def configured_wickets(self, checks: frozenset[str]) -> tuple[WicketDefinition, ...]:
        """Every resolved wicket configuring one of ``checks``, in manifest order."""
        return tuple(w for w in self.wickets.values() if w.check in checks)

    def plan(self) -> PlanDefinition | None:
        """The compiled plan quoted directly in the resolved
        ``plan_containment`` wicket's own ``config`` (``guards/plan.py``'s
        module docstring: "a compiled plan IS a wicket config") -- ``None``
        when no ``plan_containment`` wicket is active in this manifest."""
        config = self.wicket_config("plan_containment")
        return parse_plan_definition(config) if config else None


def _check_profile(manifest: Manifest, profile: PolicyProfile | None, wickets: dict[str, WicketDefinition]) -> None:
    """Fail closed unless ``profile`` is exactly the one the manifest pins and
    every value in it replaces a default an installed pack's wicket declares."""
    if manifest.profile_digest is None:
        if profile is not None:
            raise PolicyManifestError(
                PROFILE_UNPINNED, "a profile was supplied but the manifest pins none, so nothing records it"
            )
        return
    if profile is None:
        raise PolicyManifestError(
            PROFILE_MISSING, f"manifest pins profile {manifest.profile_digest}, but no profile was supplied"
        )
    if profile.profile_digest() != manifest.profile_digest:
        raise PolicyManifestError(
            PROFILE_DIGEST_DRIFT,
            f"manifest pins profile {manifest.profile_digest}, but the supplied profile digests to "
            f"{profile.profile_digest()}",
        )
    installed = {pack_name(p.pack_id) for p in manifest.packs}
    for entry in profile.packs:
        if entry.pack not in installed:
            raise PolicyManifestError(
                PROFILE_UNKNOWN_PACK, f"profile sets values for pack {entry.pack!r}, which the manifest does not install"
            )
        for check, keys in entry.parameters.items():
            wicket = next((w for w in wickets.values() if w.check == check), None)
            for key, values in keys.items():
                if wicket is None or key not in CONFIGURABLE.get(check, frozenset()):
                    raise PolicyManifestError(
                        PROFILE_UNKNOWN_PARAMETER, f"profile sets {check}.{key}, which no installed wicket lets a user set"
                    )
                defaults = wicket.config.get(key) or {}
                undeclared = sorted(set(values) - set(defaults))
                if undeclared:
                    raise PolicyManifestError(
                        PROFILE_UNKNOWN_PARAMETER,
                        f"profile sets {check}.{key} for {undeclared}, which wicket {wicket.wicket_id!r} declares "
                        "no default for; a profile replaces a default, it never adds a limit",
                    )


def resolve_manifest(
    manifest: Manifest,
    *,
    fold_catalog_dir: str | Path,
    wicket_catalog_dir: str | Path,
    profile: PolicyProfile | None = None,
) -> ResolvedManifest:
    fold_catalog = FoldCatalog(fold_catalog_dir)
    wicket_catalog = WicketCatalog(wicket_catalog_dir)

    folds: dict[str, FoldDefinition] = {}
    for ref in manifest.folds:
        if ref.engine not in SUPPORTED_FOLD_ENGINES:
            raise PolicyManifestError(
                UNKNOWN_ENGINE,
                f"manifest cites fold {ref.fold_id!r} with engine {ref.engine!r}, this build only "
                f"evaluates fold engines {sorted(SUPPORTED_FOLD_ENGINES)}",
            )
        entry = fold_catalog.get(ref.fold_id)
        if entry is None:
            raise PolicyManifestError(
                UNKNOWN_FOLD_ID, f"manifest cites fold_id {ref.fold_id!r}, not found in catalog {fold_catalog_dir}"
            )
        if entry.digest != ref.digest:
            raise PolicyManifestError(
                FOLD_DIGEST_DRIFT,
                f"manifest pins fold {ref.fold_id!r} at digest {ref.digest}, but the catalog's current "
                f"definition digests to {entry.digest} -- the fold definition has changed since the "
                "manifest was written",
            )
        folds[ref.fold_id] = entry.definition

    wickets: dict[str, WicketDefinition] = {}
    for ref in manifest.wickets:
        if ref.engine not in SUPPORTED_WICKET_ENGINES:
            raise PolicyManifestError(
                UNKNOWN_ENGINE,
                f"manifest cites wicket {ref.wicket_id!r} with engine {ref.engine!r}, this build only "
                f"evaluates wicket engines {sorted(SUPPORTED_WICKET_ENGINES)}",
            )
        entry = wicket_catalog.get(ref.wicket_id)
        if entry is None:
            raise PolicyManifestError(
                UNKNOWN_WICKET_ID,
                f"manifest cites wicket_id {ref.wicket_id!r}, not found in catalog {wicket_catalog_dir}",
            )
        if entry.digest != ref.digest:
            raise PolicyManifestError(
                WICKET_DIGEST_DRIFT,
                f"manifest pins wicket {ref.wicket_id!r} at digest {ref.digest}, but the catalog's current "
                f"definition digests to {entry.digest} -- the wicket definition has changed since the "
                "manifest was written",
            )
        wickets[ref.wicket_id] = entry.definition

    _check_profile(manifest, profile, wickets)

    return ResolvedManifest(
        manifest=manifest, manifest_digest=manifest.manifest_digest(), folds=folds, wickets=wickets, profile=profile
    )
