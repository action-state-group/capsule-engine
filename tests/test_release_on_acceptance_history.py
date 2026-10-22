# SPDX-License-Identifier: Apache-2.0
"""seller.release_on_acceptance/1.2.0: a share reads its classes of data and
finds the sale's acceptance in the history it is given.

Three layers, each against the same rule:

- the check itself, on a ledger written as ``history_ledger`` writes a
  history act (``history_act_payload``): the classes, the deal, and the
  buyer matched on the per-person identity kinds only;
- a live check (``history_ledger`` and ``action_for_check_input``) on
  capsulectl's own records from ``fixtures/live-history/``: buyer A's commit
  and its act are real; the share is A's (or B's) last check turned into a
  share, re-bound to its new body, so its capsule no longer verifies. That
  fixture's openings named a buyer by name and domain only, so its blocks
  seal no per-person kind: the buyer is matched on the name and the domain
  together. Most tests add a profile id fingerprint to the counterparty block
  on both sides, as capsulectl seals one for a buyer whose opening names a
  marketplace profile; the rc14-shaped tests keep the blocks as sealed;
- the same check replayed (``replay`` with a ``sale_history``), which writes
  the history through ``history_ledger`` too, cut to the live window.

1.0.0 and 1.1.0 (no ``binding``) decide as they always did; the last tests
pin that they read none of this.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from functools import cache
from pathlib import Path

import pytest
from agent_action_capsule import json_digest
from cll.ledger import LedgerStore

import capsule_engine
from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.checks import RUNNABLE_CHECKS, check_release_on_acceptance
from capsule_engine.guards.history_state import INCOMPLETE, LIVE_HISTORY, RULES_HISTORY_DAYS
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.report.live_history import history_act_payload, history_ledger
from capsule_engine.report.replay import SaleCheck, action_for_check_input, replay
from capsule_engine.report.sale_bundle import load_sale_bundle, sale_history

ROOT = Path(capsule_engine.__file__).parent
DEFS = ROOT / "guards" / "wickets" / "catalog_defs"
RELEASE = load_definition_file(DEFS / "release_on_acceptance.seller.v1.2.yaml")
RELEASE_1_1 = load_definition_file(DEFS / "release_on_acceptance.seller.v1.1.yaml")
CONFIG = RELEASE.config
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
FIXTURE = Path(__file__).parent / "fixtures" / "live-history"
SALE_FIXTURE = Path(__file__).parent / "fixtures" / "sale-replay"

OPERATOR = "seller"
ITEM = "c" * 64
DEAL_A, DEAL_B = "deal-a", "deal-b"
FP_ALG = "hmac-sha256-chain-key"
NOT_ACCEPTED = "the history given shows no acceptance of this sale by this buyer in this deal"


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# Buyer A's thread-keyed fingerprints as the thread default seals them (the
# opening's kinds), and B's.
IDS_A = {"profile_id": _fp("profile-a"), "name": _fp("Buyer A"), "payee": _fp("Buyer A"), "domain": _fp("market")}
IDS_B = {"profile_id": _fp("profile-b"), "name": _fp("Buyer B"), "payee": _fp("Buyer B"), "domain": _fp("market")}
# A buyer an opening named by name and domain only.
NAME_DOMAIN_A = {"name": IDS_A["name"], "payee": IDS_A["payee"], "domain": IDS_A["domain"]}


# -- the check, on a history written as history_ledger writes it -------------------


def _commit(*, deal: str | None = DEAL_A, ids: dict | None = None, fp_alg: str = FP_ALG, item: str = ITEM) -> Action:
    return Action(verb="commit", operator=OPERATOR, developer="capsulectl", action_class="agreement.accept",
                  amount_minor=185_000, currency="USD", item_ref=item, deal_id=deal, target="payee-fp:x",
                  counterparty_ids=IDS_A if ids is None else ids, counterparty_fp_alg=fp_alg)


def _history(store: LedgerStore, *acts: Action, disposition: str | None = "accept", complete: bool = True) -> None:
    """Each act as ``history_ledger`` writes a history act, then the
    incomplete marker when the history is not complete."""
    for n, act in enumerate(acts):
        record = {"capsule_id": _fp(f"act-{n}"), "operator": OPERATOR, "developer": "capsulectl",
                  "action_type": "fyi", "action_id": f"commit/{n}", "timestamp": f"2026-10-10T02:30:0{n}Z",
                  "asg_payload": history_act_payload(act, sealed_disposition=disposition is not None)}
        if disposition is not None:
            record["disposition"] = {"decision": disposition}
        store.append(record, consequential=False)
    if not complete:
        store.append({"capsule_id": _fp("incomplete"), "operator": OPERATOR, "timestamp": "2026-10-10T02:31:00Z",
                      "asg_payload": {LIVE_HISTORY: INCOMPLETE}}, consequential=False)


def _share(classes: tuple[str, ...] = ("phone",), **overrides) -> Action:
    fields = dict(verb="share_contact", operator=OPERATOR, developer="capsulectl",
                  action_class="disclosure.personal", recipient_role="buyer", disclosed_classes=tuple(sorted(classes)),
                  item_ref=ITEM, deal_id=DEAL_A, counterparty_ids=IDS_A, counterparty_fp_alg=FP_ALG,
                  target="payee-fp:x")
    fields.update(overrides)
    return Action(**fields)


def _check(action: Action, store) -> object:
    return check_release_on_acceptance(action, store, **CONFIG).constraint


def _value_free(outcome) -> None:
    serialised = json.dumps({"reason": outcome.reason, "evidence": outcome.evidence})
    for value in (ITEM, DEAL_A, DEAL_B, *IDS_A.values(), *IDS_B.values(), _fp("act-0")):
        assert value not in serialised, value


def test_the_definition_binds_from_the_history_and_releases_the_contact_classes():
    assert RELEASE.wicket_id == "seller.release_on_acceptance/1.2.0"
    assert CONFIG == {
        "binding": "same_deal_same_buyer",
        "release_classes": ["name", "phone", "email", "home_address", "address", "pickup_location",
                            "other_contact", "pickup_area"],
        "pre_acceptance_classes": ["pickup_area"],
        "acceptance_classes": ["agreement.accept"],
        "action_classes": ["disclosure.personal"],
        "except_recipient_roles": ["platform"],
    }


def test_the_history_carries_the_counterparty_beside_the_act_never_in_it():
    payload = history_act_payload(_commit(), sealed_disposition=True)
    assert payload["thread_counterparty"] == {"fp_alg": FP_ALG, "ids": IDS_A}
    from capsule_engine.guards.capsule import act_payload

    assert "thread_counterparty" not in act_payload(_commit())
    assert "counterparty_ids" not in json.dumps(act_payload(_commit()))


def test_a_share_after_the_buyers_acceptance_in_its_deal_passes(store):
    _history(store, _commit())
    outcome = _check(_share(), store)
    assert (outcome.result, outcome.evidence, outcome.method) == ("pass", {
        "constraint_id": "release_on_acceptance", "disclosed_classes": ["phone"], "bound_to_acceptance": True,
        "missing_field": None}, "same_deal_same_buyer_v1")
    _value_free(outcome)


def test_the_pickup_area_passes_before_any_acceptance(store):
    outcome = _check(_share(("pickup_area",), deal_id=None, counterparty_ids=None, counterparty_fp_alg=None), store)
    assert (outcome.result, outcome.evidence["bound_to_acceptance"]) == ("pass", False)


@pytest.mark.parametrize("classes", [("phone",), ("pickup_area", "phone"), ("address",), ("pickup_location",)])
def test_a_contact_class_before_acceptance_fails(store, classes):
    outcome = _check(_share(classes), store)
    assert (outcome.result, outcome.reason) == ("fail", NOT_ACCEPTED)
    _value_free(outcome)


@pytest.mark.parametrize("cls", ["credential", "verification_code", "payment_card", "id_document"])
def test_a_class_never_released_fails_even_after_acceptance(store, cls):
    _history(store, _commit())
    outcome = _check(_share((cls, "phone")), store)
    assert (outcome.result, outcome.evidence["disclosed_classes"]) == ("fail", sorted([cls, "phone"]))


def test_a_share_naming_no_class_fails(store):
    _history(store, _commit())
    outcome = _check(_share(representation_class="address", disclosed_classes=None), store)
    assert (outcome.result, outcome.evidence["missing_field"]) == ("fail", "disclosed_classes")


@pytest.mark.parametrize(("overrides", "field"), [
    ({"deal_id": None}, "deal_id"),
    ({"deal_id": None, "deal_id_conflict": ("chain_id", "deal_id")}, "deal_id"),
    ({"item_ref": None}, "item_ref"),
    ({"counterparty_ids": None}, "counterparty"),
    ({"counterparty_fp_alg": None}, "counterparty"),
])
def test_a_share_missing_an_input_fails_naming_it(store, overrides, field):
    _history(store, _commit())
    outcome = _check(_share(**overrides), store)
    assert (outcome.result, outcome.evidence["missing_field"]) == ("fail", field)


def test_a_share_in_another_deal_fails(store):
    _history(store, _commit())
    assert _check(_share(deal_id=DEAL_B), store).reason == NOT_ACCEPTED


def test_an_acceptance_with_no_deal_binds_no_share(store):
    _history(store, _commit(deal=None))
    assert _check(_share(), store).result == "fail"


def test_an_acceptance_of_another_item_binds_no_share(store):
    _history(store, _commit(item="d" * 64))
    assert _check(_share(), store).result == "fail"


def test_an_incomplete_history_fails(store):
    _history(store, _commit(), complete=False)
    assert _check(_share(), store).reason == NOT_ACCEPTED


def test_a_commit_act_with_no_disposition_fails(store):
    """An agent_card approval seals no disposition: whether it was accepted
    is not known, which is no acceptance."""
    _history(store, _commit(), disposition=None)
    assert _check(_share(), store).reason == NOT_ACCEPTED


def test_a_declined_commit_is_no_acceptance(store):
    _history(store, _commit(), disposition="reject")
    assert _check(_share(), store).result == "fail"


# The buyer match: per-person identity kinds only.
@pytest.mark.parametrize(("accepted", "shared", "result"), [
    # The same profile id under another name passes.
    (IDS_A, {**IDS_A, "name": _fp("Buyer A."), "payee": _fp("Buyer A.")}, "pass"),
    # A commit naming the buyer explicitly by profile id only; the share uses
    # the thread default.
    ({"profile_id": IDS_A["profile_id"]}, IDS_A, "pass"),
    ({"profile_id": IDS_A["profile_id"], "email": _fp("a@x")}, IDS_A, "pass"),
    # Another buyer in the accepting deal.
    (IDS_A, IDS_B, "fail"),
    # A subset of only the domain, or only the name and payee, is no one.
    ({"domain": IDS_A["domain"]}, IDS_A, "fail"),
    ({"name": IDS_A["name"], "payee": IDS_A["payee"]}, IDS_A, "fail"),
    # No kind in common.
    ({"email": _fp("a@x")}, {"phone": _fp("555")}, "fail"),
    ({"domain": _fp("market"), "email": _fp("a@x")}, {"domain": _fp("market"), "phone": _fp("555")}, "fail"),
    # One per-person kind equal, another on both sides differs.
    ({**IDS_A, "email": _fp("a@x")}, {**IDS_A, "email": _fp("b@x")}, "fail"),
    # Neither side seals a per-person kind: the name and the domain together,
    # the payee never read.
    (NAME_DOMAIN_A, NAME_DOMAIN_A, "pass"),
    (NAME_DOMAIN_A, {**NAME_DOMAIN_A, "payee": _fp("Buyer A.")}, "pass"),
    (NAME_DOMAIN_A, {**NAME_DOMAIN_A, "name": _fp("Buyer B")}, "fail"),
    (NAME_DOMAIN_A, {**NAME_DOMAIN_A, "domain": _fp("elsewhere")}, "fail"),
    ({"name": IDS_A["name"]}, {"name": IDS_A["name"]}, "fail"),
    ({"domain": IDS_A["domain"]}, {"domain": IDS_A["domain"]}, "fail"),
    ({"name": IDS_A["name"], "payee": IDS_A["payee"]}, {"name": IDS_A["name"], "payee": IDS_A["payee"]}, "fail"),
    # A per-person kind on one side only.
    (NAME_DOMAIN_A, {"domain": IDS_A["domain"], "email": _fp("a@x")}, "fail"),
    ({**NAME_DOMAIN_A, "email": _fp("a@x")}, NAME_DOMAIN_A, "fail"),
])
def test_the_buyer_is_one_person_by_the_per_person_kinds(store, accepted, shared, result):
    _history(store, _commit(ids=accepted))
    assert _check(_share(counterparty_ids=shared), store).result == result


def test_both_sides_must_name_one_fingerprint_algorithm(store):
    _history(store, _commit(fp_alg="hmac-sha256-deal-key"))
    assert _check(_share(), store).result == "fail"


def test_a_platform_share_is_left_to_recipient_role(store):
    assert _check(_share(("phone",), recipient_role="platform"), store).result == "n/a"


def test_an_acceptance_with_no_thread_counterparty_binds_no_share(store, signer):
    """A decision the engine sealed itself carries no counterparty block: the
    buyer cannot be told, so nothing is released."""
    from capsule_engine.folds.loader import load_definition_file as load_fold

    spend = load_fold(ROOT / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")
    engine = GuardEngine(ledger=store, caps_fold=spend, signer_provider=lambda: signer)
    engine.check(_commit(), dry_run=False)
    assert _check(_share(), store).reason == NOT_ACCEPTED


# -- 1.0.0 and 1.1.0 read none of it ----------------------------------------------


def test_1_1_0_still_reads_the_statement_class_and_the_citation(store):
    """The same history and share under 1.1.0: no representation_class, so it
    fails as it always did, with its own evidence shape and method."""
    _history(store, _commit())
    outcome = check_release_on_acceptance(_share(), store, **RELEASE_1_1.config).constraint
    assert (outcome.result, outcome.reason, outcome.method) == (
        "fail", "the disclosure declares no class this rule knows; it is not released", "cited_sealed_acceptance_v1")
    assert outcome.evidence == {"constraint_id": "release_on_acceptance", "representation_class": None,
                                "bound_to_acceptance": False, "missing_field": "representation_class"}


def test_an_unknown_binding_is_refused_at_check_time(store):
    with pytest.raises(ValueError):
        check_release_on_acceptance(_share(), store, **{**CONFIG, "binding": "cited"})


# -- a live check on capsulectl's records ------------------------------------------


# Reads the vendored fixture: the test's decoding boundary.
def _check_input(name: str) -> dict:
    return json.loads((FIXTURE / "check-inputs" / f"{name}.json").read_text(encoding="utf-8"))


PROFILE_A = _fp("live-profile-a")
PROFILE_B = _fp("live-profile-b")
# The thread each fixture deal is, by its chain id, and its buyer's profile id.
_PROFILES = {"deal-a4a44a1583055667": PROFILE_A, "deal-c8f7695110ba493e": PROFILE_B}
# The base check whose record becomes the share: A's second commit check, B's commit check.
_BASE = {"a": "a-commit-again-1900", "b": "b-commit-1850"}
# What a share check states no part of: the commit's money and terms.
_NOT_ON_A_SHARE = ("amount_minor", "currency", "spend_minor", "terms", "material_fields_basis",
                   "material_fields_changed", "offer_fields_basis", "offer_fields_changed")


def share_input(thread: str, classes: tuple[str, ...], *, disposition: bool = True, complete: bool = True,
                share_ids: dict | None = None, act_ids: dict | None = None, profile: bool = True) -> dict:
    """A share check input in ``thread`` after buyer A's commit act, from
    capsulectl's own check input: the checked record's body turned into a
    share of ``classes`` and re-bound, a profile id added to every counterparty
    block (``_PROFILES``), and, with ``disposition``, an ``accept`` sealed on
    every history act (capsulectl seals one on an approved commit act).
    ``share_ids`` and ``act_ids`` replace the share's ids and those beside
    A's commit act. Without ``profile`` the blocks stay as rc14 sealed them:
    name, payee and domain."""
    envelope = copy.deepcopy(_check_input(_BASE[thread]))
    record = envelope["record"]
    shown = record["agent_input"]
    body = shown["body"]
    for key in _NOT_ON_A_SHARE:
        body.pop(key, None)
    body.update(action="share_contact", action_class="disclosure.personal", disclosing=list(classes),
                recipient_role="buyer")
    if profile:
        body["counterparty"]["ids"]["profile_id"] = _PROFILES[shown["chain_id"]]
    if share_ids is not None:
        body["counterparty"]["ids"] = share_ids
    record.pop("proposal_at", None)
    record["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(shown)
    for entry in envelope["history"]:
        chain = entry["agent_input"]["chain_id"]
        if profile:
            entry["counterparty"]["ids"]["profile_id"] = _PROFILES[chain]
        if act_ids is not None and entry["agent_input"]["body"]["action_class"] == "agreement.accept":
            entry["counterparty"]["ids"] = act_ids
        if disposition:
            entry["disposition"] = {"decision": "accept"}
    envelope["history_scope"]["complete"] = complete
    return envelope


def _engine(store: LedgerStore, tmp: str) -> GuardEngine:
    installed = install_pack(PACK, project_dir=Path(tmp) / "project", mode="observe")
    resolved = installed.resolved
    wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
    gates, asks = engine_ask_sets(installed.pack, wickets)
    signer = LocalSigner(key_id="release-history", secret=b"release-history-fixture")
    return GuardEngine(ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
                       caps_minor=resolved.caps_minor() or {}, manifest_digest=resolved.manifest_digest,
                       wickets=wickets, ask_gate_selectors=gates, ask_wickets=asks,
                       evaluate_under_record_taxonomy=True)


def _s13(decision):
    (found,) = [c for c in decision.constraints if c.id == "release_on_acceptance"]
    return found


def live(envelope: dict):
    """The decision a live check makes: a fresh engine on the input's history."""
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        engine = _engine(store, tmp)
        history_ledger(envelope, store)
        action = action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref"))
        return engine.check(action, task_authority_record=envelope.get("task_authority_record"))


