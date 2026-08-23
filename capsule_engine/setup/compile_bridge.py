# SPDX-License-Identifier: Apache-2.0
"""Bridges a stored candidate (``setup/candidates.py``'s D-shape) to the
real compiler (``compiler/compile.py``'s ``Declaration``/
``CompiledDeclaration``) -- the one place a stored candidate becomes
something ``compile_declaration`` or ``check_plan_containment`` can
actually run against. Kept separate from ``candidates.py`` (the template
catalog) so that module carries no dependency on the compiler package;
only ``confirm``/``enforce``/``verify`` need this bridge.
"""
from __future__ import annotations

from capsule_compiler.compiler.compile import (
    BackwardCompilation,
    CompiledDeclaration,
    Declaration,
    ForwardCompilation,
    compile_declaration,
)

from .candidates import AttainmentCandidate
from .declarations import StoredCandidate

__all__ = ["attainment_declaration_for", "compiled_declaration_for"]


def attainment_declaration_for(c: AttainmentCandidate) -> Declaration:
    """A pure function of the candidate's own fields -- no corpus, no
    ledger read -- so re-running it always reproduces the identical
    ``Declaration`` and, via ``compile_declaration``, the identical
    ``PlanDefinition``: the re-derivability property design §2.3 asks for,
    made literal. This is what ``enforce`` calls fresh on every shadow run
    and every reproduction -- the plan is never itself persisted, only D."""
    return Declaration(outcome_id=c.outcome_id, statement=c.statement, allowed_actions=(c.action_class,))


def compiled_declaration_for(stored: StoredCandidate) -> CompiledDeclaration:
    """The ``CompiledDeclaration`` T1 freezes (``confirm.confirm_accept``).

    Attainment candidates go through the real compiler
    (``compile_declaration``) -- D alone determines P and F,
    corpus-independent, so recompiling here can never disagree with what
    ``enforce`` recomputes later. Offer/response and refused candidates
    have no plan/fold for this module to (re)compile -- their backward
    verdict is graded against the corpus AT PROPOSE TIME (design §3.4);
    T1 freezes exactly the verdict pair ``propose`` computed and persisted,
    never a live recompute against whatever the corpus looks like right
    now."""
    c = stored.candidate
    if isinstance(c, AttainmentCandidate):
        return compile_declaration(attainment_declaration_for(c))
    forward = ForwardCompilation(
        verdict=stored.forward_verdict,
        refusal_reason_code=stored.refusal_reason_code if stored.forward_verdict == "REFUSED" else None,
    )
    backward = BackwardCompilation(
        verdict=stored.backward_verdict,
        refusal_reason_code=stored.refusal_reason_code if stored.backward_verdict == "REFUSED" else None,
    )
    return CompiledDeclaration(outcome_id=c.outcome_id, forward=forward, backward=backward)
