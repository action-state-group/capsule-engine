# SPDX-License-Identifier: Apache-2.0
"""Policy manifest: parse validation, a pinned-digest worked example (the
built-in default manifest), and resolve-time drift detection against the
real fold/wicket catalogs it cites."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_ledger.policy import (
    PolicyManifestError,
    load_manifest_file,
    load_manifest_text,
    resolve_manifest,
)
from capsule_ledger.policy.manifest import PackRef, parse_manifest

CATALOG_DIR = Path(__file__).parent.parent / "capsule_ledger" / "folds" / "catalog_defs"
WICKET_CATALOG_DIR = Path(__file__).parent.parent / "capsule_ledger" / "guards" / "wickets" / "catalog_defs"
DEFAULT_MANIFEST_PATH = Path(__file__).parent.parent / "capsule_ledger" / "policy" / "catalog_defs" / "default.yaml"

# Pinned: independently recomputable from the checked-in default.yaml. A
# change to that file's content (a fold/wicket added, removed, or
# re-pinned) is a real policy change and MUST move this digest.
EXPECTED_DEFAULT_DIGEST = "0e99f3ee3a6ebf3ee93aa464f27e8fcd1a401ccc45460eb267efde327f5c218c"


def test_default_manifest_digest_is_pinned():
    manifest = load_manifest_file(DEFAULT_MANIFEST_PATH)
    assert manifest.manifest_id == "default/1.0.0"
    assert manifest.manifest_digest() == EXPECTED_DEFAULT_DIGEST


def test_default_manifest_resolves_cleanly_against_the_real_catalogs():
    manifest = load_manifest_file(DEFAULT_MANIFEST_PATH)
    resolved = resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert resolved.manifest_digest == EXPECTED_DEFAULT_DIGEST
    assert set(resolved.folds) == {"spend.weekly/1.0.0"}
    assert set(resolved.wickets) == {"dedupe/1.0.0", "caps/1.0.0", "verify_before_dispatch/1.0.0"}
    assert resolved.caps_minor() == {"money.transfer": 10_000_000}
    assert resolved.dedupe_window_days() == 30
    assert resolved.caps_fold().fold_id == "spend.weekly/1.0.0"


def test_digest_changes_when_a_fold_ref_changes():
    base = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "a" * 64}],
            "wickets": [],
        }
    )
    mutant = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "b" * 64}],
            "wickets": [],
        }
    )
    assert base.manifest_digest() != mutant.manifest_digest()


def test_digest_changes_when_engine_changes_with_same_fold_id_and_digest():
    """``engine`` really participates in the digest -- not just a field that
    sits there unused. Same fold_id, same digest, only the engine differs."""
    base = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "a" * 64}],
            "wickets": [],
        }
    )
    mutant = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "a/1.0.0", "engine": "fold/2", "digest": "a" * 64}],
            "wickets": [],
        }
    )
    assert base.manifest_digest() != mutant.manifest_digest()


def test_digest_changes_when_entry_order_changes():
    """List order is part of the digested content (mirrors a fold
    definition's own ``reads`` list) -- reordering IS a manifest change."""
    forward = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [
                {"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "a" * 64},
                {"fold_id": "b/1.0.0", "engine": "fold/1", "digest": "b" * 64},
            ],
            "wickets": [],
        }
    )
    backward = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [
                {"fold_id": "b/1.0.0", "engine": "fold/1", "digest": "b" * 64},
                {"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "a" * 64},
            ],
            "wickets": [],
        }
    )
    assert forward.manifest_digest() != backward.manifest_digest()


@pytest.mark.parametrize(
    "data,expected_reason",
    [
        ({"manifest_id": "not valid"}, "invalid_manifest_id_namespace"),
        ({"manifest_id": "m/1.0.0", "folds": "not-a-list"}, "malformed_manifest"),
        ({"manifest_id": "m/1.0.0", "folds": [{"fold_id": "a/1.0.0"}]}, "malformed_manifest"),  # missing digest
        (
            # missing engine -- required, not optional-with-a-default
            {"manifest_id": "m/1.0.0", "folds": [{"fold_id": "a/1.0.0", "digest": "a" * 64}]},
            "malformed_manifest",
        ),
        (
            {
                "manifest_id": "m/1.0.0",
                "folds": [{"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "not-hex64"}],
            },
            "invalid_digest_shape",
        ),
        (
            {
                "manifest_id": "m/1.0.0",
                "folds": [
                    {"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "a" * 64},
                    {"fold_id": "a/1.0.0", "engine": "fold/1", "digest": "b" * 64},
                ],
            },
            "duplicate_fold_ref",
        ),
        (
            {
                "manifest_id": "m/1.0.0",
                "wickets": [
                    {"wicket_id": "w/1.0.0", "engine": "wicket/1", "digest": "a" * 64},
                    {"wicket_id": "w/1.0.0", "engine": "wicket/1", "digest": "b" * 64},
                ],
            },
            "duplicate_wicket_ref",
        ),
        (
            # missing mode -- required, not optional-with-a-default
            {"manifest_id": "m/1.0.0", "packs": [{"pack_id": "p/1.0.0", "engine": "pack/1", "digest": "a" * 64}]},
            "malformed_manifest",
        ),
        (
            {
                "manifest_id": "m/1.0.0",
                "packs": [{"pack_id": "p/1.0.0", "engine": "pack/1", "digest": "a" * 64, "mode": "enforced"}],
            },
            "invalid_pack_mode",
        ),
        (
            {
                "manifest_id": "m/1.0.0",
                "packs": [
                    {"pack_id": "p/1.0.0", "engine": "pack/1", "digest": "a" * 64, "mode": "observe"},
                    {"pack_id": "p/1.0.0", "engine": "pack/1", "digest": "b" * 64, "mode": "observe"},
                ],
            },
            "duplicate_pack_ref",
        ),
        ("not-a-mapping", "malformed_manifest"),
    ],
)
def test_must_fail_cases(data, expected_reason):
    with pytest.raises(PolicyManifestError) as exc_info:
        parse_manifest(data)
    assert exc_info.value.reason == expected_reason


def test_empty_document_is_malformed():
    with pytest.raises(PolicyManifestError) as exc_info:
        load_manifest_text("")
    assert exc_info.value.reason == "malformed_manifest"


def test_folds_and_wickets_default_to_empty_when_omitted():
    manifest = parse_manifest({"manifest_id": "m/1.0.0"})
    assert manifest.folds == ()
    assert manifest.wickets == ()
    assert manifest.packs == ()


def test_pack_ref_round_trips_and_participates_in_the_digest():
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "packs": [{"pack_id": "payments_safety/1.0.0", "engine": "pack/1", "digest": "a" * 64, "mode": "observe"}],
        }
    )
    assert manifest.packs == (
        PackRef(pack_id="payments_safety/1.0.0", engine="pack/1", digest="a" * 64, mode="observe"),
    )
    assert "packs" in manifest.canonical_dict()

    without_pack = parse_manifest({"manifest_id": "m/1.0.0"})
    assert "packs" not in without_pack.canonical_dict()
    assert manifest.manifest_digest() != without_pack.manifest_digest()

    flipped = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "packs": [{"pack_id": "payments_safety/1.0.0", "engine": "pack/1", "digest": "a" * 64, "mode": "enforce"}],
        }
    )
    assert flipped.manifest_digest() != manifest.manifest_digest()


