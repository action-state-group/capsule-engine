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


class PolicyManifestError(ValueError):
    """A policy manifest fails to parse, validate, or resolve. Carries a named reason."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")
