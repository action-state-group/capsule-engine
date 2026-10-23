# SPDX-License-Identifier: Apache-2.0
"""Decision-level differential vectors for asg/seller/0.1.0.

corpus.json is the seller pack's fixture scenario (tests/test_pack_seller_acceptance.py),
exported as data: one synthetic sale, two buyer threads and a third buyer, run IN ORDER on
ONE ledger, because required_disclosure, single_commitment and verify_before_dispatch read
what the earlier steps sealed. A step with "cites_acceptance": true sets
cited_mandate_capsule_id to the capsule_id the step named by "acceptance_step" sealed.
Every step is checked with the corpus's task_authority_record and commercial_bounds_opening;
a step naming "authorization_record" is checked with that record too.

Writes python-decisions.json. The Go port must reproduce, per step: outcome, decision,
verdict_class, failing_constraints and results (every constraint's result).

    python -P generate_python_decisions.py <capsule-engine checkout>     # writes the decisions
    python -P generate_python_decisions.py <capsule-engine checkout> --export-corpus
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.classes import TAXONOMY_DIGEST, TAXONOMY_VERSION
from capsule_engine.packs import build_engine, install_pack, load_pack_dir

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="seller-0-1-0-differential-key", secret=b"seller-0-1-0-differential-fixed-key")


def _scenario_module(checkout: Path):
    spec = importlib.util.spec_from_file_location("seller_acceptance", checkout / "tests" / "test_pack_seller_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export_corpus(checkout: Path) -> None:
    m = _scenario_module(checkout)
    defaults = {f.name: f.default for f in dataclasses.fields(Action) if f.default is not dataclasses.MISSING}
    steps = []
    for name, action, expected in m._scenarios():
        fields = {k: v for k, v in dataclasses.asdict(action).items() if k in ("verb", "operator", "developer") or v != defaults.get(k)}
        cites = fields.get("cited_mandate_capsule_id") == m.CITES_ACCEPTANCE
        if cites:
            del fields["cited_mandate_capsule_id"]
        step = {"name": name, "dry_run": name not in m.REAL_RUN, "cites_acceptance": cites, "action": fields,
                "expected_outcome": expected}
        if name in m.APPROVALS:
            step["authorization_record"] = m.APPROVALS[name]
        steps.append(step)
    corpus = {
        "note": __doc__.split("\n\n")[1].replace("\n", " "),
        "pack": "capsule_engine/packs/catalog/seller (asg/seller/0.1.0)",
        "acceptance_step": m.ACCEPTANCE,
        "task_authority_record": m.TASK,
        "task_authority_ref": m.TASK_REF,
        "commercial_bounds_opening": m.OPENING,
        "steps": steps,
    }
    (HERE / "corpus.json").write_text(json.dumps(corpus, indent=1) + "\n")


def main(checkout: Path) -> None:
    corpus = json.loads((HERE / "corpus.json").read_text())
    pack_dir = checkout / "capsule_engine" / "packs" / "catalog" / "seller"
    pack = load_pack_dir(pack_dir)
    out: dict = {
        "capsule_engine_commit": subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip(),
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_jcs_digest": TAXONOMY_DIGEST,
        "pack": {
            "pack_id": pack.pack_id,
            "definition_digest": pack.definition_digest(),
            "pack_yaml_sha256": hashlib.sha256((pack_dir / "pack.yaml").read_bytes()).hexdigest(),
            "taxonomy_pin": {"taxonomy_version": pack.taxonomy.taxonomy_version, "digest": pack.taxonomy.digest},
            "wicket_digests": {c.wicket_id: c.definition_digest() for c in pack.constraints},
            "declared_dispositions": {o.id: o.default_disposition for o in pack.obligations},
        },
        "decisions": {},
    }
    sealed: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        installed = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe")
        engine = build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
        for step in corpus["steps"]:
            fields = dict(step["action"])
            if step["cites_acceptance"]:
                fields["cited_mandate_capsule_id"] = sealed[corpus["acceptance_step"]]
            decision = engine.check(
                Action(**fields),
                dry_run=step["dry_run"],
                task_authority_record=corpus["task_authority_record"],
                authorization_record=step.get("authorization_record"),
                commercial_bounds_opening=corpus["commercial_bounds_opening"],
            )
            if decision.outcome != step["expected_outcome"]:
                raise SystemExit(f"{step['name']}: expected {step['expected_outcome']}, got {decision.outcome}")
            sealed[step["name"]] = decision.capsule["capsule_id"]
            results = {c.id: c.result for c in decision.constraints}
            out["decisions"][step["name"]] = {
                "outcome": decision.outcome,
                "decision": decision.capsule["disposition"]["decision"],
                "verdict_class": decision.capsule["disposition"].get("verdict_class"),
                "failing_constraints": sorted(c for c, r in results.items() if r == "fail"),
                "results": results,
            }
    (HERE / "python-decisions.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(out['decisions'])} decisions under {pack.pack_id} {pack.definition_digest()}")


if __name__ == "__main__":
    if "--export-corpus" in sys.argv:
        export_corpus(Path(sys.argv[1]))
    main(Path(sys.argv[1]))
