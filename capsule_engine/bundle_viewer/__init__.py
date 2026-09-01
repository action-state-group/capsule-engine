# SPDX-License-Identifier: Apache-2.0
"""``capsule bundle --with-viewer``'s embedded, self-contained recipient
viewer -- see ``capsule_engine.bundle_viewer.viewer`` for the renderer and
``scripts/vendor_bundle_viewer.py`` for how the vendored template is kept
in sync with scitt-cose's offline shell.

Also the fragment-carried BASE viewer + its domain-module plug-in seam
(``base_viewer.py``) -- moved here from capsule-ledger
([ldg-ledger-scope-re-extraction] RESIDUALS pass, §3.2): presenting/narrating
evidence is operational, not the neutral honest-records core. NOTE: the
``conversation_exchange_card.js`` narration strings were authored directly
here (in the company repo), not vendored from a neutral upstream verifier
first -- a known F.1.2 gap, tracked, not blocking this move.
"""
from .base_viewer import (
    build_entry,
    build_payload,
    encode_fragment,
    render_base_viewer_html,
)
from .viewer import render_offline_viewer_html

__all__ = [
    "render_offline_viewer_html",
    # The fragment-carried base viewer + its domain-module plug-in seam.
    "render_base_viewer_html",
    "build_entry",
    "build_payload",
    "encode_fragment",
]