def replayed(envelope: dict, seen: list | None = None):
    """The same check replayed as a sale's: ``replay`` gives it the sale's
    history through a ``sale_history`` that writes the envelope's, as
    ``sale_checks`` does from a sale bundle. ``seen`` collects each window
    the replay asks for."""
    record = envelope["record"]
    shown = record["agent_input"]
    bare = {k: v for k, v in record.items() if k != "agent_input"}

    def write(capsule_id: str, ledger, days):
        if seen is not None:
            seen.append(days)
        history_ledger({**envelope, "record": bare}, ledger)
        return SaleCheck(item_ref=envelope["item_ref"])

    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "project", mode="observe")
        resolved = installed.resolved
        result = replay([bare], disclosed={bare["capsule_id"]: shown}, caps_fold=resolved.caps_fold(),
                        caps_minor=resolved.caps_minor(), manifest_digest=resolved.manifest_digest,
                        wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack,
                        sale_history=write)
    (decision,) = result.decisions
    return decision.decision


@cache
def _decided(thread: str, classes: tuple[str, ...], disposition: bool = True, complete: bool = True):
    envelope = share_input(thread, classes, disposition=disposition, complete=complete)
    return live(envelope), replayed(envelope)


def _results(pair) -> tuple[str, str]:
    return _s13(pair[0]).result, _s13(pair[1]).result


