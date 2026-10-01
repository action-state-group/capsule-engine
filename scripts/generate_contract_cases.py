# SPDX-License-Identifier: Apache-2.0
"""Generate the Evidence Contract edge-case library in
``tests/fixtures/contract-cases/``.

Every case is declared here by intent: the input, the outcome it must have,
and a one-line rationale. Expected outcomes are written by hand, never
computed by the code under test -- the one exception is a ``pin`` case's
digest, which exists so a second implementation (capsulectl) can prove it
computes the same bytes. ``tests/test_contract_cases.py`` runs every case and
fails if this script's output differs from the committed files.

Three kinds of case:

* ``validate`` -- one contract; ``expect`` is ``{"valid": true}`` or
  ``{"valid": false, "error": {"path", "keyword"}}``: the violation that must
  be among the validator's issues.
* ``diff`` -- contracts ``a`` and ``b``; ``expect`` is ``{"breaking", "changes":
  [{"path", "kind"}]}`` (the exact change list) or ``{"error": <code>}`` for
  inputs that cannot be diffed.
* ``pin`` -- one contract; ``expect`` is the reference and digest a Result
  names it by.

Usage: python scripts/generate_contract_cases.py [OUT_DIR]
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "contract-cases"

CASES: list[dict[str, Any]] = []


def _case(case_id: str, kind: str, rationale: str, expect: dict[str, Any], **inputs: Any) -> None:
    assert not any(c["id"] == case_id for c in CASES), case_id
    CASES.append({"id": case_id, "kind": kind, "rationale": rationale, **inputs, "expect": expect})


# -- base contracts (synthetic) ----------------------------------------------

BASE: dict[str, Any] = {
    "id": "ec:example-fixture:2026-09-30",
    "version": "1",
    "subject": {"job_type": "example-job", "population_selector": "every job in evaluation_period"},
    "requirements": [
        {
            "id": "req-a",
            "profile": "outcome",
            "statement": "the record was written before the action was dispatched",
            "evidence_requirements": {
                "accepted_epistemic_types": ["OBSERVED_EVENT", "SYSTEM_OF_RECORD_FACT"],
                "required_sources": ["source-one"],
                "minimum_assurance": ["witnessed"],
                "freshness": "P7D",
            },
            "adjudication": {"mode": "deterministic"},
            "uncertainty": {"on_missing": "insufficient", "on_conflict": "contradicted"},
        },
        {
            "id": "req-b",
            "profile": "process",
            "statement": "a review step preceded the change",
            "required_sequence": ["requested", "approved", "applied"],
            "approvals": ["one reviewer who is not the author"],
            "escalation_path": "queue-one",
        },
        {
            "id": "req-c",
            "profile": "obligation",
            "statement": "the example policy's review control was evidenced",
            "clause_ref": "example-policy/section-1/v1",
            "evidence_requirements": {"accepted_epistemic_types": ["OBSERVED_EVENT"]},
        },
    ],
    "temporal": {"evaluation_period": "2026-09"},
    "consequence": {"class": "report", "rule_ref": "example-report-v1"},
}

NATIVE: dict[str, Any] = {
    "id": "ec:example-native:2026-09-30",
    "version": "1",
    "requirements": [
        {
            "id": "req-n",
            "profile": "outcome",
            "statement": "the refund was confirmed by the system of record",
            "evidence_rule": "refund record chained to the request, status confirmed",
            "forward_verdict": "DETERMINISTIC",
            "backward_verdict": "DETERMINISTIC",
            "window": {"duration": "P7D", "cure": None, "grace": None},
            "tier": "informational",
            "required_assurance_grade": "witnessed",
            "mode": "structural",
        },
        {
            "id": "req-o",
            "profile": "obligation",
            "statement": "the retention control was evidenced",
            "evidence_rule": "retention record chained to the clause",
            "forward_verdict": "DETERMINISTIC",
            "backward_verdict": "DETERMINISTIC",
            "clause": {"instrument": "Example Instrument", "article": "Article 1", "source_url": "https://example.org/a1"},
        },
    ],
}


def _req(doc: dict[str, Any], rid: str) -> dict[str, Any]:
    return next(r for r in doc["requirements"] if r["id"] == rid)


def mutate(base: dict[str, Any], fn: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    doc = copy.deepcopy(base)
    fn(doc)
    return doc


def minimal(*reqs: dict[str, Any]) -> dict[str, Any]:
    return {"id": "ec:example-minimal:2026-09-30", "version": "1", "requirements": list(reqs)}


def ch(path: str, kind: str) -> dict[str, str]:
    return {"path": path, "kind": kind}


# -- validate: positive edges ------------------------------------------------

OK = {"valid": True}
ATTR = {"id": "r1", "profile": "attribution", "ref": "example-attribution-v1"}


def ok(case_id: str, rationale: str, contract: dict[str, Any]) -> None:
    _case(f"validate-ok-{case_id}", "validate", rationale, OK, contract=contract)


ok("minimal", "the smallest valid contract: id, version and one requirement", minimal(ATTR))
ok("base", "the base contract every diff case starts from validates", BASE)
ok("native-base", "the native-shape base contract validates", NATIVE)
ok("profile-outcome-generic", "outcome in the abstract shape needs only id, profile and statement",
   minimal({"id": "r1", "profile": "outcome", "statement": "s"}))
ok("profile-obligation-generic", "obligation in the abstract shape needs a clause_ref",
   minimal({"id": "r1", "profile": "obligation", "statement": "s", "clause_ref": "p/1"}))
ok("profile-process", "process stub needs only id, profile and statement",
   minimal({"id": "r1", "profile": "process", "statement": "s"}))
ok("profile-quality", "quality stub needs only id, profile and statement",
   minimal({"id": "r1", "profile": "quality", "statement": "s"}))
ok("profile-human-role", "human_role stub needs only id, profile and statement",
   minimal({"id": "r1", "profile": "human_role", "statement": "s"}))
ok("profile-settlement", "settlement carries only a ref at the root",
   minimal({"id": "r1", "profile": "settlement", "ref": "example-settlement-v1"}))
ok("native-without-profile", "the native shape treats profile as optional (an outcome by default)",
   minimal({"id": "r1", "statement": "s", "evidence_rule": "e",
            "forward_verdict": "DETERMINISTIC", "backward_verdict": "DETERMINISTIC"}))
ok("mixed-profiles", "one contract may mix all seven profiles across its requirements", minimal(
    {"id": "r1", "profile": "outcome", "statement": "s"},
    {"id": "r2", "profile": "obligation", "statement": "s", "clause_ref": "p/1"},
    {"id": "r3", "profile": "process", "statement": "s"},
    {"id": "r4", "profile": "quality", "statement": "s"},
    {"id": "r5", "profile": "human_role", "statement": "s"},
    {"id": "r6", "profile": "attribution", "ref": "a"},
    {"id": "r7", "profile": "settlement", "ref": "s"},
))
ok("all-epistemic-types", "all eight epistemic types may be accepted at once", minimal(
    {"id": "r1", "profile": "outcome", "statement": "s", "evidence_requirements": {"accepted_epistemic_types": [
        "OBSERVED_EVENT", "SYSTEM_OF_RECORD_FACT", "PRODUCER_CLAIM", "HUMAN_REPORT",
        "SEMANTIC_JUDGMENT", "DERIVED_METRIC", "ADJUDICATION", "OBLIGATION_REFERENCE"]}}))
ok("empty-evidence-requirements", "an empty evidence_requirements block is valid and constrains nothing",
   minimal({"id": "r1", "profile": "outcome", "statement": "s", "evidence_requirements": {}}))
ok("principal-nostr", "a principal_ref is scheme:value; the scheme is profile-defined",
   mutate(BASE, lambda d: d.update(principal={"principal_ref": "nostr-pubkey:" + "ab" * 32})))
ok("principal-authority-context", "authority_context is a free list of citations, not a grant",
   mutate(BASE, lambda d: d.update(principal={"principal_ref": "example:p1", "authority_context": ["delegation-1"]})))
ok("process-extra-field", "the process stub stays open to extension: an unknown field is accepted",
   minimal({"id": "r1", "profile": "process", "statement": "s", "x_example_extension": {"k": 1}}))
ok("quality-free-fields", "quality's sketched fields accept any value",
   minimal({"id": "r1", "profile": "quality", "statement": "s", "correctness": 0, "robustness": ["a"]}))
ok("human-role-blocks", "human_role review/override/escalation are open objects",
   minimal({"id": "r1", "profile": "human_role", "statement": "s",
            "review": {"by": "role"}, "override": {}, "escalation": {"to": "role"}}))
ok("unicode-statement", "statements may be any non-empty Unicode text",
   minimal({"id": "r1", "profile": "outcome", "statement": "Prüfung vor Freigabe — 審査済み ✓"}))
ok("unicode-requirement-id", "requirement ids are any non-empty string",
   minimal({"id": "réq 1/α", "profile": "outcome", "statement": "s"}))
ok("non-numeric-version", "version is an opaque label, not a number",
   mutate(BASE, lambda d: d.update(version="2026-09-30.rc1")))
ok("no-subject", "subject is optional", minimal({"id": "r1", "profile": "outcome", "statement": "s"}))
ok("window-null-cure-grace", "a window's cure and grace may be null",
   minimal({"id": "r1", "statement": "s", "evidence_rule": "e", "forward_verdict": "DETERMINISTIC",
            "backward_verdict": "DETERMINISTIC", "window": {"duration": "P1D", "cure": None, "grace": None}}))
ok("clause-all-fields", "a clause may carry every optional field", minimal(
    {"id": "r1", "profile": "obligation", "statement": "s", "evidence_rule": "e",
     "forward_verdict": "DETERMINISTIC", "backward_verdict": "MANUAL",
     "clause": {"instrument": "Example Instrument", "article": "Article 2", "paragraph": "3",
                "as_amended_by": ["Example Instrument, revision 2"], "jurisdiction": "example",
                "text_snapshot_digest": "0" * 64, "effective_from": "2026-01-01",
                "source_url": "https://example.org/a2", "contested": True}}))
ok("consequence-settlement", "consequence.class settlement carries only a reference at the root",
   mutate(BASE, lambda d: d.update(consequence={"class": "settlement", "rule_ref": "example-rule"})))
ok("adjudication-hybrid", "adjudication mode hybrid with a policy reference",
   minimal({"id": "r1", "profile": "outcome", "statement": "s",
            "adjudication": {"mode": "hybrid", "policy_ref": "example-policy"}}))
ok("uncertainty-escalate", "uncertainty may escalate on both missing and conflicting evidence",
   minimal({"id": "r1", "profile": "outcome", "statement": "s",
            "uncertainty": {"on_missing": "escalate", "on_conflict": "escalate"}}))
ok("native-tool-call-instrument", "a native requirement may name a tool call as its instrument",
   minimal({"id": "r1", "statement": "s", "evidence_rule": "e", "forward_verdict": "DETERMINISTIC",
            "backward_verdict": "DETERMINISTIC", "evidence_instrument": {"kind": "tool_call_name", "name": "t"}}))
ok("native-refused", "a refused requirement states why it cannot be measured",
   minimal({"id": "r1", "statement": "s", "evidence_rule": "e", "forward_verdict": "REFUSED",
            "backward_verdict": "REFUSED", "refusal_reason_code": "subjective_state_unattestable"}))
ok("native-fold-cohort", "native fold modes and the informational tier are valid",
   minimal({"id": "r1", "statement": "s", "evidence_rule": "e", "forward_verdict": "DETERMINISTIC",
            "backward_verdict": "DETERMINISTIC", "mode": "fold_cohort", "tier": "informational"}))
ok("fifty-requirements", "a contract may carry many requirements",
   minimal(*({"id": f"r{i:02d}", "profile": "outcome", "statement": f"s{i}"} for i in range(50))))
ok("duplicate-requirement-ids", "the schema does not enforce unique requirement ids (contract diff does)",
   minimal({"id": "r1", "profile": "outcome", "statement": "s"}, {"id": "r1", "profile": "process", "statement": "t"}))
ok("obligation-refs-on-outcome", "an outcome may cite the obligations it is traced to",
   minimal({"id": "r1", "profile": "outcome", "statement": "s", "obligation_refs": ["p/1", "p/2"]}))
ok("temporal-and-disclosure", "temporal.reversal_window and disclosure.policy_ref are optional references",
   mutate(BASE, lambda d: d.update(temporal={"evaluation_period": "2026-09", "reversal_window": "P30D"},
                                    disclosure={"policy_ref": "example-disclosure"})))


# -- validate: negatives -----------------------------------------------------


def bad(case_id: str, rationale: str, contract: dict[str, Any], path: str, keyword: str) -> None:
    _case(f"validate-bad-{case_id}", "validate", rationale,
          {"valid": False, "error": {"path": path, "keyword": keyword}}, contract=contract)


def drop(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda d: d.pop(key)


def on_req(rid: str, fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    return lambda d: fn(_req(d, rid))


def on_er(rid: str, fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    return lambda d: fn(_req(d, rid)["evidence_requirements"])


bad("missing-id", "a contract without an id cannot be referenced by a Result", mutate(BASE, drop("id")), "<root>", "required")
bad("missing-version", "a contract without a version cannot be pinned", mutate(BASE, drop("version")), "<root>", "required")
bad("missing-requirements", "a contract must say what must be established",
    mutate(BASE, drop("requirements")), "<root>", "required")
bad("empty-requirements", "a contract with zero requirements establishes nothing",
    mutate(BASE, lambda d: d.update(requirements=[])), "requirements", "minItems")
bad("empty-id", "an empty id is no id", mutate(BASE, lambda d: d.update(id="")), "id", "minLength")
bad("empty-version", "an empty version label cannot name a version",
    mutate(BASE, lambda d: d.update(version="")), "version", "minLength")
bad("numeric-version", "version is a string label; a JSON number is rejected",
    mutate(BASE, lambda d: d.update(version=1)), "version", "type")
bad("unknown-root-field", "the root is closed: an unknown field is rejected, not ignored",
    mutate(BASE, lambda d: d.update(status="SATISFIED")), "<root>", "additionalProperties")
bad("requirements-not-array", "requirements must be an array",
    mutate(BASE, lambda d: d.update(requirements={"req-a": {}})), "requirements", "type")
bad("requirement-not-object", "each requirement must be an object",
    minimal("req-a"), "requirements/0", "type")
bad("missing-profile", "an abstract-shape requirement must name its profile",
    minimal({"id": "r1", "statement": "s"}), "requirements/0", "required")
bad("unknown-profile", "profile is closed at seven values",
    minimal({"id": "r1", "profile": "warranty", "statement": "s"}), "requirements/0/profile", "enum")
bad("profile-wrong-case", "profile values are case-sensitive",
    minimal({"id": "r1", "profile": "Outcome", "statement": "s"}), "requirements/0/profile", "enum")
bad("profile-number", "profile must be a string",
    minimal({"id": "r1", "profile": 1, "statement": "s"}), "requirements/0/profile", "enum")
bad("status-on-outcome", "a requirement never self-declares its status",
    mutate(BASE, on_req("req-a", lambda r: r.update(status="SATISFIED"))), "requirements/0/status", "false")
bad("result-on-process", "a requirement never self-declares its result",
    mutate(BASE, on_req("req-b", lambda r: r.update(result="met"))), "requirements/1/result", "false")
bad("status-on-attribution", "the self-declaration ban holds even on a ref-only stub",
    minimal({"id": "r1", "profile": "attribution", "ref": "a", "status": "SATISFIED"}), "requirements/0/status", "false")
bad("status-on-native", "the self-declaration ban holds on the native shape",
    minimal({"id": "r1", "statement": "s", "evidence_rule": "e", "forward_verdict": "DETERMINISTIC",
             "backward_verdict": "DETERMINISTIC", "status": "SATISFIED"}), "requirements/0/status", "false")
bad("obligation-missing-clause-ref", "an abstract obligation must cite its clause",
    minimal({"id": "r1", "profile": "obligation", "statement": "s"}), "requirements/0", "required")
bad("native-obligation-missing-clause", "a native obligation must carry its clause",
    minimal({"id": "r1", "profile": "obligation", "statement": "s", "evidence_rule": "e",
             "forward_verdict": "DETERMINISTIC", "backward_verdict": "DETERMINISTIC"}), "requirements/0", "required")
bad("unknown-epistemic-type", "epistemic types are a closed set of eight",
    mutate(BASE, on_er("req-a", lambda e: e.update(accepted_epistemic_types=["RUMOUR"]))),
    "requirements/0/evidence_requirements/accepted_epistemic_types/0", "enum")
bad("lowercase-epistemic-type", "epistemic type values are upper-case",
    mutate(BASE, on_er("req-a", lambda e: e.update(accepted_epistemic_types=["observed_event"]))),
    "requirements/0/evidence_requirements/accepted_epistemic_types/0", "enum")
bad("empty-epistemic-types", "an empty accepted list would accept nothing; omit the field instead",
    mutate(BASE, on_er("req-a", lambda e: e.update(accepted_epistemic_types=[]))),
    "requirements/0/evidence_requirements/accepted_epistemic_types", "minItems")
bad("evidence-requirements-unknown-field", "evidence_requirements is closed",
    mutate(BASE, on_er("req-a", lambda e: e.update(sources=["s"]))),
    "requirements/0/evidence_requirements", "additionalProperties")
bad("empty-required-source", "a required source must be named",
    mutate(BASE, on_er("req-a", lambda e: e.update(required_sources=[""]))),
    "requirements/0/evidence_requirements/required_sources/0", "minLength")
bad("adjudication-unknown-mode", "adjudication mode is closed at four values",
    mutate(BASE, on_req("req-a", lambda r: r.update(adjudication={"mode": "vibes"}))),
    "requirements/0/adjudication/mode", "enum")
bad("adjudication-missing-mode", "an adjudication block must name its mode",
    mutate(BASE, on_req("req-a", lambda r: r.update(adjudication={"policy_ref": "p"}))),
    "requirements/0/adjudication", "required")
bad("uncertainty-unknown-on-missing", "on_missing is closed at three values",
    mutate(BASE, on_req("req-a", lambda r: r.update(uncertainty={"on_missing": "assume_satisfied"}))),
    "requirements/0/uncertainty/on_missing", "enum")
bad("principal-uppercase-scheme", "a principal_ref scheme is lower-case",
    mutate(BASE, lambda d: d.update(principal={"principal_ref": "NOSTR:abc"})), "principal/principal_ref", "pattern")
bad("principal-missing-ref", "a principal block must cite a principal",
    mutate(BASE, lambda d: d.update(principal={"authority_context": ["x"]})), "principal", "required")
bad("subject-missing-job-type", "a subject must name its job type",
    mutate(BASE, lambda d: d.update(subject={"population_selector": "all"})), "subject", "required")
bad("subject-empty-job-type", "an empty job type names nothing",
    mutate(BASE, lambda d: d.update(subject={"job_type": ""})), "subject/job_type", "minLength")
bad("consequence-unknown-class", "consequence class is closed",
    mutate(BASE, lambda d: d.update(consequence={"class": "penalty"})), "consequence/class", "enum")
bad("consequence-missing-class", "a consequence must name its class",
    mutate(BASE, lambda d: d.update(consequence={"rule_ref": "r"})), "consequence", "required")
bad("temporal-unknown-field", "temporal is closed",
    mutate(BASE, lambda d: d.update(temporal={"deadline": "2026-10-01"})), "temporal", "additionalProperties")
bad("disclosure-unknown-field", "disclosure is closed",
    mutate(BASE, lambda d: d.update(disclosure={"public": True})), "disclosure", "additionalProperties")
bad("clause-missing-article", "a clause must name its article",
    mutate(NATIVE, on_req("req-o", lambda r: r["clause"].pop("article"))), "requirements/1/clause", "required")
bad("clause-uppercase-digest", "a clause text digest is lower-case hex",
    mutate(NATIVE, on_req("req-o", lambda r: r["clause"].update(text_snapshot_digest="A" * 64))),
    "requirements/1/clause/text_snapshot_digest", "pattern")
bad("clause-bad-effective-date", "effective_from is a calendar date",
    mutate(NATIVE, on_req("req-o", lambda r: r["clause"].update(effective_from="01/01/2026"))),
    "requirements/1/clause/effective_from", "pattern")
bad("native-unknown-forward-verdict", "forward_verdict is closed",
    mutate(NATIVE, on_req("req-n", lambda r: r.update(forward_verdict="MAYBE"))),
    "requirements/0/forward_verdict", "enum")
bad("native-window-missing-duration", "a window must have a duration",
    mutate(NATIVE, on_req("req-n", lambda r: r.update(window={"cure": "P1D"}))), "requirements/0/window", "required")
bad("native-window-unknown-field", "a window is closed",
    mutate(NATIVE, on_req("req-n", lambda r: r["window"].update(timezone="UTC"))),
    "requirements/0/window", "additionalProperties")
bad("native-unknown-tier", "tier is must_have or informational",
    mutate(NATIVE, on_req("req-n", lambda r: r.update(tier="critical"))), "requirements/0/tier", "enum")
bad("native-unknown-mode", "native evaluation mode is closed",
    mutate(NATIVE, on_req("req-n", lambda r: r.update(mode="vibes"))), "requirements/0/mode", "enum")
bad("native-unknown-instrument-kind", "evidence instrument kind is closed",
    mutate(NATIVE, on_req("req-n", lambda r: r.update(evidence_instrument={"kind": "screenshot"}))),
    "requirements/0/evidence_instrument/kind", "enum")
bad("attribution-missing-ref", "a ref-only stub must carry its ref",
    minimal({"id": "r1", "profile": "attribution"}), "requirements/0", "required")
bad("settlement-extra-field", "settlement logic never enters the root: the stub is closed",
    minimal({"id": "r1", "profile": "settlement", "ref": "s", "amount": 10}), "requirements/0", "additionalProperties")
bad("outcome-generic-extra-field", "the abstract outcome shape is closed",
    minimal({"id": "r1", "profile": "outcome", "statement": "s", "weight": 2}), "requirements/0", "additionalProperties")
bad("empty-statement", "an empty statement states nothing",
    minimal({"id": "r1", "profile": "outcome", "statement": ""}), "requirements/0/statement", "minLength")
bad("empty-requirement-id", "an empty requirement id cannot be cited by a claim",
    minimal({"id": "", "profile": "outcome", "statement": "s"}), "requirements/0/id", "minLength")


# -- diff --------------------------------------------------------------------


def diff(case_id: str, rationale: str, a: dict[str, Any], b: dict[str, Any], breaking: bool,
         *changes: dict[str, str]) -> None:
    expect = {"breaking": breaking, "changes": sorted(changes, key=lambda c: (c["path"], c["kind"]))}
    _case(f"diff-{case_id}", "diff", rationale, expect, a=a, b=b)


def v2(fn: Callable[[dict[str, Any]], Any], base: dict[str, Any] = BASE) -> dict[str, Any]:
    """``fn`` applied to a copy of ``base`` that is also bumped to version 2."""
    return mutate(base, lambda d: (d.update(version="2"), fn(d)))


VC = ch("version", "version_changed")
RA = "requirements[req-a]"
RB = "requirements[req-b]"
RC = "requirements[req-c]"
RN = "requirements[req-n]"
RO = "requirements[req-o]"
ERA = f"{RA}/evidence_requirements"


def _noop(d: dict[str, Any]) -> None:
    return None


diff("identical", "a contract diffed against itself has no changes", BASE, BASE, False)
diff("key-order-only", "member order is not content: same JCS bytes, no changes",
     BASE, json.loads(json.dumps(BASE, sort_keys=True)), False)
diff("version-bump-only", "a new version label with no other change is non-breaking", BASE, v2(_noop), False, VC)
diff("version-reused", "one version label must never name two different contracts",
     BASE, mutate(BASE, on_req("req-a", lambda r: r.update(statement="reworded"))), True,
     ch("version", "version_reused"), ch(f"{RA}/statement", "editorial"))
diff("contract-id-changed", "claims name their contract by id; a new id is a different contract",
     BASE, mutate(BASE, lambda d: d.update(id="ec:example-renamed:2026-09-30")), True, ch("id", "contract_id_changed"))
diff("requirement-added", "a new requirement owes new evidence",
     BASE, v2(lambda d: d["requirements"].append({"id": "req-d", "profile": "quality", "statement": "s"})), True,
     VC, ch("requirements[req-d]", "requirement_added"))
diff("requirement-removed", "a claim against A that cites a removed requirement no longer resolves in B",
     BASE, v2(lambda d: d["requirements"].pop(1)), True, VC, ch(RB, "requirement_removed"))
diff("requirement-reid", "re-identifying a requirement orphans every claim that cites the old id",
     BASE, v2(on_req("req-a", lambda r: r.update(id="req-a2"))), True, VC, ch(RA, "requirement_reid"))
diff("requirement-reid-with-change", "a new id with different content is a removal plus an addition",
     BASE, v2(on_req("req-a", lambda r: r.update(id="req-a2", statement="other"))), True,
     VC, ch(RA, "requirement_removed"), ch("requirements[req-a2]", "requirement_added"))
diff("requirements-reordered", "requirements are keyed by id; order carries no meaning",
     BASE, v2(lambda d: d["requirements"].reverse()), False, VC, ch("requirements", "requirements_reordered"))
diff("statement-deterministic", "rewording a deterministically evaluated requirement is editorial",
     BASE, v2(on_req("req-a", lambda r: r.update(statement="the record preceded dispatch"))), False,
     VC, ch(f"{RA}/statement", "editorial"))
diff("statement-process", "a process requirement with no deterministic adjudication may be judged on its wording",
     BASE, v2(on_req("req-b", lambda r: r.update(statement="a review happened"))), True,
     VC, ch(f"{RB}/statement", "changed"))
diff("statement-semantic", "under semantic adjudication the statement is what is judged",
     mutate(BASE, on_req("req-a", lambda r: r.update(adjudication={"mode": "semantic"}))),
     v2(on_req("req-a", lambda r: r.update(adjudication={"mode": "semantic"}, statement="reworded"))), True,
     VC, ch(f"{RA}/statement", "changed"))
diff("statement-becomes-judged", "a statement reworded while adjudication moves off deterministic is breaking twice",
     BASE, v2(on_req("req-a", lambda r: r.update(adjudication={"mode": "human"}, statement="reworded"))), True,
     VC, ch(f"{RA}/statement", "changed"), ch(f"{RA}/adjudication/mode", "changed"))
diff("epistemic-narrowed", "accepting fewer epistemic types tightens the requirement",
     BASE, v2(on_er("req-a", lambda e: e.update(accepted_epistemic_types=["OBSERVED_EVENT"]))), True,
     VC, ch(f"{ERA}/accepted_epistemic_types", "tightened"))
diff("epistemic-widened", "accepting more epistemic types loosens the requirement",
     BASE, v2(on_er("req-a", lambda e: e["accepted_epistemic_types"].append("PRODUCER_CLAIM"))), False,
     VC, ch(f"{ERA}/accepted_epistemic_types", "loosened"))
diff("epistemic-swapped", "swapping an accepted type may orphan evidence of the old type",
     BASE, v2(on_er("req-a", lambda e: e.update(accepted_epistemic_types=["OBSERVED_EVENT", "HUMAN_REPORT"]))), True,
     VC, ch(f"{ERA}/accepted_epistemic_types", "changed"))
diff("epistemic-reordered", "accepted types are a set: reordering them is no change",
     BASE, v2(on_er("req-a", lambda e: e["accepted_epistemic_types"].reverse())), False, VC)
diff("epistemic-dropped", "dropping the accepted list accepts every type",
     BASE, v2(on_er("req-a", lambda e: e.pop("accepted_epistemic_types"))), False,
     VC, ch(f"{ERA}/accepted_epistemic_types", "loosened"))
diff("epistemic-introduced", "introducing an accepted list where there was none restricts types",
     mutate(BASE, on_er("req-a", lambda e: e.pop("accepted_epistemic_types"))), v2(_noop), True,
     VC, ch(f"{ERA}/accepted_epistemic_types", "tightened"))
diff("source-added", "requiring one more source tightens the requirement",
     BASE, v2(on_er("req-a", lambda e: e["required_sources"].append("source-two"))), True,
     VC, ch(f"{ERA}/required_sources", "tightened"))
diff("source-removed", "requiring fewer sources loosens the requirement",
     mutate(BASE, on_er("req-a", lambda e: e["required_sources"].append("source-two"))), v2(_noop), False,
     VC, ch(f"{ERA}/required_sources", "loosened"))
diff("source-changed", "replacing a source is breaking: evidence from the old source no longer counts",
     BASE, v2(on_er("req-a", lambda e: e.update(required_sources=["source-two"]))), True,
     VC, ch(f"{ERA}/required_sources", "changed"))
diff("source-list-dropped", "dropping required_sources entirely requires no particular source",
     BASE, v2(on_er("req-a", lambda e: e.pop("required_sources"))), False,
     VC, ch(f"{ERA}/required_sources", "loosened"))
diff("assurance-raised", "a higher assurance floor tightens the requirement",
     BASE, v2(on_er("req-a", lambda e: e.update(minimum_assurance=["countersigned"]))), True,
     VC, ch(f"{ERA}/minimum_assurance", "tightened"))
diff("assurance-lowered", "a lower assurance floor loosens the requirement",
     BASE, v2(on_er("req-a", lambda e: e.update(minimum_assurance=["self-attested"]))), False,
     VC, ch(f"{ERA}/minimum_assurance", "loosened"))
diff("assurance-same-floor", "a multi-grade list is undefined, so changing its members at the same floor is breaking",
     BASE, v2(on_er("req-a", lambda e: e.update(minimum_assurance=["witnessed", "countersigned"]))), True,
     VC, ch(f"{ERA}/minimum_assurance", "changed"))
diff("assurance-unknown-grade", "a grade off the ladder has no direction: breaking",
     BASE, v2(on_er("req-a", lambda e: e.update(minimum_assurance=["notarised"]))), True,
     VC, ch(f"{ERA}/minimum_assurance", "changed"))
diff("assurance-introduced", "introducing an assurance floor where there was none tightens",
     mutate(BASE, on_er("req-a", lambda e: e.pop("minimum_assurance"))), v2(_noop), True,
     VC, ch(f"{ERA}/minimum_assurance", "tightened"))
diff("freshness-shortened", "evidence must be newer: tightened",
     BASE, v2(on_er("req-a", lambda e: e.update(freshness="P1D"))), True, VC, ch(f"{ERA}/freshness", "tightened"))
diff("freshness-lengthened", "older evidence now counts: loosened",
     BASE, v2(on_er("req-a", lambda e: e.update(freshness="P30D"))), False, VC, ch(f"{ERA}/freshness", "loosened"))
diff("freshness-respelled", "P7D and P1W are the same length: editorial",
     BASE, v2(on_er("req-a", lambda e: e.update(freshness="P1W"))), False, VC, ch(f"{ERA}/freshness", "editorial"))
diff("freshness-time-units", "durations compare by length across units: PT167H is an hour short of P7D",
     BASE, v2(on_er("req-a", lambda e: e.update(freshness="PT167H"))), True, VC, ch(f"{ERA}/freshness", "tightened"))
diff("freshness-unparseable", "a freshness that is not an ISO-8601 duration has no direction",
     BASE, v2(on_er("req-a", lambda e: e.update(freshness="one week"))), True, VC, ch(f"{ERA}/freshness", "changed"))
diff("freshness-overlong-component", "a duration component over nine digits does not parse, so has no direction",
     BASE, v2(on_er("req-a", lambda e: e.update(freshness="P1234567890D"))), True, VC, ch(f"{ERA}/freshness", "changed"))
diff("freshness-dangling-t", "a time designator with no time component is not a duration",
     BASE, v2(on_er("req-a", lambda e: e.update(freshness="P7DT"))), True, VC, ch(f"{ERA}/freshness", "changed"))
diff("freshness-month-vs-days", "a month is not a fixed number of days: P1M to P30D is not comparable",
     mutate(BASE, on_er("req-a", lambda e: e.update(freshness="P1M"))),
     v2(on_er("req-a", lambda e: e.update(freshness="P30D"))), True, VC, ch(f"{ERA}/freshness", "changed"))
diff("freshness-month-vs-one-day", "months and days are never ordered against each other, even when obvious",
     mutate(BASE, on_er("req-a", lambda e: e.update(freshness="P1M"))),
     v2(on_er("req-a", lambda e: e.update(freshness="P1D"))), True, VC, ch(f"{ERA}/freshness", "changed"))
diff("freshness-year-as-months", "a year is exactly twelve months: P1Y to P12M is a respelling",
     mutate(BASE, on_er("req-a", lambda e: e.update(freshness="P1Y"))),
     v2(on_er("req-a", lambda e: e.update(freshness="P12M"))), False, VC, ch(f"{ERA}/freshness", "editorial"))
diff("freshness-months-lengthened", "months compare with months: P1M to P2M is longer",
     mutate(BASE, on_er("req-a", lambda e: e.update(freshness="P1M"))),
     v2(on_er("req-a", lambda e: e.update(freshness="P2M"))), False, VC, ch(f"{ERA}/freshness", "loosened"))
diff("freshness-month-plus-hours", "same months and more hours is longer in one part and equal in the other",
     mutate(BASE, on_er("req-a", lambda e: e.update(freshness="P1M"))),
     v2(on_er("req-a", lambda e: e.update(freshness="P1MT1H"))), False, VC, ch(f"{ERA}/freshness", "loosened"))
diff("freshness-mixed-opposite", "longer in months but shorter in days cannot be ordered",
     mutate(BASE, on_er("req-a", lambda e: e.update(freshness="P1M10D"))),
     v2(on_er("req-a", lambda e: e.update(freshness="P2M"))), True, VC, ch(f"{ERA}/freshness", "changed"))
diff("freshness-month-from-unbounded", "any limit where there was none tightens, months included",
     mutate(BASE, on_er("req-a", lambda e: e.pop("freshness"))),
     v2(on_er("req-a", lambda e: e.update(freshness="P1M"))), True, VC, ch(f"{ERA}/freshness", "tightened"))
diff("native-window-days-to-month", "a window in days replaced by one in months is not comparable",
     NATIVE, v2(on_req("req-n", lambda r: r["window"].update(duration="P1M")), NATIVE), True,
     VC, ch(f"{RN}/window/duration", "changed"))
diff("freshness-dropped", "no freshness limit at all is the loosest",
     BASE, v2(on_er("req-a", lambda e: e.pop("freshness"))), False, VC, ch(f"{ERA}/freshness", "loosened"))
diff("independence-changed", "independence is an opaque predicate: any change is breaking",
     BASE, v2(on_er("req-a", lambda e: e.update(independence="two producers"))), True,
     VC, ch(f"{ERA}/independence", "changed"))
diff("coverage-changed", "coverage is an opaque predicate: any change is breaking",
     BASE, v2(on_er("req-a", lambda e: e.update(coverage="every job"))), True, VC, ch(f"{ERA}/coverage", "changed"))
diff("adjudication-mode", "a different adjudication mode evaluates differently",
     BASE, v2(on_req("req-a", lambda r: r.update(adjudication={"mode": "hybrid"}))), True,
     VC, ch(f"{RA}/adjudication/mode", "changed"))
diff("uncertainty-on-missing", "how missing evidence resolves changes every gap",
     BASE, v2(on_req("req-a", lambda r: r["uncertainty"].update(on_missing="unknown"))), True,
     VC, ch(f"{RA}/uncertainty/on_missing", "changed"))
diff("approval-added", "one more approval owed tightens",
     BASE, v2(on_req("req-b", lambda r: r["approvals"].append("a second reviewer"))), True,
     VC, ch(f"{RB}/approvals", "tightened"))
diff("approval-removed", "fewer approvals owed loosens",
     BASE, v2(on_req("req-b", lambda r: r.pop("approvals"))), False, VC, ch(f"{RB}/approvals", "loosened"))
diff("sequence-step-added", "one more required step tightens",
     BASE, v2(on_req("req-b", lambda r: r["required_sequence"].insert(2, "tested"))), True,
     VC, ch(f"{RB}/required_sequence", "tightened"))
diff("sequence-step-removed", "dropping a required step loosens",
     BASE, v2(on_req("req-b", lambda r: r["required_sequence"].remove("approved"))), False,
     VC, ch(f"{RB}/required_sequence", "loosened"))
diff("sequence-reordered", "reordering required steps is neither tighter nor looser",
     BASE, v2(on_req("req-b", lambda r: r.update(required_sequence=["approved", "requested", "applied"]))), True,
     VC, ch(f"{RB}/required_sequence", "changed"))
diff("escalation-path", "where an escalation goes is not read when sufficiency is decided",
     BASE, v2(on_req("req-b", lambda r: r.update(escalation_path="queue-two"))), False,
     VC, ch(f"{RB}/escalation_path", "editorial"))
diff("process-extension-field", "an extension field on an open stub has no rule: breaking",
     BASE, v2(on_req("req-b", lambda r: r.update(x_example_extension="v"))), True,
     VC, ch(f"{RB}/x_example_extension", "changed"))
diff("profile-changed", "a different profile is a different kind of requirement",
     BASE, v2(on_req("req-a", lambda r: r.update(profile="process"))), True, VC, ch(f"{RA}/profile", "changed"))
diff("clause-ref-changed", "citing a different clause changes which obligation is evidenced",
     BASE, v2(on_req("req-c", lambda r: r.update(clause_ref="example-policy/section-1/v2"))), True,
     VC, ch(f"{RC}/clause_ref", "changed"))
diff("obligation-refs-added", "the obligation trace changes what a result is cited for",
     BASE, v2(on_req("req-a", lambda r: r.update(obligation_refs=["example-policy/section-2/v1"]))), True,
     VC, ch(f"{RA}/obligation_refs", "changed"))
diff("population-changed", "a different population selector evaluates different jobs",
     BASE, v2(lambda d: d["subject"].update(population_selector="jobs over a threshold")), True,
     VC, ch("subject/population_selector", "changed"))
diff("consequence-class", "a different consequence class changes what a result triggers",
     BASE, v2(lambda d: d["consequence"].update({"class": "escalation"})), True, VC, ch("consequence/class", "changed"))
diff("evaluation-period", "a different evaluation period is a different window of evidence",
     BASE, v2(lambda d: d["temporal"].update(evaluation_period="2026-10")), True,
     VC, ch("temporal/evaluation_period", "changed"))
diff("principal-added", "naming a principal where none was named changes who the contract is about",
     BASE, v2(lambda d: d.update(principal={"principal_ref": "example:p1"})), True, VC, ch("principal", "changed"))
diff("native-window-shortened", "a shorter window tightens",
     NATIVE, v2(on_req("req-n", lambda r: r["window"].update(duration="P3D")), NATIVE), True,
     VC, ch(f"{RN}/window/duration", "tightened"))
diff("native-window-lengthened", "a longer window loosens",
     NATIVE, v2(on_req("req-n", lambda r: r["window"].update(duration="P14D")), NATIVE), False,
     VC, ch(f"{RN}/window/duration", "loosened"))
diff("native-cure-added", "a cure period where there was none loosens",
     NATIVE, v2(on_req("req-n", lambda r: r["window"].update(cure="P2D")), NATIVE), False,
     VC, ch(f"{RN}/window/cure", "loosened"))
diff("native-grace-dropped-to-null", "null grace is zero grace: removing a grace period tightens",
     mutate(NATIVE, on_req("req-n", lambda r: r["window"].update(grace="P1D"))),
     v2(_noop, NATIVE), True, VC, ch(f"{RN}/window/grace", "tightened"))
diff("native-window-removed", "no window at all is the loosest",
     NATIVE, v2(on_req("req-n", lambda r: r.pop("window")), NATIVE), False, VC, ch(f"{RN}/window", "loosened"))
diff("native-window-added", "a window where there was none tightens",
     NATIVE, v2(on_req("req-o", lambda r: r.update(window={"duration": "P7D"})), NATIVE), True,
     VC, ch(f"{RO}/window", "tightened"))
diff("native-tier-raised", "informational to must_have now gates the result",
     NATIVE, v2(on_req("req-n", lambda r: r.update(tier="must_have")), NATIVE), True, VC, ch(f"{RN}/tier", "tightened"))
diff("native-tier-defaulted", "an explicit informational tier and an absent tier are the same",
     NATIVE, v2(on_req("req-n", lambda r: r.pop("tier")), NATIVE), False, VC)
diff("native-tier-lowered", "must_have to absent (informational) no longer gates the result",
     mutate(NATIVE, on_req("req-n", lambda r: r.update(tier="must_have"))),
     v2(on_req("req-n", lambda r: r.pop("tier")), NATIVE), False, VC, ch(f"{RN}/tier", "loosened"))
diff("native-grade-raised", "a higher required grade tightens",
     NATIVE, v2(on_req("req-n", lambda r: r.update(required_assurance_grade="countersigned")), NATIVE), True,
     VC, ch(f"{RN}/required_assurance_grade", "tightened"))
diff("native-evidence-rule", "the evidence rule is the predicate: any change is breaking",
     NATIVE, v2(on_req("req-n", lambda r: r.update(evidence_rule="refund record, any status")), NATIVE), True,
     VC, ch(f"{RN}/evidence_rule", "changed"))
diff("native-statement-deterministic", "a deterministic native requirement may be reworded",
     NATIVE, v2(on_req("req-n", lambda r: r.update(statement="the refund was confirmed")), NATIVE), False,
     VC, ch(f"{RN}/statement", "editorial"))
diff("native-statement-manual", "a manually checked requirement is checked against its wording",
     mutate(NATIVE, on_req("req-n", lambda r: r.update(backward_verdict="MANUAL"))),
     v2(on_req("req-n", lambda r: r.update(backward_verdict="MANUAL", statement="reworded")), NATIVE), True,
     VC, ch(f"{RN}/statement", "changed"))
diff("native-statement-judged-mode", "a judged-mode requirement is judged against its wording",
     mutate(NATIVE, on_req("req-n", lambda r: r.update(mode="judged"))),
     v2(on_req("req-n", lambda r: r.update(mode="judged", statement="reworded")), NATIVE), True,
     VC, ch(f"{RN}/statement", "changed"))
diff("native-forward-verdict", "a different forward verdict is a different evaluation",
     NATIVE, v2(on_req("req-n", lambda r: r.update(forward_verdict="UNAVAILABLE-STATE-REQUIRED")), NATIVE), True,
     VC, ch(f"{RN}/forward_verdict", "changed"))
diff("clause-source-url", "where the clause text is published is not read when sufficiency is decided",
     NATIVE, v2(on_req("req-o", lambda r: r["clause"].update(source_url="https://example.org/a1-v2")), NATIVE), False,
     VC, ch(f"{RO}/clause/source_url", "editorial"))
diff("clause-article", "a different article is a different obligation",
     NATIVE, v2(on_req("req-o", lambda r: r["clause"].update(article="Article 2")), NATIVE), True,
     VC, ch(f"{RO}/clause/article", "changed"))
diff("mixed-tighten-and-loosen", "one tightening makes the whole diff breaking, however much else loosens",
     BASE, v2(lambda d: (_req(d, "req-a")["evidence_requirements"].update(freshness="P30D", required_sources=[]),
                         _req(d, "req-b")["approvals"].append("a second reviewer"))), True,
     VC, ch(f"{ERA}/freshness", "loosened"), ch(f"{ERA}/required_sources", "loosened"),
     ch(f"{RB}/approvals", "tightened"))
diff("all-non-breaking", "loosenings and editorial changes together stay non-breaking",
     BASE, v2(lambda d: (_req(d, "req-a").update(statement="reworded"),
                         _req(d, "req-a")["evidence_requirements"].update(freshness="P30D"),
                         _req(d, "req-b").update(escalation_path="queue-two"))), False,
     VC, ch(f"{RA}/statement", "editorial"), ch(f"{ERA}/freshness", "loosened"),
     ch(f"{RB}/escalation_path", "editorial"))
diff("requirement-removed-among-loosenings", "one removed requirement makes an otherwise loosening diff breaking",
     BASE, v2(lambda d: (_req(d, "req-a")["evidence_requirements"].update(freshness="P30D"),
                         d["requirements"].pop(2))), True,
     VC, ch(f"{ERA}/freshness", "loosened"), ch(RC, "requirement_removed"))

# Extension fields on open profiles (process, quality, human_role accept any
# extra field): a field rule applies only where the schema defines the field,
# so an extension that shares a rule's name is compared with no rule.
R1 = "requirements[r1]"


def ext(profile: str, **fields: Any) -> dict[str, Any]:
    return minimal({"id": "r1", "profile": profile, "statement": "s", **fields})


def ext2(profile: str, **fields: Any) -> dict[str, Any]:
    return mutate(ext(profile, **fields), lambda d: d.update(version="2"))


diff("ext-window-extra-field", "an unknown member inside an extension window is still a change, not dropped",
     ext("quality", window={"duration": "P7D", "anchor": "start"}),
     ext2("quality", window={"duration": "P7D", "anchor": "end"}), True, VC, ch(f"{R1}/window/anchor", "changed"))
diff("ext-window-string", "an extension window that is not an object has no rule: breaking, never a crash",
     ext("process", window="P7D"), ext2("process", window="P1D"), True, VC, ch(f"{R1}/window", "changed"))
diff("ext-window-shortened", "the window rule is the native shape's; on quality a shorter window is just a change",
     ext("quality", window={"duration": "P7D"}), ext2("quality", window={"duration": "P14D"}), True,
     VC, ch(f"{R1}/window/duration", "changed"))
diff("ext-source-url", "source_url is editorial only inside a native clause, not as an extension field",
     ext("quality", source_url="x"), ext2("quality", source_url="y"), True, VC, ch(f"{R1}/source_url", "changed"))
diff("ext-tier", "tier is ranked only on the native shape; an extension tier has no direction",
     ext("quality", tier="must_have"), ext2("quality", tier="informational"), True, VC, ch(f"{R1}/tier", "changed"))
diff("ext-escalation-path-on-human-role", "escalation_path is editorial on process, not as a human_role extension",
     ext("human_role", escalation_path="q1"), ext2("human_role", escalation_path="q2"), True,
     VC, ch(f"{R1}/escalation_path", "changed"))
diff("ext-evidence-requirements-on-quality", "quality defines no evidence_requirements; its freshness has no rule",
     ext("quality", evidence_requirements={"freshness": "P7D"}),
     ext2("quality", evidence_requirements={"freshness": "P30D"}), True,
     VC, ch(f"{R1}/evidence_requirements/freshness", "changed"))
diff("ext-adjudication-on-process", "an extension adjudication cannot make a process statement editorial",
     ext("process", adjudication={"mode": "deterministic"}),
     mutate(ext2("process", adjudication={"mode": "deterministic"}), on_req("r1", lambda r: r.update(statement="t"))),
     True, VC, ch(f"{R1}/statement", "changed"))
diff("process-sequence-rule-still-applies", "on process, required_sequence is defined and keeps its rule",
     ext("process", required_sequence=["a", "b"]), ext2("process", required_sequence=["a"]), False,
     VC, ch(f"{R1}/required_sequence", "loosened"))
diff("shape-changed-generic-to-native", "moving a requirement between shapes compares fields with no rule",
     ext("outcome"), mutate(ext2("outcome"), on_req("r1", lambda r: r.update(
         evidence_rule="e", forward_verdict="DETERMINISTIC", backward_verdict="DETERMINISTIC", tier="informational"))),
     True, VC, ch(f"{R1}/backward_verdict", "changed"), ch(f"{R1}/evidence_rule", "changed"),
     ch(f"{R1}/forward_verdict", "changed"), ch(f"{R1}/tier", "changed"))


def diff_error(case_id: str, rationale: str, a: dict[str, Any], b: dict[str, Any], code: str,
               *unvalidated: dict[str, str]) -> None:
    """``unvalidated``, when given, is the exact change list a diff that skips
    validation must still produce: defence in depth for a caller that diffs
    without validating first. Every such change list is breaking."""
    expect: dict[str, Any] = {"error": code}
    if unvalidated:
        expect["unvalidated"] = {"breaking": True,
                                 "changes": sorted(unvalidated, key=lambda c: (c["path"], c["kind"]))}
    _case(f"diff-bad-{case_id}", "diff", rationale, expect, a=a, b=b)


diff_error("a-invalid", "an invalid contract is refused before any diffing",
           mutate(BASE, drop("version")), BASE, "invalid_contract")
diff_error("b-invalid", "both sides are validated, not just the first",
           BASE, mutate(BASE, lambda d: d.update(requirements=[])), "invalid_contract")
diff_error("duplicate-requirement-id", "two requirements with one id cannot be matched to claims",
           BASE, v2(lambda d: d["requirements"].append(dict(_req(d, "req-a")))), "duplicate_requirement_id")
diff_error("b-self-declared-status", "a contract that self-declares a status is refused, not diffed",
           BASE, v2(on_req("req-a", lambda r: r.update(status="SATISFIED"))), "invalid_contract")

# Type changes: invalid, so refused; a diff that skips validation must still
# never rank a value of the wrong type.
diff_error("approvals-not-a-list", "approvals turned from a list into a string is refused, never 'loosened'",
           BASE, v2(on_req("req-b", lambda r: r.update(approvals="one reviewer who is not the author"))),
           "invalid_contract", VC, ch(f"{RB}/approvals", "changed"))
diff_error("required-sources-not-a-list", "required_sources as a string is refused, never 'loosened'",
           BASE, v2(on_er("req-a", lambda e: e.update(required_sources="source-one"))),
           "invalid_contract", VC, ch(f"{ERA}/required_sources", "changed"))
diff_error("epistemic-types-not-a-list", "accepted types as a string is refused, never ranked",
           BASE, v2(on_er("req-a", lambda e: e.update(accepted_epistemic_types="OBSERVED_EVENT"))),
           "invalid_contract", VC, ch(f"{ERA}/accepted_epistemic_types", "changed"))
diff_error("sequence-not-a-list", "a required sequence as a string is refused, never ranked",
           BASE, v2(on_req("req-b", lambda r: r.update(required_sequence="requested"))),
           "invalid_contract", VC, ch(f"{RB}/required_sequence", "changed"))
diff_error("escalation-path-null", "a null escalation path is refused; unvalidated it is a change, not editorial",
           mutate(BASE, on_req("req-b", lambda r: r.update(escalation_path=None))), v2(_noop),
           "invalid_contract", VC, ch(f"{RB}/escalation_path", "changed"))
diff_error("assurance-not-a-list", "a minimum assurance that is a number is refused, never ranked",
           BASE, v2(on_er("req-a", lambda e: e.update(minimum_assurance=3))),
           "invalid_contract", VC, ch(f"{ERA}/minimum_assurance", "changed"))
diff_error("native-tier-not-a-string", "a tier that is a list is refused; unvalidated it is a change, never a crash",
           NATIVE, v2(on_req("req-n", lambda r: r.update(tier=["must_have"])), NATIVE),
           "invalid_contract", VC, ch(f"{RN}/tier", "changed"))
diff_error("unknown-epistemic-type-added", "adding a type outside the closed set is refused, not 'loosened'",
           BASE, v2(on_er("req-a", lambda e: e["accepted_epistemic_types"].append("ATTESTED_CLAIM"))),
           "invalid_contract")


# -- pin ---------------------------------------------------------------------


def pin(case_id: str, rationale: str, contract: dict[str, Any]) -> None:
    # The digest is the one computed value in the library: it is what a second
    # implementation must reproduce byte for byte.
    from agent_action_capsule.canonical import json_digest

    expect = {
        "contract_ref": f"{contract['id']}@{contract['version']}",
        "contract_digest": {"digest_alg": "SHA-256", "digest": json_digest(contract)},
    }
    _case(f"pin-{case_id}", "pin", rationale, expect, contract=contract)


pin("base", "a Result names the base contract by reference and JCS digest", BASE)
pin("base-key-order", "member order does not change the digest (same value as pin-base)",
    json.loads(json.dumps(BASE, sort_keys=True)))
pin("version-2", "a new version label changes the digest", v2(_noop))
pin("unicode", "non-ASCII text is digested as UTF-8 per JCS",
    minimal({"id": "réq 1/α", "profile": "outcome", "statement": "Prüfung vor Freigabe — 審査済み ✓"}))
pin("escapes", "control characters and quotes use JCS escapes",
    minimal({"id": "r1", "profile": "outcome", "statement": "line\nbreak \"quoted\" tab\t\u001f"}))
pin("native-nulls", "explicit nulls are digested, not dropped", NATIVE)


# -- write -------------------------------------------------------------------


def _outcome(case: dict[str, Any]) -> str:
    e = case["expect"]
    if case["kind"] == "validate":
        return "valid" if e["valid"] else f"invalid: {e['error']['keyword']} at {e['error']['path']}"
    if case["kind"] == "diff":
        if "error" in e:
            return f"error: {e['error']}"
        if not e["changes"]:
            return "identical"
        return "breaking" if e["breaking"] else "non-breaking"
    return "digest"


def write(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for stale in [*out.glob("*.json"), out / "SHA256SUMS"]:
        if stale.exists():
            stale.unlink()
    index = []
    for case in CASES:
        name = f"{case['id']}.json"
        (out / name).write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n")
        negative = case["kind"] != "pin" and (case["expect"].get("valid") is False or "error" in case["expect"]
                                               or case["expect"].get("breaking") is True)
        index.append({"id": case["id"], "kind": case["kind"], "file": name, "negative": negative,
                      "outcome": _outcome(case), "rationale": case["rationale"]})
    (out / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    lines = [
        "# Evidence Contract edge cases",
        "",
        f"{len(index)} cases, {sum(1 for i in index if i['negative'])} of them negative (must fail: an invalid",
        "contract, a breaking diff, or inputs a diff refuses). Generated by",
        "`scripts/generate_contract_cases.py`; run by `tests/test_contract_cases.py`. Do not edit by hand.",
        "",
        "| id | kind | expected outcome | rationale |",
        "|---|---|---|---|",
    ]
    lines += [f"| `{i['id']}` | {i['kind']} | {i['outcome']} | {i['rationale']} |" for i in index]
    (out / "INDEX.md").write_text("\n".join(lines) + "\n")
    # One line per file, in `sha256sum` format: a copy of this library
    # elsewhere pins the digest of this file to prove it has not drifted.
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
            for p in sorted(out.iterdir(), key=lambda p: p.name) if p.name != "SHA256SUMS"]
    (out / "SHA256SUMS").write_text("".join(sums))


if __name__ == "__main__":
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT)
