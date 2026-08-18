# SPDX-License-Identifier: Apache-2.0
"""``capsule bundle --with-viewer``'s embedded, self-contained recipient
viewer -- see ``capsule_ledger.bundle_viewer.viewer`` for the renderer and
``scripts/vendor_bundle_viewer.py`` for how the vendored template is kept
in sync with scitt-cose's offline shell.
"""
from .viewer import render_offline_viewer_html

__all__ = ["render_offline_viewer_html"]