@pytest.mark.parametrize("cls", ["phone", "email", "name", "address", "home_address", "pickup_location"])
def test_live_a_share_to_the_buyer_who_accepted_passes_and_replays_the_same(cls):
    pair = _decided("a", (cls,))
    assert _results(pair) == ("pass", "pass")
    assert pair[0].outcome == pair[1].outcome == "allow", pair[0].reason
    # The decision seals no counterparty block: it stays in the history.
    sealed = json.dumps(pair[0].capsule)
    assert "thread_counterparty" not in sealed and PROFILE_A not in sealed


def test_live_a_share_to_another_threads_buyer_refuses_but_the_area_passes():
    assert _results(_decided("b", ("phone",))) == ("fail", "fail")
    assert _results(_decided("b", ("pickup_area",))) == ("pass", "pass")
    assert _decided("b", ("phone",))[0].outcome == "deny"


def test_live_the_area_beside_a_contact_class_needs_the_acceptance():
    assert _results(_decided("b", ("phone", "pickup_area"))) == ("fail", "fail")


def test_live_a_commit_act_sealed_with_no_disposition_is_no_acceptance():
    """What the rc14 fixture seals: every act fyi, no disposition."""
    assert _results(_decided("a", ("phone",), disposition=False)) == ("fail", "fail")
    assert _results(_decided("a", ("pickup_area",), disposition=False)) == ("pass", "pass")


