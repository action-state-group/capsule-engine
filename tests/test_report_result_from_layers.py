# SPDX-License-Identifier: Apache-2.0
"""``result_from_layers``: one Result v0 per layer, built from a
``deal reconcile`` report and per-action layer observations.

``RECONCILE`` below is a TEST FIXTURE written in the shape of the JSON that
``capsulectl deal reconcile`` prints at tag v0.1.0-rc6 (internal/cli/
deal_reconcile.go, ``reconcileDeals``, unchanged since rc5). It is not the
output of any run: the ids, times and digests are made up, and the
``cannot_see`` lines are copied from that version's constants so the shape
is exact.
"""
from __future__ import annotations

import copy

import pytest

from capsule_engine.report.errors import (
    COVERAGE_NOT_COMPUTED,
    INVALID_LAYER_TALLY,
    INVALID_VERDICT,
    ResultError,
)
from capsule_engine.report.result import validate_against_schema, verify_result
from capsule_engine.report.result_from_layers import (
    ActionLayer,
    LayerEntry,
    LayerObservation,
    LayerSpec,
    LayerTallyDoc,
    build_layer_tally,
    render_layer_tally,
    verify_layer_tally,
)

CONTRACT_REF = "ec:example-layers:2026-10-04@1"
GENERATED_AT = "2026-10-04T23:00:00Z"
TOOL = {"tool_name": "capsulectl", "tool_version": "v0.1.0-rc6"}
JUDGE_PIN = "a" * 64
EVIDENCE = "b" * 64
# Stands in for the SHA-256 of the executions file reconcile read.
EXECUTIONS = "e" * 64

CANNOT_SEE = [
    "Only the execution records in the file it was given: a session, side conversation or sub-task whose records were not exported is not seen.",
    "Anything the agent host summarised away or never wrote to its execution records is not seen.",
    "A failed or ambiguous attempt that left no local trace is not seen.",
    'Which tool calls are consequential is decided by the reader that produced the file; a call it mapped to "other" is not checked.',
    "Matching uses the deal id, or the action, amount, currency, merchant domain and time; it does not see the merchant's or the payment provider's records.",
    "It runs on the agent's own machine, over the agent host's own records: it can list actions that have no deal record, but it cannot prove that nothing else happened.",
    "Authority is not established: the user's authority for each action cannot be read from the agent host's records. Host approval ids, where given, are the host's own references, not a record of who approved or under what authority.",
    "The agent host's history of approvals was not available, so whether the user approved each action on the host is not shown.",
]


def _row(rec_id: str, action: str = "pay", **extra: object) -> dict[str, object]:
    """One reconcile output row: raw JSON, the shape the adapter parses."""
    row = {"id": rec_id, "at": "2026-10-04T18:00:00Z", "tool": "make_payment", "action": action, "status": "succeeded"}
    row.update(extra)
    return row


# a1 recorded by an act, a2 recorded by a check, a3 never recorded (no
# check invoked), a4 a failed attempt. 2 calls were not consequential and 1
# fell outside the period: records_read = 4 consequential-or-failed + 2.
RECONCILE: dict = {
    "scope": "This pass covers the execution records it was given, for this period. It is not a record of everything the agent did.",
    "period": {"from": "2026-10-03T23:00:00Z", "to": "2026-10-04T23:00:00Z"},
    "summary": "1 of 3 consequential actions have no deal record (6 execution records read for 2026-10-03T23:00:00Z to 2026-10-04T23:00:00Z). This lists what is missing from the records read; it cannot prove that nothing else happened.",
    "records_read": 6,
    "outside_period": 1,
    "not_consequential": 2,
    "consequential": 3,
    "recorded": [
        _row("a1", deal_id="d1", matched_by="act", step=3, capsule_id="c" * 64),
        _row("a2", deal_id="d2", matched_by="check", step=2, capsule_id="not-a-digest"),
    ],
    "unrecorded": [_row("a3")],
    "failed_attempts": [_row("a4", status="failed")],
    "coverage": {
        "reads": "the agent host's execution records (tool calls of the agent and its sub-tasks), not the conversation",
        "host_approvals": {"available": False, "read": 0},
        "authority": "not established",
        "cannot_see": CANNOT_SEE,
    },
}