def test_resolve_fails_closed_on_unknown_fold_id():
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "no.such.fold/9.9.9", "engine": "fold/1", "digest": "a" * 64}],
        }
    )
    with pytest.raises(PolicyManifestError) as exc_info:
        resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert exc_info.value.reason == "unknown_fold_id"


def test_resolve_fails_closed_on_unknown_wicket_id():
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "wickets": [{"wicket_id": "no.such.wicket/9.9.9", "engine": "wicket/1", "digest": "a" * 64}],
        }
    )
    with pytest.raises(PolicyManifestError) as exc_info:
        resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert exc_info.value.reason == "unknown_wicket_id"


def test_resolve_detects_fold_digest_drift():
    """The mutant: a real digest pin that no longer matches the catalog's
    current definition MUST be rejected -- a resolver that accepts a stale
    pin isn't verifying anything."""
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "spend.weekly/1.0.0", "engine": "fold/1", "digest": "0" * 64}],
        }
    )
    with pytest.raises(PolicyManifestError) as exc_info:
        resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert exc_info.value.reason == "fold_digest_drift"


def test_resolve_detects_wicket_digest_drift():
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "wickets": [{"wicket_id": "caps/1.0.0", "engine": "wicket/1", "digest": "0" * 64}],
        }
    )
    with pytest.raises(PolicyManifestError) as exc_info:
        resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert exc_info.value.reason == "wicket_digest_drift"


def test_resolve_succeeds_when_pinned_digest_is_correct():
    """The positive twin of the drift tests above: the *real* current
    digest, freshly recomputed, must resolve without raising."""
    from capsule_ledger.folds.catalog import Catalog as FoldCatalog

    real_digest = FoldCatalog(CATALOG_DIR).get("spend.weekly/1.0.0").digest
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "spend.weekly/1.0.0", "engine": "fold/1", "digest": real_digest}],
        }
    )
    resolved = resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert resolved.folds["spend.weekly/1.0.0"].fold_id == "spend.weekly/1.0.0"


def test_resolve_fails_closed_on_unrecognized_fold_engine():
    """A manifest naming an evaluation engine this build doesn't know how
    to run MUST be rejected, not silently ignored -- the actual load-bearing
    part of making the manifest format engine-agnostic."""
    from capsule_ledger.folds.catalog import Catalog as FoldCatalog

    real_digest = FoldCatalog(CATALOG_DIR).get("spend.weekly/1.0.0").digest
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "spend.weekly/1.0.0", "engine": "rego/1", "digest": real_digest}],
        }
    )
    with pytest.raises(PolicyManifestError) as exc_info:
        resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert exc_info.value.reason == "unknown_engine"


def test_resolve_fails_closed_on_unrecognized_wicket_engine():
    from capsule_ledger.guards.wickets.catalog import Catalog as WicketCatalog

    real_digest = WicketCatalog(WICKET_CATALOG_DIR).get("caps/1.0.0").digest
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "wickets": [{"wicket_id": "caps/1.0.0", "engine": "cedar/1", "digest": real_digest}],
        }
    )
    with pytest.raises(PolicyManifestError) as exc_info:
        resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert exc_info.value.reason == "unknown_engine"


def test_resolve_rejects_unrecognized_engine_before_checking_digest_drift():
    """Engine unrecognized takes priority over digest drift -- even a
    garbage digest pin should surface the engine problem first, since an
    unrecognized engine means this build can't evaluate the entry at all."""
    manifest = parse_manifest(
        {
            "manifest_id": "m/1.0.0",
            "folds": [{"fold_id": "spend.weekly/1.0.0", "engine": "rego/1", "digest": "0" * 64}],
        }
    )
    with pytest.raises(PolicyManifestError) as exc_info:
        resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
    assert exc_info.value.reason == "unknown_engine"
