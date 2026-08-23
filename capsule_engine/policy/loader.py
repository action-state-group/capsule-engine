# SPDX-License-Identifier: Apache-2.0
"""YAML front door for policy manifests: YAML text/file -> validated Manifest."""
from __future__ import annotations

from pathlib import Path

import yaml

from .errors import MALFORMED_MANIFEST, PolicyManifestError
from .manifest import Manifest, parse_manifest

__all__ = ["load_manifest_text", "load_manifest_file"]


def load_manifest_text(text: str) -> Manifest:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyManifestError(MALFORMED_MANIFEST, f"invalid YAML: {exc}") from exc
    if data is None:
        raise PolicyManifestError(MALFORMED_MANIFEST, "empty manifest document")
    return parse_manifest(data)


def load_manifest_file(path: str | Path) -> Manifest:
    return load_manifest_text(Path(path).read_text())