INVOKED = LayerSpec("invoked", "layer:invoked", "recomputed", self_reported=True)
EXTRACTED = LayerSpec("extracted", "layer:extracted", "judged", gate="invoked", judge_pin=JUDGE_PIN)
OBEYED = LayerSpec("obeyed", "layer:obeyed", "recomputed", gate="invoked")
# Two further layers with opaque ids: the adapter takes layer ids as data.
LAYER_4 = LayerSpec("layer-4", "layer:4", "recomputed", gate="obeyed")
LAYER_5 = LayerSpec("layer-5", "layer:5", "recomputed", gate="invoked")
LAYERS = [EXTRACTED, OBEYED, LAYER_4, LAYER_5]


def _obs(action_id: str, layer_id: str, verdict: str = "met", sufficiency: str = "SATISFIED") -> LayerObservation:
    return LayerObservation(action_id, layer_id, "self-attested", sufficiency, verdict, (EVIDENCE,))


OBSERVATIONS = [
    _obs("a1", "extracted"),
    _obs("a2", "extracted", "not_met"),
    _obs("a1", "obeyed"),
    _obs("a1", "layer-4", "not_evaluable", "INSUFFICIENT"),
    _obs("a1", "layer-5"),
]
# a2 got no disposition back, so the obeyed layer did not apply to it.
NOT_APPLICABLE = [ActionLayer("a2", "obeyed")]


def _tally(**overrides: object) -> LayerTallyDoc:
    kwargs: dict = {
        "reconcile": RECONCILE,
        "invocation": INVOKED,
        "layers": LAYERS,
        "observations": OBSERVATIONS,
        "contract_ref": CONTRACT_REF,
        "generated_at": GENERATED_AT,
        "not_applicable": NOT_APPLICABLE,
        "executions_sha256": EXECUTIONS,
        **TOOL,
    }
    kwargs.update(overrides)
    return build_layer_tally(**kwargs).to_dict()


def _layer(doc: LayerTallyDoc, layer_id: str) -> LayerEntry:
    return next(e for e in doc["layers"] if e["layer"] == layer_id)


def test_five_layers_five_results_each_valid_and_verified():
    doc = _tally()
    assert [e["layer"] for e in doc["layers"]] == ["invoked", "extracted", "obeyed", "layer-4", "layer-5"]
    claim_ids: list[str] = []
    for entry in doc["layers"]:
        validate_against_schema(entry["result"])
        verify_result(entry["result"])
        claim_ids += [c["id"] for c in entry["result"]["claims"]]
    assert len(claim_ids) == len(set(claim_ids)), "no claim is shared across layers"
    verify_layer_tally(doc)


def test_date_and_version_are_in_the_artifact():
    doc = _tally()
    assert doc["generated_at"] == GENERATED_AT
    assert doc["tool"] == {"name": "capsulectl", "version": "v0.1.0-rc6"}
    assert all(e["result"]["generated_at"] == GENERATED_AT for e in doc["layers"])
    with pytest.raises(ResultError) as exc:
        _tally(tool_version="")
    assert exc.value.reason == INVALID_LAYER_TALLY


def test_never_invoked_is_not_met_on_invoked_and_excluded_on_extracted():
    doc = _tally()
    invoked = _layer(doc, "invoked")
    extracted = _layer(doc, "extracted")
    assert invoked["result"]["aggregate"]["buckets"]["not_met"] == ["invoked:a3"]
    assert "extracted:a3" not in {c["id"] for c in extracted["result"]["claims"]}
    # a3 (not_met) and a4 (failed, unsettled -> not an exclusion) at the gate:
    # only a3 is excluded on extracted.
    assert extracted["coverage"]["excluded_not_applicable"] == 1
    for entry in doc["layers"]:
        assert not any(cid.endswith(":a3") for cid in entry["result"]["aggregate"]["buckets"]["met"])


def test_invocation_layer_reads_reconcile_rows():
    invoked = _layer(_tally(), "invoked")
    buckets = invoked["result"]["aggregate"]["buckets"]
    assert buckets == {"met": ["invoked:a1", "invoked:a2"], "not_met": ["invoked:a3"], "not_evaluable": ["invoked:a4"]}
    # not_consequential is the exclusion; a failed attempt is UNKNOWN, never met.
    assert invoked["coverage"] == {"evaluated_population": 4, "excluded_not_applicable": 2, "unknown_count": 1}
    claims = {c["id"]: c for c in invoked["result"]["claims"]}
    assert claims["invoked:a1"]["evidence"][2]["digest"] == "c" * 64
    assert len(claims["invoked:a2"]["evidence"]) == 2, "a non-digest capsule_id is covered by the row digest only"
    assert all(c["grade"] == "self-attested" and c["tier"] == "recomputed" for c in claims.values())


