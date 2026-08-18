# SPDX-License-Identifier: Apache-2.0
"""Activation round trip (task acceptance): activate a manifest, confirm the
config-change capsule lands in the ledger, and confirm its cited digest
matches independently recomputing the manifest's digest."""
from __future__ import annotations

from pathlib import Path

from agent_action_capsule import compute_capsule_id

from capsule_ledger.policy import (
    EVENT_MANIFEST_ACTIVATED,
    GENESIS_PARENT,
    build_manifest_activation_capsule,
    find_latest_activation,
    load_manifest_file,
    resolve_manifest,
)

CATALOG_DIR = Path(__file__).parent.parent / "capsule_ledger" / "folds" / "catalog_defs"
WICKET_CATALOG_DIR = Path(__file__).parent.parent / "capsule_ledger" / "guards" / "wickets" / "catalog_defs"
DEFAULT_MANIFEST_PATH = Path(__file__).parent.parent / "capsule_ledger" / "policy" / "catalog_defs" / "default.yaml"


def _resolved():
    manifest = load_manifest_file(DEFAULT_MANIFEST_PATH)
    return resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)


def test_activation_lands_in_the_ledger_and_cites_the_recomputed_digest(store, signer):
    resolved = _resolved()
    assert find_latest_activation(store) is None

    capsule = build_manifest_activation_capsule(
        resolved=resolved, operator="acme", developer="ops", signer=signer
    )
    store.append(capsule, consequential=False)

    fetched = store.fetch(capsule["capsule_id"])
    assert fetched is not None

    # Independently recompute the manifest's own digest from the checked-in
    # file (never trust the capsule's own claim about itself) and compare.
    independently_recomputed = load_manifest_file(DEFAULT_MANIFEST_PATH).manifest_digest()
    assert independently_recomputed == resolved.manifest_digest
    cited = fetched.capsule["asg_payload"]["detail"]["manifest_digest"]
    assert cited == independently_recomputed

    # The capsule itself re-verifies (tamper-evident, like every capsule).
    assert compute_capsule_id(fetched.capsule) == fetched.capsule_id


def test_activation_is_a_passive_fyi_record_never_a_gate_decision(store, signer):
    resolved = _resolved()
    capsule = build_manifest_activation_capsule(resolved=resolved, operator="acme", developer="ops", signer=signer)
    assert capsule["action_type"] == "fyi"
    assert "disposition" not in capsule
    assert capsule["asg_payload"]["event"] == EVENT_MANIFEST_ACTIVATED


def test_first_activation_chains_to_the_genesis_sentinel(store, signer):
    resolved = _resolved()
    capsule = build_manifest_activation_capsule(resolved=resolved, operator="acme", developer="ops", signer=signer)
    assert capsule["chain"] == {"parent_capsule_id": GENESIS_PARENT, "relation": "epoch_opens"}


def test_second_activation_chains_to_the_first(store, signer):
    resolved = _resolved()
    first = build_manifest_activation_capsule(resolved=resolved, operator="acme", developer="ops", signer=signer)
    store.append(first, consequential=False)

    previous = find_latest_activation(store)
    assert previous is not None
    assert previous.capsule_id == first["capsule_id"]

    second = build_manifest_activation_capsule(
        resolved=resolved,
        operator="acme",
        developer="ops",
        signer=signer,
        previous_activation_capsule_id=previous.capsule_id,
    )
    store.append(second, consequential=False)

    assert second["chain"] == {"parent_capsule_id": first["capsule_id"], "relation": "epoch_opens"}
    latest = find_latest_activation(store)
    assert latest.capsule_id == second["capsule_id"]


def test_tampering_the_activation_capsule_is_detected():
    """The mutant: flip one byte of a real activation capsule's detail and
    confirm recompute stops matching -- a verifier that can't catch this
    isn't verifying anything."""
    resolved = _resolved()
    from capsule_ledger.guards import LocalSigner

    signer = LocalSigner(key_id="k", secret=b"s")
    capsule = build_manifest_activation_capsule(resolved=resolved, operator="acme", developer="ops", signer=signer)
    assert compute_capsule_id(capsule) == capsule["capsule_id"]

    tampered = dict(capsule)
    tampered["asg_payload"] = dict(capsule["asg_payload"])
    tampered["asg_payload"]["detail"] = dict(capsule["asg_payload"]["detail"])
    tampered["asg_payload"]["detail"]["manifest_digest"] = "f" * 64
    assert compute_capsule_id(tampered) != tampered["capsule_id"]
