# SPDX-License-Identifier: Apache-2.0
"""A policy profile: one user's own values for the parameters a pack exposes,
kept as its own artifact so the pack and its wickets stay byte-identical for
every user.

Before this existed, accepting a limit (``packs/enforce.py``'s
``accept_thresholds``) merged the value into the ``caps`` wicket's config,
so 25.00 -> 40.00 moved the wicket's digest, then the pack's, then the
manifest's. That made "what was in force" provable, but it also meant two
households with different limits could not run the same wicket. The checks
already take their limits as arguments (``guards/checks/caps.py``), so the
fix is where the value is stored, not how it is evaluated: the wicket carries
the pack's default, a profile carries the user's value, and the manifest pins
the profile by digest (``Manifest.profile_digest``) the same way it pins
every fold and wicket. "What was in force" stays provable because the
manifest digest moves when the profile does, and the activation record
carries the profile's values (``policy/activation.py``).

Shape (``policy-profile/v0``)::

    format: policy-profile/v0
    packs:
      - pack: asg/everyday              # publisher/name -- no version
        parameters:
          caps:                          # the check the values configure
            per_action_minor: {money.purchase: 4000}
            caps_minor: {money.purchase: 20000}

``pack`` names a pack without its version so a profile can outlive a pack
upgrade; carrying values across an upgrade is a later step, and today a
value the installed pack no longer defines fails closed at resolve time.
The profile names no owner: two households that chose the same values have
the same profile digest.

Which parameters a profile may set is a closed table (``CONFIGURABLE``), and
a value may only replace a default the pack's wicket already declares: a
profile never adds a class the pack does not limit. Anything else is refused
(``resolve.py``), never ignored.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from agent_action_capsule.canonical import json_digest

from .errors import MALFORMED_PROFILE, PolicyManifestError

__all__ = [
    "CONFIGURABLE",
    "PROFILE_FORMAT",
    "PackParameters",
    "PolicyProfile",
    "load_profile_file",
    "pack_name",
    "parse_profile",
]

PROFILE_FORMAT = "policy-profile/v0"

# check -> the config keys a profile may set on a wicket running that check.
# Every value is a mapping of action class -> non-negative integer minor units.
CONFIGURABLE: dict[str, frozenset[str]] = {
    "caps": frozenset({"caps_minor", "per_action_minor"}),
}

# publisher/name: a pack_id (``publisher/name/semver``) without its version.
PACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9-]*$")

# Largest integer every JSON implementation reads exactly.
_MAX_SAFE_INT = 2**53 - 1

ClassValues = dict[str, int]


class PackParametersDict(TypedDict):
    pack: str
    parameters: dict[str, dict[str, ClassValues]]


class PolicyProfileDict(TypedDict):
    format: str
    packs: list[PackParametersDict]


@dataclass(frozen=True)
class PackParameters:
    """The values one profile sets for one pack: check -> key -> class -> value."""

    pack: str
    parameters: dict[str, dict[str, ClassValues]] = field(default_factory=dict)

    def canonical_dict(self) -> PackParametersDict:
        return {"pack": self.pack, "parameters": self.parameters}


@dataclass(frozen=True)
class PolicyProfile:
    packs: tuple[PackParameters, ...] = ()

    def canonical_dict(self) -> PolicyProfileDict:
        """The JCS-canonicalizable form. Mapping order carries no meaning
        (JCS sorts keys); ``packs`` order does, like every list a manifest
        digests."""
        return {"format": PROFILE_FORMAT, "packs": [p.canonical_dict() for p in self.packs]}

    def profile_digest(self) -> str:
        """SHA-256 over the JCS bytes of ``canonical_dict()`` -- the digest
        ``Manifest.profile_digest`` pins."""
        return json_digest(self.canonical_dict())

    def for_pack(self, pack_id: str) -> PackParameters | None:
        name = pack_name(pack_id)
        return next((p for p in self.packs if p.pack == name), None)


def pack_name(pack_id: str) -> str:
    """``asg/everyday/0.1.0`` -> ``asg/everyday``."""
    return pack_id.rsplit("/", 1)[0]


def _refuse(message: str) -> PolicyManifestError:
    return PolicyManifestError(MALFORMED_PROFILE, message)


def _keys_exactly(keys: Iterable[object], expected: set[str], context: str) -> None:
    actual = set(keys)
    if actual != expected:
        raise _refuse(f"{context} must have exactly the keys {sorted(expected)}, got {sorted(actual, key=str)}")


def _parse_class_values(raw: Any, context: str) -> ClassValues:
    if not isinstance(raw, dict):
        raise _refuse(f"{context} must be a mapping of action class to an integer")
    out: ClassValues = {}
    for action_class, value in raw.items():
        if not isinstance(action_class, str) or not action_class:
            raise _refuse(f"{context} has a key that is not a non-empty string: {action_class!r}")
        # bool is an int subclass; True is not a limit.
        if type(value) is not int or not 0 <= value <= _MAX_SAFE_INT:
            raise _refuse(f"{context}[{action_class!r}] must be an integer from 0 to {_MAX_SAFE_INT}, got {value!r}")
        out[action_class] = value
    return out


def _parse_pack_entry(raw: Any) -> PackParameters:
    if not isinstance(raw, dict):
        raise _refuse(f"each packs entry must be a mapping: {raw!r}")
    _keys_exactly(raw.keys(), {"pack", "parameters"}, "packs entry")
    name = raw["pack"]
    if not isinstance(name, str) or not PACK_NAME_RE.match(name):
        raise _refuse(f"packs[].pack must be '<publisher>/<name>' with no version, got {name!r}")
    raw_params = raw["parameters"]
    if not isinstance(raw_params, dict):
        raise _refuse(f"packs[{name!r}].parameters must be a mapping")
    parameters: dict[str, dict[str, ClassValues]] = {}
    for check, keys in raw_params.items():
        if not isinstance(check, str) or not isinstance(keys, dict):
            raise _refuse(f"packs[{name!r}].parameters[{check!r}] must be a mapping of config key to values")
        parameters[check] = {
            key: _parse_class_values(values, f"packs[{name!r}].parameters[{check!r}][{key!r}]")
            for key, values in keys.items()
        }
    return PackParameters(pack=name, parameters=parameters)


def parse_profile(data: Any) -> PolicyProfile:
    """Validate a plain dict (as loaded from JSON or YAML) into a ``PolicyProfile``.

    Checks shape only. Whether each value names a parameter the installed
    pack defines is checked against the resolved wickets (``resolve.py``)."""
    if not isinstance(data, dict):
        raise _refuse("profile must be a mapping")
    _keys_exactly(data.keys(), {"format", "packs"}, "profile")
    if data["format"] != PROFILE_FORMAT:
        raise _refuse(f"format must be {PROFILE_FORMAT!r}, got {data['format']!r}")
    if not isinstance(data["packs"], list):
        raise _refuse("packs must be a list")
    packs = tuple(_parse_pack_entry(entry) for entry in data["packs"])
    names = [p.pack for p in packs]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        raise _refuse(f"pack {duplicated[0]!r} appears more than once")
    return PolicyProfile(packs=packs)


def load_profile_file(path: str | Path) -> PolicyProfile:
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise _refuse(f"{path} is not valid JSON: {exc}") from exc
    return parse_profile(data)
