# SPDX-License-Identifier: Apache-2.0
"""The oracle cross-check a ``measurability: declared_not_measured`` claim
needs to be DATA rather than merely asserted ([pack-harden-tau2-oracle]:
closes an adversarial-review finding -- see ``adv-tau2-demo.md`` Area 1/4 --
that the airline-engagement pack's A2/A3a/A5 rows were rendered inapplicable
on every unit via a hardcoded ``always_false`` lambda, with nothing in the
framework checking that the claim "this term is genuinely unmeasurable on
this corpus" was actually true. A future coder could point that same lambda
at a term with a real fail and nothing would catch it).

**What this module proves, and what it deliberately does not.** A term
declared ``measurability: declared_not_measured`` names an
``evidence_instrument`` (``schema.EvidenceInstrument``) -- the specific
signal (a structured field, a named tool call) the term's real check would
need. ``verify_declared_not_measured`` scans a corpus of unit dicts and
raises the moment that signal is found ANYWHERE, proving the "this pack's
corpus never carries the record this term needs" claim rather than trusting
it. It does NOT (and cannot) prove the term's STATEMENT is permanently
unmeasurable in general -- only that, on the corpus actually being verified,
no unit exposes the declared signal. That is the honest, narrow claim
``measurability: declared_not_measured`` is actually making (a fact about
this pack's fixtures/corpus, not a permanent judgment about the statement),
and it is exactly the claim this oracle can mechanically check.

**Corpus shape.** A corpus is any iterable of unit mappings shaped
``{"messages": [{"role": ..., "content": ..., "tool_call_names": [...],
...}]}`` -- the same tau2-``Results.save()`` shape every unit already takes
in this codebase's judge-run tooling (``record_grounding_bench.judge_run
.transcript.Sim.to_dict()``). ``structured_field`` checks any key on a
message dict beyond the ones already named in ``_BASE_MESSAGE_FIELDS``;
``tool_call_name`` checks membership in ``tool_call_names``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .errors import DECLARED_NOT_MEASURED_EVIDENCE_RESOLVED, CorpusVerificationError
from .schema import EvidenceInstrument, PackDefinition

__all__ = ["verify_declared_not_measured", "resolves_instrument"]

# The message-dict keys every unit in this codebase's judge-run corpora
# already carries by construction (transcript.py's `_turn_message`) -- a
# `structured_field` instrument only ever asks about a key OUTSIDE this set;
# a field named e.g. "content" would trivially "resolve" on every unit and
# prove nothing, so that's refused as a pack-authoring mistake, not silently
# accepted.
_BASE_MESSAGE_FIELDS = frozenset({"role", "content", "tool_call_names"})


def _resolves_structured_field(messages: Iterable[Mapping[str, Any]], field: str) -> bool:
    for message in messages:
        if field in _BASE_MESSAGE_FIELDS:
            continue
        value = message.get(field)
        if value not in (None, "", [], {}, ()):
            return True
    return False


def _resolves_tool_call_name(messages: Iterable[Mapping[str, Any]], name: str) -> bool:
    for message in messages:
        if name in (message.get("tool_call_names") or ()):
            return True
    return False


def resolves_instrument(instrument: EvidenceInstrument, messages: Iterable[Mapping[str, Any]]) -> bool:
    """Whether ``instrument`` (a ``structured_field`` or ``tool_call_name``
    signal) resolves anywhere in one unit's ``messages``. The single
    resolve-check implementation in this codebase -- ``verify_declared_not_measured``
    below and ``packs.measurability_report`` (the generic "would this pack
    work" report, a second real caller as of ``[pack-propose-generic]``) both
    call this rather than each carrying their own copy. Public (was
    ``_resolves``): promoted the moment a second caller needed it."""
    if instrument.kind == "structured_field":
        assert instrument.field is not None  # loader guarantees this
        return _resolves_structured_field(messages, instrument.field)
    assert instrument.kind == "tool_call_name" and instrument.name is not None
    return _resolves_tool_call_name(messages, instrument.name)


def verify_declared_not_measured(pack: PackDefinition, corpus: Iterable[Mapping[str, Any]]) -> None:
    """For every outcome in ``pack`` declared ``measurability:
    declared_not_measured``, prove its ``evidence_instrument`` resolves to
    ZERO hits across ``corpus`` -- raising ``CorpusVerificationError`` the
    moment one doesn't. Units are consumed once each (a generator corpus is
    fine, it is only ever iterated over, never rewound) but every
    declared-not-measured outcome is checked against every unit, so a
    generator that can only be iterated once should be materialized by the
    caller first if more than one outcome needs checking (the common case)."""
    declared_not_measured = [o for o in pack.outcomes if o.measurability == "declared_not_measured"]
    if not declared_not_measured:
        return
    units = list(corpus)
    violations: list[str] = []
    for outcome in declared_not_measured:
        instrument = outcome.evidence_instrument
        # loader.py requires this whenever measurability == declared_not_measured;
        # a PackDefinition built by hand (bypassing the loader) could still omit
        # it, so this is a real, not merely defensive, check.
        if instrument is None:
            raise CorpusVerificationError(
                DECLARED_NOT_MEASURED_EVIDENCE_RESOLVED,
                f"outcome {outcome.id!r} declares measurability=declared_not_measured but carries no "
                "evidence_instrument -- nothing for this oracle to check, which is itself the unverifiable "
                "state this module exists to close",
            )
        for unit in units:
            messages = unit.get("messages") or ()
            if resolves_instrument(instrument, messages):
                violations.append(
                    f"outcome {outcome.id!r} declares measurability=declared_not_measured with "
                    f"evidence_instrument={instrument.to_dict()!r}, but that instrument DOES resolve on this "
                    "corpus -- this term is measurable here; either the declared measurability is wrong, or "
                    "a real verdict is being hidden behind a false 'inapplicable' claim"
                )
                break
    if violations:
        raise CorpusVerificationError(DECLARED_NOT_MEASURED_EVIDENCE_RESOLVED, "; ".join(violations))
