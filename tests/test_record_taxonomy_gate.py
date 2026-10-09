# SPDX-License-Identifier: Apache-2.0
"""An action's class is evaluated only under the taxonomy it was drawn from
(``guards/engine.py``).

Live, an action naming another taxonomy version than the engine's is not
evaluated: every check keyed on ``action_class`` is ``n/a``, in scope, with
evidence naming both versions. When nothing else fails it is refused, sealed
``reject``, its verdict ``not_evaluable``: another version never escapes a
class-keyed refusal, and no later check counts the act as seen or as spend.
A replay evaluates a sealed record under its own version when the engine
carries that table, and holds it the same way when it does not; every record
still gets a decision.

The earlier tables are carried as they shipped. Each file's SHA-256 is pinned
here against the table at the merge that shipped it: 2 at #81 (afedae0dd0f42d9336e851d41e7159fb4f4f4248), 3 at #99
(a5e9e300ff9ded2f098bf484c3f16dbca2c17ce7), 4 at #109 (5300e0be262fa2d878b18eca630cbf539f785752), 5 at #114
(e5ec2c1ba84f9d9269cbb164eb6a92400e4d5d49).
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_engine.guards.checks.action_class_gate import check_action_class_gate
from capsule_engine.guards.classes import (
    CARRIED_TAXONOMY_VERSIONS,
    ENGINE_TAXONOMY,
    TAXONOMY_VERSION,
    carried_taxonomy,
)
from capsule_engine.guards.engine import NOT_EVALUABLE
from capsule_engine.guards.wickets import load_definition_file as load_wicket
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.report.replay import replay

ROOT = Path(__file__).parent.parent / "capsule_engine"
PACK_DIR = ROOT / "packs" / "catalog" / "everyday"
SPEND_WEEKLY = ROOT / "folds" / "catalog_defs" / "spend.weekly.yaml"
SIGNER = LocalSigner(key_id="record-taxonomy-gate-key", secret=b"record-taxonomy-gate-fixed-key")
CARRIED_SHA256 = {
    "2": "0432d61bc2a309dd410722276f9f79c497edb8bad3fcf576a47c7129ff512afb",
    "3": "8d6ce5917cd76e239ce5675ba88b4257f537974f8605e848d15713f646956e64",
    "4": "9e10156647792a0e3fe6cf24e5211ebb8757cb8362c1e868dcfe6db6e8d891a7",
    "5": "7d31e892cdea7352d0485c89430dba028bf02ce91fb0e932f4752c1f4066022f",
}
# The checks the everyday pack runs that a mismatch never holds: credential_pattern
# reads no action_class, and dedupe and verify_before_dispatch are integrity checks.
NEVER_HELD = {"dedupe", "verify_before_dispatch", "credential_pattern"}
PROMISE_NEVER = load_wicket(ROOT / "guards" / "wickets" / "catalog_defs" / "promise_never.yaml")


def _file_sha256(name: str) -> str:
    return hashlib.sha256(resources.files("capsule_engine.guards").joinpath(name).read_bytes()).hexdigest()


# -- the carried tables ---------------------------------------------------------------


def test_the_engine_carries_every_shipped_table_and_no_other():
    assert TAXONOMY_VERSION == "6"
    assert CARRIED_TAXONOMY_VERSIONS == ("2", "3", "4", "5", "6")
    # Version 1 is what an unversioned record is read as; it never had a table.
    assert carried_taxonomy("1") is None
    assert carried_taxonomy(5) is None, "only a string names a version"
    assert carried_taxonomy(TAXONOMY_VERSION) is ENGINE_TAXONOMY


@pytest.mark.parametrize("version", sorted(CARRIED_SHA256))
def test_each_earlier_table_is_byte_identical_to_the_one_that_shipped(version):
    assert _file_sha256(f"action_taxonomy_v{version}.json") == CARRIED_SHA256[version]
    table = carried_taxonomy(version)
    assert table is not None and table.version == version


def test_the_carried_tables_differ_only_in_approver_role():
    """So the caps limits, which resolve a class's name through the engine's
    table, key every carried version's class the same way; and a replay under
    an earlier table changes only whether a hold may ask."""
    def shape(version: str) -> tuple:
        table = carried_taxonomy(version)
        assert table is not None
        rows = {n: replace(ac, approver_role=None) for n, ac in table.classes.items()}
        return rows, dict(table.aliases), table.trigger_classes, table.unversioned_read_as

    assert all(shape(v) == shape(TAXONOMY_VERSION) for v in CARRIED_TAXONOMY_VERSIONS)
    roles = {v: carried_taxonomy(v).classify("agreement.accept").approver_role for v in ("5", "6")}
    assert roles == {"5": None, "6": "account_holder"}


def test_the_gate_names_the_table_it_resolved_under():
    selectors = {"accepts": {"action_classes": ["agreement.accept"], "on_match": "fail"}}
    action = _action("agreement.accept", 1, 100)
    for version in ("5", "6"):
        out = check_action_class_gate(action, selectors=selectors, table=carried_taxonomy(version)).constraint
        assert (out.result, out.evidence["taxonomy_version"]) == ("fail", version)


# -- live: another version is not evaluated -------------------------------------------


def _action(action_class: str, n: int, amount_minor: int, **fields) -> Action:
    return Action(
        verb="make_purchase",
        operator="household-taxonomy-gate",
        developer="household-assistant-tg@v1",
        action_class=action_class,
        currency="EUR",
        rail="card",
        target="shop/garden-centre",
        amount_minor=amount_minor,
        action_id=f"make_purchase/taxonomy-gate-{n}",
        timestamp=f"2026-08-10T12:{n:02d}:00Z",
        **fields,
    )


@pytest.fixture
def engine(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    installed = install_pack(load_pack_dir(PACK_DIR), project_dir=tmp_path / "project", mode="observe")
    yield build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
    store.close()


def _held(decision) -> set[str]:
    return {c.id for c in decision.constraints if c.result == "n/a" and "taxonomy_mismatch" in (c.evidence or {})}


def test_a_live_action_at_taxonomy_5_is_not_evaluable_naming_5_and_6(engine):
    decision = engine.check(_action("money.purchase", 1, 1_800, taxonomy_version="5"))
    assert decision.taxonomy_mismatch == {"record_taxonomy_version": "5", "engine_taxonomy_version": "6"}
    assert (decision.outcome, decision.verdict) == (DENY, NOT_EVALUABLE)
    disposition = decision.capsule["disposition"]
    assert (disposition["decision"], disposition["verdict_class"]) == ("reject", "blocked")
    ids = {c.id for c in decision.constraints}
    assert NEVER_HELD <= ids
    assert _held(decision) == ids - NEVER_HELD
    for c in decision.constraints:
        if c.id in NEVER_HELD:
            assert "taxonomy_mismatch" not in (c.evidence or {}), c.id
            continue
        assert c.evidence == {
            "constraint_id": c.id,
            "in_scope": True,
            "missing_field": None,
            "taxonomy_mismatch": {"record_taxonomy_version": "5", "engine_taxonomy_version": "6"},
        }
        assert c.reason == (
            "taxonomy_version 5 on the action; the engine's action taxonomy is version 6: "
            "action_class is not evaluated against a different table"
        )
    assert "not evaluable: taxonomy_version 5" in decision.reason
    assert decision.reason.endswith("; refused because its action class was not evaluated")
    assert decision.fold_envelopes == ()


def test_another_version_does_not_escape_a_class_keyed_refusal(engine):
    """A class the taxonomy has no row for is refused by the gate at 6; named
    at 5, the gate is not evaluated, and the action is still refused."""
    at_6 = engine.check(_action("money.unlisted", 6, 100, taxonomy_version="6"))
    assert "action_class_gate" in [c.id for c in at_6.constraints if c.result == "fail"]
    assert at_6.verdict == at_6.outcome == DENY
    at_5 = engine.check(_action("money.unlisted", 7, 200, taxonomy_version="5"))
    assert [c.id for c in at_5.constraints if c.result == "fail"] == []
    assert (at_5.outcome, at_5.verdict) == (DENY, NOT_EVALUABLE)
    # Refused for its version, not for a missing approver: nothing failed.
    assert at_5.reason.endswith("; refused because its action class was not evaluated")


def test_a_held_act_is_never_counted_as_seen_or_as_spend(engine):
    """A held purchase at a merchant leaves it new and spends nothing, so the
    next purchase there, at the engine's version, is a first contact with
    nothing spent this week."""
    assert engine.check(_action("money.purchase", 8, 1_800, taxonomy_version="5")).outcome == DENY
    decision = engine.check(_action("money.purchase", 9, 1_000, taxonomy_version="6"))
    (seen,) = [c for c in decision.constraints if c.id == "counterparty_seen_before"]
    assert (seen.result, seen.evidence["prior_count"]) == ("fail", 0)
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    assert (caps.result, caps.evidence["weekly_spend_minor"]) == ("pass", 0)
    assert decision.outcome == ESCALATE


def test_the_same_action_at_the_engines_version_is_evaluated(engine):
    """The action the test above holds asks at taxonomy 6: a first purchase at
    an unseen merchant. So the hold is what changed it, not the action."""
    decision = engine.check(_action("money.purchase", 1, 1_800, taxonomy_version="6"))
    assert decision.taxonomy_mismatch is None and _held(decision) == set()
    assert decision.verdict == decision.outcome == ESCALATE


def test_an_action_naming_no_version_is_read_under_the_engines(engine):
    decision = engine.check(_action("money.purchase", 1, 1_800))
    assert decision.taxonomy_mismatch is None and _held(decision) == set()
    assert decision.outcome == ESCALATE


def test_a_live_mismatch_still_refuses_a_repeat_on_dedupe(engine):
    first = _action("money.purchase", 2, 900, taxonomy_version="5")
    assert engine.check(first).verdict == NOT_EVALUABLE
    repeat = engine.check(first)
    assert [c.id for c in repeat.constraints if c.result == "fail"] == ["dedupe"]
    assert repeat.verdict == repeat.outcome == DENY


def test_a_live_mismatch_never_holds_an_integrity_check(tmp_path):
    """promise_never refuses whatever the taxonomy says, so a mismatch leaves
    its refusal standing."""
    with LedgerStore(tmp_path / "ledger") as store:
        engine = GuardEngine(
            ledger=store, caps_fold=load_fold(SPEND_WEEKLY), signer_provider=lambda: SIGNER, wickets=(PROMISE_NEVER,)
        )
        decision = engine.check(_action("agreement.accept", 4, 100, taxonomy_version="5", representation_class="authenticity"))
    assert decision.taxonomy_mismatch is not None
    (never,) = [c for c in decision.constraints if c.id == "promise_never"]
    assert never.result == "fail"
    assert decision.verdict == decision.outcome == DENY


@pytest.mark.parametrize("version", [6, 5])
def test_a_version_that_is_not_a_string_is_not_evaluated(engine, version):
    decision = engine.check(_action("money.purchase", 5, 1_800, taxonomy_version=version))
    assert decision.verdict == NOT_EVALUABLE
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    assert caps.reason.startswith(f"taxonomy_version {version!r} is not a version string")


# -- replay: under the record's own table, or held ------------------------------------


class _Body(TypedDict):
    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    spend_minor: int


class _DealBlock(TypedDict):
    record_type: str
    deal_id: str
    seq: int


_SealedCheck = TypedDict("_SealedCheck", {"body": _Body, "x-deal-v0": _DealBlock})


class _Attestation(TypedDict):
    agent_input_digest: str


class _ModelAttestation(TypedDict):
    compute_attestation: _Attestation


class _BoundCapsule(TypedDict):
    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: _ModelAttestation


def _sealed_check(seq: int, version: str, spend_minor: int = 500) -> tuple[_BoundCapsule, _SealedCheck]:
    """An agreement.accept deal check sealed at taxonomy ``version``, and the
    capsule binding it. Each spends its own amount, so no two are one act for
    dedupe."""
    body = _Body(action="sign", action_class="agreement.accept", taxonomy_version=version, currency="EUR",
                 spend_minor=spend_minor + seq)
    record: _SealedCheck = {"body": body, "x-deal-v0": {"record_type": "check", "deal_id": f"deal-{seq:016x}", "seq": seq}}
    capsule = _BoundCapsule(
        capsule_id=f"{seq:064x}",
        action_id=f"deal-{seq:016x}/{seq}",
        action_type="fyi",
        operator="household-taxonomy-gate",
        developer="capsulectl-deal",
        timestamp=f"2026-10-0{seq}T12:00:00Z",
        model_attestation={"compute_attestation": {"agent_input_digest": json_digest(record)}},
    )
    return capsule, record


def _replayed(*checks: tuple[_BoundCapsule, _SealedCheck]):
    result = replay(
        [dict(capsule) for capsule, _ in checks],
        caps_fold=load_fold(SPEND_WEEKLY),
        caps_minor={"agreement.accept": 100},
        disclosed={capsule["capsule_id"]: dict(record) for capsule, record in checks},
    )
    return [sourced.decision for sourced in result.decisions]


def test_a_record_sealed_at_5_replays_under_5():
    """Over the limit, a hold asks only a class with an approver: in taxonomy 5
    agreement.accept had none, in 6 it has. Each record gets its own table's
    answer, and neither is held."""
    at_5, at_6 = _replayed(_sealed_check(1, "5"), _sealed_check(2, "6"))
    for decision in (at_5, at_6):
        assert [c.id for c in decision.constraints if c.result == "fail"] == ["caps"]
        assert decision.taxonomy_mismatch is None and _held(decision) == set()
    assert (at_5.outcome, at_6.outcome) == (DENY, ESCALATE)


def test_a_record_at_a_carried_version_is_allowed_when_it_passes():
    """Only an uncarried version is refused for its version: under the limit,
    a record sealed at 5 is evaluated under 5 and allowed."""
    (decision,) = _replayed(_sealed_check(1, "5", spend_minor=10))
    assert decision.taxonomy_mismatch is None
    assert decision.verdict == decision.outcome == ALLOW


def test_a_record_at_a_version_the_engine_does_not_carry_is_held_and_refused():
    (decision,) = _replayed(_sealed_check(1, "1"))
    assert decision.capsule is not None, "the record still gets a decision"
    assert decision.taxonomy_mismatch == {"record_taxonomy_version": "1", "engine_taxonomy_version": "6"}
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    assert caps.result == "n/a"
    assert caps.evidence["taxonomy_mismatch"] == {"record_taxonomy_version": "1", "engine_taxonomy_version": "6"}
    assert caps.reason == (
        "taxonomy_version 1 on the record; this engine carries action taxonomy versions 2, 3, 4, 5, 6 "
        "and not 1: action_class is not evaluated against a different table"
    )
    assert (decision.outcome, decision.verdict) == (DENY, NOT_EVALUABLE)
    assert decision.capsule["disposition"]["decision"] == "reject"
