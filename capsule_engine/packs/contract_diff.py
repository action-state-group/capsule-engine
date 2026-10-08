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

The same rules, aimed at a RuleSet. ``diff_rulesets`` diffs what is in force
-- the installed packs (by pin and mode) and the user's policy profile
(``policy/profile.py``) -- so the screen that shows an upgrade or a changed
limit is a rendering of this output (``render_diff``), never a second
computation. A RuleSet is ``{"packs": [{pack_id, digest, mode}], "profile":
<policy-profile/v0>}``, the shape an activation record carries
(``ruleset_from_activation``); packs are keyed by ``publisher/name``.

* pack added -- ``tightened``: its rules now run. Removed -- ``loosened``.
* pack version label and digest -- the contract pin rule: the same version
  naming a different digest is ``version_reused`` (always breaking, and the
  diff reports itself ``refused``); a new version label alone is
  ``version_changed``; a new digest under a new label is ``changed``,
  because which way each rule inside moved needs the pack definitions, which
  this diff does not read.
* pack ``mode`` -- observe to enforce is ``tightened``, the reverse
  ``loosened``.
* profile ``caps_minor`` and ``per_action_minor``, per action class -- a lower
  limit is ``tightened``, a higher one ``loosened``. A class set on one side
  only is ``changed``: the other side uses the pack default, which a profile
  does not carry. A value that is not an integer is ``changed``.
* everything else in a RuleSet -- ``changed`` (breaking), by the same rule.

Each change also carries a ``direction`` (``Change.direction``): its kind when
that is ``tightened`` or ``loosened``, ``neutral`` for a change that moves no
rule (a version label, a reorder, an editorial field), and ``changed`` for
everything else, whose direction cannot be determined.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

import jsonschema
from agent_action_capsule.canonical import json_digest

from capsule_engine.packs.contract_validate import validate_evidence_contract
from capsule_engine.policy.profile import CONFIGURABLE, PROFILE_FORMAT, PolicyProfileDict, pack_name

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
    "diff_rulesets",
    "PackPin",
    "RuleSet",
    "main",
    "render_diff",
    "ruleset_from_activation",
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
    "pack_id_changed": BREAKING,
}

# Which way a change moves what is enforced. Kinds not listed are "changed":
# their direction cannot be determined.
_DIRECTION = {
    "tightened": "tightened",
    "loosened": "loosened",
    "version_changed": "neutral",
    "requirements_reordered": "neutral",
    "editorial": "neutral",
}

ASSURANCE_LADDER = ("self-attested", "witnessed", "countersigned")
_TIER_RANK = {"informational": 0, "must_have": 1}
_JUDGED_ADJUDICATION = {"semantic", "human", "hybrid"}

_MISSING = object()


class ContractDiffError(ValueError):
    """The inputs cannot be diffed (e.g. two requirements share an id)."""


class ChangeResult(TypedDict):
    path: str
    direction: str
    compatibility: str
    reason: str


@dataclass(frozen=True)
class Change:
    path: str
    kind: str
    reason: str

    @property
    def severity(self) -> str:
        return _SEVERITY[self.kind]

    @property
    def direction(self) -> str:
        return _DIRECTION.get(self.kind, "changed")

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

    @property
    def refused(self) -> bool:
        """A version label names two different contents: no upgrade or
        activation may proceed on this pair."""
        return any(c.kind == "version_reused" for c in self.changes)

    def results(self) -> list[ChangeResult]:
        """One result per change: what moved, which way, and whether it breaks."""
        return [
            {"path": c.path, "direction": c.direction, "compatibility": c.severity, "reason": c.reason}
            for c in self.changes
        ]

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


def _version_changes(path: str, version_a: str, version_b: str, digest_a: Any, digest_b: Any, *, what: str) -> list[Change]:
    """The pin rule: a version label names exactly one digest."""
    if version_a == version_b:
        if digest_a != digest_b:
            return [Change(path, "version_reused", f"one version label now names two different {what}")]
        return []
    return [Change(path, "version_changed", f"version {version_a} -> {version_b}")]


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
    else:
        changes += _version_changes("version", a["version"], b["version"], pin_a["contract_digest"],
                                    pin_b["contract_digest"], what="contracts")

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


# -- RuleSets ----------------------------------------------------------------

_PACK_MODE_RANK = {"observe": 0, "enforce": 1}

