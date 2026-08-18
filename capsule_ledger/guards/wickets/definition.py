# SPDX-License-Identifier: Apache-2.0
"""Wicket definitions: parsing, validation, and the definition_digest.

A "wicket" is this workspace's name for a guard
constraint -- caps / dedupe / verify_before_dispatch (``guards/checks/*.py``).
Unlike a fold, a wicket's CHECK LOGIC is Python code, not declarative data --
there is no YAML that fully describes "how caps computes weekly spend". What
*is* declarative, and varies per deployment, is the wicket's own
*configuration*: which check it invokes and the parameters that check runs
with (caps' per-class minor-unit limits, dedupe's window). A ``WicketDefinition``
captures exactly that slice -- ``wicket_id``, ``check``, ``config`` -- the same
way ``folds/definition.py``'s ``FoldDefinition`` captures a fold. Its digest
(SHA-256 over the JCS-canonical bytes, via the same
``agent_action_capsule.canonical.json_digest`` the fold definitions and the
capsule format itself use) identifies "this configuration of this check", not
the Python source file the check lives in -- a source-code change with no
config change is invisible to this digest, and that is intentional: this is a
policy-configuration digest, not a code-integrity digest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

from .errors import (
    FLOAT_IN_DEFINITION,
    INVALID_WICKET_ID,
    MALFORMED_DEFINITION,
    UNKNOWN_CHECK,
    UNSAFE_INTEGER_IN_DEFINITION,
    WicketDefinitionError,
)

__all__ = ["WICKET_ID_RE", "KNOWN_CHECKS", "WicketDefinition", "parse_definition"]

# wicket_id: same "human name + semver" shape as fold_id (folds/definition.py).
WICKET_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*/\d+\.\d+\.\d+$")

# The closed, registered set of checks a wicket may configure -- the three
# reference checks ``guards/engine.py`` evaluates and ``cli/constraints_cmd.
# py``'s ``CHECKS`` catalog lists, plus ``hold_reconcile`` (``holds/engine.
# py``'s tolerance check for planned-vs-executed reconciliation, capsule-emit
# #53). Closed for the same reason ``folds/definition.py``'s
# ``KNOWN_REDUCERS`` is: an unregistered name is a typo or a not-yet-built
# check, never silently accepted as data.
KNOWN_CHECKS = frozenset({"dedupe", "caps", "verify_before_dispatch", "hold_reconcile"})


@dataclass(frozen=True)
class WicketDefinition:
    wicket_id: str
    check: str
    config: dict[str, Any] = field(default_factory=dict)

    def canonical_dict(self) -> dict:
        """The JCS-canonicalizable form of this definition -- drives definition_digest."""
        return {"wicket_id": self.wicket_id, "check": self.check, "config": self.config}

    def definition_digest(self) -> str:
        """SHA-256 over the JCS bytes of the canonical definition."""
        try:
            return json_digest(self.canonical_dict())
        except FloatInDigestError as exc:
            raise WicketDefinitionError(FLOAT_IN_DEFINITION, str(exc)) from exc
        except UnsafeIntegerError as exc:
            raise WicketDefinitionError(UNSAFE_INTEGER_IN_DEFINITION, str(exc)) from exc


def parse_definition(data: Any) -> WicketDefinition:
    """Validate a plain dict (as loaded from YAML) into a ``WicketDefinition``."""
    if not isinstance(data, dict):
        raise WicketDefinitionError(MALFORMED_DEFINITION, "definition must be a mapping")

    wicket_id = data.get("wicket_id")
    if not isinstance(wicket_id, str) or not WICKET_ID_RE.match(wicket_id):
        raise WicketDefinitionError(
            INVALID_WICKET_ID,
            f"wicket_id {wicket_id!r} must match '<namespace>[.<namespace>...]/<major>.<minor>.<patch>' "
            "(e.g. 'caps/1.0.0')",
        )

    check = data.get("check")
    if check not in KNOWN_CHECKS:
        raise WicketDefinitionError(
            UNKNOWN_CHECK, f"check {check!r} is not in the closed registered set {sorted(KNOWN_CHECKS)}"
        )

    config = data.get("config") if "config" in data else {}
    if not isinstance(config, dict):
        raise WicketDefinitionError(MALFORMED_DEFINITION, "config must be a mapping")

    return WicketDefinition(wicket_id=wicket_id, check=check, config=config)
