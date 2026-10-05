# SPDX-License-Identifier: Apache-2.0
"""Versioning and diff rules for ``capsule_engine/schemas/evidence-contract-v0.json``.

Two things live here:

* ``contract_pin`` -- how a Result names the exact contract it was evaluated
  against: the compact ``<id>@<version>`` reference every claim already
  carries, plus the SHA-256 digest of the contract's RFC 8785 (JCS) bytes. The
  version label is what a reader quotes; the digest is what a verifier
  recomputes. A version label names exactly one digest: the same
  ``<id>@<version>`` with two different digests is reported as
  ``version_reused`` (always breaking).

* ``diff_contracts`` -- classify every difference between two contracts as
  ``breaking`` or ``non_breaking``, with a reason.

What "breaking" means. A change from A to B is non-breaking only when every
evidence set that satisfied a requirement of B's under A still satisfies it
under B, and every claim made against A still names a requirement of B. Put
the other way: a change is breaking if evidence that satisfied A might not
satisfy B (a tightening), if a requirement's identity changed (id, profile,
clause), or if the direction of the change cannot be determined (an opaque
predicate string, a new evidence source in place of an old one). Loosening is
non-breaking but is still reported, so a reader sees assurance going down.
Anything without an explicit rule below is breaking: a field is safe to change
only because a rule here says so.

The rules, by field (paths are relative to one requirement unless rooted). A
field rule applies only where the schema defines that field for the
requirement's shape on both sides -- e.g. ``tier`` and ``window`` only on the
native shape, ``required_sequence`` only on ``process``. An extension field on
an open profile (process, quality, human_role) that happens to share a rule's
name gets no rule, so any change to it is breaking.

* requirement added -- breaking (``requirement_added``): new evidence is owed.
* requirement removed -- breaking (``requirement_removed``): a claim made
  against A that cites it no longer names a requirement of B.
* requirement re-id (same content, new id) -- breaking (``requirement_reid``):
  claims cite requirements by id, so every prior claim stops resolving.
* requirements reordered -- non-breaking (``requirements_reordered``):
  requirements are keyed by id.
* root ``id`` changed -- breaking (``contract_id_changed``).
* root ``version`` changed -- non-breaking on its own (``version_changed``).
* ``statement`` reworded -- non-breaking (``editorial``) only when the
  requirement is deterministically evaluated on both sides; otherwise breaking,
  because the statement is what a judge or a human evaluates.
* ``accepted_epistemic_types`` -- narrower is ``tightened``, wider is
  ``loosened``, neither is ``changed``. Absent means any type is accepted.
* ``required_sources``, ``approvals`` -- more is ``tightened``, fewer is
  ``loosened``, a swap (a source changed) is ``changed``. Absent means none.
* ``minimum_assurance``, ``required_assurance_grade`` -- compared on the
  assurance ladder self-attested < witnessed < countersigned (the Evidence
  Result's ``Grade`` values); a higher floor is ``tightened``. An unknown
  grade is ``changed``. Absent means no floor. A list whose members change
  but whose floor does not is ``changed`` (breaking): what a list of several
  grades means is not yet defined by the schema.
* ``freshness``, ``window.duration``, ``window.cure``, ``window.grace`` --
  ISO-8601 durations; shorter is ``tightened``, longer is ``loosened``. An
  absent ``freshness`` or ``window`` is unbounded; an absent or null cure or
  grace is zero. Years and months are compared with each other (a year is
  twelve months), and weeks, days, hours, minutes and seconds with each other,
  but a month is never equated with a number of days: when one side is longer
  in months and the other in days, the two are not comparable and the change is
  ``changed``. A duration that does not parse is ``changed``.
* ``tier`` -- informational to must_have is ``tightened``, the reverse
  ``loosened``. Absent means informational.
* ``required_sequence`` -- B a subsequence of A is ``loosened``, A a
  subsequence of B is ``tightened``, otherwise ``changed``.
* ``escalation_path`` (process, and ``authority.escalation_path`` on the
  abstract outcome shape), ``clause.source_url`` (native shape) --
  ``editorial``: neither is read when sufficiency is decided.
* everything else, at the root or in a requirement -- ``changed`` (breaking).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from agent_action_capsule.canonical import json_digest

from capsule_engine.packs.contract_validate import validate_evidence_contract

__all__ = [
    "BREAKING",
    "NON_BREAKING",
    "Change",
    "ContractDiff",
    "ContractDiffError",
    "contract_digest",
    "contract_pin",
    "contract_ref",
    "diff_contracts",
    "main",
]

BREAKING = "breaking"
NON_BREAKING = "non_breaking"

_SEVERITY = {
    "contract_id_changed": BREAKING,
    "version_reused": BREAKING,
    "version_changed": NON_BREAKING,
    "requirement_added": BREAKING,
    "requirement_removed": BREAKING,
    "requirement_reid": BREAKING,
    "requirements_reordered": NON_BREAKING,
    "tightened": BREAKING,
    "loosened": NON_BREAKING,
    "changed": BREAKING,
    "editorial": NON_BREAKING,
}

ASSURANCE_LADDER = ("self-attested", "witnessed", "countersigned")
_TIER_RANK = {"informational": 0, "must_have": 1}
_JUDGED_ADJUDICATION = {"semantic", "human", "hybrid"}

_MISSING = object()


class ContractDiffError(ValueError):
    """The inputs cannot be diffed (e.g. two requirements share an id)."""


@dataclass(frozen=True)
class Change:
    path: str
    kind: str
    reason: str

    @property
    def severity(self) -> str:
        return _SEVERITY[self.kind]

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "severity": self.severity, "reason": self.reason}


@dataclass(frozen=True)
class ContractDiff:
    a: dict[str, Any]
    b: dict[str, Any]
    changes: list[Change] = field(default_factory=list)

    @property
    def breaking(self) -> bool:
        return any(c.severity == BREAKING for c in self.changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "a": self.a,
            "b": self.b,
            "breaking": self.breaking,
            "changes": [c.to_dict() for c in self.changes],
        }


def contract_ref(doc: dict[str, Any]) -> str:
    return f"{doc['id']}@{doc['version']}"


def contract_digest(doc: dict[str, Any]) -> str:
    """Lowercase-hex SHA-256 of the contract's RFC 8785 (JCS) serialization."""
    return json_digest(doc)