def test_live_an_incomplete_history_is_no_acceptance():
    assert _results(_decided("a", ("phone",), complete=False)) == ("fail", "fail")


def test_live_an_explicit_who_at_the_commit_and_the_thread_default_on_the_share_are_one_buyer():
    """The commit's check named the buyer by profile id only; the share's
    check seals the opening's kinds. Through the reconstructed history,
    live and replayed, they are one buyer; a share naming another profile
    in the same deal is not."""
    explicit = {"profile_id": PROFILE_A}
    same = share_input("a", ("phone",), act_ids=explicit)
    assert (_s13(live(same)).result, _s13(replayed(same)).result) == ("pass", "pass")
    other = share_input("a", ("phone",), act_ids=explicit,
                        share_ids={"profile_id": PROFILE_B, "name": _fp("x"), "payee": _fp("x")})
    assert (_s13(live(other)).result, _s13(replayed(other)).result) == ("fail", "fail")


def test_live_a_domain_and_another_kind_on_each_side_is_no_buyer():
    envelope = share_input("a", ("phone",), act_ids={"domain": _fp("d"), "email": _fp("a@x")},
                           share_ids={"domain": _fp("d"), "phone": _fp("555")})
    assert (_s13(live(envelope)).result, _s13(replayed(envelope)).result) == ("fail", "fail")


