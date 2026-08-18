# SPDX-License-Identifier: Apache-2.0
"""Render ``capsule bundle --with-viewer``'s self-contained HTML artifact.

The template (``static/offline_shell.html``) is a vendored, byte-for-byte
copy of scitt-cose's ``hosted_profiles.hosted.render_bundle_page(offline=True)``
output -- see ``scripts/vendor_bundle_viewer.py`` for how it's re-synced.
scitt-cose's own ``hosted_profiles/`` package is deliberately excluded from
its published wheel, so there is no pip dependency that could pull this in
at install time; vendoring one generated artifact (both repos are
Apache-2.0) is the mechanism, not a live fetch from a running verify
service -- ``capsule bundle`` is otherwise a fully local, network-free
command reading only the local ledger file, and ``--with-viewer`` keeps
that property rather than making bundle *production* depend on a service
being reachable. The template embeds MMR_JS and BUNDLE_JS inline (no
``<script src>``) so the artifact this writes needs no network to open or
verify, on either end.

The template carries one real embed slot,
``window.__BUNDLE_FRAGMENT_B64U__="@@BUNDLE_FRAGMENT@@"`` -- substituted
here with the same base64url fragment ``capsule bundle`` already computes
for its permalink. Other appearances of that placeholder text elsewhere in
the template are BUNDLE_JS's own placeholder-detection / re-splice logic
(its in-browser "download self-contained copy" button) and must not be
touched, so this replaces only the one assignment, not every occurrence of
the bare token.
"""
from __future__ import annotations

import json
from importlib import resources

__all__ = ["render_offline_viewer_html"]

_EMBED_SLOT_PLACEHOLDER = '<script>window.__BUNDLE_FRAGMENT_B64U__="@@BUNDLE_FRAGMENT@@";</script>'


def _load_template() -> str:
    return (
        resources.files("capsule_ledger.bundle_viewer")
        .joinpath("static", "offline_shell.html")
        .read_text(encoding="utf-8")
    )


def render_offline_viewer_html(fragment: str) -> str:
    """Return the self-contained HTML for *fragment* (the same base64url
    bundle fragment used in the bundle's permalink). The result carries no
    external references at all -- open it directly, no server required."""
    template = _load_template()
    if template.count(_EMBED_SLOT_PLACEHOLDER) != 1:
        raise RuntimeError(
            "vendored offline_shell.html's embed slot is missing or no longer "
            "unique -- re-check it against a fresh vendor pass "
            "(scripts/vendor_bundle_viewer.py) before shipping --with-viewer"
        )
    embed_slot = f"<script>window.__BUNDLE_FRAGMENT_B64U__={json.dumps(fragment)};</script>"
    return template.replace(_EMBED_SLOT_PLACEHOLDER, embed_slot, 1)
