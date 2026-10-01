# SPDX-License-Identifier: Apache-2.0
"""Coverage report per requirement, on synthetic records only. Every
negative flips one thing and confirms the result changes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from capsule_engine.report.coverage import (
    STATUS_TO_SUFFICIENCY,
    Remedy,
    build_coverage_report,
    default_producer_of,
    required_producers,
)
from capsule_engine.report.errors import ResultError
from capsule_engine.report.result import (
    Claim,
    DigestRef,
    DisclosureCarrier,
    ProofRef,
    build_result,
    validate_against_schema,
    verify_result,
)

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "examples" / "contracts" / "ai-act-human-oversight.json"
OTEL_BLOCK_KEY = "org.agentactioncapsule.otel"  # capsule_emit.otel.OTEL_BLOCK_KEY


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text())


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _record(seed: str, source: str, *, operator: str = "synthetic-op", developer: str = "agent@v1") -> dict:
    return {
        "capsule_id": _hex(seed),
        "operator": operator,
        "developer": developer,
        "payload": {"source": source},
    }


def _span_record(seed: str, source: str, *, operator: str = "synthetic-op", developer: str = "agent@v1") -> dict:
    """The shape the OTel processor emits: the digest-only block under
    model_attestation.compute_attestation, producer = the exporter's
    operator/developer."""
    record = _record(seed, source, operator=operator, developer=developer)
    record["model_attestation"] = {
        "compute_attestation": {
            OTEL_BLOCK_KEY: {
                "trace_id": _hex("trace-1"),
                "span_id": _hex(f"span-{seed}"),
                "resource": {"service.name": "synthetic-agent"},
            }
        }
    }
    return record


def _backfilled(seed: str, source: str, **kw) -> dict:
    record = _record(seed, source, **kw)
    record["provenance_mode"] = {
        "mode": "backfilled",
        "source_ref": {"type": "source_record", "digest_alg": "sha256", "digest": _hex(f"src-{seed}")},
        "source_asserted_at": "2026-08-01T00:00:00Z",
        "import_batch": "batch-1",
        "imported_at": "2026-09-22T00:00:00Z",
    }
    return record


def _source_of(record: dict) -> str | None:
    return record.get("payload", {}).get("source")


def _req(report, ref):
    return next(r for r in report.requirements if r.requirement_ref == ref)


def _full_records() -> list[dict]:
    """Every required source of the synthetic contract present, each
    requirement corroborated by a second producer where the sources allow."""
    return [
        _record("r1", "role-assignment-record", operator="hr-system", developer="sor@v1"),
        _record("r2", "authority-competence-record", operator="training-system", developer="sor@v1"),
        _record("r3", "ui-explanation-capability-record"),
        _record("r4", "review-interaction-records", operator="review-tool", developer="ui@v2"),
        _record("r5", "review-events"),
        _record("r6", "override-events", operator="review-tool", developer="ui@v2"),
        _record("r7", "exception-events"),
    ]


REMEDIES = {
    "override-events": Remedy(connector="human_approval", raises_to="observed"),
    "exception-events": Remedy(connector="otel", raises_to="observed"),
    "review-events": Remedy(connector="mcp_proxy", raises_to="committed"),
}


# --- what exists -----------------------------------------------------------


def test_every_source_present_satisfies_every_requirement():
    report = build_coverage_report(_contract(), _full_records(), source_of=_source_of)
    assert [r.status for r in report.requirements] == ["SATISFIED"] * 3
    assert report.summary.gaps == 0
    for row in report.requirements:
        assert all(s.record_count == 1 and len(s.evidence) == 1 for s in row.sources)
        assert row.sufficiency == "SATISFIED"


def test_requirement_lists_obligations_and_its_claims():
    contract = _contract()
    claim = _claim("c-3", "req-human-role-3")
    report = build_coverage_report(contract, _full_records(), source_of=_source_of, claims=[claim])
    row = _req(report, "req-human-role-3")
    assert row.obligation_refs == ("eu-ai-act:article-14",)
    assert row.claim_ids == ("c-3",)
    assert _req(report, "req-human-role-1").claim_ids == ()


def test_evidence_digests_are_the_capsule_ids():
    records = _full_records()
    report = build_coverage_report(_contract(), records, source_of=_source_of)
    src = _req(report, "req-human-role-1").sources[0]
    assert src.evidence == (DigestRef(digest=records[0]["capsule_id"]),)


def test_record_without_hex_capsule_id_gets_a_content_digest():
    records = _full_records()
    records[0]["capsule_id"] = "not-hex"
    report = build_coverage_report(_contract(), records, source_of=_source_of)
    digest = _req(report, "req-human-role-1").sources[0].evidence[0].digest
    assert len(digest) == 64 and digest != "not-hex"


# --- what is missing, and what would close it ------------------------------


def test_missing_source_is_not_found_with_named_remedy():
    records = [r for r in _full_records() if _source_of(r) != "override-events"]
    report = build_coverage_report(_contract(), records, source_of=_source_of, remedies=REMEDIES)
    row = _req(report, "req-human-role-3")
    assert row.status == "NOT_FOUND"
    assert row.sufficiency == "GAP"
    (gap,) = row.gaps
    assert gap.kind == "missing_source"
    assert gap.source == "override-events"
    assert gap.remedy == Remedy(connector="human_approval", raises_to="observed")
    assert report.summary.gaps_without_remedy == 0


def test_missing_source_without_catalog_entry_is_counted_not_dropped():
    records = [r for r in _full_records() if _source_of(r) != "role-assignment-record"]
    report = build_coverage_report(_contract(), records, source_of=_source_of, remedies=REMEDIES)
    (gap,) = _req(report, "req-human-role-1").gaps
    assert gap.remedy is None
    assert report.summary.gaps == 1
    assert report.summary.gaps_without_remedy == 1


def test_no_records_at_all_every_source_is_a_gap():
    report = build_coverage_report(_contract(), [], source_of=_source_of)
    assert [r.status for r in report.requirements] == ["NOT_FOUND"] * 3
    assert report.summary.gaps == 7  # one per required source; no correlation gap without records


def test_requirement_without_declared_sources_is_unknown():
    contract = _contract()
    del contract["requirements"][0]["evidence_requirements"]["required_sources"]
    report = build_coverage_report(contract, _full_records(), source_of=_source_of)
    row = _req(report, "req-human-role-1")
    assert row.status == "UNKNOWN"
    assert [g.kind for g in row.gaps] == ["no_sources_declared"]


def test_backfilled_only_source_cannot_clear_committed():
    contract = _contract()
    contract["requirements"][2]["evidence_requirements"]["minimum_assurance"] = ["committed"]
    records = [r for r in _full_records() if _source_of(r) != "review-events"]
    records.append(_backfilled("b1", "review-events"))
    report = build_coverage_report(contract, records, source_of=_source_of, remedies=REMEDIES)
    row = _req(report, "req-human-role-3")
    assert row.status == "INSUFFICIENT"
    (gap,) = row.gaps
    assert gap.kind == "assurance_below_minimum"
    assert gap.remedy.raises_to == "committed"
    # mutant: drop the assurance floor and the same records satisfy
    del contract["requirements"][2]["evidence_requirements"]["minimum_assurance"]
    assert _req(build_coverage_report(contract, records, source_of=_source_of), "req-human-role-3").status == "SATISFIED"


def test_backfilled_duplicate_is_collapsed_not_counted():
    records = _full_records()
    dup = _backfilled("d1", "review-events")
    dup["chain"] = {"relation": "duplicates", "parent_capsule_id": records[4]["capsule_id"]}
    records.append(dup)
    src = next(s for s in _req(build_coverage_report(_contract(), records, source_of=_source_of), "req-human-role-3").sources if s.source == "review-events")
    assert src.record_count == 1
    assert src.duplicates_collapsed == 1
    assert src.backfilled_count == 0


# --- same-producer spans are correlation, not corroboration ----------------


def _independent_contract() -> dict:
    contract = _contract()
    contract["requirements"][2]["evidence_requirements"]["independence"] = "independent-producers"
    return contract


def _one_producer_span_records() -> list[dict]:
    # Ten spans from one exporter, covering all three sources of req-human-role-3.
    records = [_span_record(f"s{i}", src) for i, src in enumerate(["review-events", "override-events", "exception-events"] * 3)]
    records.append(_span_record("s9", "review-events"))
    return records


def test_same_producer_spans_are_correlation_not_corroboration():
    report = build_coverage_report(_independent_contract(), _one_producer_span_records(), source_of=_source_of)
    row = _req(report, "req-human-role-3")
    assert row.independence.required_producers == 2
    assert row.independence.independent_producers == 1
    assert row.independence.correlated_records == 9
    assert row.independence.met is False
    assert row.status == "INSUFFICIENT"
    assert [g.kind for g in row.gaps] == ["correlated_only"]


def test_one_record_from_a_second_producer_corroborates():
    records = _one_producer_span_records()
    records.append(_record("x1", "override-events", operator="review-tool", developer="ui@v2"))
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.independent_producers == 2
    assert row.status == "SATISFIED"


def test_mutant_counting_records_instead_of_producers_would_pass_correlation():
    """Pins that producers, not records, are counted: a producer_of that
    makes every record its own producer (the bug) turns the correlated
    report SATISFIED; the real default keeps it INSUFFICIENT."""
    records = _one_producer_span_records()
    mutant = build_coverage_report(
        _independent_contract(), records, source_of=_source_of, producer_of=lambda r: r["capsule_id"]
    )
    assert _req(mutant, "req-human-role-3").status == "SATISFIED"
    real = build_coverage_report(_independent_contract(), records, source_of=_source_of)
    assert _req(real, "req-human-role-3").status == "INSUFFICIENT"


def test_spans_from_two_services_of_one_exporter_are_still_one_producer():
    records = _one_producer_span_records()
    for r in records[::2]:
        r["model_attestation"]["compute_attestation"][OTEL_BLOCK_KEY]["resource"]["service.name"] = "other-service"
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.independent_producers == 1


def test_real_otel_block_records_are_correlated():
    otel = pytest.importorskip("capsule_emit.otel")
    records = []
    for i, src in enumerate(["review-events", "override-events", "exception-events"]):
        block = otel.build_otel_block(
            "tool.call", trace_id="0" * 31 + "1", span_id=f"{i + 1:016x}", resource_attributes={"service.name": "synthetic"}
        )
        record = _record(f"o{i}", src)
        record["model_attestation"] = {"compute_attestation": {otel.OTEL_BLOCK_KEY: block}}
        records.append(record)
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.independent_producers == 1
    assert row.status == "INSUFFICIENT"


def test_correlation_gap_takes_the_corroboration_remedy():
    remedy = Remedy(connector="system_of_record", raises_to="retrospectively_evidenced")
    report = build_coverage_report(
        _independent_contract(), _one_producer_span_records(), source_of=_source_of, corroboration_remedy=remedy
    )
    (gap,) = _req(report, "req-human-role-3").gaps
    assert gap.remedy == remedy


def test_one_record_answering_two_sources_counts_once():
    contract = _independent_contract()
    record = _record("both", "review-events")
    records = [record]
    report = build_coverage_report(
        contract, records, source_of=lambda r: "review-events" if r is record else None
    )
    assert _req(report, "req-human-role-3").independence.correlated_records == 0


def _unattributed(record: dict) -> dict:
    out = dict(record)
    out.pop("operator", None)
    out.pop("developer", None)
    return out


def test_unattributed_records_count_toward_no_producer():
    a = {"capsule_id": _hex("u1"), "payload": {"source": "x"}}
    assert default_producer_of(a) is None


def test_probe_stripped_attribution_does_not_manufacture_corroboration():
    """The review probe: ten single-producer spans with the attribution
    removed must not become ten independent producers."""
    records = [_unattributed(r) for r in _one_producer_span_records()]
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.independent_producers == 0
    assert row.independence.unattributed_records == 10
    assert row.independence.producer_basis == "none"
    assert row.independence.met is False
    assert row.status == "INSUFFICIENT"
    assert [g.kind for g in row.gaps] == ["unattributed_only"]


def test_unattributed_only_is_insufficient_even_without_independence():
    records = [_unattributed(r) for r in _full_records()]
    row = _req(build_coverage_report(_contract(), records, source_of=_source_of), "req-human-role-1")
    assert row.independence.required_producers == 1
    assert row.status == "INSUFFICIENT"
    assert [g.kind for g in row.gaps] == ["unattributed_only"]


def test_unattributed_records_beside_one_producer_do_not_corroborate():
    records = _one_producer_span_records()[:3] + [_unattributed(r) for r in _one_producer_span_records()[3:]]
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.independent_producers == 1
    assert row.independence.unattributed_records == 7
    assert row.independence.correlated_records == 2
    assert [g.kind for g in row.gaps] == ["correlated_only"]


def _keyed(record: dict, key: str) -> dict:
    return dict(record, key_id=key)


def test_signer_key_is_the_producer_when_every_record_has_one():
    # Two key ids under one asserted operator/developer: two producers by key.
    records = [_keyed(r, _hex("k1") if i % 2 else _hex("k2")) for i, r in enumerate(_one_producer_span_records())]
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.producer_basis == "key"
    assert row.independence.independent_producers == 2
    assert row.status == "SATISFIED"


def test_one_key_under_two_asserted_names_is_one_producer():
    records = [
        _keyed(_record("a", "review-events", operator="op-a"), _hex("k1")),
        _keyed(_record("b", "override-events", operator="op-b"), _hex("k1")),
        _keyed(_record("c", "exception-events", operator="op-c"), _hex("k1")),
    ]
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.producer_basis == "key"
    assert row.independence.independent_producers == 1
    assert row.status == "INSUFFICIENT"


def test_without_keys_the_producer_is_asserted():
    row = _req(build_coverage_report(_contract(), _full_records(), source_of=_source_of), "req-human-role-3")
    assert row.independence.producer_basis == "asserted"


def test_partly_signed_producer_is_not_counted_twice():
    # One producer signs some records and not others: one asserted basis for
    # the whole requirement, so it stays one producer.
    records = _one_producer_span_records()
    records = [_keyed(r, _hex("k1")) if i < 5 else r for i, r in enumerate(records)]
    row = _req(build_coverage_report(_independent_contract(), records, source_of=_source_of), "req-human-role-3")
    assert row.independence.producer_basis == "asserted"
    assert row.independence.independent_producers == 1


def test_caller_producer_of_is_asserted_and_none_means_unattributed():
    records = _one_producer_span_records()
    row = _req(
        build_coverage_report(
            _independent_contract(), records, source_of=_source_of, producer_of=lambda r: None
        ),
        "req-human-role-3",
    )
    assert row.independence.unattributed_records == 10
    assert row.independence.producer_basis == "none"


def test_lowercase_catalog_type_is_accepted_and_carried_uppercase():
    report = build_coverage_report(
        _contract(), _full_records(), source_of=_source_of, source_catalog={"review-events": "observed_event"}
    )
    src = next(s for s in _req(report, "req-human-role-3").sources if s.source == "review-events")
    assert src.epistemic_type == "OBSERVED_EVENT"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 1), ("", 1), ("none", 1), ("3", 3), ("0", 1), ("independent-producers", 2)],
)
def test_required_producers(value, expected):
    assert required_producers(value) == expected


# --- the section in the Result ---------------------------------------------


def _claim(claim_id: str, requirement_ref: str, contract_ref: str = "ec:ai-act-human-oversight:2026-09-21@1") -> Claim:
    ev = (DigestRef(digest=_hex(claim_id)),)
    return Claim(
        id=claim_id,
        contract_ref=contract_ref,
        requirement_ref=requirement_ref,
        tier="recomputed",
        grade="self-attested",
        sufficiency="SATISFIED",
        verdict="met",
        evidence=ev,
        proofs=(ProofRef(kind="inclusion_proof", digest=_hex(f"p-{claim_id}")),),
        presentation=DisclosureCarrier(status="SATISFIED", evidence=ev),
    )


def _result_doc(records=None) -> dict:
    claims = [_claim("c-1", "req-human-role-1"), _claim("c-3", "req-human-role-3")]
    records = _full_records() if records is None else records
    coverage = build_coverage_report(_contract(), records, source_of=_source_of, claims=claims, remedies=REMEDIES)
    return build_result(claims, generated_at="2026-10-01T00:00:00Z", coverage_report=coverage).to_dict()


def test_result_with_coverage_report_validates_and_verifies():
    records = [r for r in _full_records() if _source_of(r) != "override-events"]
    doc = _result_doc(records)
    validate_against_schema(doc)
    verify_result(doc)
    assert doc["coverage_report"]["spec_version"] == "coverage-report/v0"
    assert doc["coverage_report"]["contract_ref"] == "ec:ai-act-human-oversight:2026-09-21@1"
    assert doc["coverage_report"]["summary"] == {
        "requirements": 3, "satisfied": 2, "with_gaps": 1, "gaps": 1, "gaps_without_remedy": 0
    }


def test_result_without_coverage_report_is_unchanged():
    doc = build_result([_claim("c-1", "req-human-role-1")], generated_at="2026-10-01T00:00:00Z").to_dict()
    assert "coverage_report" not in doc


def test_claim_from_another_contract_is_refused():
    with pytest.raises(ResultError):
        build_coverage_report(_contract(), [], source_of=_source_of, claims=[_claim("c-x", "req-human-role-1", "ec:other@1")])


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["summary"].__setitem__("satisfied", 3), id="summary-does-not-recompute"),
        pytest.param(lambda d: d["requirements"][0]["claim_ids"].append("c-missing"), id="claim-id-without-claim"),
        pytest.param(lambda d: d["requirements"][2]["claim_ids"].__setitem__(0, "c-1"), id="claim-for-other-requirement"),
        pytest.param(lambda d: d["requirements"][2].__setitem__("sufficiency", "SATISFIED"), id="sufficiency-not-derived"),
        pytest.param(lambda d: d["requirements"][0]["sources"][0].__setitem__("record_count", 2), id="counts-do-not-add-up"),
        pytest.param(lambda d: d["requirements"][0]["independence"].__setitem__("met", False), id="independence-met-disagrees"),
        pytest.param(
            lambda d: d["requirements"][0]["independence"].update(
                {"required_producers": 2, "independent_producers": 1, "correlated_records": 1}
            ),
            id="correlated-counted-as-met",
        ),
        pytest.param(
            lambda d: d["requirements"][0]["independence"].__setitem__("correlated_records", 5),
            id="producer-counts-do-not-match-records",
        ),
        pytest.param(
            lambda d: d["requirements"][0]["independence"].__setitem__("unattributed_records", 3),
            id="unattributed-count-does-not-match-records",
        ),
        pytest.param(
            lambda d: d["requirements"][0]["independence"].__setitem__("producer_basis", "none"),
            id="basis-none-with-producers",
        ),
    ],
)
def test_verify_rejects_a_tampered_coverage_report(mutate):
    records = [r for r in _full_records() if _source_of(r) != "override-events"]
    doc = _result_doc(records)
    verify_result(doc)
    tampered = copy.deepcopy(doc)
    mutate(tampered["coverage_report"])
    with pytest.raises(ResultError):
        verify_result(tampered)


def test_schema_rejects_satisfied_row_with_a_gap():
    import jsonschema

    doc = _result_doc()
    doc["coverage_report"]["requirements"][0]["gaps"].append(
        {"kind": "missing_source", "source": "x", "detail": "d", "remedy": None}
    )
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(doc)


def test_verifier_limit_counts_moved_to_independent_pass_the_document_check():
    """Documented limit: without producer identities, a hand edit that moves
    correlated records to independent producers passes verify_result. Only
    a recompute catches it -- this pins that the limit is real, so the
    docstring stays honest."""
    records = _one_producer_span_records()
    claims = [_claim("c-3", "req-human-role-3")]
    contract = _independent_contract()
    coverage = build_coverage_report(contract, records, source_of=_source_of, claims=claims)
    doc = build_result(claims, generated_at="2026-10-01T00:00:00Z", coverage_report=coverage).to_dict()
    row = doc["coverage_report"]["requirements"][2]
    row["independence"].update({"independent_producers": 2, "correlated_records": 8, "met": True})
    row["status"], row["sufficiency"], row["gaps"] = "SATISFIED", "SATISFIED", []
    rows = doc["coverage_report"]["requirements"]
    gaps = [g for r in rows for g in r["gaps"]]
    doc["coverage_report"]["summary"] = {
        "requirements": len(rows),
        "satisfied": sum(r["status"] == "SATISFIED" for r in rows),
        "with_gaps": sum(bool(r["gaps"]) for r in rows),
        "gaps": len(gaps),
        "gaps_without_remedy": sum(g["remedy"] is None for g in gaps),
    }
    verify_result(doc)  # passes: the limit
    recomputed = build_coverage_report(contract, records, source_of=_source_of, claims=claims).to_dict()
    assert recomputed["requirements"][2]["independence"]["independent_producers"] == 1


def test_status_to_sufficiency_is_the_fixed_mapping():
    assert STATUS_TO_SUFFICIENCY == {
        "SATISFIED": "SATISFIED", "NOT_FOUND": "GAP", "INSUFFICIENT": "INSUFFICIENT", "UNKNOWN": "UNKNOWN"
    }


# --- optional epistemic_type, from a caller-supplied source catalog --------

CATALOG = {"review-events": "OBSERVED_EVENT", "override-events": "HUMAN_REPORT"}


def test_source_catalog_types_the_named_sources_only():
    report = build_coverage_report(_contract(), _full_records(), source_of=_source_of, source_catalog=CATALOG)
    rows = {s.source: s.to_dict() for s in _req(report, "req-human-role-3").sources}
    assert rows["review-events"]["epistemic_type"] == "OBSERVED_EVENT"
    assert rows["override-events"]["epistemic_type"] == "HUMAN_REPORT"
    assert "epistemic_type" not in rows["exception-events"]


def test_missing_source_keeps_its_declared_type():
    records = [r for r in _full_records() if _source_of(r) != "override-events"]
    report = build_coverage_report(_contract(), records, source_of=_source_of, source_catalog=CATALOG)
    src = next(s for s in _req(report, "req-human-role-3").sources if s.source == "override-events")
    assert src.status == "NOT_FOUND"
    assert src.epistemic_type == "HUMAN_REPORT"


def test_type_never_changes_status():
    with_catalog = build_coverage_report(_contract(), [], source_of=_source_of, source_catalog=CATALOG)
    without = build_coverage_report(_contract(), [], source_of=_source_of)
    assert [r.status for r in with_catalog.requirements] == [r.status for r in without.requirements]


def test_unknown_epistemic_type_in_catalog_is_refused():
    with pytest.raises(ResultError):
        build_coverage_report(_contract(), [], source_of=_source_of, source_catalog={"review-events": "TRUSTED_FACT"})


def test_result_with_typed_sources_validates():
    claims = [_claim("c-3", "req-human-role-3")]
    coverage = build_coverage_report(
        _contract(), _full_records(), source_of=_source_of, claims=claims, source_catalog=CATALOG
    )
    doc = build_result(claims, generated_at="2026-10-01T00:00:00Z", coverage_report=coverage).to_dict()
    validate_against_schema(doc)
    verify_result(doc)


def test_vendored_epistemic_type_enum_matches_engine_values():
    from capsule_engine.packs.schema import EPISTEMIC_TYPE_VALUES
    from capsule_engine.report.result import load_schema

    assert set(load_schema()["$defs"]["EpistemicType"]["enum"]) == EPISTEMIC_TYPE_VALUES
