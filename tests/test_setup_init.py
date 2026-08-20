# SPDX-License-Identifier: Apache-2.0
import pytest

from capsule_ledger.ledger import LedgerStore
from capsule_engine.setup.init import setup_init, signer_for


def test_setup_init_creates_ledger_and_declarations_dir(tmp_path):
    result = setup_init(tmp_path, key_id="k", secret=b"s" * 4)
    assert result.ledger_dir.is_dir()
    assert result.declarations_dir.is_dir()
    assert result.key_id == "k"
    assert result.secret == b"s" * 4
    assert not result.generated_secret


def test_setup_init_generates_a_secret_when_none_given(tmp_path):
    result = setup_init(tmp_path)
    assert result.generated_secret
    assert len(result.secret) > 0


def test_setup_init_ledger_is_immediately_appendable(tmp_path):
    result = setup_init(tmp_path, key_id="k", secret=b"s" * 4)
    signer = signer_for(result)
    from capsule_engine.guards.capsule import build_event_capsule

    capsule = build_event_capsule(operator="op", developer="dev", signer=signer, event="test.event", detail={})
    with LedgerStore(result.ledger_dir) as ledger:
        ledger.append(capsule, consequential=False)
        assert ledger.fetch(capsule["capsule_id"]) is not None


def test_setup_init_tenant_mode_requires_tenants_root(tmp_path):
    with pytest.raises(ValueError):
        setup_init(tmp_path, tenant_id="t1")


def test_setup_init_tenant_mode_delegates_to_tenant_kit(tmp_path):
    tenants_root = tmp_path / "tenants"
    result = setup_init(tmp_path, tenant_id="tenant-a", tenants_root=tenants_root)
    assert result.tenant is not None
    assert result.ledger_dir == result.tenant.layout.ledger_dir
    assert result.ledger_dir.is_dir()
