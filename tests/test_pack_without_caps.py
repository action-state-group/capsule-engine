# SPDX-License-Identifier: Apache-2.0
"""A pack may cite no ``caps`` definition (``guards/engine.py``).

The engine then records ``caps`` on every decision as ``n/a``, out of scope,
with no method: there is no fold to name. It reads no limit for it, so a
limit given without a definition is refused when the engine is built. A
taxonomy mismatch does not hold an absent ``caps``: it reads nothing keyed on
the class. A replay of such a pack runs the same way.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, not_applicable_evidence
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.report.replay import replay

ROOT = Path(__file__).parent.parent / "capsule_engine"
SELLER_DIR = ROOT / "packs" / "catalog" / "seller"
SIGNER = LocalSigner(key_id="pack-without-caps-key", secret=b"pack-without-caps-fixed-key")
OUT_OF_SCOPE = json_digest(not_applicable_evidence("caps", in_scope=False))


def _transfer(n: int, **fields) -> Action:
    """A money transfer: the one class caps/1.0.0 limits, so caps would
    read it if a definition were cited."""
    return Action(
        verb="send_money",
        operator="pack-without-caps",
        developer="assistant@v1",
        action_class="money.transfer",
        currency="EUR",
        rail="card",
        target="payee/garden-centre",
        amount_minor=9_999_999,
        action_id=f"send_money/pack-without-caps-{n}",
        timestamp=f"2026-10-09T12:{n:02d}:00Z",
        **fields,
    )


def _caps(decision) -> dict:
    (record,) = [c for c in decision.capsule["constraints"] if c["id"] == "caps"]
    return record


@pytest.fixture
def store(tmp_path):
    s = LedgerStore(tmp_path / "ledger")
    yield s
    s.close()


def test_with_no_caps_definition_caps_is_out_of_scope_and_the_action_is_decided(store):
    engine = GuardEngine(ledger=store, caps_fold=None, signer_provider=lambda: SIGNER)
    decision = engine.check(_transfer(1))
    assert decision.outcome == ALLOW
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    assert (caps.result, caps.method, caps.reason) == ("n/a", None, "no caps definition is configured")
    assert caps.evidence == not_applicable_evidence("caps", in_scope=False)
    record = _caps(decision)
    assert record["evidence_digest"] == OUT_OF_SCOPE and "method" not in record
    assert decision.fold_envelopes == ()


@pytest.mark.parametrize("limits", [{"caps_minor": {"money.transfer": 100}},
                                    {"per_action_minor": {"money.transfer": 100}}])
def test_a_limit_without_a_caps_definition_is_refused_when_the_engine_is_built(store, limits):
    with pytest.raises(ValueError, match="no caps fold definition"):
        GuardEngine(ledger=store, caps_fold=None, signer_provider=lambda: SIGNER, **limits)


def test_a_taxonomy_mismatch_leaves_an_absent_caps_out_of_scope_and_still_refuses(store):
    engine = GuardEngine(ledger=store, caps_fold=None, signer_provider=lambda: SIGNER)
    decision = engine.check(_transfer(2, taxonomy_version="5"))
    assert decision.taxonomy_mismatch is not None
    assert _caps(decision)["evidence_digest"] == OUT_OF_SCOPE
    # Its class was never evaluated: refused, never sealed as accepted.
    assert decision.outcome == DENY


def test_the_seller_fixture_replays_under_the_pack_with_caps_out_of_scope(tmp_path):
    installed = install_pack(load_pack_dir(SELLER_DIR), project_dir=tmp_path / "project", mode="observe")
    resolved = installed.resolved
    assert resolved.caps_fold() is None
    lines = (SELLER_DIR / "fixtures" / "mini_ledger.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    result = replay(
        records,
        caps_fold=resolved.caps_fold(),
        manifest_digest=resolved.manifest_digest,
        wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
        pack=installed.pack,
    )
    assert result.decisions
    for sourced in result.decisions:
        (caps,) = [c for c in sourced.decision.capsule["constraints"] if c["id"] == "caps"]
        assert caps["evidence_digest"] == OUT_OF_SCOPE and "method" not in caps
