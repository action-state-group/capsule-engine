# SPDX-License-Identifier: Apache-2.0
"""``capsule bundle --with-viewer``'s embedded, self-contained recipient
viewer -- see ``capsule_engine.bundle_viewer.viewer`` for the renderer and
``scripts/vendor_bundle_viewer.py`` for how the vendored template is kept
in sync with scitt-cose's offline shell.

The fragment-carried BASE viewer + its domain-module plug-in seam
(previously ``base_viewer.py`` here, moved from capsule-ledger) has moved OUT of this
package entirely, to the standalone ``capsule-viewer`` repo/package:
presenting/narrating evidence is
neutral, donation-bound surface, not this engine's product core, and it now
also renders Evidence Result v0 documents (``result/v0``), which have no
capsule concept to justify living here at all. capsule-engine keeps no copy
-- if this package ever needs that viewer again, it depends on
``capsule-viewer`` rather than re-vendoring it.
"""
from .viewer import render_offline_viewer_html

__all__ = [
    "render_offline_viewer_html",
]