def contract_pin(doc: dict[str, Any]) -> dict[str, Any]:
    """The exact contract a Result was evaluated against: the compact
    reference a reader quotes, and the digest a verifier recomputes."""
    return {
        "contract_ref": contract_ref(doc),
        "contract_digest": {"digest_alg": "SHA-256", "digest": contract_digest(doc)},
    }


# -- field rules -------------------------------------------------------------


def _not_a(kind: type, *values: Any) -> bool:
    """Defence in depth for unvalidated input: a value of the wrong JSON type
    is never ranked, only reported as changed."""
    return any(v is not _MISSING and not isinstance(v, kind) for v in values)


def _set(value: Any) -> set[str] | None:
    return None if value is _MISSING else set(value)


def _names(values: set[str]) -> str:
    return ", ".join(sorted(values))


def _compare_sets(path: str, a: Any, b: Any, *, more_is_tighter: bool, what: str) -> list[Change]:
    """``more_is_tighter``: each added member is one more thing owed (sources,
    approvals). Otherwise each added member is one more thing accepted
    (epistemic types), and absence means "everything is accepted"."""
    if _not_a(list, a, b):
        return [Change(path, "changed", f"{what}: not a list; direction cannot be determined")]
    sa, sb = _set(a), _set(b)
    if more_is_tighter:
        sa, sb = sa or set(), sb or set()
    if sa == sb:
        return []
    if not more_is_tighter:
        if sa is None:
            return [Change(path, "tightened", f"{what}: was unrestricted, now only {_names(sb)}")]
        if sb is None:
            return [Change(path, "loosened", f"{what}: was {_names(sa)}, now unrestricted")]
    added, removed = sb - sa, sa - sb
    if added and removed:
        return [Change(path, "changed", f"{what}: {_names(removed)} replaced by {_names(added)}; evidence for the old members may not count")]
    grew = bool(added)
    tighter = grew if more_is_tighter else not grew
    detail = f"added {_names(added)}" if grew else f"removed {_names(removed)}"
    return [Change(path, "tightened" if tighter else "loosened", f"{what}: {detail}")]


def _grade_floor(value: Any) -> int | None:
    """Rank of the lowest grade named, -1 for no floor, None if a grade is unknown."""
    if value is _MISSING:
        return -1
    if not isinstance(value, (str, list)):
        return None
    grades = [value] if isinstance(value, str) else list(value)
    if not grades:
        return -1
    if any(g not in ASSURANCE_LADDER for g in grades):
        return None
    return min(ASSURANCE_LADDER.index(g) for g in grades)