_LIMIT_NAMES = {"caps_minor": "rolling-window limit", "per_action_minor": "per-action limit"}


def _compare_limits(path: str, a: Any, b: Any, *, what: str) -> list[Change]:
    """Per action class: a lower limit is tighter. A class set on one side
    only is ``changed``: the other side falls back to a default this diff
    does not see."""
    a = {} if a is _MISSING else a
    b = {} if b is _MISSING else b
    if _not_a(dict, a, b):
        return [Change(path, "changed", f"{what}: not a mapping; direction cannot be determined")]
    out: list[Change] = []
    for cls in sorted(set(a) | set(b)):
        va, vb = a.get(cls, _MISSING), b.get(cls, _MISSING)
        if va == vb and type(va) is type(vb):
            continue
        p = f"{path}/{cls}"
        if va is _MISSING or vb is _MISSING:
            out.append(Change(p, "changed", f"{what} for {cls}: set on one side only; the other uses the pack default"))
        elif type(va) is not int or type(vb) is not int:
            out.append(Change(p, "changed", f"{what} for {cls}: not an integer; direction cannot be determined"))
        else:
            out.append(Change(p, "tightened" if vb < va else "loosened", f"{what} for {cls}: {va} -> {vb}"))
    return out


# Field rules for one profile pack entry, keyed by the path relative to it.
_PROFILE_RULES: dict[str, Any] = {
    f"parameters/{check}/{key}": (lambda p, a, b, _w=_LIMIT_NAMES[key]: _compare_limits(p, a, b, what=_w))
    for check, keys in CONFIGURABLE.items()
    for key in keys
}


def _profile_entries(profile: dict[str, Any]) -> dict[str, Any]:
    packs = profile.get("packs")
    if not isinstance(packs, list) or not all(isinstance(e, dict) and isinstance(e.get("pack"), str) for e in packs):
        raise ContractDiffError("profile: packs must be a list of entries that each name a pack")
    return {e["pack"]: e for e in packs}


