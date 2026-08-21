# SPDX-License-Identifier: Apache-2.0
"""Opt-in model-drafted PROSE for ``capsule setup propose`` (design decision
[ldg-live-compile-demo]: "the only step that calls a model"). Mirrors the
judge harness's ``--scorer deepeval|static`` seam (``judge/scorers/``,
``cli/judge_cmds.py``) but inverted on the default: judge's scorer IS the
verdict, so it defaults on; here the model drafts ``ProposedOutcome.rationale``
ONLY -- the forward/backward verdict, coverage_n/coverage_m,
missing_instrument, refusal_reason_code, and the candidate/declaration
itself are ``setup.propose``'s pre-existing DETERMINISTIC computation,
untouched -- so this stays OFF by default and ``propose_from_ledger`` never
imports this module unless a caller opts in.

``draft_rationales`` is the only seam that touches a live ``ProposedOutcome``:
it replaces ``rationale`` field-by-field via ``dataclasses.replace``, so
every other field is the SAME object/value, not recomputed -- there is no
code path here that can touch a verdict or a coverage number.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from .propose import ProposalSet, ProposedOutcome

__all__ = [
    "DRAFTER_DEPENDENCY_MISSING",
    "DRAFTER_NO_OUTPUT",
    "DrafterError",
    "RationaleDrafter",
    "StaticRationaleDrafter",
    "DeepEvalRationaleDrafter",
    "draft_rationales",
]

DRAFTER_DEPENDENCY_MISSING = "drafter_dependency_missing"
DRAFTER_NO_OUTPUT = "drafter_no_output"


class DrafterError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@runtime_checkable
class RationaleDrafter(Protocol):
    def draft(self, outcome: ProposedOutcome) -> str: ...


@dataclass(frozen=True)
class StaticRationaleDrafter:
    """No-network deterministic reference (``--drafter static``) -- proves
    the ``draft_rationales`` plumbing (this module, the CLI wiring) end to
    end without a model call or nondeterminism, same role as the judge
    harness's ``StaticScorer``."""

    def draft(self, outcome: ProposedOutcome) -> str:
        fraction = outcome.coverage_fraction()
        prefix = f"observed on {fraction} -- " if fraction is not None else ""
        return f"{prefix}{outcome.statement} ({outcome.rationale})"


class DeepEvalRationaleDrafter:
    """The default BYOM drafter (``--drafter deepeval``) -- one ``deepeval``
    G-Eval call per outcome, prompted to turn the SAME deterministic facts
    (statement, verdicts, coverage, the existing machine-written rationale)
    into plain-language prose. ``deepeval`` is an optional dependency
    (``pip install capsule-ledger[judge]``), imported lazily so
    ``capsule_ledger.setup`` stays importable without it -- the same seam as
    ``judge.scorers.deepeval_scorer.DeepEvalScorer``."""

    def __init__(self, *, model: str | None = None):
        try:
            from deepeval.metrics import GEval
            from deepeval.test_case import LLMTestCase, SingleTurnParams
        except ImportError as exc:
            raise DrafterError(
                DRAFTER_DEPENDENCY_MISSING,
                "DeepEvalRationaleDrafter requires the optional 'deepeval' package -- "
                "install with `pip install capsule-ledger[judge]`",
            ) from exc
        self._GEval = GEval
        self._LLMTestCase = LLMTestCase
        self._eval_params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
        self._model = model

    def draft(self, outcome: ProposedOutcome) -> str:
        fraction = outcome.coverage_fraction()
        facts = (
            f"outcome_id={outcome.outcome_id}; statement={outcome.statement!r}; "
            f"forward_verdict={outcome.forward_verdict}; backward_verdict={outcome.backward_verdict}; "
            f"coverage={fraction or 'n/a'}; deterministic_rationale={outcome.rationale!r}"
        )
        test_case = self._LLMTestCase(input=facts, actual_output=outcome.rationale)
        metric = self._GEval(
            name=f"propose-drafter::{outcome.outcome_id}",
            criteria=(
                "Given the deterministic facts above (verdicts, coverage, evidence rule), write ONE "
                "short paragraph of plain-language rationale prose for a non-technical reader. Do not "
                "invent any number, verdict, or claim not present in the facts."
            ),
            evaluation_params=self._eval_params,
            model=self._model,
        )
        metric.measure(test_case)
        drafted = getattr(metric, "reason", None)
        if not drafted:
            raise DrafterError(
                DRAFTER_NO_OUTPUT,
                f"DeepEvalRationaleDrafter produced no prose for {outcome.outcome_id!r}",
            )
        return drafted


def draft_rationales(proposal_set: ProposalSet, drafter: RationaleDrafter) -> ProposalSet:
    """Replace ONLY ``rationale`` on every proposal, via the given
    ``drafter`` -- every other field is ``dataclasses.replace``d from the
    SAME ``ProposedOutcome`` it started from, never recomputed, so verdict
    pairs and coverage numbers cannot drift from the deterministic run this
    is called on."""
    return replace(
        proposal_set,
        proposals=tuple(replace(p, rationale=drafter.draft(p)) for p in proposal_set.proposals),
    )
