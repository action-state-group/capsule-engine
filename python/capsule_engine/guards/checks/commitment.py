# SPDX-License-Identifier: Apache-2.0
"""Opening a salted commitment a deal record seals (commit_alg
``sha256-jcs-nonce256``): SHA-256 over the JCS bytes of ``{"nonce", "text"}``,
with a 256-bit nonce in lowercase hex. capsulectl seals every ``*_commitment``
this way (capsule-cli ``internal/cli/deal_ids.go``, ``CommitText``).

``price_floor`` opens the task authority's ``bounds_commitment`` with the
commercial-bounds document as the text; ``promise_never`` opens a represent
check's ``representing.text_commitment`` with the words it is about to say.
Nothing an opening holds is read until it opens the sealed commitment.
"""
from __future__ import annotations

import re

from agent_action_capsule.canonical import json_digest

__all__ = ["opens_commitment", "text_commitment"]

_HEX64 = re.compile(r"[0-9a-f]{64}")


def text_commitment(nonce: str, text: str) -> str:
    """The commitment to ``text`` under ``nonce``."""
    return json_digest({"nonce": nonce, "text": text})


def opens_commitment(nonce: object, text: object, sealed: object) -> bool:
    """Whether ``nonce`` and ``text``, decoded JSON from a checker input,
    open ``sealed``: a nonce of 64 lowercase hex, a string text, a sealed
    commitment of 64 lowercase hex, and the commitment they recompute equal
    to it."""
    if not isinstance(nonce, str) or not _HEX64.fullmatch(nonce) or not isinstance(text, str):
        return False
    if not isinstance(sealed, str) or not _HEX64.fullmatch(sealed):
        return False
    try:
        return text_commitment(nonce, text) == sealed
    # A text holding a lone surrogate has no JCS bytes, so it opens nothing.
    except UnicodeEncodeError:
        return False
