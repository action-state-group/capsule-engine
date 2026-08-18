# SPDX-License-Identifier: Apache-2.0
"""Engine-instance-per-tenant provisioning (``capsule_ledger.tenants`` +
``capsule tenant init/upgrade/list``): one physically-separate ledger dir +
pinned manifest + signing key per tenant, templated so provisioning many
tenants is a script, not bespoke work per customer."""
from __future__ import annotations

import json

import pytest

from capsule_ledger.cli.main import main
from capsule_ledger.ledger import LedgerStore
from capsule_ledger.packs import load_pack_dir
from capsule_ledger.policy import load_manifest_file
from capsule_ledger.tenants import (
    TenantProvisionError,
    init_tenant,
    list_tenants,
    tenant_layout,
    upgrade_tenant,
)

PACK_CATALOG_DIR = None  # resolved lazily below, mirrors cli/init_cmds.py's built-in catalog


def _payments_safety_pack():
    from capsule_ledger.cli.init_cmds import BUILTIN_PACK_CATALOG_DIR

    return load_pack_dir(BUILTIN_PACK_CATALOG_DIR / "payments-safety")


# -- unit: tenant_layout / tenant_id validation -------------------------------


def test_tenant_layout_rejects_path_traversal(tmp_path):
    for bad_id in ["../evil", "a/b", ".hidden", "-leading-dash", "UPPER", ""]:
        with pytest.raises(TenantProvisionError) as exc_info:
            tenant_layout(tmp_path, bad_id)
        assert exc_info.value.reason == "invalid_tenant_id"


def test_tenant_layout_accepts_a_normal_id(tmp_path):
    layout = tenant_layout(tmp_path, "acme-corp")
    assert layout.root == tmp_path / "acme-corp"
    assert layout.ledger_dir == tmp_path / "acme-corp" / "ledger"


# -- unit: init_tenant ---------------------------------------------------------


def test_init_tenant_default_manifest_creates_layout_and_activation(tmp_path):
    result = init_tenant(tmp_path, "acme", operator="acme-ops", developer="acme-agent")

    layout = result.layout
    assert layout.ledger_dir.is_dir()
    assert layout.manifest_path.is_file()
    assert layout.metadata_path.is_file()

    manifest = load_manifest_file(layout.manifest_path)
    assert manifest.manifest_id == result.manifest_id

    with LedgerStore(layout.ledger_dir) as store:
        records = list(store.scan())
    assert len(records) == 1
    assert records[0].capsule_id == result.activation_capsule_id
    assert records[0].capsule["asg_payload"]["detail"]["manifest_digest"] == result.manifest_digest

    metadata = json.loads(layout.metadata_path.read_text())
    assert metadata["tenant_id"] == "acme"
    assert metadata["manifest_digest"] == result.manifest_digest
    assert metadata["pack_id"] is None
    assert metadata["key_id"] == result.key_id
    # The signing secret must never be persisted to disk, anywhere.
    assert result.secret.decode("utf-8") not in layout.metadata_path.read_text()
    assert result.generated_secret is True


def test_init_tenant_with_pack_installs_pack_manifest(tmp_path):
    pack = _payments_safety_pack()
    result = init_tenant(tmp_path, "widgetco", pack=pack)

    metadata = json.loads(result.layout.metadata_path.read_text())
    assert metadata["pack_id"] == pack.pack_id

    manifest = load_manifest_file(result.layout.manifest_path)
    assert len(manifest.packs) == 1
    assert manifest.packs[0].pack_id == pack.pack_id
    assert any((result.layout.catalog_root / "catalog" / "folds").glob("payments_safety.*.yaml"))


def test_init_tenant_refuses_when_already_provisioned(tmp_path):
    init_tenant(tmp_path, "acme")
    with pytest.raises(TenantProvisionError) as exc_info:
        init_tenant(tmp_path, "acme")
    assert exc_info.value.reason == "already_initialized"


def test_init_tenant_honors_explicit_key_and_secret(tmp_path):
    result = init_tenant(tmp_path, "acme", key_id="acme-k1", secret=b"acme-secret-material")
    assert result.key_id == "acme-k1"
    assert result.secret == b"acme-secret-material"
    assert result.generated_secret is False


# -- unit: upgrade_tenant -------------------------------------------------------


def test_upgrade_tenant_refuses_when_never_initialized(tmp_path):
    with pytest.raises(TenantProvisionError) as exc_info:
        upgrade_tenant(tmp_path, "ghost", key_id="k", secret=b"s")
    assert exc_info.value.reason == "not_initialized"


def test_upgrade_tenant_is_a_noop_when_manifest_unchanged(tmp_path):
    init_result = init_tenant(tmp_path, "acme", key_id="acme-k1", secret=b"acme-secret-material")

    upgrade_result = upgrade_tenant(tmp_path, "acme", key_id="acme-k1", secret=b"acme-secret-material")
    assert upgrade_result.changed is False
    assert upgrade_result.manifest_digest == init_result.manifest_digest
    assert upgrade_result.activation_capsule_id == init_result.activation_capsule_id

    with LedgerStore(init_result.layout.ledger_dir) as store:
        records = list(store.scan())
    assert len(records) == 1  # no pointless second activation


