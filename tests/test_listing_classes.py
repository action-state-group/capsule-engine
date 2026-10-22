# SPDX-License-Identifier: Apache-2.0
"""The sale's listing counts as a statement for s05 (capsule-engine#190).

capsulectl seals, on each offer and commit check of a sale's thread, the rule
input ``listing_classes``: the classes every live listing of the sale states
when the check is made, the intersection over the latest post per platform.
``required_disclosure/1.2.0`` counts a required class as stated when it is in
``Action.listing_classes``, bridged from the check's own sealed body and never
sealed on the decision, beside 1.1.0's two paths (an earlier statement to the
target, a claim the agent made in the deal). Seller 0.1.5 cites it.

The live decisions run on the real check inputs in ``fixtures/live-history``,
the replay on the bundles beside them; each with ``listing_classes`` added to
the checked record, rebound to its capsule (what capsulectl seals from this
batch; synthetic, so those capsules no longer verify).
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest
from agent_action_capsule import json_digest
from cll.ledger import LedgerStore

import capsule_engine
from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardDecision, GuardEngine, LocalSigner
from capsule_engine.guards.action import listing_classes_of
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.checks.required_disclosure import check_required_disclosure
from capsule_engine.guards.statements import make_statement_record
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.report.live_history import history_ledger
from capsule_engine.report.replay import (
    action_for_check_input,
    load_disclosed,
    load_records,
    load_withheld,
    replay,
)

ROOT = Path(capsule_engine.__file__).parent
FOLDS = ROOT / "folds" / "catalog_defs"
WICKETS = ROOT / "guards" / "wickets" / "catalog_defs"
CLASS_SEEN = load_fold(FOLDS / "disclosure.class_seen.yaml")
STATEMENT_SEEN = load_fold(FOLDS / "disclosure.statement_seen.yaml")
V1_1 = load_definition_file(WICKETS / "required_disclosure.v1.1.yaml")
V1_2 = load_definition_file(WICKETS / "required_disclosure.v1.2.yaml")
FIXTURE = Path(__file__).parent / "fixtures" / "live-history"
OWN = (FIXTURE / "buyer-a.bundle.json", FIXTURE / "buyer-b.bundle.json")
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
FROZEN_0_1_4 = load_pack_dir(Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.4")
OPERATOR = "synthetic-seller"


# -- the check -----------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    with LedgerStore(tmp_path / "ledger") as s:
        yield s


def _action(**overrides) -> Action:
    fields = {"verb": "offer", "operator": OPERATOR, "developer": "d", "action_class": "marketplace.offer",
              "target": "buyer-fp", "deal_id": "deal-a"}
    fields.update(overrides)
    return Action(**fields)


def _check(store, action: Action, definition=V1_2):
    config = definition.config
    return check_required_disclosure(
        action, store, definition=CLASS_SEEN, representation_classes=config["representation_classes"],
        required_classes=config["required_classes"], action_classes=config["action_classes"],
        statement_definition=STATEMENT_SEEN, count_listing_classes=config.get("count_listing_classes", False),
    ).constraint


@pytest.mark.parametrize(("listing", "result"), [
    (("condition", "price"), "pass"),
    (("condition",), "pass"),
    (("price",), "fail"),
    ((), "fail"),
    (None, "fail"),
], ids=["the listing states it", "only it", "a re-post without it", "listings sharing no class", "no post"])
def test_a_listing_counts_only_when_it_states_the_class(store, listing, result):
    found = _check(store, _action(listing_classes=listing))
    assert found.result == result
    assert found.evidence["listing_classes"] == (None if listing is None else list(listing))
    assert found.evidence["missing_classes"] == ([] if result == "pass" else ["condition"])


def test_a_claim_in_the_deal_still_counts_with_no_listing(store):
    store.append(make_statement_record(operator=OPERATOR, timestamp="2026-10-10T00:00:00Z", sealed_class="condition",
                                       source_kind="agent", deal_id="deal-a", claim="c" * 64))
    for listing in (None, (), ("price",)):
        found = _check(store, _action(listing_classes=listing))
        assert (found.result, found.evidence["stated_counts"]) == ("pass", {"condition": 1}), listing


def test_version_1_1_never_reads_a_listing(store):
    found = _check(store, _action(listing_classes=("condition",)), definition=V1_1)
    assert found.result == "fail"
    assert "listing_classes" not in found.evidence


def test_1_2_is_1_1_with_only_the_listing_switch_added():
    assert {k: v for k, v in V1_2.config.items() if k != "count_listing_classes"} == V1_1.config
    assert V1_2.config["count_listing_classes"] is True
    assert V1_2.check == V1_1.check


def test_the_switch_needs_the_statement_fold(store):
    with pytest.raises(ValueError, match="count_listing_classes goes with statement_fold_id"):
        check_required_disclosure(
            _action(), store, definition=CLASS_SEEN, representation_classes=V1_2.config["representation_classes"],
            required_classes=["condition"], action_classes=["marketplace.offer"], count_listing_classes=True)


@pytest.mark.parametrize(("value", "expected"), [
    (["price", "condition", "price"], ("condition", "price")),
    ([], ()),
    ("condition", None),
    (["Condition"], None),
    ([5], None),
    (None, None),
], ids=["sorted, once each", "empty", "a string", "not a token", "a number", "absent"])
def test_listing_classes_out_of_shape_is_dropped_whole(value, expected):
    assert listing_classes_of(value) == expected


def test_an_action_holds_listing_classes_sorted_and_once_each():
    with pytest.raises(ValueError, match="listing_classes"):
        _action(listing_classes=("price", "condition"))
    with pytest.raises(ValueError, match="listing_classes"):
        _action(listing_classes=["condition"])


# -- live: the real check inputs, with listing_classes in the checked record ----------


def _check_input(name: str) -> dict:
    return json.loads((FIXTURE / "check-inputs" / f"{name}.json").read_text(encoding="utf-8"))


def _rebound(record: dict, body_change: dict) -> dict:
    """``record`` (a capsule with its disclosed ``agent_input``) with its body
    changed, and the capsule rebound to the new bytes."""
    record = copy.deepcopy(record)
    body = record["agent_input"]["body"]
    for key, value in body_change.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    record["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(record["agent_input"])
    return record


def _live(name: str, listing: list[str] | None, pack=PACK) -> GuardDecision:
    envelope = _check_input(name)
    envelope["record"] = _rebound(envelope["record"], {"listing_classes": listing})
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        installed = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe")
        resolved = installed.resolved
        wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
        gates, asks = engine_ask_sets(installed.pack, wickets)
        signer = LocalSigner(key_id="listing", secret=b"listing-fixture")
        engine = GuardEngine(
            ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
            caps_minor=resolved.caps_minor() or {}, manifest_digest=resolved.manifest_digest, wickets=wickets,
            ask_gate_selectors=gates, ask_wickets=asks, evaluate_under_record_taxonomy=True,
        )
        history_ledger(envelope, store)
        action = action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref"))
        assert action.listing_classes == (None if listing is None else tuple(sorted(listing)))
        return engine.check(action, task_authority_record=envelope.get("task_authority_record"),
                            commercial_bounds_opening=envelope.get("commercial_bounds_opening"))


def _s05(decision: GuardDecision):
    (found,) = [c for c in decision.constraints if c.id == "required_disclosure"]
    return found


@pytest.mark.parametrize("name", ["a-offer-1900", "b-offer-1850", "a-commit-1900"])
def test_live_an_offer_or_commit_whose_listing_stated_the_condition_passes_s05(name):
    assert _s05(_live(name, None)).result == "fail", "as sealed, no chat claim reaches a live check"
    decision = _live(name, ["condition", "price"])
    assert _s05(decision).result == "pass"
    assert _s05(decision).evidence["listing_classes"] == ["condition", "price"]


@pytest.mark.parametrize("listing", [["price"], []], ids=["a re-post without the condition", "no class in common"])
def test_live_a_listing_without_the_condition_asks(listing):
    decision = _live("a-offer-1900", listing)
    assert _s05(decision).result == "fail"
    assert decision.outcome in ("escalate", "deny")


def test_live_0_1_4_never_reads_the_listing():
    assert _s05(_live("a-offer-1900", ["condition"], pack=FROZEN_0_1_4)).result == "fail"


def test_live_the_listing_is_never_sealed_on_the_decision():
    decision = _live("a-offer-1900", ["condition", "price"])
    assert "listing_classes" not in decision.capsule["asg_payload"]
    assert "listing_classes" not in json.dumps(decision.capsule)


# -- replay: the real bundles, each check carrying the listing it was made under -------


def _replay(listings: dict[str, list[str] | None], *, drop_claims: bool = True, pack=PACK) -> dict[str, GuardDecision]:
    """The bundles replayed with each named check (by action_id) carrying
    ``listing_classes``; the agent's chat claims left out unless kept."""
    records, disclosed = load_records(OWN), load_disclosed(OWN)
    records = copy.deepcopy(records)
    disclosed = copy.deepcopy(disclosed)
    if drop_claims:
        records = [r for r in records
                   if ((disclosed.get(r["capsule_id"]) or {}).get("x-deal-v0") or {}).get("record_type") != "claim"]
    for r in records:
        if r["action_id"] not in listings:
            continue
        shown = disclosed[r["capsule_id"]]
        assert shown["type"] == "proposed-action/v0"
        if listings[r["action_id"]] is not None:
            shown["body"]["listing_classes"] = listings[r["action_id"]]
        r["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(shown)
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe")
        resolved = installed.resolved
        result = replay(
            records, caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
            manifest_digest=resolved.manifest_digest, disclosed=disclosed, withheld=load_withheld(OWN),
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack,
        )
    return {s.record["action_id"]: s.decision for s in result.decisions}


DEAL_A, DEAL_B = "deal-a4a44a1583055667", "deal-c8f7695110ba493e"


def test_replay_reads_the_listing_each_check_sealed():
    """Thread A's offer was checked under a listing stating the condition, and
    its commit after a re-post without it: the offer passes and keeps that,
    the commit asks. Thread B's offer, under listings sharing no class,
    asks; with no listing at all, its commit asks too."""
    decisions = _replay({f"{DEAL_A}/4": ["condition", "price"], f"{DEAL_A}/8": ["price"], f"{DEAL_B}/4": []})
    assert _s05(decisions[f"{DEAL_A}/4"]).result == "pass"
    assert _s05(decisions[f"{DEAL_A}/8"]).result == "fail"
    assert _s05(decisions[f"{DEAL_B}/4"]).result == "fail"
    commits_b = [k for k in decisions if k.startswith(f"{DEAL_B}/") and _s05(decisions[k]).result != "n/a"
                 and k != f"{DEAL_B}/4"]
    assert commits_b and all(_s05(decisions[k]).result == "fail" for k in commits_b)


def test_replay_a_chat_claim_still_counts_with_no_listing():
    decisions = _replay({}, drop_claims=False)
    checked = {k: d for k, d in decisions.items() if d.capsule is not None and _s05(d).result != "n/a"}
    assert checked and all(_s05(d).result == "pass" for d in checked.values())


def test_replay_under_0_1_4_never_reads_the_listing():
    decisions = _replay({f"{DEAL_A}/4": ["condition", "price"]}, pack=FROZEN_0_1_4)
    assert _s05(decisions[f"{DEAL_A}/4"]).result == "fail"