def _compare_grades(path: str, a: Any, b: Any) -> list[Change]:
    if a == b:
        return []
    fa, fb = _grade_floor(a), _grade_floor(b)
    if fa is None or fb is None:
        return [Change(path, "changed", "assurance grade not on the ladder; direction cannot be determined")]
    if fa == fb:
        return [Change(path, "changed", "grades listed changed at the same floor; multi-grade lists are not yet defined")]
    if fb > fa:
        return [Change(path, "tightened", "assurance floor raised")]
    return [Change(path, "loosened", "assurance floor lowered")]


# Each component is at most 9 digits so every implementation can compare in a
# 64-bit integer; a longer component does not parse.
_DURATION = re.compile(
    r"^P(?!$)(?:(\d{1,9})Y)?(?:(\d{1,9})M)?(?:(\d{1,9})W)?(?:(\d{1,9})D)?"
    r"(?:T(?=\d)(?:(\d{1,9})H)?(?:(\d{1,9})M)?(?:(\d{1,9})S)?)?$"
)
# A duration is (months, seconds): years and months are calendar units of
# varying length, so they are never converted into seconds.
_UNBOUNDED = "unbounded"
_ZERO = (0, 0)


def _duration_parts(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    m = _DURATION.match(value)
    if not m:
        return None
    y, mo, w, d, h, mi, sec = (int(g) if g else 0 for g in m.groups())
    return 12 * y + mo, (((7 * w + d) * 24 + h) * 60 + mi) * 60 + sec


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def _order(da: Any, db: Any) -> int | None:
    """-1 if ``db`` is shorter, 0 if equal, 1 if longer, None if the two
    cannot be ordered (one longer in months, the other in seconds)."""
    if da == _UNBOUNDED or db == _UNBOUNDED:
        return 0 if da == db else (1 if db == _UNBOUNDED else -1)
    dm, ds = _sign(db[0] - da[0]), _sign(db[1] - da[1])
    if dm == 0 or ds == 0 or dm == ds:
        return dm or ds
    return None


def _compare_durations(path: str, a: Any, b: Any, *, absent: Any, what: str) -> list[Change]:
    """A longer duration is looser. ``absent`` is what an absent (or null)
    value stands for: ``_UNBOUNDED`` for a limit, ``_ZERO`` for a grace."""
    if a == b:
        return []
    da = absent if a is _MISSING or a is None else _duration_parts(a)
    db = absent if b is _MISSING or b is None else _duration_parts(b)
    if da is None or db is None:
        return [Change(path, "changed", f"{what}: not an ISO-8601 duration; direction cannot be determined")]
    order = _order(da, db)
    if order is None:
        return [Change(path, "changed", f"{what}: months and days are not exactly comparable")]
    if order == 0:
        return [Change(path, "editorial", f"{what}: same length, spelled differently")]
    if order < 0:
        return [Change(path, "tightened", f"{what}: shortened")]
    return [Change(path, "loosened", f"{what}: lengthened")]


def _compare_window(path: str, a: Any, b: Any) -> list[Change]:
    if a == b:
        return []
    if a is _MISSING:
        return [Change(path, "tightened", "window added: evidence must now fall inside it")]
    if b is _MISSING:
        return [Change(path, "loosened", "window removed")]
    if not isinstance(a, dict) or not isinstance(b, dict):
        return [Change(path, "changed", "value changed; no rule says this is safe")]
    out = _compare_durations(f"{path}/duration", a.get("duration", _MISSING), b.get("duration", _MISSING),
                             absent=_UNBOUNDED, what="window duration")
    for key in ("cure", "grace"):
        out += _compare_durations(f"{path}/{key}", a.get(key, _MISSING), b.get(key, _MISSING),
                                  absent=_ZERO, what=f"window {key}")
    for key in sorted((set(a) | set(b)) - {"duration", "cure", "grace"}):
        out += _compare(f"{path}/{key}", key, a.get(key, _MISSING), b.get(key, _MISSING), {})
    return out


def _compare_tier(path: str, a: Any, b: Any) -> list[Change]:
    # An absent tier is "informational" (EvidenceContract.tier's default).
    ta = "informational" if a is _MISSING else a
    tb = "informational" if b is _MISSING else b
    if ta == tb:
        return []
    if _not_a(str, ta, tb):
        return [Change(path, "changed", "tier changed")]
    ra, rb = _TIER_RANK.get(ta), _TIER_RANK.get(tb)
    if ra is None or rb is None:
        return [Change(path, "changed", "tier changed")]
    return [Change(path, "tightened" if rb > ra else "loosened", f"tier {ta} -> {tb}")]


def _is_subsequence(short: list[Any], long: list[Any]) -> bool:
    it = iter(long)
    return all(x in it for x in short)


def _compare_sequence(path: str, a: Any, b: Any) -> list[Change]:
    if a != b and _not_a(list, a, b):
        return [Change(path, "changed", "required sequence is not a list; direction cannot be determined")]
    la = [] if a is _MISSING else list(a)
    lb = [] if b is _MISSING else list(b)
    if la == lb:
        return []
    if _is_subsequence(lb, la):
        return [Change(path, "loosened", "required sequence has fewer steps")]
    if _is_subsequence(la, lb):
        return [Change(path, "tightened", "required sequence has more steps")]
    return [Change(path, "changed", "required sequence reordered or replaced")]


def _editorial(path: str, a: Any, b: Any) -> list[Change]:
    if a == b:
        return []
    if _not_a(str, a, b):
        return [Change(path, "changed", "not a string; no rule says this is safe")]
    return [Change(path, "editorial", "not read when sufficiency is decided")]


_EVIDENCE_REQUIREMENTS_RULES: dict[str, Any] = {
    "evidence_requirements/accepted_epistemic_types":
        lambda p, a, b: _compare_sets(p, a, b, more_is_tighter=False, what="accepted epistemic types"),
    "evidence_requirements/required_sources":
        lambda p, a, b: _compare_sets(p, a, b, more_is_tighter=True, what="required sources"),
    "evidence_requirements/minimum_assurance": _compare_grades,
    "evidence_requirements/freshness":
        lambda p, a, b: _compare_durations(p, a, b, absent=_UNBOUNDED, what="freshness"),
}

# Field rules by requirement shape, keyed by the path relative to the
# requirement: a rule applies only where the schema defines that field.
_SHAPE_RULES: dict[str, dict[str, Any]] = {
    "native": {
        "window": _compare_window,
        "tier": _compare_tier,
        "required_assurance_grade": _compare_grades,
        "clause/source_url": _editorial,
    },
    "outcome": {**_EVIDENCE_REQUIREMENTS_RULES, "authority/escalation_path": _editorial},
    "obligation": dict(_EVIDENCE_REQUIREMENTS_RULES),
    "process": {
        **_EVIDENCE_REQUIREMENTS_RULES,
        "required_sequence": _compare_sequence,
        "approvals": lambda p, a, b: _compare_sets(p, a, b, more_is_tighter=True, what="approvals"),
        "escalation_path": _editorial,
    },
    "human_role": dict(_EVIDENCE_REQUIREMENTS_RULES),
}


def _shape(req: dict[str, Any]) -> str | None:
    """``native`` for the pack-declared shape (outcome or obligation with an
    ``evidence_rule``), otherwise the profile."""
    profile = req.get("profile")
    if "evidence_rule" in req and profile in (None, "outcome", "obligation"):
        return "native"
    return profile


def _compare(path: str, rel: str, a: Any, b: Any, rules: dict[str, Any]) -> list[Change]:
    """``rel`` is ``path`` relative to the requirement (or the root), the key
    ``rules`` is looked up by."""
    if rel in rules:
        return rules[rel](path, a, b)
    if a == b:
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[Change] = []
        for k in sorted(set(a) | set(b)):
            out += _compare(f"{path}/{k}", f"{rel}/{k}", a.get(k, _MISSING), b.get(k, _MISSING), rules)
        return out
    if a is _MISSING:
        return [Change(path, "changed", "field added; no rule says this is safe")]
    if b is _MISSING:
        return [Change(path, "changed", "field removed; no rule says this is safe")]
    return [Change(path, "changed", "value changed; no rule says this is safe")]


def _deterministic(req: dict[str, Any]) -> bool:
    """Positively evaluated without a judge: the only case where rewording
    the statement cannot move a result."""
    shape = _shape(req)
    if shape == "outcome":
        adjudication = req.get("adjudication")
        return isinstance(adjudication, dict) and adjudication.get("mode") == "deterministic"
    if shape == "native":
        return req.get("backward_verdict") == "DETERMINISTIC" and req.get("mode") != "judged"
    return False


def _compare_requirement(rid: str, a: dict[str, Any], b: dict[str, Any]) -> list[Change]:
    base = f"requirements[{rid}]"
    shape = _shape(a)
    rules = _SHAPE_RULES.get(shape, {}) if shape is not None and shape == _shape(b) else {}
    out: list[Change] = []
    for key in sorted((set(a) | set(b)) - {"id"}):
        va, vb = a.get(key, _MISSING), b.get(key, _MISSING)
        path = f"{base}/{key}"
        if key == "statement" and va != vb:
            if _deterministic(a) and _deterministic(b):
                out.append(Change(path, "editorial", "statement reworded; the requirement is evaluated deterministically"))
            else:
                out.append(Change(path, "changed", "statement reworded; a judge or human evaluates this wording"))
            continue
        if key == "profile" and va != vb:
            out.append(Change(path, "changed", "profile changed: a different kind of requirement"))
            continue
        out += _compare(path, key, va, vb, rules)
    return out


def _by_id(doc: dict[str, Any], side: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for req in doc["requirements"]:
        if req["id"] in out:
            raise ContractDiffError(f"contract {side} has two requirements with id {req['id']}")
        out[req["id"]] = req
    return out


def _without_id(req: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in req.items() if k != "id"}


def diff_contracts(a: dict[str, Any], b: dict[str, Any]) -> ContractDiff:
    """Classify every difference from contract ``a`` to contract ``b``. Both
    are expected to be schema-valid; see the module docstring for the rules."""
    reqs_a, reqs_b = _by_id(a, "A"), _by_id(b, "B")
    pin_a, pin_b = contract_pin(a), contract_pin(b)
    changes: list[Change] = []

    if a["id"] != b["id"]:
        changes.append(Change("id", "contract_id_changed", "claims name their contract by id; claims against A do not name B"))
    elif a["version"] == b["version"] and pin_a["contract_digest"] != pin_b["contract_digest"]:
        changes.append(Change("version", "version_reused", "one version label now names two different contracts"))
    if a["version"] != b["version"]:
        changes.append(Change("version", "version_changed", f"version {a['version']} -> {b['version']}"))

    for key in sorted((set(a) | set(b)) - {"id", "version", "requirements"}):
        changes += _compare(key, key, a.get(key, _MISSING), b.get(key, _MISSING), {})

    removed = [rid for rid in reqs_a if rid not in reqs_b]
    added = [rid for rid in reqs_b if rid not in reqs_a]
    for old in list(removed):
        match = next((new for new in added if _without_id(reqs_a[old]) == _without_id(reqs_b[new])), None)
        if match is not None:
            removed.remove(old)
            added.remove(match)
            changes.append(Change(f"requirements[{old}]", "requirement_reid", f"re-identified as {match}; claims citing {old} no longer resolve"))
    for rid in removed:
        changes.append(Change(f"requirements[{rid}]", "requirement_removed", "no longer required: claims against A that cite it do not resolve in B"))
    for rid in added:
        changes.append(Change(f"requirements[{rid}]", "requirement_added", "new requirement: evidence that satisfied A says nothing about it"))

    common_a = [rid for rid in reqs_a if rid in reqs_b]
    common_b = [rid for rid in reqs_b if rid in reqs_a]
    if common_a != common_b:
        changes.append(Change("requirements", "requirements_reordered", "requirements are keyed by id; order carries no meaning"))
    for rid in common_a:
        changes += _compare_requirement(rid, reqs_a[rid], reqs_b[rid])

    changes.sort(key=lambda c: (c.path, c.kind))
    return ContractDiff(a=pin_a, b=pin_b, changes=changes)


# -- CLI ---------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text())
    validate_evidence_contract(doc)
    return doc


def main(argv: list[str] | None = None) -> int:
    """Exit 0: identical or non-breaking. Exit 1: breaking. Exit 2: an input
    is missing, malformed, or not a valid Evidence Contract."""
    parser = argparse.ArgumentParser(prog="python -m capsule_engine.packs.contract_diff")
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--json", action="store_true", help="emit the diff as JSON")
    args = parser.parse_args(argv)
    try:
        diff = diff_contracts(_load(args.a), _load(args.b))
    except (OSError, json.JSONDecodeError, jsonschema.exceptions.ValidationError, ContractDiffError) as exc:
        message = exc.message if isinstance(exc, jsonschema.exceptions.ValidationError) else str(exc)
        print(f"contract diff: {message}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(diff.to_dict(), indent=2))
    else:
        print(f"A: {diff.a['contract_ref']} sha256:{diff.a['contract_digest']['digest']}")
        print(f"B: {diff.b['contract_ref']} sha256:{diff.b['contract_digest']['digest']}")
        if not diff.changes:
            print("identical")
        for c in diff.changes:
            print(f"  {c.severity:<12} {c.kind:<22} {c.path}: {c.reason}")
        print("BREAKING" if diff.breaking else "non-breaking")
    return 1 if diff.breaking else 0


if __name__ == "__main__":
    raise SystemExit(main())
