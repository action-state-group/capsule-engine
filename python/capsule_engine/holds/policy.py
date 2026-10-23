# SPDX-License-Identifier: Apache-2.0
"""Resolve a ``HoldEngine``'s cap/tolerance configuration from a
``ResolvedManifest`` (``policy/resolve.py``) -- #53.2: "tolerance is policy,
not code: declared in a digest-pinned definition (rides the policy
manifest)". Reuses the manifest's own declare-attest-verify mechanism
directly rather than a parallel config path: every value here traces back to
a wicket/fold digest the manifest pins, cross-checked against the live
catalog by ``resolve_manifest`` before this module ever sees it.

``ResolvedManifest.caps_fold()``/``caps_minor()`` (``policy/resolve.py``)
pick the *first* wicket configuring the ``caps`` check, which is fine for a
manifest with exactly one -- a manifest carrying both a plain money-cap
wicket and a hold-aware one needs to disambiguate by wicket_id, so this
module looks wickets/folds up by id directly instead.
"""
from __future__ import annotations

from ..policy.resolve import ResolvedManifest
from .errors import HoldError

__all__ = ["HoldPolicy", "resolve_hold_policy"]

DEFAULT_CAPS_WICKET_ID = "caps_holds/1.0.0"
DEFAULT_RECONCILE_WICKET_ID = "hold_reconcile/1.0.0"


class HoldPolicy:
    def __init__(self, *, fold_id: str, fold_digest: str, caps_minor: dict[str, int], tolerance_minor: dict[str, int]):
        self.fold_id = fold_id
        self.fold_digest = fold_digest
        self.caps_minor = caps_minor
        self.tolerance_minor = tolerance_minor


def resolve_hold_policy(
    resolved: ResolvedManifest,
    *,
    caps_wicket_id: str = DEFAULT_CAPS_WICKET_ID,
    reconcile_wicket_id: str = DEFAULT_RECONCILE_WICKET_ID,
) -> HoldPolicy:
    caps_wicket = resolved.wickets.get(caps_wicket_id)
    if caps_wicket is None:
        raise HoldError(
            "unknown_caps_wicket_id",
            f"manifest {resolved.manifest.manifest_id!r} does not cite wicket_id {caps_wicket_id!r}",
        )
    fold_id = caps_wicket.config.get("fold_id")
    if fold_id not in resolved.folds:
        raise HoldError(
            "unknown_hold_fold_id",
            f"caps wicket {caps_wicket_id!r} cites fold_id {fold_id!r}, not resolved from manifest "
            f"{resolved.manifest.manifest_id!r}",
        )
    reconcile_wicket = resolved.wickets.get(reconcile_wicket_id)
    tolerance_minor = dict(reconcile_wicket.config.get("tolerance_minor") or {}) if reconcile_wicket is not None else {}

    return HoldPolicy(
        fold_id=fold_id,
        fold_digest=resolved.folds[fold_id].definition_digest(),
        caps_minor=dict(caps_wicket.config.get("caps_minor") or {}),
        tolerance_minor=tolerance_minor,
    )
