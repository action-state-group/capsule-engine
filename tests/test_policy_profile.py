# SPDX-License-Identifier: Apache-2.0
"""A user's limits live in a policy profile (``policy/profile.py``), not in
the pack or the wicket: changing a limit opens a new activation and leaves
every catalog artifact byte-identical, two households with different limits
share one wicket digest and one pack digest, and what was in force is
recomputable from the activation record alone."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_action_capsule import compute_capsule_id
from agent_action_capsule.canonical import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.packs import (
    accept_thresholds,
    build_engine,
    enforce_pack,
    install_pack,
    load_pack_dir,
    profile_for_accepted,
    record_pack_activation,
)
from capsule_engine.policy import (
    PROFILE_FORMAT,
    FoldRef,
    Manifest,
    PackRef,
    PolicyManifestError,
    PolicyProfile,
    WicketRef,
    load_profile_file,
    parse_profile,
    resolve_manifest,
)
from capsule_engine.policy.errors import (
    MALFORMED_PROFILE,
    PROFILE_DIGEST_DRIFT,
    PROFILE_MISSING,
    PROFILE_UNKNOWN_PACK,
    PROFILE_UNKNOWN_PARAMETER,
    PROFILE_UNPINNED,
)

EVERYDAY_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
PAYMENTS_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "payments-safety"
SIGNER = LocalSigner(key_id="profile-test-key", secret=b"profile-test-fixed-key")


def _profile(per_action: int) -> PolicyProfile:
    return parse_profile(
        {
            "format": PROFILE_FORMAT,
            "packs": [{"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"money.purchase": per_action}}}}],
        }
    )


def _catalog_bytes(project: Path) -> dict[str, bytes]:
    root = project / ".capsule" / "catalog"
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _caps_wicket_digest(installed) -> str:
    return next(w.digest for w in installed.manifest.wickets if w.wicket_id.startswith("caps/"))


def _purchase(name: str, amount_minor: int) -> Action:
    return Action(
        verb="make_purchase",
        operator="household-profile-fixture",
        developer="household-assistant@v1",
        action_class="money.purchase",
        amount_minor=amount_minor,
        currency="USD",
        target="shop/hardware-store",
        rail="card",
        recurrence="one_time",
        action_id=f"make_purchase/profile-{name}",
        timestamp="2026-10-06T10:00:00Z",
    )


def test_changing_a_limit_is_a_new_activation_and_no_new_catalog_artifact(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    project = tmp_path / "household-a"
    ledger = LedgerStore(tmp_path / "ledger")

    at_25 = install_pack(pack, project_dir=project, mode="enforce", profile=_profile(2_500))
    first = record_pack_activation(at_25, ledger=ledger, operator="household-a", developer="ops", signer=SIGNER,
                                   timestamp="2026-10-06T09:00:00Z")
    catalog_before = _catalog_bytes(project)

    at_40 = install_pack(pack, project_dir=project, mode="enforce", profile=_profile(4_000))
    second = record_pack_activation(at_40, ledger=ledger, operator="household-a", developer="ops", signer=SIGNER,
                                    timestamp="2026-10-06T09:05:00Z")
    ledger.close()

    assert _catalog_bytes(project) == catalog_before
    assert at_40.manifest.wickets == at_25.manifest.wickets
    assert at_40.manifest.packs == at_25.manifest.packs
    # The policy did change: the manifest pins the new profile, and the new
    # activation chains to the old one.
    assert at_40.resolved.manifest_digest != at_25.resolved.manifest_digest
    assert second["chain"]["parent_capsule_id"] == first["capsule_id"]
    d1, d2 = first["asg_payload"]["detail"], second["asg_payload"]["detail"]
    assert d1["profile"]["profile_digest"] != d2["profile"]["profile_digest"]
    assert at_40.resolved.per_action_minor()["money.purchase"] == 4_000


def test_two_households_share_the_wicket_and_pack_and_differ_only_in_profile(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    a = install_pack(pack, project_dir=tmp_path / "household-a", mode="enforce", profile=_profile(2_500))
    b = install_pack(pack, project_dir=tmp_path / "household-b", mode="enforce", profile=_profile(4_000))

    assert _caps_wicket_digest(a) == _caps_wicket_digest(b)
    assert a.manifest.packs[0].digest == b.manifest.packs[0].digest == pack.definition_digest()
    assert _catalog_bytes(tmp_path / "household-a") == _catalog_bytes(tmp_path / "household-b")
    assert a.manifest.profile_digest != b.manifest.profile_digest

    # The same 30.00 purchase trips the per-action limit at 25.00 and passes
    # at 40.00. (A caps breach on a class with no approver denies.) Each
    # household's merchant is new to its ledger, so everyday 0.3.0's
    # first-purchase check fails in both.
    outcomes = {}
    for name, installed in (("a", a), ("b", b)):
        ledger = LedgerStore(tmp_path / f"ledger-{name}")
        # A profile's values apply once an activation binds them. 40.00 is a
        # raise over the pack's 25.00, so it is activated more than the 12-hour
        # cooling-off before the purchase.
        record_pack_activation(installed, ledger=ledger, operator=f"household-{name}", developer="ops",
                               signer=SIGNER, timestamp="2026-10-05T09:00:00Z")
        engine = build_engine(installed, ledger=ledger, signer_provider=lambda: SIGNER,
                              clock=lambda: "2026-10-06T10:00:00Z")
        decision = engine.check(_purchase(name, 3_000), dry_run=False)
        (caps,) = [c for c in decision.constraints if c.id == "caps"]
        outcomes[name] = (caps.result, [c.id for c in decision.constraints if c.result == "fail"])
        assert decision.capsule["asg_payload"]["manifest_digest"] == installed.resolved.manifest_digest
        ledger.close()
    assert outcomes == {
        "a": ("fail", ["caps", "counterparty_seen_before"]),
        "b": ("pass", ["counterparty_seen_before"]),
    }


def test_what_was_in_force_is_recomputable_from_the_activation_alone(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    installed = install_pack(pack, project_dir=tmp_path / "p", mode="enforce", profile=_profile(4_000))
    ledger = LedgerStore(tmp_path / "ledger")
    capsule = record_pack_activation(installed, ledger=ledger, operator="household-b", developer="ops", signer=SIGNER)
    ledger.close()
    detail = capsule["asg_payload"]["detail"]

    # The values ride in the record; their digest is the one the manifest pins.
    values = detail["profile"]["values"]
    assert json_digest(values) == detail["profile"]["profile_digest"]
    assert parse_profile(values).profile_digest() == detail["profile"]["profile_digest"]
    assert values["packs"][0]["parameters"]["caps"]["per_action_minor"] == {"money.purchase": 4_000}

    # The manifest digest the record cites is recomputable from the record.
    recomputed = Manifest(
        manifest_id=detail["manifest_id"],
        folds=tuple(FoldRef(fold_id=f["fold_id"], engine="fold/1", digest=f["digest"]) for f in detail["folds"]),
        wickets=tuple(WicketRef(wicket_id=w["wicket_id"], engine="wicket/1", digest=w["digest"]) for w in detail["wickets"]),
        packs=tuple(PackRef(pack_id=p["pack_id"], engine="pack/1", digest=p["digest"], mode=p["mode"]) for p in detail["packs"]),
        profile_digest=detail["profile"]["profile_digest"],
    ).manifest_digest()
    assert recomputed == detail["manifest_digest"]
    assert compute_capsule_id(capsule) == capsule["capsule_id"]


def test_without_a_profile_the_manifest_and_activation_are_unchanged(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    installed = install_pack(pack, project_dir=tmp_path / "p", mode="observe")
    assert "profile_digest" not in installed.manifest.canonical_dict()
    assert not (tmp_path / "p" / ".capsule" / "policy" / "profile.json").exists()
    ledger = LedgerStore(tmp_path / "ledger")
    capsule = record_pack_activation(installed, ledger=ledger, operator="o", developer="d", signer=SIGNER)
    ledger.close()
    assert set(capsule["asg_payload"]["detail"]) == {"manifest_id", "manifest_digest", "folds", "wickets", "packs"}


def test_the_profile_file_written_at_install_reloads_to_the_pinned_digest(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    installed = install_pack(pack, project_dir=tmp_path / "p", mode="enforce", profile=_profile(4_000))
    reloaded = load_profile_file(tmp_path / "p" / ".capsule" / "policy" / "profile.json")
    assert reloaded.profile_digest() == installed.manifest.profile_digest
    again = resolve_manifest(
        installed.manifest,
        fold_catalog_dir=installed.fold_catalog_dir,
        wicket_catalog_dir=installed.wicket_catalog_dir,
        profile=reloaded,
    )
    assert again.manifest_digest == installed.resolved.manifest_digest


def test_an_unset_parameter_keeps_the_pack_default(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    installed = install_pack(pack, project_dir=tmp_path / "p", mode="enforce", profile=_profile(4_000))
    defaults = install_pack(pack, project_dir=tmp_path / "d", mode="enforce").resolved
    assert installed.resolved.caps_minor() == defaults.caps_minor()
    per_action = installed.resolved.per_action_minor()
    assert per_action.pop("money.purchase") == 4_000
    expected = defaults.per_action_minor()
    expected.pop("money.purchase")
    assert per_action == expected


def test_enforce_pack_records_accepted_caps_in_a_profile_not_the_wicket(tmp_path):
    pack = load_pack_dir(PAYMENTS_DIR)
    observe = install_pack(pack, project_dir=tmp_path / "o", mode="observe")
    enforced = enforce_pack(pack, project_dir=tmp_path / "e", accepted={"money.transfer": 500_000})
    assert enforced.manifest.wickets == observe.manifest.wickets
    assert enforced.manifest.packs[0].digest == observe.manifest.packs[0].digest
    assert enforced.resolved.caps_minor() == {"money.transfer": 500_000}
    assert enforced.profile == profile_for_accepted(pack, {"money.transfer": 500_000})


def test_accept_thresholds_still_rewrites_the_wicket_for_existing_callers():
    pack = load_pack_dir(PAYMENTS_DIR)
    accepted = accept_thresholds(pack, {"money.transfer": 500_000})
    assert accepted.definition_digest() != pack.definition_digest()


# -- fail closed -------------------------------------------------------------


def _manifest_pinning(installed, profile_digest):
    m = installed.manifest
    return Manifest(manifest_id=m.manifest_id, folds=m.folds, wickets=m.wickets, packs=m.packs, profile_digest=profile_digest)


def _resolve(installed, manifest, profile):
    return resolve_manifest(
        manifest,
        fold_catalog_dir=installed.fold_catalog_dir,
        wicket_catalog_dir=installed.wicket_catalog_dir,
        profile=profile,
    )


def test_a_pinned_profile_that_is_not_supplied_fails_closed(tmp_path):
    installed = install_pack(load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path, mode="enforce", profile=_profile(4_000))
    with pytest.raises(PolicyManifestError) as exc:
        _resolve(installed, installed.manifest, None)
    assert exc.value.reason == PROFILE_MISSING


def test_a_profile_the_manifest_does_not_pin_fails_closed(tmp_path):
    installed = install_pack(load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path, mode="enforce")
    with pytest.raises(PolicyManifestError) as exc:
        _resolve(installed, installed.manifest, _profile(4_000))
    assert exc.value.reason == PROFILE_UNPINNED


def test_a_profile_whose_digest_moved_fails_closed(tmp_path):
    installed = install_pack(load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path, mode="enforce", profile=_profile(4_000))
    with pytest.raises(PolicyManifestError) as exc:
        _resolve(installed, installed.manifest, _profile(4_001))
    assert exc.value.reason == PROFILE_DIGEST_DRIFT


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({"pack": "asg/payments-safety", "parameters": {"caps": {"caps_minor": {"money.transfer": 1}}}},
         PROFILE_UNKNOWN_PACK),
        ({"pack": "asg/everyday", "parameters": {"dedupe": {"window_days": {"money.purchase": 1}}}},
         PROFILE_UNKNOWN_PARAMETER),
        ({"pack": "asg/everyday", "parameters": {"caps": {"fold_id": {"money.purchase": 1}}}},
         PROFILE_UNKNOWN_PARAMETER),
        # A profile replaces a default; it never adds a class the pack does not limit.
        ({"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"comms.external": 1}}}},
         PROFILE_UNKNOWN_PARAMETER),
    ],
)
def test_a_profile_entry_the_pack_does_not_define_fails_closed(tmp_path, entry, reason):
    installed = install_pack(load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path, mode="enforce")
    profile = parse_profile({"format": PROFILE_FORMAT, "packs": [entry]})
    with pytest.raises(PolicyManifestError) as exc:
        _resolve(installed, _manifest_pinning(installed, profile.profile_digest()), profile)
    assert exc.value.reason == reason


def test_install_refuses_a_profile_the_pack_does_not_define(tmp_path):
    bad = parse_profile({"format": PROFILE_FORMAT, "packs": [
        {"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"comms.external": 1}}}}]})
    with pytest.raises(PolicyManifestError) as exc:
        install_pack(load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path, mode="enforce", profile=bad)
    assert exc.value.reason == PROFILE_UNKNOWN_PARAMETER
    # Nothing on disk pins the refused profile.
    assert not (tmp_path / ".capsule" / "policy" / "manifest.yaml").exists()
    assert not (tmp_path / ".capsule" / "policy" / "profile.json").exists()


def test_reinstalling_without_a_profile_removes_the_unpinned_profile_file(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    install_pack(pack, project_dir=tmp_path, mode="enforce", profile=_profile(4_000))
    assert (tmp_path / ".capsule" / "policy" / "profile.json").exists()
    install_pack(pack, project_dir=tmp_path, mode="enforce")
    assert not (tmp_path / ".capsule" / "policy" / "profile.json").exists()


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"packs": []},
        {"format": "policy-profile/v1", "packs": []},
        {"format": PROFILE_FORMAT, "packs": [], "owner": "household-a"},
        {"format": PROFILE_FORMAT, "packs": {}},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday/0.1.0", "parameters": {}}]},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {}, "x": 1}]},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {}},
                                             {"pack": "asg/everyday", "parameters": {}}]},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"money.purchase": -1}}}}]},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"money.purchase": True}}}}]},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"money.purchase": 25.0}}}}]},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"money.purchase": 2**53}}}}]},
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": []}}}]},
    ],
)
def test_a_malformed_profile_is_refused(data):
    with pytest.raises(PolicyManifestError) as exc:
        parse_profile(data)
    assert exc.value.reason == MALFORMED_PROFILE


def test_the_profile_digest_does_not_depend_on_key_order():
    a = parse_profile({"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"caps": {
        "per_action_minor": {"money.purchase": 1, "money.transfer": 2}, "caps_minor": {"money.purchase": 3}}}}]})
    b = parse_profile({"packs": [{"parameters": {"caps": {
        "caps_minor": {"money.purchase": 3}, "per_action_minor": {"money.transfer": 2, "money.purchase": 1}}}, "pack": "asg/everyday"}],
        "format": PROFILE_FORMAT})
    assert a.profile_digest() == b.profile_digest()
