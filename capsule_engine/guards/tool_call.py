# SPDX-License-Identifier: Apache-2.0
"""The tool-call lane (C3, ``[ldg-plan-containment]``): reads emit passive
``fyi`` records; writes route through ``GuardEngine.check()``, which already
IS the enforcement point -- a departure fails the ``plan_containment``
constraint, ``GuardEngine`` denies, and the resulting decision capsule (with
``disposition.decision == "reject"``, ``verdict_class == "blocked"`` --
``guards/capsule.py``) is the refusal capsule. No new gate is built here;
this module only gives tool-call reads the same "record what happened, never
gate it" shape ``build_event_capsule`` already gives every other passive
observation (a conversation turn, a confirm-ingester poll), so a read and a
write are each recorded through the one mechanism suited to what they are.

Observe vs. enforce is a CALLER decision, not something this module decides:
``GuardEngine.check()`` always evaluates every constraint and always records
its verdict, regardless of ``dry_run`` (which only affects the ledger
append's own durability flag -- ``guards/engine.py``). What makes a write
"observed" rather than "enforced" is whether the caller goes on to actually
dispatch the underlying action when ``decision.outcome != "allow"`` -- e.g.
``examples/plan_containment_demo/demo.py``'s Run B never calls (simulates)
``export_user_list`` once containment denies it. ``capsule guard enforce``
(``cli/guard_cmds.py``) is the existing, separate marker for "this
integration has moved from observe to enforce" -- this module doesn't
duplicate it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from capsule_ledger.ledger.api import LedgerAPI
from capsule_ledger.ledger.records import LedgerRecord

from ..events.capsule import build_event_capsule
from .signing import Signer

__all__ = ["TOOL_CALL_LANE", "ToolCallLane"]

# Marker carried in a tool-call read's ``asg_payload.detail`` so a reader (or
# the demo page's rendering vocabulary) can group rows by lane without
# maintaining a separate hardcoded verb list -- the SAME lane marker a
# write's own decision capsule carries via its ``action_class``/verb shape
# implicitly (a "decide"-typed capsule is always a tool-call-lane write; see
# this module's docstring for why writes need no marker of their own).
TOOL_CALL_LANE = "tool_call"


@dataclass
class ToolCallLane:
    """One tool-using agent's read side of the tool-call lane. Mirrors
    ``conversation.session.ConversationSession``'s shape: ``signer_provider``
    is called once per record, never cached."""

    ledger: LedgerAPI
    operator: str
    developer: str
    signer_provider: Callable[[], Signer]

    def record_read(
        self,
        *,
        verb: str,
        detail: dict,
        timestamp: str | None = None,
        action_id: str | None = None,
        chain_parent: str | None = None,
        chain_relation: str | None = None,
    ) -> LedgerRecord:
        """Record one tool-call READ as a passive ``fyi`` capsule, event
        ``verb`` (e.g. ``"read_user_directory"``) -- never gated, same as
        every other observation this codebase records with
        ``build_event_capsule``."""
        capsule = build_event_capsule(
            operator=self.operator,
            developer=self.developer,
            signer=self.signer_provider(),
            event=verb,
            detail={"lane": TOOL_CALL_LANE, **detail},
            timestamp=timestamp,
            action_id=action_id,
            chain_parent=chain_parent,
            chain_relation=chain_relation,
        )
        return self.ledger.append(capsule, consequential=False)
