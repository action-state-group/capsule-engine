# SPDX-License-Identifier: Apache-2.0
"""Refusal capsules -- the load-bearing artifact of the compiler, not an
ethic (design/build-plan: "refusal capsules ... not an ethic, because
discipline is copyable"). Narrated as an attributed COMPILER claim, never
as a verifier verdict.

Shape, fixed: ``verdict_class`` (the ``VerdictPair`` that was computed, one
side of which is ``"REFUSED"``) + ``statement_digest`` (which declared
statement this refusal is about, cited by digest, never copied) +
``reason_code`` (closed set, ``vocabulary.REFUSAL_REASON_CODES``) +
optionally one labelled proxy-or-instrumentation item.

**Zero free prose on the capsule.** The labelled item's ``label`` is a
short slug (identifier-shaped: lowercase, digits, underscores), never a
sentence -- "names the missing instrument" (design §3.3) means exactly
that, a name, not an explanation. A human-readable explanation belongs in
the disclosable evidence object this capsule's ``statement_digest`` points
at, never inline here.
"""
from __future__ import annotations

import re

from agent_action_capsule.contracts import is_hex64

from capsule_engine.guards.capsule import build_event_capsule
from capsule_engine.guards.signing import Signer

from .vocabulary import REFUSAL_REASON_CODES, VerdictPair

__all__ = ["EVENT_REFUSAL", "LABEL_ITEM_KINDS", "InvalidLabel", "build_refusal_capsule"]

EVENT_REFUSAL = "compiler.refusal"

LABEL_ITEM_KINDS = frozenset({"proxy", "instrumentation"})

_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class InvalidLabel(ValueError):
    """A labelled item's ``label`` is not a short identifier-shaped slug --
    i.e. it looks like prose, which this capsule must never carry."""


def build_refusal_capsule(
    *,
    verdict: VerdictPair,
    statement_digest: str,
    reason_code: str,
    operator: str,
    developer: str,
    signer: Signer,
    labelled_item_kind: str | None = None,
    labelled_item_label: str | None = None,
    timestamp: str | None = None,
    action_id: str | None = None,
) -> dict:
    if "REFUSED" not in (verdict.forward, verdict.backward):
        raise ValueError("a refusal capsule requires at least one side of the verdict pair to be REFUSED")
    if not is_hex64(statement_digest):
        raise ValueError(f"statement_digest must be a 64-hex SHA-256 digest; got {statement_digest!r}")
    if reason_code not in REFUSAL_REASON_CODES:
        raise ValueError(f"reason_code must be one of {sorted(REFUSAL_REASON_CODES)}; got {reason_code!r}")
    if (labelled_item_kind is None) != (labelled_item_label is None):
        raise ValueError("labelled_item_kind and labelled_item_label must both be set or both be omitted")

    detail: dict = {
        "verdict_class": {"forward": verdict.forward, "backward": verdict.backward},
        "statement_digest": statement_digest,
        "reason_code": reason_code,
    }
    if labelled_item_kind is not None:
        if labelled_item_kind not in LABEL_ITEM_KINDS:
            raise ValueError(f"labelled_item_kind must be one of {sorted(LABEL_ITEM_KINDS)}; got {labelled_item_kind!r}")
        if not _LABEL_RE.match(labelled_item_label):
            raise InvalidLabel(
                f"labelled_item_label must be a short slug matching {_LABEL_RE.pattern} "
                f"(names the missing instrument/proxy; never a sentence) -- got {labelled_item_label!r}"
            )
        detail["labelled_item"] = {"kind": labelled_item_kind, "label": labelled_item_label}

    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_REFUSAL,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id or f"compiler.refusal/{statement_digest}",
    )
