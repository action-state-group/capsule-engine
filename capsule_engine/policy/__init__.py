# SPDX-License-Identifier: Apache-2.0
"""The policy manifest: declares which fold and wicket definitions govern
guard decisions, by digest -- the declare-attest-verify piece for guard
policy (in-toto/SLSA pattern)."""
from .activation import (
    EVENT_MANIFEST_ACTIVATED,
    GENESIS_PARENT,
    build_manifest_activation_capsule,
    find_latest_activation,
)
from .errors import PolicyManifestError
from .loader import load_manifest_file, load_manifest_text
from .manifest import FoldRef, Manifest, PackRef, WicketRef, parse_manifest
from .profile import PROFILE_FORMAT, PackParameters, PolicyProfile, load_profile_file, parse_profile
from .resolve import ResolvedManifest, resolve_manifest

__all__ = [
    "Manifest",
    "FoldRef",
    "WicketRef",
    "PackRef",
    "parse_manifest",
    "load_manifest_text",
    "load_manifest_file",
    "PolicyManifestError",
    "PROFILE_FORMAT",
    "PolicyProfile",
    "PackParameters",
    "parse_profile",
    "load_profile_file",
    "ResolvedManifest",
    "resolve_manifest",
    "build_manifest_activation_capsule",
    "find_latest_activation",
    "EVENT_MANIFEST_ACTIVATED",
    "GENESIS_PARENT",
]
