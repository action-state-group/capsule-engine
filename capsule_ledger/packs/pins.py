# SPDX-License-Identifier: Apache-2.0
"""Registry-pin verification: fail closed if a pack (or a fold it ships)
doesn't match a trusted, pre-recorded digest.

The registry-architecture ruling (2026-08-10, registry-architecture-and-
namespace-2026-08-10.md §6) requires ``capsule init`` to verify fetched
pack/fold definition digests against registry-pinned digests before
install, fail closed on mismatch -- but the registry that pins are meant to
come from (``capsule-registry``) is itself confirmed not to exist yet
(created "at the capsule-ledger flip", same doc, §6 item 5). This module is
the verification GATE, deliberately decoupled from where the pins came
from: ``verify_pins()`` takes a plain ``{artifact_id: digest}`` mapping.
Today that mapping is loaded from a local YAML file (``load_pins_file()``);
swapping in a real ``capsule-registry`` HTTP fetch later is a change to
*how the mapping is obtained*, not to this gate's interface or its fail-
closed behavior.

A missing pin is treated identically to a mismatched one -- "no pin on
record" is not a way to skip verification. This mirrors the fail-closed
default every other unknown/unrecognized case in this codebase gets
(an unclassified action class, an unrecognized manifest engine, an
unregistered check): absence of a positive match is a deny, never a
silent pass.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from agent_action_capsule.contracts import is_hex64

from .errors import MALFORMED_PINS_FILE, PIN_DIGEST_MISMATCH, PIN_NOT_FOUND, RegistryPinError
from .schema import PackDefinition

__all__ = ["load_pins_file", "verify_pins"]


def load_pins_file(path: str | Path) -> dict[str, str]:
    """A pins file is a flat mapping of artifact id (a pack_id or fold_id,
    exactly as it appears on the artifact) -> its trusted, 64-hex-char
    ``definition_digest()``."""
    path = Path(path)
    try:
        data: Any = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise RegistryPinError(MALFORMED_PINS_FILE, f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryPinError(
            MALFORMED_PINS_FILE,
            f"{path} must be a mapping of artifact id -> digest, e.g.:\n"
            "asg/payments-safety/1.0.0: <64-hex-char digest>\n"
            "payments_safety.spend.weekly/1.0.0: <64-hex-char digest>",
        )
    pins: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            raise RegistryPinError(MALFORMED_PINS_FILE, f"{path}: every pin key must be a non-empty string, got {key!r}")
        if not isinstance(value, str) or not is_hex64(value):
            raise RegistryPinError(
                MALFORMED_PINS_FILE, f"{path}: pins[{key!r}] must be a 64-hex-char digest string, got {value!r}"
            )
        pins[key] = value
    return pins


def verify_pins(pack: PackDefinition, pins: dict[str, str]) -> None:
    """Fail closed: the pack itself, plus every fold it ships, must have a
    matching entry in ``pins``. Raises ``RegistryPinError`` on the first
    missing or mismatched artifact; does not partially verify."""
    _check_one(pack.pack_id, pack.definition_digest(), pins)
    for fold in pack.folds:
        _check_one(fold.fold_id, fold.definition_digest(), pins)


def _check_one(artifact_id: str, actual_digest: str, pins: dict[str, str]) -> None:
    pinned = pins.get(artifact_id)
    if pinned is None:
        raise RegistryPinError(
            PIN_NOT_FOUND,
            f"{artifact_id!r} has no entry in the pins file -- fail closed: an artifact with no "
            f"recorded pin is not installed. If this is the definition you trust, add it to the pins "
            f"file: {artifact_id}: {actual_digest}",
        )
    if pinned != actual_digest:
        raise RegistryPinError(
            PIN_DIGEST_MISMATCH,
            f"{artifact_id!r} digests to {actual_digest}, but the pins file pins it at {pinned} -- "
            "fail closed: this definition does not match what the pins file says it should be. Do not "
            "install it without investigating why (a tampered local copy, a stale pin, or a genuine "
            "version bump that needs a new pin).",
        )
