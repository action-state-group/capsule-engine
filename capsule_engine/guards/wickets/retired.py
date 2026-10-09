# SPDX-License-Identifier: Apache-2.0
"""Retired wicket definitions: an (id, digest) pair that must not load.

A definition is retired when its meaning moved without its digest moving, so
the pin no longer tells a reader which verdicts it produces. The bytes stay
recomputable (the digest is listed here verbatim); what changes is that the
loader, the catalog and a pack citing the pair all refuse it, naming the
replacement. A retirement is permanent: a row is never removed.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RetiredDefinition", "RETIRED", "RETIRED_IDS", "retired_entry"]


@dataclass(frozen=True)
class RetiredDefinition:
    wicket_id: str
    digest: str
    reason: str
    replaced_by: str


RETIRED: tuple[RetiredDefinition, ...] = (
    RetiredDefinition(
        wicket_id="offer_expiry/1.0.0",
        digest="7b1072fc6997b07e7f08941a723e60d53fd3a54dbccfda6fa7391124ec2702ee",
        reason=(
            "its check changed meaning under this digest (an expired or unverifiable proposal went from "
            "not applicable to fail) because the digest covered config only"
        ),
        replaced_by="offer_expiry/1.0.1",
    ),
)

_BY_PAIR = {(r.wicket_id, r.digest): r for r in RETIRED}
RETIRED_IDS = frozenset(r.wicket_id for r in RETIRED)


def retired_entry(wicket_id: str, digest: str) -> RetiredDefinition | None:
    """The retirement row for this exact (id, digest) pair, if there is one."""
    return _BY_PAIR.get((wicket_id, digest))
