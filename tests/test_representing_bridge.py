# SPDX-License-Identifier: Apache-2.0
"""s04 and s10 decide a capsulectl represent check (capsule-engine#180, #189).

capsulectl seals what the seller's agent is about to tell the buyer as
``body.representing``: the statement's class in the clear, the words by a
salted commitment (``text_commitment``), and, for the user's own authenticity
claim, ``attributed_to: "user"``. The bridge carries the class as
``Action.representation_class`` (it dropped it, so both rules read a missing
class as not applicable and passed every statement), the attribution as
``statement_attributed_to`` and the commitment as
``representation_text_commitment``. Live, capsulectl passes the words beside
the check (``record.representing_opening``) and the task-authority record
(``task_authority_record``); a replay reads the task-authority record the
bundle discloses under the digest the check cites.

The records here are synthetic, in capsulectl's typed shape
(``proposed-action/v0``), each bound to its capsule by digest.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from agent_action_capsule import json_digest
from cll.ledger import LedgerStore

import capsule_engine
from capsule_engine.guards import Action, GuardDecision, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE, not_applicable_evidence
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.checks.commitment import text_commitment
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.engine import NOT_EVALUABLE
from capsule_engine.guards.wickets import Catalog
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import (
    MALFORMED_REPRESENTATION_CLASS,
    action_for_check_input,
    action_for_record,
    load_representing_openings,
    replay,
    representing_openings,
)
from capsule_engine.report.sale_bundle import SaleBundle, SaleThread, load_sale_bundle, replay_sale

ROOT = Path(capsule_engine.__file__).parent
PACK = load_pack_dir(ROOT / "packs" / "catalog" / "seller")
CATALOG = Catalog(ROOT / "guards" / "wickets" / "catalog_defs")
PACK_0_1_4 = load_pack_dir(Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.4")
SIGNER = LocalSigner(key_id="representing-bridge", secret=b"representing-bridge-fixture")

DEAL = "deal-5eca1b0e5eca1b0e"
AT = "2026-10-22T09:00:00Z"
BUYER = {"fp_alg": "hmac-sha256-chain-key", "ids": {"payee": "b" * 64}}
NONCE = "7" * 64
IN_TEMPLATE = "The seller says it's authentic (unverified)"
S04, S10 = "s04-statement-that-needs-the-users-word", "s10-statement-never-made"


def _task_authority(ordered: list[str]) -> dict:
    """capsulectl's task authority of a sale, with the classes the user
    ordered the agent to state."""
    return {"type": "task-authority/v0", "canonicalization": "jcs", "chain_id": DEAL, "seq": 1, "at": AT,
            "body": {"outcome_id": "seller.sell_one_item/1.0.0", "allowed_actions": ["offer", "commit"],
                     "preconditions": [], "ordered_representations": ordered}}


ORDERED = _task_authority(["warranty"])
UNORDERED = _task_authority([])


def _ref(record: dict) -> dict:
    return {"type": "record", "digest_alg": "SHA-256", "digest": json_digest(record)}


def _represent(seq: int, cls: str, text: str, *, authority: dict | None = ORDERED,
               attributed_to: object = None, **representing) -> dict:
    """A typed represent check of ``text`` in class ``cls``, citing
    ``authority`` (none when ``None``)."""
    rep = {"class": cls, "text_commitment": text_commitment(NONCE, text), **representing}
    if attributed_to is not None:
        rep["attributed_to"] = attributed_to
    body = {"action": "represent", "action_class": "communication.send", "taxonomy_version": TAXONOMY_VERSION,
            "counterparty": BUYER, "representing": rep}
    if authority is not None:
        body["task_authority_ref"] = _ref(authority)
    return {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": DEAL, "seq": seq, "at": AT,
            "body": body}


def _capsule(record: dict, seq: int) -> dict:
    return {"capsule_id": f"{seq:064x}", "action_id": f"{DEAL}/{seq}", "action_type": "decide",
            "operator": "seller-a", "developer": "capsulectl-deal", "timestamp": AT,
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}


def _entry(record: dict, seq: int, text: str | None = None) -> dict:
    """The external-check-input/v0 ``record`` entry of a check, with its words
    opened beside it when ``text`` is given."""
    entry = {**_capsule(record, seq), "agent_input": record}
    if text is not None:
        entry["representing_opening"] = {"nonce": NONCE, "text": text}
    return entry


def _engine(store: LedgerStore, tmp: str, pack=PACK) -> GuardEngine:
    resolved = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe").resolved
    wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
    gates, asks = engine_ask_sets(pack, wickets)
    return GuardEngine(ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: SIGNER,
                       caps_minor=resolved.caps_minor() or {}, manifest_digest=resolved.manifest_digest,
                       wickets=wickets, ask_gate_selectors=gates, ask_wickets=asks,
                       evaluate_under_record_taxonomy=True)


def _live(entry: dict, authority: dict | None = ORDERED, pack=PACK) -> GuardDecision:
    """A live check of ``entry`` as a rules checker decides it, with the
    task-authority record capsulectl passes beside it."""
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        return _engine(store, tmp, pack).check(
            action_for_check_input(entry), task_authority_record=authority,
            representing_opening=entry.get("representing_opening"))


def _rules(decision: GuardDecision, pack=PACK) -> dict[str, str]:
    return {r.obligation_id: r.result for r in obligation_results(pack, decision.constraints)}


def _constraint(decision: GuardDecision, check_id: str):
    (found,) = [c for c in decision.constraints if c.id == check_id]
    return found


# -- the bridge ------------------------------------------------------------------


def test_the_bridge_carries_the_class_the_attribution_and_the_commitment():
    record = _represent(2, "authenticity", IN_TEMPLATE, attributed_to="user")
    action = action_for_record(_capsule(record, 2), record)
    assert action.representation_class == "authenticity"
    assert action.statement_attributed_to == "user"
    assert action.representation_text_commitment == text_commitment(NONCE, IN_TEMPLATE)
    assert action.task_authority_ref == json_digest(ORDERED)


@pytest.mark.parametrize("attributed_to", ["agent", "User", "", ["user"]])
def test_an_attribution_outside_its_closed_set_is_the_agents_own(attributed_to):
    record = _represent(2, "authenticity", IN_TEMPLATE, attributed_to=attributed_to)
    assert action_for_record(_capsule(record, 2), record).statement_attributed_to is None


def test_a_commitment_out_of_shape_is_not_carried():
    record = _represent(2, "authenticity", IN_TEMPLATE, attributed_to="user")
    record["body"]["representing"]["text_commitment"] = "A" * 64
    assert action_for_record(_capsule(record, 2), record).representation_text_commitment is None


def test_a_check_with_no_representing_carries_no_statement():
    """An action other than represent with no representing is no statement."""
    record = _represent(2, "warranty", "Covered for a year")
    del record["body"]["representing"]
    record["body"]["action"] = "offer"
    action = action_for_record(_capsule(record, 2), record)
    assert (action.representation_class, action.statement_attributed_to,
            action.representation_text_commitment) == (None, None, None)


_MALFORMED_CLASSES = [None, [], ["authenticity"], {}, 17, False, ""]


@pytest.mark.parametrize("representing", [
    *({"class": c, "text_commitment": text_commitment(NONCE, IN_TEMPLATE)} for c in _MALFORMED_CLASSES),
    {"text_commitment": text_commitment(NONCE, IN_TEMPLATE)}, [], "authenticity", None, 17, "absent",
], ids=lambda r: repr(r)[:40])
def test_a_represent_check_whose_class_cannot_be_read_fails_s04_and_s10(representing):
    """Never n/a: a represent check is a statement, so one whose class is
    missing or out of shape fails both rules as not recognised."""
    record = _represent(2, "authenticity", IN_TEMPLATE, attributed_to="user")
    if representing == "absent":
        del record["body"]["representing"]
    else:
        record["body"]["representing"] = representing
    action = action_for_check_input(_entry(record, 2))
    assert action.representation_class == MALFORMED_REPRESENTATION_CLASS
    decision = _live(_entry(record, 2, IN_TEMPLATE))
    assert decision.outcome == DENY
    rules = _rules(decision)
    assert (rules[S04], rules[S10]) == ("fail", "fail")
    assert not _constraint(decision, "promise_never").evidence["recognised"]


def test_a_capsulectl_check_built_without_the_attribution_seals_none():
    """A decision seals the attribution when there is one, and never the
    commitment to the words."""
    record = _represent(2, "warranty", "Covered for a year")
    payload = _live(_entry(record, 2)).capsule["asg_payload"]
    assert payload["representation_class"] == "warranty"
    assert "statement_attributed_to" not in payload and "representation_text_commitment" not in payload


# -- live: s04 and s10 decide ------------------------------------------------------


def test_an_unordered_warranty_asks_on_s04():
    decision = _live(_entry(_represent(2, "warranty", "Covered for a year", authority=UNORDERED), 2),
                     authority=UNORDERED)
    assert decision.outcome == ESCALATE
    assert _rules(decision)[S04] == "fail"


def test_an_ordered_warranty_passes_live():
    decision = _live(_entry(_represent(2, "warranty", "Covered for a year"), 2))
    assert decision.outcome == ALLOW
    assert _rules(decision)[S04] == "pass"


@pytest.mark.parametrize("authority", [None, UNORDERED])
def test_a_missing_or_mismatched_task_authority_asks(authority):
    decision = _live(_entry(_represent(2, "warranty", "Covered for a year"), 2), authority=authority)
    assert decision.outcome == ESCALATE
    assert _rules(decision)[S04] == "fail"


@pytest.mark.parametrize("ordered", [["warranty", 17], ["warranty", {}], ["warranty", None], ["warranty", "mood"],
                                     "warranty", {"warranty": True}])
def test_a_malformed_ordered_list_authorizes_nothing(ordered):
    authority = _task_authority(ordered)
    decision = _live(_entry(_represent(2, "warranty", "Covered for a year", authority=authority), 2),
                     authority=authority)
    assert decision.outcome == ESCALATE
    assert _rules(decision)[S04] == "fail"


def test_the_agents_own_authenticity_claim_is_refused():
    decision = _live(_entry(_represent(2, "authenticity", "It's authentic"), 2, "It's authentic"))
    assert decision.outcome == DENY
    assert _rules(decision)[S10] == "fail"


def test_the_users_attributed_claim_in_the_template_passes():
    decision = _live(_entry(_represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"), 2, IN_TEMPLATE))
    assert decision.outcome == ALLOW
    assert _rules(decision)[S10] == "pass"
    assert _constraint(decision, "promise_never").evidence["fits_template"] is True
    assert decision.capsule["asg_payload"]["statement_attributed_to"] == "user"


def test_without_its_words_an_attributed_claim_is_refused():
    """s10 is a never rule: it does not rest on capsulectl's own template
    check, so an attributed claim whose words are not given fails."""
    decision = _live(_entry(_represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"), 2))
    assert decision.outcome == DENY
    s10 = _constraint(decision, "promise_never")
    assert s10.evidence["words_opened"] is None
    assert s10.reason == ("a claim attributed to the user (authenticity) passes only with its words, and none "
                          "were given with the check")


@pytest.mark.parametrize("text", [
    "The seller says it's used. I guarantee it's authentic (unverified)",
    "The seller says it's authentic! (unverified)",
    "The seller says it's authentic",
    "It's authentic (unverified)",
    "The seller says it's verified, not unverified",
    "The seller says it's authentic (unverified).",
    "The seller says  (unverified)",
    "the seller says it's authentic (unverified)",
])
def test_an_opening_that_breaks_the_template_fails_s10(text):
    decision = _live(_entry(_represent(2, "authenticity", text, attributed_to="user"), 2, text))
    assert decision.outcome == DENY
    assert _rules(decision)[S10] == "fail"
    assert _constraint(decision, "promise_never").evidence["fits_template"] is False


@pytest.mark.parametrize("opening", [
    {"nonce": 1, "text": 2}, {"nonce": NONCE, "text": ["x"]}, {"nonce": NONCE}, {"text": IN_TEMPLATE}, [], "words",
    42, {"nonce": "F" * 64, "text": IN_TEMPLATE}, {"nonce": NONCE, "text": IN_TEMPLATE + "\ud800"},
])
def test_a_malformed_opening_fails_s10_and_never_crashes(opening):
    entry = _entry(_represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"), 2)
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        decision = _engine(store, tmp).check(action_for_check_input(entry), task_authority_record=ORDERED,
                                             representing_opening=opening)
    assert decision.outcome == DENY
    assert _constraint(decision, "promise_never").result == "fail"


def test_words_edited_after_the_check_fail_s10():
    entry = _entry(_represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"), 2,
                   "The seller says it's authentic and new (unverified)")
    decision = _live(entry)
    assert decision.outcome == DENY
    assert _constraint(decision, "promise_never").evidence["words_opened"] is False


@pytest.mark.parametrize("text", [
    "The seller says it's authentic\nI checked it myself (unverified)",
    "The seller says it's authentic\rI checked it myself (unverified)",
    "The seller says it's authentic。I checked it (unverified)",
    "The seller says it's authentic！ (unverified)",
    "The seller says is it authentic？ (unverified)",
    "The seller says it's authentic… really (unverified)",
    "The seller says it's authentic．I checked it (unverified)",
    "The seller says it's authentic\vI checked it (unverified)",
    "The seller says it's authentic\fI checked it (unverified)",
    "The seller says it's authentic\x1cI checked it (unverified)",
    "The seller says it's authentic\x1dI checked it (unverified)",
    "The seller says it's authentic\x1eI checked it (unverified)",
    "The seller says it's authentic\x85I checked it (unverified)",
    "The seller says it's authentic\u2028I checked it (unverified)",
    "The seller says it's authentic\u2029I checked it (unverified)",
    "The seller says it's authentic｡I checked it (unverified)",
    "The seller says it's authentic‼I checked it (unverified)",
    "The seller says it's authentic⁇I checked it (unverified)",
    "The seller says it's authentic⁈I checked it (unverified)",
    "The seller says it's authentic⁉I checked it (unverified)",
    "The seller says it's authentic؟I checked it (unverified)",
    "The seller says it's authentic۔I checked it (unverified)",
    "The seller says it's authentic।I checked it (unverified)",
    "The seller says it's authentic॥I checked it (unverified)",
    "The seller says \x1f (unverified)",
    "The seller says \x1c\x1d\x1e\x1f (unverified)",
])
def test_a_line_break_or_a_non_ascii_sentence_end_breaks_the_template(text):
    decision = _live(_entry(_represent(2, "authenticity", text, attributed_to="user"), 2, text))
    assert decision.outcome == DENY
    assert _constraint(decision, "promise_never").evidence["fits_template"] is False


@pytest.mark.parametrize("commitment", ["absent", "A" * 64, 17, None, ""])
def test_an_attributed_check_sealing_no_commitment_fails_live_and_on_replay(commitment):
    """A malformed record is not missing words: s10 fails, before the
    replay's not-in-bundle case."""
    record = _represent(2, "authenticity", IN_TEMPLATE, attributed_to="user")
    if commitment == "absent":
        del record["body"]["representing"]["text_commitment"]
    else:
        record["body"]["representing"]["text_commitment"] = commitment
    live = _live(_entry(record, 2, IN_TEMPLATE))
    (replayed,) = _replayed(ORDERED, record)
    for decision in (live, replayed):
        s10 = _constraint(decision, "promise_never")
        assert (s10.result, s10.reason) == ("fail", "the check seals no commitment to the words of this attributed "
                                                    "claim")
        assert decision.outcome == DENY and decision.verdict == DENY