def test_invocation_claims_cite_the_executions_file_first():
    doc = _tally()
    assert doc["source"]["executions_sha256"] == EXECUTIONS
    claims = _layer(doc, "invoked")["result"]["claims"]
    assert all(c["evidence"][0]["digest"] == EXECUTIONS for c in claims)
    with pytest.raises(ResultError):
        _tally(executions_sha256="not-a-digest")
    with pytest.raises(TypeError, match="executions_sha256"):  # required, no default
        build_layer_tally(
            RECONCILE, INVOKED, LAYERS, OBSERVATIONS, contract_ref=CONTRACT_REF, generated_at=GENERATED_AT, **TOOL
        )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda doc: doc["source"].__setitem__("executions_sha256", "f" * 64), id="source-swapped"),
        pytest.param(lambda doc: doc["source"].pop("executions_sha256"), id="source-removed"),
        pytest.param(
            lambda doc: _layer(doc, "invoked")["result"]["claims"][0]["evidence"].pop(0), id="claim-stops-citing"
        ),
    ],
)
def test_tally_whose_invocation_claims_do_not_cite_the_executions_file_does_not_verify(mutate):
    doc = _tally()
    mutate(doc)
    with pytest.raises(ResultError) as exc:
        verify_layer_tally(doc)
    assert exc.value.reason == INVALID_LAYER_TALLY


def test_executions_digest_is_checked_when_no_claim_cites_it():
    """With no consequential action there is no invocation claim to carry
    the digest, so it is checked on its own, at build and at verify."""
    empty = copy.deepcopy(RECONCILE)
    empty.update(recorded=[], unrecorded=[], failed_attempts=[], consequential=0, records_read=2)
    with pytest.raises(ResultError):
        _tally(reconcile=empty, observations=[], not_applicable=[], executions_sha256="not-a-digest")
    doc = _tally(reconcile=empty, observations=[], not_applicable=[])
    assert _layer(doc, "invoked")["result"] is None
    verify_layer_tally(doc)
    doc["source"]["executions_sha256"] = "not-a-digest"
    with pytest.raises(ResultError, match="executions_sha256") as exc:
        verify_layer_tally(doc)
    assert exc.value.reason == INVALID_LAYER_TALLY


# A hold-style layer: its population is the agent's own sealed reports, so it
# is a lower bound like invocation, tamper-evident but still self-report.
HOLD = LayerSpec("hold", "layer:hold", "recomputed", gate="invoked", self_reported=True)
HOLD_OBSERVATIONS = [*OBSERVATIONS, _obs("a1", "hold"), _obs("a2", "hold", "not_met")]


def _tally_with_hold(**overrides: object) -> LayerTallyDoc:
    return _tally(layers=[*LAYERS, HOLD], observations=HOLD_OBSERVATIONS, **overrides)


@pytest.mark.parametrize("layer_id", ["invoked", "hold"])
def test_self_reported_layer_is_never_graded_witnessed(layer_id):
    doc = _tally_with_hold()
    entry = _layer(doc, layer_id)
    assert entry["self_reported"] is True
    assert entry["result"]["claims"]
    assert all(c["grade"] == "self-attested" for c in entry["result"]["claims"])
    verify_layer_tally(doc)
    # Mutant: one claim relabelled witnessed in the document. Still valid Result v0.
    entry["result"]["claims"][0]["grade"] = "witnessed"
    validate_against_schema(entry["result"])
    with pytest.raises(ResultError, match="self-reported layer and graded 'witnessed'"):
        verify_layer_tally(doc)


def test_self_reported_layer_refuses_a_witnessed_observation():
    witnessed = LayerObservation("a1", "hold", "witnessed", "SATISFIED", "met", (EVIDENCE,))
    with pytest.raises(ResultError, match="must be self-attested"):
        _tally(layers=[*LAYERS, HOLD], observations=[*OBSERVATIONS, witnessed])
    with pytest.raises(ResultError, match="self-reported"):
        _tally(invocation=LayerSpec("invoked", "layer:invoked", "recomputed"))


