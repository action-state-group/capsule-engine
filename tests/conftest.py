# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.folds.loader import load_definition_file
from capsule_engine.guards import LocalSigner

CATALOG_DIR = Path(__file__).parent.parent / "capsule_engine" / "folds" / "catalog_defs"
WICKET_CATALOG_DIR = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
DEFAULT_MANIFEST_PATH = Path(__file__).parent.parent / "capsule_engine" / "policy" / "catalog_defs" / "default.yaml"


@pytest.fixture
def store(tmp_path):
    s = LedgerStore(tmp_path)
    yield s
    s.close()


@pytest.fixture
def caps_fold():
    return load_definition_file(CATALOG_DIR / "spend.weekly.yaml")


@pytest.fixture
def signer():
    return LocalSigner(key_id="test-key-1", secret=b"test-secret")


@pytest.fixture
def resolved_manifest():
    from capsule_engine.policy import load_manifest_file, resolve_manifest

    manifest = load_manifest_file(DEFAULT_MANIFEST_PATH)
    return resolve_manifest(manifest, fold_catalog_dir=CATALOG_DIR, wicket_catalog_dir=WICKET_CATALOG_DIR)