def _both(envelope: dict) -> tuple[str, str]:
    return _s13(live(envelope)).result, _s13(replayed(envelope)).result


def test_live_an_rc14_thread_named_by_name_and_domain_is_its_own_buyer():
    """The blocks as rc14 sealed them, no per-person kind: a share in A's
    thread after A's commit passes on the name and the domain; B's thread
    refuses, by deal and by name."""
    envelope = share_input("a", ("phone",), profile=False)
    assert _both(envelope) == ("pass", "pass")
    assert live(envelope).outcome == "allow"
    assert _both(share_input("b", ("phone",), profile=False)) == ("fail", "fail")


def test_live_an_rc14_share_in_the_same_deal_under_another_name_refuses():
    sealed = _check_input(_BASE["a"])["record"]["agent_input"]["body"]["counterparty"]["ids"]
    envelope = share_input("a", ("phone",), profile=False, share_ids={**sealed, "name": _fp("someone else")})
    assert _both(envelope) == ("fail", "fail")


def test_live_a_per_person_kind_on_one_side_only_refuses():
    sealed = _check_input(_BASE["a"])["record"]["agent_input"]["body"]["counterparty"]["ids"]
    envelope = share_input("a", ("phone",), profile=False,
                           share_ids={"domain": sealed["domain"], "email": _fp("a@x")})
    assert _both(envelope) == ("fail", "fail")


def test_replay_gives_s13_the_live_window_and_s11_the_whole_history():
    seen: list = []
    replayed(share_input("a", ("phone",)), seen)
    assert seen == [None, RULES_HISTORY_DAYS]


# -- the replayed sale history is cut to the live window ------------------------------


def test_a_sale_history_cut_to_a_window_leaves_older_acts_out_and_stays_complete():
    sale = load_sale_bundle(SALE_FIXTURE / "sale.bundle.json")
    checked = json.loads((SALE_FIXTURE / "check-inputs" / "a-commit-again-1900.json").read_text())["record"]
    whole = sale_history(sale, checked["capsule_id"])
    window = sale_history(sale, checked["capsule_id"], days=RULES_HISTORY_DAYS)
    assert whole == window and whole["history"]
    none = sale_history(sale, checked["capsule_id"], days=0)
    at = checked["timestamp"]
    assert [e for e in whole["history"] if e["timestamp"] >= at] == none["history"]
    assert len(none["history"]) < len(whole["history"])
    assert none["history_scope"] == whole["history_scope"]
