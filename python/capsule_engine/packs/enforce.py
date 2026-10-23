# SPDX-License-Identifier: Apache-2.0
"""``capsule enforce --pack``: a human accepts proposed thresholds, the
install transitions observe -> enforce.

The transition is not a runtime flag flip -- it is a new manifest.
``enforce_pack()`` records the accepted values in a policy profile
(``profile_for_accepted()``, ``policy/profile.py``) and re-installs through
the exact same ``install_pack()`` every ``capsule init`` uses, just with
``mode="enforce"`` and that profile -- there is no separate "enforce" code
path to drift from "observe". The pack and its wickets keep their digests;
the manifest pins the profile's digest, so "provable what was in force"
still holds through the whole chain.

``accept_thresholds()`` is the earlier path: it rewrites the ``caps``
wicket's config with the accepted values, so the wicket's digest, the
pack's and the manifest's all move. It is kept unchanged for the callers
that build a pack this way; new callers use a profile.
"""
from __future__ import annotations

from dataclasses import replace

from ..guards.wickets.definition import WicketDefinition
from ..policy.profile import PackParameters, PolicyProfile, pack_name
from .install import InstalledPack, install_pack
from .schema import PackDefinition

__all__ = ["accept_thresholds", "enforce_pack", "profile_for_accepted"]


def accept_thresholds(
    pack: PackDefinition, accepted: dict[str, int], *, accepted_per_action: dict[str, int] | None = None
) -> PackDefinition:
    """A new ``PackDefinition`` with the ``caps`` wicket's ``caps_minor``
    merged with ``accepted`` (action_class -> accepted cap, minor units),
    and its ``per_action_minor`` merged with ``accepted_per_action`` the
    same way. Every action class already configured keeps its existing cap unless
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
        if accepted_per_action:
            config["per_action_minor"] = {**(config.get("per_action_minor") or {}), **accepted_per_action}
        new_constraints.append(WicketDefinition(wicket_id=wicket.wicket_id, check=wicket.check, config=config))
        updated = True
    if not updated:
        raise ValueError(f"pack {pack.pack_id!r} has no 'caps' constraint to accept thresholds into")
    return replace(pack, constraints=tuple(new_constraints))


def profile_for_accepted(
    pack: PackDefinition, accepted: dict[str, int], *, accepted_per_action: dict[str, int] | None = None
) -> PolicyProfile:
    """A profile setting the pack's ``caps`` limits to ``accepted`` (window)
    and ``accepted_per_action`` (per action), action_class -> minor units.
    Every class it does not name keeps the pack's default."""
    if not any(w.check == "caps" for w in pack.constraints):
        raise ValueError(f"pack {pack.pack_id!r} has no 'caps' constraint to accept thresholds into")
    caps: dict[str, dict[str, int]] = {}
    if accepted:
        caps["caps_minor"] = dict(accepted)
    if accepted_per_action:
        caps["per_action_minor"] = dict(accepted_per_action)
    return PolicyProfile(packs=(PackParameters(pack=pack_name(pack.pack_id), parameters={"caps": caps}),))


def enforce_pack(pack: PackDefinition, *, project_dir, accepted: dict[str, int]) -> InstalledPack:
    """Install ``pack`` in ``mode="enforce"`` with ``accepted`` recorded in
    a policy profile. A class the pack's ``caps`` wicket declares no default
    for is refused (``profile_unknown_parameter``)."""
    return install_pack(
        pack, project_dir=project_dir, mode="enforce", profile=profile_for_accepted(pack, accepted)
    )
