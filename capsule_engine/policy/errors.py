# SPDX-License-Identifier: Apache-2.0
"""Named-reason errors for policy manifests: parse-time (this module) and
resolve-time (``resolve.py``) failures, both carrying a stable reason code
(same discipline as ``folds/errors.py`` / ``guards/wickets/errors.py``)."""
from __future__ import annotations

# Parse-time (this file's own load/validate).
INVALID_MANIFEST_ID = "invalid_manifest_id_namespace"
MALFORMED_MANIFEST = "malformed_manifest"
INVALID_DIGEST = "invalid_digest_shape"
DUPLICATE_FOLD_REF = "duplicate_fold_ref"
DUPLICATE_WICKET_REF = "duplicate_wicket_ref"
DUPLICATE_PACK_REF = "duplicate_pack_ref"
INVALID_PACK_MODE = "invalid_pack_mode"

# Resolve-time (``resolve.py``: cross-checking a manifest's pinned digests
# against the real fold/wicket catalogs it references).
UNKNOWN_FOLD_ID = "unknown_fold_id"
UNKNOWN_WICKET_ID = "unknown_wicket_id"
FOLD_DIGEST_DRIFT = "fold_digest_drift"
WICKET_DIGEST_DRIFT = "wicket_digest_drift"
UNKNOWN_ENGINE = "unknown_engine"

# Policy profile (``profile.py``): parse-time shape, then resolve-time
# cross-checks against the manifest's pin and the resolved wickets.
MALFORMED_PROFILE = "malformed_profile"
PROFILE_MISSING = "profile_missing"
PROFILE_UNPINNED = "profile_unpinned"
PROFILE_DIGEST_DRIFT = "profile_digest_drift"
PROFILE_UNKNOWN_PACK = "profile_unknown_pack"
PROFILE_UNKNOWN_PARAMETER = "profile_unknown_parameter"
# ``limits.py``: the activation history a profile's limits are read from.
# The installed manifest is not the one the latest activation binds:
PROFILE_UNBOUND = "profile_unbound"
# An activation record fails verification under the engine's signer:
ACTIVATION_UNVERIFIED = "activation_unverified"
# An activation is timestamped before the one appended ahead of it:
ACTIVATION_OUT_OF_ORDER = "activation_out_of_order"


class PolicyManifestError(ValueError):
    """A policy manifest fails to parse, validate, or resolve. Carries a named reason."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")
