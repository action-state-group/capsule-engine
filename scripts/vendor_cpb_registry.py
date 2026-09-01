# SPDX-License-Identifier: Apache-2.0
"""Re-vendor the CPB registry snapshot from a local scitt-payload-binding checkout.

The machine-readable CPB registry of record is
``action-state-group/scitt-payload-binding/registry.json`` (live tables) plus
``spec/cpb-provisional-registry.md`` (the Rung-3 provisional entries). It is
meant to be *vendored* into consuming packages as a local, no-network snapshot
so a viewer/verifier can resolve a payload class's status offline. This mirrors
the existing ``scripts/vendor_bundle_viewer.py`` pattern: one generated artifact
is copied in by hand, re-run when the upstream changes, and the exact upstream
commit is recorded for provenance.

This package already vendors ``capsule_engine/registry/conventions.json``
(action-class labels). This script adds the CPB registry alongside it:

* ``capsule_engine/registry/cpb_registry.json`` — the live-table
  ``registry.json`` verbatim, with a provenance envelope (pinned commit).
* the ``provisional_field_conventions`` block inside ``conventions.json`` — the
  human labels for the AAC six-registry field values that vendored *provisional*
  payload classes (e.g. ``mesh-inference-exchange``) set on the surrounding
  capsule. The verify path (via ``agent_action_capsule.verify``, consumed by
  capsule-ledger) resolves those values as known-provisional against its own
  vendored CPB snapshot; this file carries the *display* labels for the same
  values so this package's own display surfaces render them consistently.

**Interim vendor copy, not the registry-of-record** ([ldg-ledger-scope-re-extraction]
RESIDUALS pass §3.1(a)): this is a company-side vendor copy of the neutral
``scitt-payload-binding`` registry, moved here from capsule-ledger. A future
neutral ``capsule-registry`` repo would supersede it the same way this copy
supersedes capsule-ledger's; never read this copy as the registry-of-record
itself -- re-run this script against a fresh ``scitt-payload-binding``
checkout, don't hand-edit the vendored JSON.

The field-value → payload-class binding is hand-curated from the producer named
in the CPB entry's Reference (``action-state-group/capsule-emit-mesh``) and is
NOT fabricated into the CPB registry itself — the CPB markdown stays normative.

Usage:
    python scripts/vendor_cpb_registry.py [path-to-scitt-payload-binding-checkout]
    python scripts/vendor_cpb_registry.py --check [path-to-scitt-payload-binding-checkout]

If no path is given, tries ``$SCITT_PAYLOAD_BINDING_PATH``, else a sibling
checkout at ``../scitt-payload-binding`` next to this repo.

``--check`` is what CI's vendor-drift workflow runs (see
``.github/workflows/vendor-drift.yml``). It compares the vendored content --
``cpb_registry.json``'s ``registry`` field and ``conventions.json``'s
``provisional_field_conventions`` block -- against what a fresh vendor run
against the checkout would produce, ignoring the commit-stamped provenance
fields (``_vendored_commit`` / ``provisional_field_conventions._provenance.
source``'s embedded commit sha), same rationale as
``scripts/vendor_envcompat.py``'s ``--check``: those always differ from
whatever commit the checkout happens to be at, so diffing them would fail
vendor-drift on every upstream commit, not just on real content drift.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PKG = REPO_ROOT / "capsule_engine" / "registry"
CPB_OUT = REGISTRY_PKG / "cpb_registry.json"
CONVENTIONS = REGISTRY_PKG / "conventions.json"

# Hand-curated capsule-field-value labels for provisional payload classes.
# Keyed by AAC six-registry field name. Sourced from the producer named in the
# CPB provisional entry's Reference; kept in sync with the AAC-side vendored
# provisional snapshot (agent_action_capsule/data/cpb_provisional.json).
_PROVISIONAL_FIELD_CONVENTIONS = {
    "_provenance": {
        "status": "provisional",
        "source": (
            "action-state-group/capsule-emit-mesh "
            "(plugins/admission-policy/src/capsule_emit.rs, capsule_sidecar.py) "
            "via the CPB provisional payload class 'mesh-inference-exchange' "
            "(action-state-group/scitt-payload-binding@{commit}, "
            "spec/cpb-provisional-registry.md)"
        ),
        "note": (
            "Vendored, provisional field-value labels for the "
            "mesh-inference-exchange payload class. These are the AAC "
            "six-registry values that class's producer sets on the surrounding "
            "capsule; they are known-with-status-provisional (resolved by "
            "agent_action_capsule.verify check 8 against the vendored CPB "
            "provisional snapshot), never a rejection. Refresh with "
            "scripts/vendor_cpb_registry.py."
        ),
    },
    "effect.type": {
        "inference_completion": {
            "label": "Inference completion",
            "description": "A model inference the record attests as completed. Set by the mesh-inference-exchange provisional payload class.",
            "payload_class": "mesh-inference-exchange",
            "status": "provisional",
        }
    },
    "effect_attestation": {
        "host_served_observed": {
            "label": "Host-served, observed",
            "description": "The effect was observed by the serving host at the wire (passive collector vantage), not asserted by a gate. Set by the mesh-inference-exchange provisional payload class.",
            "payload_class": "mesh-inference-exchange",
            "status": "provisional",
        }
    },
    "chain.relation": {
        "follows": {
            "label": "Follows",
            "description": "Same-node sequential ordering: this record follows the prior record in a node-run chain. Emitted by the mesh capsule-producer plugin. Set by the mesh-inference-exchange provisional payload class.",
            "payload_class": "mesh-inference-exchange",
            "status": "provisional",
        }
    },
}


def _find_spb(explicit: str | None) -> Path:
    candidates = [Path(explicit)] if explicit else []
    env = os.environ.get("SCITT_PAYLOAD_BINDING_PATH")
    if env:
        candidates.append(Path(env))
    candidates.append(REPO_ROOT.parent / "scitt-payload-binding")
    for c in candidates:
        if c and (c / "registry.json").exists() and (c / "REGISTRY.md").exists():
            return c.resolve()
    raise SystemExit(
        "no scitt-payload-binding checkout found -- pass a path, set "
        "$SCITT_PAYLOAD_BINDING_PATH, or place a checkout at "
        "../scitt-payload-binding next to this repo"
    )


def _pinned_commit(spb: Path) -> str:
    commit = subprocess.run(
        ["git", "-C", str(spb), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(spb), "status", "--porcelain", "registry.json",
         "spec/cpb-provisional-registry.md"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit(
            f"refusing to vendor from a dirty scitt-payload-binding checkout ({spb}) -- "
            "commit or stash the registry sources first so the recorded commit sha "
            "is meaningful"
        )
    return commit


def _without_provenance_source(prov: dict) -> dict:
    prov = json.loads(json.dumps(prov))  # deep copy
    if "_provenance" in prov:
        prov["_provenance"].pop("source", None)
    return prov


def _check(spb: Path, live: dict) -> int:
    problems = []

    if not CPB_OUT.exists():
        raise SystemExit(f"{CPB_OUT} does not exist -- run scripts/vendor_cpb_registry.py to create it")
    current_cpb = json.loads(CPB_OUT.read_text(encoding="utf-8"))
    if current_cpb.get("registry") != live:
        problems.append(f"{CPB_OUT}'s 'registry' field is out of sync with {spb}/registry.json")

    if not CONVENTIONS.exists():
        raise SystemExit(f"{CONVENTIONS} does not exist -- run scripts/vendor_cpb_registry.py to create it")
    conventions = json.loads(CONVENTIONS.read_text(encoding="utf-8"))
    current_prov = _without_provenance_source(conventions.get("provisional_field_conventions", {}))
    expected_prov = _without_provenance_source(_PROVISIONAL_FIELD_CONVENTIONS)
    if current_prov != expected_prov:
        problems.append(
            f"{CONVENTIONS}'s 'provisional_field_conventions' block is out of sync with the "
            "hand-curated table in scripts/vendor_cpb_registry.py"
        )

    if problems:
        raise SystemExit(
            "\n".join(problems) + "\n-- run scripts/vendor_cpb_registry.py and commit the result"
        )
    print(f"{CPB_OUT} and {CONVENTIONS}'s provisional_field_conventions match {spb}")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--check"]
    check = "--check" in argv[1:]
    spb = _find_spb(args[0] if args else None)

    live = json.loads((spb / "registry.json").read_text(encoding="utf-8"))

    if check:
        # --check compares content only -- registry.json's table plus the
        # hand-curated provisional_field_conventions block (minus its
        # commit-stamped provenance source string, same rationale as
        # vendor_envcompat.py's --check) -- and never needs a commit sha, so
        # a dirty or detached checkout doesn't compromise it the way it
        # would a real vendor run.
        return _check(spb, live)

    commit = _pinned_commit(spb)

    # 1) Vendor the live-table registry.json verbatim (provenance envelope).
    CPB_OUT.write_text(
        json.dumps(
            {
                "_vendored_from": "action-state-group/scitt-payload-binding",
                "_vendored_source": "registry.json",
                "_vendored_commit": commit,
                "_vendored_note": (
                    "Vendored verbatim by scripts/vendor_cpb_registry.py. Do not "
                    "hand-edit; re-run against a scitt-payload-binding checkout."
                ),
                "registry": live,
            },
            indent=2, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    # 2) Refresh the provisional_field_conventions block in conventions.json,
    #    stamping the pinned commit into the provenance source string.
    conventions = json.loads(CONVENTIONS.read_text(encoding="utf-8"))
    prov = json.loads(json.dumps(_PROVISIONAL_FIELD_CONVENTIONS))  # deep copy
    prov["_provenance"]["source"] = prov["_provenance"]["source"].format(commit=commit[:7])
    conventions["provisional_field_conventions"] = prov
    CONVENTIONS.write_text(
        json.dumps(conventions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {CPB_OUT}")
    print(f"updated {CONVENTIONS} (provisional_field_conventions)")
    print(f"vendored from scitt-payload-binding@{commit[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
