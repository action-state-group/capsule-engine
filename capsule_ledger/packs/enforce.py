# SPDX-License-Identifier: Apache-2.0
"""``capsule enforce --pack``: a human accepts proposed thresholds, the
install transitions observe -> enforce.

The transition is not a runtime flag flip -- it is a new manifest.
``accept_thresholds()`` rebuilds the pack's ``caps`` wicket with the
accepted ``caps_minor`` values merged in (a real config change, so the
wicket's own digest moves, which moves the pack's own digest, which moves
the manifest's digest -- "provable what was in force" holds through the
whole chain, not just at the pack-ref level). ``enforce_pack()`` re-installs
through the exact same ``install_pack()`` every ``capsule init`` uses, just
with ``mode="enforce"`` and the accepted config -- there is no separate
"enforce" code path to drift from "observe".
"""
from __future__ import annotations

from dataclasses import replace

from ..guards.wickets.definition import WicketDefinition
from .install import InstalledPack, install_pack
from .schema import PackDefinition

__all__ = ["accept_thresholds", "enforce_pack"]


def accept_thresholds(pack: PackDefinition, accepted: dict[str, int]) -> PackDefinition:
    """A new ``PackDefinition`` with the ``caps`` wicket's ``caps_minor``
    merged with ``accepted`` (action_class -> accepted cap, minor units).
    Every action class already configured keeps its existing cap unless
    ``accepted`` overrides it -- accepting one class's proposal never
    silently drops another's already-enforced limit."""
    new_constraints = []
    updated = False
    for wicket in pack.constraints:
        if wicket.check != "caps":
            new_constraints.append(wicket)
            continue
        config = dict(wicket.config)
        caps_minor = dict(config.get("caps_minor") or {})
        caps_minor.update(accepted)
        config["caps_minor"] = caps_minor
        new_constraints.append(WicketDefinition(wicket_id=wicket.wicket_id, check=wicket.check, config=config))
        updated = True
    if not updated:
        raise ValueError(f"pack {pack.pack_id!r} has no 'caps' constraint to accept thresholds into")
    return replace(pack, constraints=tuple(new_constraints))


def enforce_pack(pack: PackDefinition, *, project_dir, accepted: dict[str, int]) -> InstalledPack:
    """Accept ``accepted`` and install the result in ``mode="enforce"``."""
    accepted_pack = accept_thresholds(pack, accepted)
    return install_pack(accepted_pack, project_dir=project_dir, mode="enforce")
