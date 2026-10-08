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
          counterparty_list:             # a list, not class values
            mode: deny                   # required: deny | allow
            entries:                     # each names its kind and form
              - {kind: payee, fp_alg: hmac-sha256-deal-key, value: <hex>}
              - {kind: target, value: shop/example}
            disposition: ask             # optional: deny | ask

``pack`` names a pack without its version so a profile can outlive a pack
upgrade; carrying values across an upgrade is a later step, and today a
value the installed pack no longer defines fails closed at resolve time.
The profile names no owner: two households that chose the same values have
the same profile digest.

Which parameters a profile may set is a closed table (``CONFIGURABLE``), and
a value may only replace a default the pack's wicket already declares: a
profile never adds a class the pack does not limit. Anything else is refused
(``resolve.py``), never ignored.

``counterparty_list`` is the one check whose values are not per-class
amounts: its ``entries`` replace the wicket's (empty) list whole. A profile
that sets it must name ``mode``, so a list is never read as deny when the
user meant allow. Each entry names its ``kind`` (``target``, or a
fingerprint kind with its ``fp_alg``; see ``guards/checks/
counterparty_list.py``). Entries are stored in a canonical order, so the
profile digest does not depend on the order the user typed them.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from agent_action_capsule.canonical import json_digest

from ..guards.checks.counterparty_list import (
    CLEAR_KINDS,
    DISPOSITIONS,
    FINGERPRINT_KINDS,
    MODES,
    ListEntry,
    sorted_entries,
)
from .errors import MALFORMED_PROFILE, PolicyManifestError

__all__ = [
    "CONFIGURABLE",
    "LIST_CHECK",
    "LIST_KEYS",
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

# The one check whose profile values are not per-class amounts, and the keys
# a profile may set on it. Kept out of ``CONFIGURABLE``, whose every key is a
# per-class limit to its readers.
LIST_CHECK = "counterparty_list"
LIST_KEYS = frozenset({"mode", "entries", "disposition"})

# publisher/name: a pack_id (``publisher/name/semver``) without its version.
PACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9-]*$")

# Largest integer every JSON implementation reads exactly.
_MAX_SAFE_INT = 2**53 - 1

ClassValues = dict[str, int]
# A ``caps`` key carries ClassValues; a ``counterparty_list`` key carries its
# mode or disposition (str) or its entries (list[ListEntry]).
ParameterValue = ClassValues | str | list[ListEntry]


class PackParametersDict(TypedDict):
    pack: str
    parameters: dict[str, dict[str, ParameterValue]]


class PolicyProfileDict(TypedDict):
    format: str
    packs: list[PackParametersDict]


@dataclass(frozen=True)
class PackParameters:
    """The values one profile sets for one pack: check -> key -> class -> value."""

    pack: str
    parameters: dict[str, dict[str, ParameterValue]] = field(default_factory=dict)

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


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _parse_list_entry(raw: Any, context: str) -> ListEntry:
    """``{kind: target, value}`` or ``{kind: <fingerprint kind>, fp_alg,
    value}``. A bare string names no kind, and a name is never a kind."""
    kind = raw.get("kind") if isinstance(raw, dict) else None
    if kind in CLEAR_KINDS:
        _keys_exactly(raw.keys(), {"kind", "value"}, f"{context} entry of kind {kind!r}")
    elif kind in FINGERPRINT_KINDS:
        _keys_exactly(raw.keys(), {"kind", "fp_alg", "value"}, f"{context} entry of kind {kind!r}")
        if not _nonempty_str(raw["fp_alg"]):
            raise _refuse(f"{context} entry has an fp_alg that is not a non-empty string: {raw['fp_alg']!r}")
    else:
        kinds = sorted(CLEAR_KINDS | FINGERPRINT_KINDS)
        raise _refuse(f"{context} entries must be mappings whose kind is one of {kinds}, got {raw!r}")
    if not _nonempty_str(raw["value"]):
        raise _refuse(f"{context} entry has a value that is not a non-empty string: {raw['value']!r}")
    entry: ListEntry = {"kind": kind, "value": raw["value"]}
    if kind in FINGERPRINT_KINDS:
        entry["fp_alg"] = raw["fp_alg"]
    return entry


def _parse_list_parameters(raw: Any, context: str) -> dict[str, ParameterValue]:
    keys = set(raw)
    if not {"mode", "entries"} <= keys <= LIST_KEYS:
        raise _refuse(
            f"{context} must set 'mode' and 'entries' and may set 'disposition', got {sorted(keys, key=str)}"
        )
    if raw["mode"] not in MODES:
        raise _refuse(f"{context}['mode'] must be one of {sorted(MODES)}, got {raw['mode']!r}")
    if not isinstance(raw["entries"], list):
        raise _refuse(f"{context}['entries'] must be a list, got {raw['entries']!r}")
    entries = [_parse_list_entry(e, f"{context}['entries']") for e in raw["entries"]]
    if len({json_digest(e) for e in entries}) != len(entries):
        raise _refuse(f"{context}['entries'] lists a counterparty more than once")
    out: dict[str, ParameterValue] = {"mode": raw["mode"], "entries": sorted_entries(entries)}
    if "disposition" in raw:
        if raw["disposition"] not in DISPOSITIONS:
            raise _refuse(
                f"{context}['disposition'] must be one of {sorted(DISPOSITIONS)}, got {raw['disposition']!r}"
            )
        out["disposition"] = raw["disposition"]
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
    parameters: dict[str, dict[str, ParameterValue]] = {}
    for check, keys in raw_params.items():
        if not isinstance(check, str) or not isinstance(keys, dict):
            raise _refuse(f"packs[{name!r}].parameters[{check!r}] must be a mapping of config key to values")
        if check == LIST_CHECK:
            parameters[check] = _parse_list_parameters(keys, f"packs[{name!r}].parameters[{check!r}]")
            continue
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