def test_the_engine_commits_as_capsulectl_does():
    """capsulectl's own CommitText vectors (fixtures/commitment/)."""
    vectors = json.loads((Path(__file__).parent / "fixtures" / "commitment" / "capsulectl-represent-vectors.json")
                         .read_text(encoding="utf-8"))["vectors"]
    assert vectors
    for v in vectors:
        assert text_commitment(v["nonce"], v["text"]) == v["commitment"], v["text"]


@pytest.mark.parametrize("value", ["agent", "User", "", "bob"])
def test_an_action_keeps_only_the_closed_attribution(value):
    assert Action(verb="tell", operator="o", developer="d", statement_attributed_to=value).statement_attributed_to is None
    capsule = {"action_id": "tell/1", "asg_payload": {"statement_attributed_to": value}}
    assert Action.from_capsule(capsule).statement_attributed_to is None
    kept = Action(verb="tell", operator="o", developer="d", statement_attributed_to="user")
    assert kept.statement_attributed_to == "user"


def test_words_that_do_not_open_the_checked_commitment_fail_s10():
    entry = _entry(_represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"), 2, IN_TEMPLATE)
    entry["representing_opening"]["nonce"] = "6" * 64
    decision = _live(entry)
    assert decision.outcome == DENY
    assert _constraint(decision, "promise_never").evidence["words_opened"] is False


def test_an_ordered_authenticity_claim_not_attributed_still_refuses():
    ordered = _task_authority(["authenticity"])
    decision = _live(_entry(_represent(2, "authenticity", "It's authentic", authority=ordered), 2, "It's authentic"),
                     authority=ordered)
    assert decision.outcome == DENY
    assert _rules(decision)[S10] == "fail"


def test_an_x_deal_v0_warranty_with_no_task_authority_asks_saying_why():
    """An x-deal-v0 deal seals no task-authority record: only a sealed
    approval clears s04 there, and the reason says nothing of the user's covers it."""
    record = _represent(2, "warranty", "Covered for a year", authority=None)
    decision = _live(_entry(record, 2), authority=None)
    assert decision.outcome == ESCALATE
    assert _constraint(decision, "promise_requires_approval").reason == "no task authority order or approval of yours covers this promise"


def test_under_seller_0_1_4_the_bridge_makes_s10_refuse_the_users_claim():
    """What 0.1.4's s10 always said: the pass was the bug (design note §2)."""
    entry = _entry(_represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"), 2, IN_TEMPLATE)
    decision = _live(entry, pack=PACK_0_1_4)
    assert decision.outcome == DENY
    assert _rules(decision, PACK_0_1_4)[S10] == "fail"


# -- replay: the bundle's task authority, bound by digest ----------------------------


def _replayed(*records: dict, openings: dict | None = None) -> list[GuardDecision]:
    capsules = [_capsule(r, n) for n, r in enumerate(records, start=1)]
    with tempfile.TemporaryDirectory() as tmp:
        resolved = install_pack(PACK, project_dir=Path(tmp) / "project", mode="observe").resolved
        result = replay(capsules, caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                        manifest_digest=resolved.manifest_digest,
                        disclosed={c["capsule_id"]: r for c, r in zip(capsules, records, strict=True)},
                        wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=PACK,
                        representing_openings=openings)
    return [s.decision for s in result.decisions]


def test_an_ordered_warranty_passes_on_replay():
    (decision,) = _replayed(ORDERED, _represent(2, "warranty", "Covered for a year"))
    assert decision.outcome == ALLOW
    assert _rules(decision)[S04] == "pass"


def test_on_replay_a_task_authority_other_than_the_cited_one_leaves_s04_asking():
    """The bundle discloses a task authority, but not the one the check cites."""
    (decision,) = _replayed(UNORDERED, _represent(2, "warranty", "Covered for a year"))
    assert decision.outcome == ESCALATE
    assert _rules(decision)[S04] == "fail"


def test_on_replay_a_bundle_with_no_task_authority_leaves_s04_asking():
    (decision,) = _replayed(_represent(2, "warranty", "Covered for a year"))
    assert decision.outcome == ESCALATE
    assert _rules(decision)[S04] == "fail"


# -- replay: the words, from the deal report's openings -------------------------------


def _report(*entries: object) -> dict:
    return {"type": "deal_report", "representations": list(entries)}


def _said(text: str, nonce: str = NONCE) -> dict:
    return {"step": "c" * 64, "nonce": nonce, "text": text}


def _bundle(form: str, report: dict) -> dict:
    """A bundle carrying ``report`` in one of capsulectl's three forms."""
    if form == "inline":
        return {"records": [], "disclosures": {}, "extensions": {"x-deal-v0": report}}
    if form == "standalone":
        return {"records": [], "disclosures": {}, "extensions": {
            "x-deal-v0": {"sealed_report": "d" * 64},
            "x-deal-report/v0": {"record": {"capsule_id": "d" * 64}, "agent_input": {"report": report}}}}
    return {"records": [], "disclosures": {"d" * 64: {"agent_input": {"report": report}}},
            "extensions": {"x-deal-v0": {"sealed_report": "d" * 64}}}


@pytest.mark.parametrize("form", ["inline", "standalone", "in_log"])
def test_on_replay_an_approved_attributed_claim_passes_on_the_reports_opening(form):
    openings = representing_openings(_bundle(form, _report(_said(IN_TEMPLATE))))
    assert openings == {text_commitment(NONCE, IN_TEMPLATE): {"nonce": NONCE, "text": IN_TEMPLATE}}
    (decision,) = _replayed(ORDERED, _represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"),
                            openings=openings)
    assert decision.outcome == ALLOW
    assert _constraint(decision, "promise_never").evidence["fits_template"] is True


def test_on_replay_the_reports_opening_off_the_template_fails_as_live():
    words = "The seller says it's used. I guarantee it's authentic (unverified)"
    (decision,) = _replayed(ORDERED, _represent(2, "authenticity", words, attributed_to="user"),
                            openings=representing_openings(_bundle("inline", _report(_said(words)))))
    assert _constraint(decision, "promise_never").result == "fail"


@pytest.mark.parametrize("report", [
    _report(),  # held: the claim was never made, so nothing is opened
    _report({"step": "c" * 64, "text": "(withheld)"}),  # a counterparty copy, scrubbed
    _report(_said("The seller says it's authentic and new (unverified)")),  # altered text
    _report(_said(IN_TEMPLATE, "6" * 64)),  # altered nonce
], ids=["held", "scrubbed", "altered-text", "altered-nonce"])
def test_on_replay_without_matching_words_an_attributed_claim_is_not_evaluable(report):
    (decision,) = _replayed(ORDERED, _represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"),
                            openings=representing_openings(_bundle("inline", report)))
    assert _constraint(decision, "promise_never").result == "n/a"
    assert decision.verdict == NOT_EVALUABLE


@pytest.mark.parametrize("entry", [
    {"nonce": "A" * 64, "text": IN_TEMPLATE}, {"nonce": NONCE, "text": 17},
    {"nonce": NONCE, "text": IN_TEMPLATE + "\ud800"}, {"nonce": 7, "text": IN_TEMPLATE}, "entry", None, [],
])
def test_a_malformed_report_opening_is_skipped(entry):
    assert representing_openings(_bundle("inline", _report(entry))) == {}


@pytest.mark.parametrize("bundle", [None, [], {}, {"extensions": []}, {"extensions": {"x-deal-v0": []}},
                                    {"extensions": {"x-deal-v0": {"representations": "x"}}},
                                    {"extensions": {"x-deal-v0": {"sealed_report": 3}}, "disclosures": []},
                                    {"extensions": {"x-deal-report/v0": {"agent_input": {"report": []}}}}])
def test_a_malformed_bundle_yields_no_openings(bundle):
    assert representing_openings(bundle) == {}


def test_openings_are_loaded_from_bundle_files(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(_bundle("in_log", _report(_said(IN_TEMPLATE)))), encoding="utf-8")
    assert load_representing_openings([path]) == {text_commitment(NONCE, IN_TEMPLATE): {"nonce": NONCE, "text": IN_TEMPLATE}}


@pytest.mark.parametrize("words", [IN_TEMPLATE, "The seller says it's used. I guarantee it's authentic (unverified)"])
def test_on_replay_an_attributed_claim_is_not_evaluable_never_failed(words):
    """With no opening of its words in the bundle, a replay can confirm an
    attributed claim neither way: s10 is not applicable, naming the missing
    words, and fails closed (refused, verdict not_evaluable). It is never
    recorded as the agent breaking the rule over a claim the user made."""
    (decision,) = _replayed(ORDERED, _represent(2, "authenticity", words, attributed_to="user"))
    s10 = _constraint(decision, "promise_never")
    assert (s10.result, s10.reason) == ("n/a", "the words for this attributed claim are not in the bundle")
    assert s10.evidence == not_applicable_evidence("promise_never", in_scope=True, missing_field="representing_opening")
    assert decision.outcome == DENY and decision.verdict == NOT_EVALUABLE
    assert decision.not_evaluated_checks == ("promise_never",)
    assert _rules(decision)[S10] == "n/a"


def test_on_replay_the_agents_own_authenticity_claim_still_fails():
    ordered = _task_authority(["authenticity"])
    (decision,) = _replayed(ordered, _represent(2, "authenticity", "It's authentic", authority=ordered))
    assert _constraint(decision, "promise_never").result == "fail"
    assert decision.verdict == DENY


def test_a_replay_reads_the_record_only_once_its_digest_matches():
    """A record altered after the check cited it is under another digest, so
    it is never bound: the check asks."""
    altered = {**ORDERED, "body": {**ORDERED["body"], "ordered_representations": ["warranty", "delivery_date"]}}
    (decision,) = _replayed(altered, _represent(2, "warranty", "Covered for a year"))
    assert decision.outcome == ESCALATE


def test_a_real_capsulectl_report_opening_recomputes_its_sealed_claim():
    """A seller bundle capsulectl wrote (fixtures/real-seller/): each opening
    its deal report carries recomputes the text_commitment of a claim the
    bundle seals, so the reader and the commitment agree with capsulectl."""
    path = Path(__file__).parent / "fixtures" / "real-seller" / "buyer-a.bundle.json"
    openings = load_representing_openings([path])
    sealed = {d["agent_input"]["body"].get("text_commitment")
              for d in json.loads(path.read_text(encoding="utf-8"))["disclosures"].values()
              if isinstance(d.get("agent_input"), dict) and isinstance(d["agent_input"].get("body"), dict)}
    assert openings and set(openings) <= sealed


# -- a sale replay forwards each thread's openings -------------------------------------

PRODUCED_SALE = Path(__file__).parent / "fixtures" / "release-on-acceptance" / "sale.bundle.json"


def test_a_sale_bundles_standalone_report_openings_reach_its_thread():
    """capsulectl's sale bundle, whose thread copies seal their reports
    standalone (x-deal-report/v0): thread A's report opens the condition
    claim A's agent made, and each opening recomputes a claim its thread
    seals."""
    sale = load_sale_bundle(PRODUCED_SALE)
    assert sale.complete
    assert [len(t.openings) for t in sale.threads] == [1, 0]
    for thread in sale.threads:
        sealed = {shown["body"].get("text_commitment") for shown in thread.disclosed.values()
                  if isinstance(shown.get("body"), dict)}
        assert set(thread.openings) <= sealed


def test_replay_sale_decides_an_attributed_claim_on_its_threads_opening():
    records = (ORDERED, _represent(2, "authenticity", IN_TEMPLATE, attributed_to="user"))
    capsules = tuple(_capsule(r, n) for n, r in enumerate(records, start=1))
    disclosed = {c["capsule_id"]: r for c, r in zip(capsules, records, strict=True)}

    def decided(openings: dict) -> GuardDecision:
        sale = SaleBundle(key=None, certified_until=None, findings=(), threads=(
            SaleThread(thread_id=DEAL, records=capsules, disclosed=disclosed, openings=openings),))
        with tempfile.TemporaryDirectory() as tmp:
            resolved = install_pack(PACK, project_dir=Path(tmp) / "project", mode="observe").resolved
            (sourced,) = replay_sale(sale, caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                                     manifest_digest=resolved.manifest_digest,
                                     wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=PACK).decisions
        return sourced.decision

    opened = decided(representing_openings(_bundle("standalone", _report(_said(IN_TEMPLATE)))))
    assert opened.outcome == ALLOW
    assert _constraint(opened, "promise_never").evidence["fits_template"] is True
    withheld = decided({})
    assert (_constraint(withheld, "promise_never").result, withheld.verdict) == ("n/a", NOT_EVALUABLE)


# -- the sentence marks: one frozen list, as capsulectl's ------------------------------

MARKS_FIXTURE = Path(__file__).parent / "fixtures" / "sentence-marks.txt"
# The same bytes as capsule-cli's plugins/internal/plugin/testdata/sentence-marks.txt.
MARKS_SHA256 = "8869f15bbf6896160f67e2d6f19a0da449a3f33d1e12d4cb71efb77c7877b9aa"


def _fixture_marks() -> list[str]:
    return [chr(int(line[2:], 16)) for line in MARKS_FIXTURE.read_text(encoding="utf-8").splitlines()
            if line.startswith("U+")]


def test_the_sentence_marks_are_the_shared_fixture_code_point_for_code_point():
    assert hashlib.sha256(MARKS_FIXTURE.read_bytes()).hexdigest() == MARKS_SHA256
    marks = CATALOG.get("promise_never/1.1.0").definition.config["attributed_template"]["sentence_marks"]
    assert [f"{ord(m):04X}" for m in marks] == [f"{ord(m):04X}" for m in _fixture_marks()]
    assert len(marks) == len(set(marks)) == 181


@pytest.mark.parametrize("mark", ["﹒", "﹖", "﹗", "։", "።", "။", "។", "܂",
                                  "᠃", "⸮", "꓿", "߹", "\U00011047", "\U0001da88"])
def test_a_sentence_terminal_in_any_script_breaks_the_template(mark):
    text = f"The seller says it's authentic{mark}I checked it (unverified)"
    decision = _live(_entry(_represent(2, "authenticity", text, attributed_to="user"), 2, text))
    assert _constraint(decision, "promise_never").evidence["fits_template"] is False