def test_gate_unsettled_and_missing_observation_are_not_evaluable():
    extracted = _layer(_tally(), "extracted")
    claims = {c["id"]: c for c in extracted["result"]["claims"]}
    # a4's gate is not_evaluable: whether extracted applied is unsettled.
    assert claims["extracted:a4"]["verdict"] == "not_evaluable"
    assert claims["extracted:a4"]["sufficiency"] == "UNKNOWN"
    layer5 = _layer(_tally(), "layer-5")
    # a2 reached layer-5 and nothing was observed: not_evaluable, not met.
    assert "layer-5:a2" in layer5["result"]["aggregate"]["buckets"]["not_evaluable"]


def test_observed_but_unsettled_is_not_evaluable_not_pass_not_exclusion():
    layer4 = _layer(_tally(), "layer-4")
    # a1 was observed and not settled; a4's gate (obeyed) was itself unsettled.
    assert layer4["result"]["aggregate"]["buckets"] == {"met": [], "not_met": [], "not_evaluable": ["layer-4:a1", "layer-4:a4"]}
    # a2 (obeyed did not apply) and a3 (never invoked) are excluded.
    assert layer4["coverage"]["excluded_not_applicable"] == 2
    claim = layer4["result"]["claims"][0]
    assert claim["sufficiency"] == "INSUFFICIENT"
    assert claim["presentation"] == {
        "kind": "analysis",
        "status": "INSUFFICIENT",
        "summary": "the layer applied and the record did not settle it (sufficiency INSUFFICIENT)",
    }


def test_retired_spelling_is_refused():
    with pytest.raises(ResultError) as exc:
        _tally(observations=[*OBSERVATIONS[:-1], _obs("a1", "layer-5", "insufficient_evidence", "INSUFFICIENT")])
    assert exc.value.reason == INVALID_VERDICT


def test_judged_layer_carries_its_pin_and_is_never_recomputed():
    extracted = _layer(_tally(), "extracted")
    assert extracted["tier"] == "judged" and extracted["judge_pin"] == JUDGE_PIN
    assert all(c["tier"] == "judged" and c["evidence"][0]["digest"] == JUDGE_PIN for c in extracted["result"]["claims"])
    with pytest.raises(ResultError):
        LayerSpec("extracted", "layer:extracted", "judged", gate="invoked")
    with pytest.raises(ResultError):
        LayerSpec("invoked", "layer:invoked", "recomputed", judge_pin=JUDGE_PIN)
    # Mutant: relabel the judged layer recomputed in the document.
    doc = _tally()
    _layer(doc, "extracted")["tier"] = "recomputed"
    with pytest.raises(ResultError) as exc:
        verify_layer_tally(doc)
    assert exc.value.reason == INVALID_LAYER_TALLY
    # Mutant: relabel the layer and every one of its claims, consistently.
    doc = _tally()
    extracted = _layer(doc, "extracted")
    extracted["tier"] = "recomputed"
    for claim in extracted["result"]["claims"]:
        claim["tier"] = "recomputed"
    with pytest.raises(ResultError, match="recomputed and carries a judge pin"):
        verify_layer_tally(doc)


def test_zero_denominator_is_stated_never_a_pass():
    # Nothing was recorded, so no action reaches any gated layer.
    reconcile = copy.deepcopy(RECONCILE)
    reconcile["unrecorded"] = reconcile["recorded"] + reconcile["unrecorded"]
    for row in reconcile["unrecorded"]:
        for key in ("deal_id", "matched_by", "step", "capsule_id"):
            row.pop(key, None)
    reconcile["recorded"] = []
    reconcile["failed_attempts"] = []
    reconcile["records_read"] = 5
    doc = _tally(reconcile=reconcile, observations=[], not_applicable=[])
    extracted = _layer(doc, "extracted")
    assert extracted["result"] is None
    assert extracted["coverage"] == {"evaluated_population": 0, "excluded_not_applicable": 3, "unknown_count": 0}
    verify_layer_tally(doc)
    line = next(ln for ln in render_layer_tally(doc) if ln.startswith("extracted:"))
    assert line.startswith("extracted: 0 evaluated (denominator 0), no result · 3 excluded as not applicable")
    assert "met of" not in line and "pass" not in line.lower()
    assert GENERATED_AT in line and "v0.1.0-rc6" in line
    # Mutant: a null result claiming a non-zero population is refused.
    extracted["coverage"]["evaluated_population"] = 3
    with pytest.raises(ResultError) as exc:
        verify_layer_tally(doc)
    assert exc.value.reason == COVERAGE_NOT_COMPUTED


