# SPDX-License-Identifier: Apache-2.0
"""Packaging

One wheel, one codebase. ``guards-only`` keeps the
caps/dedupe/verify-before-dispatch checks and ``dry_run`` fully functional;
the evidence machinery underneath (capsules, permalinks, verify surfaces)
stays present in the code but is not surfaced: no capsule vocabulary in CLI
output, no share/verify links printed or rendered, and the record-query verbs
(``log``/``show``/``verify``) are not registered at all. ``full``
is today's existing behavior, unchanged.

``guards-only`` changes what is shown, never what is recorded:
every check still seals its record to the ledger;
the CLI simply doesn't register the record-query verbs or print capsule
vocabulary and links. Switching to full at any time surfaces the complete
history recorded all along.

Mechanism, and why: an environment variable rather than a pip extra, a
persistent config file, or a per-invocation CLI flag.

* A pip extra (``capsule-ledger[evidence]``) implies a *dependency* difference --
  something extra gets installed. Nothing does: both arms import the exact
  same modules, the only difference is what's registered/rendered. An extra
  would misdescribe what actually changed.
* A per-invocation flag (``--minimal``/``--full``) has to be remembered on
  every command, which is exactly the kind of friction that would confound
  the very activation/enforcement metrics this packaging split exists to
  measure honestly.
* A persistent config file needs an init step before first use, which adds
  friction to the "clean install" acceptance test this task is measured
  against.
* An env var can be set once (in the environment an operator already
  controls for their agent runtime) and then every ``capsule`` invocation in
  that environment picks it up consistently, with zero extra state and zero
  per-command overhead.
"""
from __future__ import annotations

from .envcompat import env_get

__all__ = ["GUARDS_ONLY", "FULL", "current_arm", "evidence_visible"]

GUARDS_ONLY = "guards-only"
FULL = "full"
_VALID = (GUARDS_ONLY, FULL)
_ENV_VAR = "CAPSULE_LEDGER_ARM"


def current_arm() -> str:
    """The active packaging arm for this process: ``$CAPSULE_LEDGER_ARM``
     if it names a valid arm, else ``full``
    (today's existing, unchanged behavior)."""
    value = env_get(_ENV_VAR, FULL).strip().lower()
    return value if value in _VALID else FULL


def evidence_visible(arm: str | None = None) -> bool:
    """Whether evidence (capsule vocabulary, permalinks, verify surfaces)
    should be surfaced to the user for the given (or current) arm."""
    return (arm or current_arm()) == FULL
