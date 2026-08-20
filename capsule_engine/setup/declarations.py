# SPDX-License-Identifier: Apache-2.0
"""A local, on-disk store of candidate declarations (``candidates.Candidate``
-- design §2.1's ``D``), keyed by ``outcome_id``. This is what makes every
other setup verb's re-derivability claim actually checkable by a caller who
did not run ``propose`` themselves.

Re-derivability (design §2.3/§2.4) comes from possessing D and recompiling,
never from possessing P. This store is exactly that possession, made
durable across CLI invocations: ``propose`` writes a candidate here as soon
as it drafts it (together with the verdict pair it computed, so
``confirm --accept`` freezes exactly what was proposed rather than
recomputing against a possibly-different corpus snapshot), ``confirm``
flips ``acceptance_state``, ``enforce`` reads an accepted attainment
candidate back to re-derive a fresh ``PlanDefinition`` to check against,
and ``capsule verify --refusal`` reads it to replay a forward refusal from
sealed inputs. Deliberately NOT the ledger -- the digest this store's
bytes produce is what gets sealed onto ledger capsules; this is the
durable copy of the bytes that digest commits to, the same relationship a
git object store has to a commit cited from it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

from agent_action_capsule.canonical import json_digest

from .candidates import Candidate, candidate_from_canonical_dict, candidate_to_canonical_dict

__all__ = ["ACCEPTANCE_STATES", "DeclarationNotFound", "DeclarationStore", "StoredCandidate", "candidate_digest"]

DECLARATIONS_DIRNAME = "declarations"

# ``proposed`` is what ``propose`` writes; ``confirm --accept``/``--refuse``
# flips it, and is the only thing that ever changes on an existing stored
# candidate -- the candidate's own fields never mutate in place (a real
# change is a new outcome_id or goes through propose again, never a silent
# edit).
ACCEPTANCE_STATES = frozenset({"proposed", "accepted", "refused"})


class DeclarationNotFound(KeyError):
    """No candidate with this ``outcome_id`` in the store."""


def candidate_digest(c: Candidate) -> str:
    """D's own digest -- what ``compilation_record.d_digest`` commits to."""
    return json_digest(candidate_to_canonical_dict(c))


@dataclass(frozen=True)
class StoredCandidate:
    candidate: Candidate
    acceptance_state: str
    d_digest: str
    forward_verdict: str | None = None
    backward_verdict: str | None = None
    refusal_reason_code: str | None = None
    missing_instrument: str | None = None


class DeclarationStore:
    """One JSON file per outcome_id under ``<root>/declarations/``. Not
    thread-safe across processes beyond what the filesystem gives a single
    writer -- same scope as every other ``.capsule-setup/`` artifact."""

    def __init__(self, root: str | Path) -> None:
        self._dir = Path(root) / DECLARATIONS_DIRNAME

    @property
    def directory(self) -> Path:
        return self._dir

    def _path(self, outcome_id: str) -> Path:
        # ``quote(..., safe="")`` is injective (unlike a bare ``/`` -> ``__``
        # replace, which collides "a/b" with a literal "a__b") -- collision-
        # free is the property that matters here, not human-readability, so
        # the filename is never decoded back; the outcome_id is read from
        # the JSON body instead.
        return self._dir / f"{quote(outcome_id, safe='')}.json"

    def save(
        self,
        c: Candidate,
        *,
        acceptance_state: str = "proposed",
        forward_verdict: str | None = None,
        backward_verdict: str | None = None,
        refusal_reason_code: str | None = None,
        missing_instrument: str | None = None,
    ) -> Path:
        if acceptance_state not in ACCEPTANCE_STATES:
            raise ValueError(f"acceptance_state must be one of {sorted(ACCEPTANCE_STATES)}; got {acceptance_state!r}")
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(c.outcome_id)
        payload = {
            "acceptance_state": acceptance_state,
            "d_digest": candidate_digest(c),
            "declaration": candidate_to_canonical_dict(c),
            "forward_verdict": forward_verdict,
            "backward_verdict": backward_verdict,
            "refusal_reason_code": refusal_reason_code,
            "missing_instrument": missing_instrument,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def set_acceptance_state(self, outcome_id: str, acceptance_state: str) -> StoredCandidate:
        """Flip ``acceptance_state`` only -- every other field (the
        candidate itself, its verdict pair) is carried over unchanged."""
        stored = self.load(outcome_id)
        self.save(
            stored.candidate,
            acceptance_state=acceptance_state,
            forward_verdict=stored.forward_verdict,
            backward_verdict=stored.backward_verdict,
            refusal_reason_code=stored.refusal_reason_code,
            missing_instrument=stored.missing_instrument,
        )
        return self.load(outcome_id)

    def load(self, outcome_id: str) -> StoredCandidate:
        path = self._path(outcome_id)
        if not path.is_file():
            raise DeclarationNotFound(outcome_id)
        data = json.loads(path.read_text())
        candidate = candidate_from_canonical_dict(data["declaration"])
        return StoredCandidate(
            candidate=candidate,
            acceptance_state=data["acceptance_state"],
            d_digest=data["d_digest"],
            forward_verdict=data.get("forward_verdict"),
            backward_verdict=data.get("backward_verdict"),
            refusal_reason_code=data.get("refusal_reason_code"),
            missing_instrument=data.get("missing_instrument"),
        )

    def exists(self, outcome_id: str) -> bool:
        return self._path(outcome_id).is_file()

    def list_ids(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(unquote(p.stem) for p in self._dir.glob("*.json"))