@pytest.mark.parametrize("where", ["aggregate", "entry", "both"])
def test_hand_set_evaluated_population_is_rejected(where):
    doc = _tally()
    entry = _layer(doc, "invoked")
    if where in ("aggregate", "both"):
        entry["result"]["aggregate"]["coverage"]["evaluated_population"] += 1
    if where in ("entry", "both"):
        entry["coverage"]["evaluated_population"] += 1
    with pytest.raises(ResultError) as exc:
        verify_layer_tally(doc)
    assert exc.value.reason == COVERAGE_NOT_COMPUTED


def test_verify_result_alone_does_not_recount_coverage():
    """Why the tally verifier owns the recount: Result v0's own cross-element
    checks cover ids and buckets, not the coverage counts."""
    doc = _tally()
    result = _layer(doc, "invoked")["result"]
    result["aggregate"]["coverage"]["evaluated_population"] += 1
    verify_result(result)
    validate_against_schema(result)


def test_cannot_see_is_carried_and_rendered():
    doc = _tally()
    assert doc["source"]["coverage"]["cannot_see"] == CANNOT_SEE
    lines = render_layer_tally(doc)
    assert lines[lines.index("cannot see:") + 1 :] == [f"- {gap}" for gap in CANNOT_SEE]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda cov: cov.pop("cannot_see"), id="missing"),
        pytest.param(lambda cov: cov.__setitem__("cannot_see", []), id="empty"),
        pytest.param(lambda cov: cov.__setitem__("cannot_see", "one gap"), id="not-a-list"),
        pytest.param(lambda cov: cov.__setitem__("cannot_see", [*CANNOT_SEE, 7]), id="non-string-item"),
        pytest.param(lambda cov: cov.__setitem__("cannot_see", [*CANNOT_SEE, ""]), id="empty-string-item"),
    ],
)
def test_reconcile_report_without_cannot_see_is_refused(mutate):
    reconcile = copy.deepcopy(RECONCILE)
    mutate(reconcile["coverage"])
    with pytest.raises(ResultError, match="cannot_see") as exc:
        _tally(reconcile=reconcile)
    assert exc.value.reason == INVALID_LAYER_TALLY


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda doc: doc["source"]["coverage"].pop("cannot_see"), id="removed"),
        pytest.param(lambda doc: doc["source"]["coverage"].__setitem__("cannot_see", []), id="emptied"),
        pytest.param(lambda doc: doc["source"].pop("coverage"), id="coverage-removed"),
    ],
)
def test_tally_without_cannot_see_does_not_verify(mutate):
    doc = _tally()
    verify_layer_tally(doc)
    mutate(doc)
    with pytest.raises(ResultError, match="cannot_see") as exc:
        verify_layer_tally(doc)
    assert exc.value.reason == INVALID_LAYER_TALLY


def test_render_states_each_layer_denominator():
    lines = render_layer_tally(_tally())
    assert lines[0] == (
        "invoked: 2 met of 4 evaluated · 1 not met · 1 not evaluable (1 unknown) · "
        "2 excluded as not applicable · recomputed · 2026-10-04T23:00:00Z · capsulectl v0.1.0-rc6"
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.__setitem__("consequential", 4), "consequential"),
        (lambda r: r.__setitem__("records_read", 7), "records_read"),
        (lambda r: r["recorded"][0].pop("matched_by"), "matched_by"),
        (lambda r: r["unrecorded"].append(_row("a1")), "more than one row"),
    ],
)
def test_inconsistent_reconcile_report_is_refused(mutate, message):
    reconcile = copy.deepcopy(RECONCILE)
    mutate(reconcile)
    if message == "more than one row":
        reconcile["consequential"] = 4
        reconcile["records_read"] = 7
    with pytest.raises(ResultError, match=message):
        _tally(reconcile=reconcile)


def test_observation_contradicting_its_gate_is_refused():
    with pytest.raises(ResultError, match="did not reach it"):
        _tally(observations=[*OBSERVATIONS, _obs("a3", "extracted")])
    with pytest.raises(ResultError, match="unsettled"):
        _tally(observations=[*OBSERVATIONS, _obs("a4", "extracted")])
    with pytest.raises(ResultError, match="never from an observation"):
        _tally(observations=[*OBSERVATIONS, _obs("a3", "invoked")])
    with pytest.raises(ResultError, match="earlier layer"):
        _tally(layers=[LAYER_4, EXTRACTED, OBEYED, LAYER_5])
