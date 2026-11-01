# SPDX-License-Identifier: Apache-2.0
"""Check every live pack source in this repository against the pack compiler.

One pack format (capsule-engine#252, evidencebook-skills SEAMS ruling 1): a
live pack is authored as ``pack-source.yaml`` with ``scope: {kind: live}``,
and evidencebook-skills' ``scripts/pack_compile.py`` compiles it to an
Evidence Contract (``contract.json``) and to the engine's pack definition
(``pack-definition.json``). For each ``pack-source.yaml`` under the catalog,
``go/packs`` and ``python/tests/fixtures/packs`` this compiles it with an
evidencebook-skills checkout and fails unless

* the compiler accepts it;
* ``contract.json`` passes this engine's ``contract validate``
  (``capsule_engine/schemas/evidence-contract-v0.json``);
* ``pack-definition.json``'s ``definition_digest`` is the digest this engine
  computes for the same directory, so the compiled form pins the pack the
  live path runs.

Usage (CI checks out evidencebook-skills at the commit conformance.yml pins, job pack-sources)::

    python scripts/check_pack_sources.py ../evidencebook-skills
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import jsonschema  # noqa: E402
from generate_go_packs import pack_digest  # noqa: E402

from capsule_engine.packs.contract_validate import validate_evidence_contract  # noqa: E402
from capsule_engine.packs.loader import PACK_SOURCE_FILE  # noqa: E402

SOURCE_ROOTS = (
    ROOT / "python" / "capsule_engine" / "packs" / "catalog",
    ROOT / "go" / "packs",
    ROOT / "python" / "tests" / "fixtures" / "packs",
)


def sources() -> list[Path]:
    return sorted(p for root in SOURCE_ROOTS for p in root.glob(f"*/{PACK_SOURCE_FILE}"))


def check(source: Path, compiler: Path, out: Path) -> list[str]:
    name = source.parent.relative_to(ROOT)
    result = subprocess.run(
        [sys.executable, str(compiler / "scripts" / "pack_compile.py"), "compile", str(source), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [f"{name}: pack_compile.py refused it: {result.stdout.strip() or result.stderr.strip()}"]
    problems = []
    try:
        validate_evidence_contract(json.loads((out / "contract.json").read_text()))
    except jsonschema.ValidationError as exc:
        problems.append(f"{name}: contract.json fails contract validate: {exc}")
    compiled = json.loads((out / "pack-definition.json").read_text())["definition_digest"]
    engine = pack_digest(source.parent)
    if compiled != engine:
        problems.append(f"{name}: the compiled definition_digest {compiled} is not the engine's {engine}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("evidencebook_skills", type=Path, help="an evidencebook-skills checkout")
    args = parser.parse_args(argv)
    compiler = args.evidencebook_skills.resolve()
    found = sources()
    if not found:
        print(f"no {PACK_SOURCE_FILE} found", file=sys.stderr)
        return 1
    problems = []
    # pack_compile.py writes only inside its own checkout.
    work = Path(tempfile.mkdtemp(prefix=".pack-sources-", dir=compiler))
    try:
        for i, source in enumerate(found):
            problems += check(source, compiler, work / str(i))
    finally:
        shutil.rmtree(work)
    for p in problems:
        print(p, file=sys.stderr)
    print(f"{len(found)} pack sources, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
