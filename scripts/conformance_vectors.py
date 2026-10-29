# SPDX-License-Identifier: Apache-2.0
"""Write, or check, the shared conformance vectors the Python and Go guards
both decide (docs/design/go-guard-module.md §5).

A vector set is ``conformance-vectors/guard/v0/<set>.json``: a list of cases,
each naming its ``kind``, the embedded pack it is decided under (a directory
of ``go/packs``), its input (a file in this repository, pinned by sha256) and
the ``expected`` decision. Only this script writes ``expected``, from the
Python engine; the Go guard's ``go/guard/conformance_test.go`` decides the
same inputs and compares.

Kinds:

* ``check-input``: one external-check-input/v0 envelope, decided by
  ``capsule_engine.report.live_check.decide_check_input`` (Go:
  ``DecodeCheckInput`` then ``CheckWithHistory``);
* ``action-ledger``: ``{"steps": [<Action fields>, ...]}``, decided in order
  by ``decide_steps``, each decision in the ledger before the next step (Go:
  ``DecodeAction`` then ``Check``, appending ``RecordFor``). The input may
  name the served pack it is decided under (``"pack": "inference"``;
  everyday when it names none), and may carry a ``"profile"``, a
  policy-profile/v0 in its canonical form, installed and activated with the
  pack (``decide_steps``'s ``profile``; Go: ``ProfileParameters`` and
  ``ProfileLimits``). Its expected
  decision is ``{"steps": [<decision>, ...]}``, each step with the
  ``capsule_id`` the engine sealed for it: the caller seals in Go, so the Go
  runner gives the record it appends that id, and a later step's evidence
  naming an earlier decision names the same capsule;
* ``sale-bundle``: ``{"sale_bundle": <path>, "verify": <path>}``, a sale bundle
  and what capsulectl's ``verify --bundle`` reported of it, replayed by
  ``decide_sale_bundle`` (Go: ``ReadSaleBundle`` with the verify report's
  states, then each check's ``Check`` and ``CheckSale`` on the replay's
  ledger). Its expected decision is ``{"sale": {"complete", "findings"},
  "steps": [<decision>, ...]}``, one step per check the replay decides, in
  its order. Here a step's ``capsule_id`` is the checked record's own (in an
  ``action-ledger`` step it is the capsule the engine sealed), and its
  ``sealed_capsule_id`` is the decision the replay sealed for it, which the
  Go runner's replay ledger takes as that record's id. The Go runner works
  out its own step list, in seal order, so a check one side decides and the
  other does not is a difference.

A decision is compared as its outcome and, per constraint in order, its id,
result, method ("" when it has none) and the JCS-SHA256 of its evidence, which
holds its structured reason. An n/a's evidence often does not say why, so an
n/a also carries its reason, which the Go guard compares wherever it keeps
one. Other reasons are presentation and are not compared. A case whose input the engine refuses is expected as
``{"error": "<ExceptionType>"}``.

``--seed`` adds a set from every input under a directory: each
external-check-input/v0 envelope, decided under the current version of the
served pack its ``party_role`` names (seller, else everyday), and each
``{"steps": ...}`` file, under the current everyday pack. ``--write``
rewrites every expected decision and moves each case to the current pack for
its input, so after a served pack cuts a new version it is the one step that
brings the vectors along. ``--check`` writes nothing and fails when any
case's input, pack or expected decision is not what this engine gives today,
or when the coverage gate fails: for each current pack, every wicket it cites
has a constraint that is not n/a in some case, and every fold it cites is
named in some case's evidence (a check writes a fold's digest only when it
ran it), apart from the exemptions in ``COVERAGE-EXEMPT.json``.

A case marked ``"frozen": true`` is decided under the embedded pack it names
and stays there: a deal's records pin the pack they were checked under, and a
replay, ``check --record`` or an older pack named with ``--pack`` re-decides
them under it, so the two engines must agree on a pack after it is no longer
served. ``--write`` never moves such a case, and ``--check`` does not ask
it to be on the current pack. The coverage gate measures the current packs
only, so a case frozen on another pack covers nothing there. ``--seed <dir> --pack <pack>`` adds a set of frozen
cases, all under that pack, named ``<dir>-<pack>.json``.

Usage::

    python scripts/conformance_vectors.py --check
    python scripts/conformance_vectors.py --write
    python scripts/conformance_vectors.py --seed go/guard/testdata/promise-11-vectors
    python scripts/conformance_vectors.py --seed go/guard/testdata/emit --pack everyday-0.3.5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from functools import cache
from pathlib import Path

from agent_action_capsule import json_digest

from capsule_engine.guards.wickets.loader import load_definition_file as load_wicket_file
from capsule_engine.packs import load_pack_dir
from capsule_engine.policy.profile import parse_profile
from capsule_engine.report.live_check import decide_check_input, decide_sale_bundle, decide_steps

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "conformance-vectors" / "guard" / "v0"
PACKS = ROOT / "go" / "packs"
DEFINITIONS = ROOT / "go" / "guard" / "testdata" / "engine-definitions.json"
ENVELOPE_SCHEMA = "external-check-input/v0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@cache
def _pack(directory: str):
    return load_pack_dir(_with_bootstrap(PACKS / directory))


def _with_bootstrap(directory: Path) -> Path:
    """A pack directory the loader accepts. A pack.yaml naming a bootstrap
    file that is not embedded loads from a copy with an empty stand-in: the
    bootstrap's bytes are not inside the pack's digest."""
    import shutil
    import tempfile

    import yaml

    bootstrap = (yaml.safe_load((directory / "pack.yaml").read_text()) or {}).get("bootstrap")
    if not bootstrap or (directory / bootstrap).exists():
        return directory
    copy = Path(tempfile.mkdtemp()) / directory.name
    shutil.copytree(directory, copy)
    (copy / bootstrap).write_text("")
    return copy


def _constraint(c) -> dict:
    out = {
        "id": c.id,
        "result": c.result,
        "method": c.method or "",
        "evidence_digest": json_digest(c.evidence) if c.evidence is not None else "",
    }
    if c.result == "n/a":
        # An n/a's evidence often does not say why (in scope, nothing missing),
        # so its reason is compared too, wherever the Go guard keeps one.
        out["reason"] = c.reason or ""
    return out


def _decision(decision) -> dict:
    return {"outcome": decision.outcome, "constraints": [_constraint(c) for c in decision.constraints]}


def _strings(value) -> set[str]:
    if isinstance(value, dict):
        return set().union(set(), *(_strings(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(_strings(v) for v in value))
    return {value} if isinstance(value, str) else set()


def decide(case: dict) -> tuple[dict, set[str]]:
    """The expected decision of one case, from the Python engine, and every
    string its constraints' evidence holds (a fold that ran names its digest
    there)."""
    if case["kind"] not in ("check-input", "action-ledger", "sale-bundle"):
        raise ValueError(f"{case['case']}: unknown kind {case['kind']!r}")
    data = json.loads((ROOT / case["input"]).read_text())
    # A profile not in canonical form is a broken vector, not an engine refusal.
    profile = _profile(data) if case["kind"] == "action-ledger" else None
    try:
        if case["kind"] == "check-input":
            decisions = [decide_check_input(_pack(case["pack"]), data).decision]
        elif case["kind"] == "sale-bundle":
            sale, result = decide_sale_bundle(_pack(case["pack"]), json.loads((ROOT / data["sale_bundle"]).read_text()))
            decisions = [s.decision for s in result.decisions]
            records = [s.record for s in result.decisions]
        else:
            decisions = decide_steps(_pack(case["pack"]), data["steps"], profile=profile)
    except Exception as exc:  # the engine refuses the input
        return {"error": type(exc).__name__}, set()
    seen = set().union(set(), *(_strings(c.evidence) for d in decisions for c in d.constraints))
    if case["kind"] == "check-input":
        return _decision(decisions[0]), seen
    if case["kind"] == "sale-bundle":
        steps = [
            {**_decision(d), "capsule_id": r.get("capsule_id", ""), "sealed_capsule_id": (d.capsule or {}).get("capsule_id", "")}
            for d, r in zip(decisions, records, strict=True)
        ]
        return {"sale": {"complete": sale.complete, "findings": list(sale.findings)}, "steps": steps}, seen
    steps = [{**_decision(d), "capsule_id": (d.capsule or {}).get("capsule_id", "")} for d in decisions]
    return {"steps": steps}, seen


def _profile(data: dict):
    """The input's profile, which must be in its canonical form: the Go
    runner digests the bytes as given."""
    if "profile" not in data:
        return None
    profile = parse_profile(data["profile"])
    if profile.canonical_dict() != data["profile"]:
        raise ValueError("an action-ledger input's profile must be in its canonical form")
    return profile


def decision_of(case: dict) -> dict:
    """The expected decision of one case, from the Python engine."""
    return decide(case)[0]


def _decisions(expected: dict) -> list[dict]:
    return expected.get("steps", [expected])


def coverage(sets: list[dict], evidence_strings: set[str]) -> list[str]:
    """What the coverage gate finds missing, with the exemptions applied. A
    wicket is covered by a case where its check is not n/a; a fold, by a case
    whose evidence names the fold's digest, which a check writes only when
    it ran the fold."""
    entries = json.loads((VECTORS / "COVERAGE-EXEMPT.json").read_text())["exempt"]
    problems = []
    for name, directory in _current().items():
        # An entry naming a pack exempts the definition in that pack alone.
        exempt = {e["definition"] for e in entries if e.get("pack", name) == name}
        provenance = json.loads((PACKS / directory / "PROVENANCE.json").read_text())
        ran = set()
        for data in sets:
            for case in data["cases"]:
                if case["pack"] != directory:
                    continue
                for d in _decisions(case["expected"]):
                    ran |= {c["id"] for c in d.get("constraints", []) if c["result"] != "n/a"}
        checks = {}
        for path in sorted((PACKS / directory / "wickets").glob("*.yaml")):
            definition = load_wicket_file(path)
            checks[definition.wicket_id] = definition.check
        for wid in provenance["wicket_digests"]:
            if checks[wid] not in ran and wid not in exempt:
                problems.append(f"{directory}: no case runs {wid} (check {checks[wid]})")
        for fid, digest in provenance["fold_digests"].items():
            if digest not in evidence_strings and fid not in exempt:
                problems.append(f"{directory}: no case runs the fold {fid}")
    return problems


def _current() -> dict[str, str]:
    return json.loads(DEFINITIONS.read_text())["current"]


def _kind_and_pack(data) -> tuple[str, str] | None:
    """An input's kind and the current pack it is decided under, or None when
    it is no input: an envelope under the served pack its ``party_role``
    names (seller, else everyday), and a steps file under the served pack it
    names, else everyday."""
    current = _current()
    if isinstance(data, dict) and data.get("schema") == ENVELOPE_SCHEMA:
        party = data.get("party_role") or (data.get("record") or {}).get("party_role")
        return "check-input", current["seller"] if party == "seller" else current["everyday"]
    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        return "action-ledger", current[data.get("pack", "everyday")]
    if isinstance(data, dict) and isinstance(data.get("sale_bundle"), str):
        return "sale-bundle", current["seller"]
    return None


def seed(directory: Path, pack: str | None = None) -> Path:
    """A new set from every input under ``directory``: each case on the
    current pack for its input, or, given ``pack``, frozen on that embedded
    pack."""
    if pack is not None and not (PACKS / pack / "pack.yaml").is_file():
        raise ValueError(f"{pack} is not an embedded pack (a directory of {PACKS.relative_to(ROOT)})")
    cases = []
    for path in sorted(directory.rglob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        found = _kind_and_pack(data)
        if found is None:
            continue
        kind, current = found
        rel = path.relative_to(ROOT).as_posix()
        case = {
            "case": path.relative_to(directory).with_suffix("").as_posix(),
            "kind": kind,
            "pack": pack or current,
            "input": rel,
            "input_sha256": _sha256(path),
        }
        if pack is not None:
            case["frozen"] = True
        case["expected"] = decision_of(case)
        cases.append(case)
    out = VECTORS / (f"{directory.name}-{pack}.json" if pack is not None else f"{directory.name}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": directory.relative_to(ROOT).as_posix(), "cases": cases}, indent=1) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="write nothing; fail on any difference")
    mode.add_argument("--write", action="store_true", help="rewrite every case's expected decision")
    mode.add_argument("--seed", type=Path, help="add a set from the envelopes under this directory")
    parser.add_argument("--pack", help="with --seed: freeze every case on this embedded pack (a go/packs directory)")
    args = parser.parse_args(argv)
    if args.pack and not args.seed:
        parser.error("--pack goes with --seed")
    if args.seed:
        print(seed(args.seed.resolve(), args.pack).relative_to(ROOT))
        return 0
    problems: list[str] = []
    sets = []
    evidence_strings: set[str] = set()
    for path in sorted(VECTORS.glob("*.json")):
        if path.name in ("KNOWN-DIFFERENCES.json", "COVERAGE-EXEMPT.json"):
            continue
        data = json.loads(path.read_text())
        sets.append(data)
        changed = False
        for case in data["cases"]:
            name = f"{path.name}:{case['case']}"
            source = ROOT / case["input"]
            if not source.is_file() or _sha256(source) != case["input_sha256"]:
                problems.append(f"{name}: the input is not the one pinned")
                continue
            # When a served pack cuts a new version, every case moves to it:
            # a case is decided under the current pack for its input, unless
            # it is frozen on the pack it names.
            _, pack = _kind_and_pack(json.loads(source.read_text()))
            if case.get("frozen") is True:
                if not (PACKS / case["pack"] / "pack.yaml").is_file():
                    problems.append(f"{name}: frozen on {case['pack']}, which is not an embedded pack")
                    continue
            elif pack != case["pack"]:
                if args.check:
                    problems.append(f"{name}: decided under {case['pack']}, not the current {pack}")
                    continue
                case["pack"], changed = pack, True
            want, seen = decide(case)
            if case["pack"] in _current().values():
                evidence_strings |= seen
            if want != case["expected"]:
                if args.check:
                    problems.append(f"{name}: the engine decides {json.dumps(want)}, not {json.dumps(case['expected'])}")
                else:
                    case["expected"], changed = want, True
        if changed:
            path.write_text(json.dumps(data, indent=1) + "\n")
    problems += coverage(sets, evidence_strings)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
