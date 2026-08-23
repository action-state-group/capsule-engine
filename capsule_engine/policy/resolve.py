# SPDX-License-Identifier: Apache-2.0
"""Resolve a parsed ``Manifest`` against the real fold/wicket catalogs it
cites, cross-checking every pinned digest.

A manifest that fails to resolve (a cited fold_id/wicket_id no longer
exists, its current catalog digest no longer matches what the manifest
pinned, or it names an evaluation ``engine`` this build doesn't recognize)
is not "real, loadable" -- every caller in this task (``manifest show``,
``manifest activate``, ``guard dry-run``) treats a resolve failure as
fail-closed, never a best-effort partial load.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..folds.catalog import Catalog as FoldCatalog
from ..folds.definition import FoldDefinition
from ..guards.wickets.catalog import Catalog as WicketCatalog
from ..guards.wickets.definition import WicketDefinition
from .errors import (
    FOLD_DIGEST_DRIFT,
    UNKNOWN_ENGINE,
    UNKNOWN_FOLD_ID,
    UNKNOWN_WICKET_ID,
    WICKET_DIGEST_DRIFT,
    PolicyManifestError,
)
from .manifest import Manifest

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

    def wicket_config(self, check: str) -> dict:
        """The declarative ``config`` of the (first) resolved wicket
        configuring the given check, or ``{}`` if none is active."""
        for wicket in self.wickets.values():
            if wicket.check == check:
                return wicket.config
        return {}

    def caps_minor(self) -> dict[str, int]:
        return dict(self.wicket_config("caps").get("caps_minor") or {})

    def dedupe_window_days(self) -> int | None:
        return self.wicket_config("dedupe").get("window_days")

    def caps_fold(self) -> FoldDefinition | None:
        fold_id = self.wicket_config("caps").get("fold_id")
        return self.folds.get(fold_id) if fold_id else None


def resolve_manifest(
    manifest: Manifest, *, fold_catalog_dir: str | Path, wicket_catalog_dir: str | Path
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

    return ResolvedManifest(
        manifest=manifest, manifest_digest=manifest.manifest_digest(), folds=folds, wickets=wickets
    )