def test_upgrade_tenant_records_new_epoch_when_manifest_changes(tmp_path):
    init_result = init_tenant(tmp_path, "acme", key_id="acme-k1", secret=b"acme-secret-material")

    pack = _payments_safety_pack()
    upgrade_result = upgrade_tenant(tmp_path, "acme", key_id="acme-k1", secret=b"acme-secret-material", pack=pack)
    assert upgrade_result.changed is True
    assert upgrade_result.manifest_digest != init_result.manifest_digest

    with LedgerStore(init_result.layout.ledger_dir) as store:
        records = list(store.scan())
    assert len(records) == 2
    assert records[0].capsule_id == init_result.activation_capsule_id
    new_activation = records[1]
    assert new_activation.capsule_id == upgrade_result.activation_capsule_id
    assert new_activation.capsule["chain"]["parent_capsule_id"] == init_result.activation_capsule_id

    metadata = json.loads(init_result.layout.metadata_path.read_text())
    assert metadata["pack_id"] == pack.pack_id
    assert metadata["manifest_digest"] == upgrade_result.manifest_digest


# -- unit: list_tenants ---------------------------------------------------------


def test_list_tenants_empty_root_returns_empty_list(tmp_path):
    assert list_tenants(tmp_path / "does-not-exist") == []


def test_list_tenants_sorted_by_id(tmp_path):
    init_tenant(tmp_path, "widgetco")
    init_tenant(tmp_path, "acme")

    tenants = list_tenants(tmp_path)
    assert [t["tenant_id"] for t in tenants] == ["acme", "widgetco"]


def test_list_tenants_skips_a_directory_with_no_metadata(tmp_path):
    (tmp_path / "half-provisioned").mkdir()
    init_tenant(tmp_path, "acme")

    tenants = list_tenants(tmp_path)
    assert [t["tenant_id"] for t in tenants] == ["acme"]


# -- CLI: capsule tenant init/upgrade/list -------------------------------------


def test_cli_tenant_init_prints_secret_once(tmp_path, capsys):
    tenants_root = tmp_path / "tenants"
    rc = main(["tenant", "init", "--tenants-root", str(tenants_root), "--tenant-id", "acme"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "provisioned tenant 'acme'" in out
    assert "signing secret (shown once" in out
    assert (tenants_root / "acme" / "tenant.json").is_file()


def test_cli_tenant_init_rejects_bad_tenant_id(tmp_path, capsys):
    rc = main(["tenant", "init", "--tenants-root", str(tmp_path), "--tenant-id", "../evil"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "invalid_tenant_id" in err
    assert not (tmp_path.parent / "evil").exists()


def test_cli_tenant_init_unknown_pack_lists_available(tmp_path, capsys):
    rc = main(["tenant", "init", "--tenants-root", str(tmp_path), "--tenant-id", "acme", "--pack", "does-not-exist"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "payments-safety" in err


def test_cli_tenant_upgrade_requires_key_and_secret(tmp_path, capsys):
    tenants_root = tmp_path / "tenants"
    main(["tenant", "init", "--tenants-root", str(tenants_root), "--tenant-id", "acme", "--key-id", "k1", "--secret", "s1"])
    capsys.readouterr()

    rc = main(["tenant", "upgrade", "--tenants-root", str(tenants_root), "--tenant-id", "acme"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--key-id/--secret are required" in err


def test_cli_tenant_upgrade_noop_then_changed(tmp_path, capsys):
    tenants_root = tmp_path / "tenants"
    main(["tenant", "init", "--tenants-root", str(tenants_root), "--tenant-id", "acme", "--key-id", "k1", "--secret", "s1"])
    capsys.readouterr()

    rc = main(
        ["tenant", "upgrade", "--tenants-root", str(tenants_root), "--tenant-id", "acme", "--key-id", "k1", "--secret", "s1"]
    )
    assert rc == 0
    assert "nothing to activate" in capsys.readouterr().out

    rc = main(
        [
            "tenant", "upgrade", "--tenants-root", str(tenants_root), "--tenant-id", "acme",
            "--key-id", "k1", "--secret", "s1", "--pack", "payments-safety",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "upgraded tenant 'acme'" in out


def test_cli_tenant_list(tmp_path, capsys):
    tenants_root = tmp_path / "tenants"
    main(["tenant", "init", "--tenants-root", str(tenants_root), "--tenant-id", "acme"])
    main(["tenant", "init", "--tenants-root", str(tenants_root), "--tenant-id", "widgetco"])
    capsys.readouterr()

    rc = main(["tenant", "list", "--tenants-root", str(tenants_root)])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 2
    assert lines[0].startswith("acme\t")
    assert lines[1].startswith("widgetco\t")


def test_cli_tenant_list_empty(tmp_path, capsys):
    rc = main(["tenant", "list", "--tenants-root", str(tmp_path / "nope")])
    assert rc == 0
    assert "no provisioned tenants" in capsys.readouterr().out