def _with_ranked_keys(params: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """``params`` with every ranked check/key the other side sets filled in as
    empty, so a limit set on one side is compared per class, not reported
    once as a whole missing mapping."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in params.items()}
    for check, keys in CONFIGURABLE.items():
        if isinstance(other.get(check), dict):
            for key in keys:
                if key in other[check] and isinstance(out.setdefault(check, {}), dict):
                    out[check].setdefault(key, {})
    return out


def _as_profile(value: Any) -> dict[str, Any]:
    """No profile is an empty one: every limit is the pack default."""
    if value is _MISSING:
        return {"format": PROFILE_FORMAT, "packs": []}
    if not isinstance(value, dict):
        raise ContractDiffError("profile must be a mapping")
    return value


def _diff_profiles(a: Any, b: Any) -> list[Change]:
    a, b = _as_profile(a), _as_profile(b)
    out: list[Change] = []
    for key in sorted((set(a) | set(b)) - {"packs"}):
        out += _compare(f"profile/{key}", key, a.get(key, _MISSING), b.get(key, _MISSING), {})
    ea, eb = _profile_entries(a), _profile_entries(b)
    for name in sorted(set(ea) | set(eb)):
        entry_a, entry_b = ea.get(name, {}), eb.get(name, {})
        base = f"profile/packs[{name}]"
        for key in sorted((set(entry_a) | set(entry_b)) - {"pack", "parameters"}):
            out += _compare(f"{base}/{key}", key, entry_a.get(key, _MISSING), entry_b.get(key, _MISSING), {})
        pa, pb = entry_a.get("parameters", {}), entry_b.get("parameters", {})
        if isinstance(pa, dict) and isinstance(pb, dict):
            pa, pb = _with_ranked_keys(pa, pb), _with_ranked_keys(pb, pa)
        out += _compare(f"{base}/parameters", "parameters", pa, pb, _PROFILE_RULES)
    return out


def _packs_by_name(ruleset: dict[str, Any], side: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in ruleset.get("packs") or []:
        pack_id = entry.get("pack_id") if isinstance(entry, dict) else None
        if not isinstance(pack_id, str) or pack_id.count("/") != 2:
            raise ContractDiffError(f"RuleSet {side}: each packs entry needs a pack_id '<publisher>/<name>/<version>'")
        name = pack_name(pack_id)
        if name in out:
            raise ContractDiffError(f"RuleSet {side} installs pack {name} twice")
        out[name] = entry
    return out


def _diff_pack(name: str, a: dict[str, Any], b: dict[str, Any]) -> list[Change]:
    base = f"packs[{name}]"
    version_a, version_b = a["pack_id"].rsplit("/", 1)[1], b["pack_id"].rsplit("/", 1)[1]
    digest_a, digest_b = a.get("digest", _MISSING), b.get("digest", _MISSING)
    out = _version_changes(f"{base}/version", version_a, version_b, digest_a, digest_b, what="pack contents")
    if version_a != version_b and digest_a != digest_b:
        out.append(Change(f"{base}/digest", "changed",
                          "pack content changed; which way each rule moved needs the pack definitions"))
    mode_a, mode_b = a.get("mode", _MISSING), b.get("mode", _MISSING)
    if mode_a != mode_b:
        ra, rb = _PACK_MODE_RANK.get(mode_a), _PACK_MODE_RANK.get(mode_b)
        if ra is None or rb is None:
            out.append(Change(f"{base}/mode", "changed", "mode not recognised; direction cannot be determined"))
        else:
            out.append(Change(f"{base}/mode", "tightened" if rb > ra else "loosened", f"mode {mode_a} -> {mode_b}"))
    for key in sorted((set(a) | set(b)) - {"pack_id", "digest", "mode"}):
        out += _compare(f"{base}/{key}", key, a.get(key, _MISSING), b.get(key, _MISSING), {})
    return out


class PackPin(TypedDict):
    pack_id: str
    digest: str
    mode: str


class _RuleSetPacks(TypedDict):
    packs: list[PackPin]


class RuleSet(_RuleSetPacks, total=False):
    profile: PolicyProfileDict


def ruleset_from_activation(detail: dict[str, Any]) -> RuleSet:
    """The RuleSet a ``policy_manifest_activated`` record put in force: its
    packs and, when one is pinned, its profile values."""
    out: RuleSet = {
        "packs": [{"pack_id": p["pack_id"], "digest": p["digest"], "mode": p["mode"]} for p in detail.get("packs") or []]
    }
    if "profile" in detail:
        out["profile"] = detail["profile"]["values"]
    return out


def diff_rulesets(a: dict[str, Any], b: dict[str, Any]) -> ContractDiff:
    """Classify every difference from RuleSet ``a`` to RuleSet ``b``; see the
    module docstring for the rules. The inputs are plain JSON, not ``RuleSet``:
    a field ``RuleSet`` does not declare must reach the diff so it can be
    reported as breaking."""
    packs_a, packs_b = _packs_by_name(a, "A"), _packs_by_name(b, "B")
    changes: list[Change] = []
    for name in sorted(set(packs_a) | set(packs_b)):
        if name not in packs_b:
            changes.append(Change(f"packs[{name}]", "loosened", "pack removed: its rules no longer run"))
        elif name not in packs_a:
            changes.append(Change(f"packs[{name}]", "tightened", "pack added: its rules now run"))
        else:
            changes += _diff_pack(name, packs_a[name], packs_b[name])
    changes += _diff_profiles(a.get("profile", _MISSING), b.get("profile", _MISSING))
    for key in sorted((set(a) | set(b)) - {"packs", "profile"}):
        changes += _compare(key, key, a.get(key, _MISSING), b.get(key, _MISSING), {})
    changes.sort(key=lambda c: (c.path, c.kind))
    return ContractDiff(a={"ruleset_digest": json_digest(a)}, b={"ruleset_digest": json_digest(b)}, changes=changes)


def _pin_label(pin: dict[str, Any]) -> str:
    if "ruleset_digest" in pin:
        return str(pin["ruleset_digest"])
    return f"{pin['contract_ref']} sha256:{pin['contract_digest']['digest']}"


def render_diff(diff: ContractDiff) -> str:
    """The text of a diff, formatted from the diff object alone: every line
    is a field the differ already set. Nothing here compares anything."""
    lines = [f"before: {_pin_label(diff.a)}", f"after:  {_pin_label(diff.b)}"]
    if not diff.changes:
        lines.append("identical")
    for r in diff.results():
        lines.append(f"  {r['direction']:<10} {r['compatibility']:<13} {r['path']}: {r['reason']}")
    if diff.refused:
        lines.append("REFUSED: a version label names different content (version_reused)")
    return "\n".join(lines)


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
