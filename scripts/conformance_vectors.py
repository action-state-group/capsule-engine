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
  ``DecodeAction`` then ``Check``, appending ``RecordFor``). Its expected
  decision is ``{"steps": [<decision>, ...]}``, each step with the
  ``capsule_id`` the engine sealed for it: the caller seals in Go, so the Go
  runner gives the record it appends that id, and a later step's evidence
  naming an earlier decision names the same capsule.

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

Usage::

    python scripts/conformance_vectors.py --check
    python scripts/conformance_vectors.py --write
    python scripts/conformance_vectors.py --seed go/guard/testdata/promise-11-vectors
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
from capsule_engine.report.live_check import decide_check_input, decide_steps

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
    if case["kind"] not in ("check-input", "action-ledger"):
        raise ValueError(f"{case['case']}: unknown kind {case['kind']!r}")
    data = json.loads((ROOT / case["input"]).read_text())
    try:
        if case["kind"] == "check-input":
            decisions = [decide_check_input(_pack(case["pack"]), data).decision]
        else:
            decisions = decide_steps(_pack(case["pack"]), data["steps"])
    except Exception as exc:  # the engine refuses the input
        return {"error": type(exc).__name__}, set()
    seen = set().union(set(), *(_strings(c.evidence) for d in decisions for c in d.constraints))
    if case["kind"] == "check-input":
        return _decision(decisions[0]), seen
    steps = [{**_decision(d), "capsule_id": (d.capsule or {}).get("capsule_id", "")} for d in decisions]
    return {"steps": steps}, seen


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
    exempt = {e["definition"] for e in json.loads((VECTORS / "COVERAGE-EXEMPT.json").read_text())["exempt"]}
    problems = []
    for directory in _current().values():
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
    names (seller, else everyday), and a steps file under everyday."""
    current = _current()
    if isinstance(data, dict) and data.get("schema") == ENVELOPE_SCHEMA:
        party = data.get("party_role") or (data.get("record") or {}).get("party_role")
        return "check-input", current["seller"] if party == "seller" else current["everyday"]
    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        return "action-ledger", current["everyday"]
    return None


def seed(directory: Path) -> Path:
    """A new set from every input under ``directory``."""
    cases = []
    for path in sorted(directory.rglob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        found = _kind_and_pack(data)
        if found is None:
            continue
        kind, pack = found
        rel = path.relative_to(ROOT).as_posix()
        case = {
            "case": path.relative_to(directory).with_suffix("").as_posix(),
            "kind": kind,
            "pack": pack,
            "input": rel,
            "input_sha256": _sha256(path),
        }
        case["expected"] = decision_of(case)
        cases.append(case)
    out = VECTORS / f"{directory.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": directory.relative_to(ROOT).as_posix(), "cases": cases}, indent=1) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="write nothing; fail on any difference")
    mode.add_argument("--write", action="store_true", help="rewrite every case's expected decision")
    mode.add_argument("--seed", type=Path, help="add a set from the envelopes under this directory")
    args = parser.parse_args(argv)
    if args.seed:
        print(seed(args.seed.resolve()).relative_to(ROOT))
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
            # a case is decided under the current pack for its input.
            _, pack = _kind_and_pack(json.loads(source.read_text()))
            if pack != case["pack"]:
                if args.check:
                    problems.append(f"{name}: decided under {case['pack']}, not the current {pack}")
                    continue
                case["pack"], changed = pack, True
            want, seen = decide(case)
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
