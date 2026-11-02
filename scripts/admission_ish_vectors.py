#!/usr/bin/env python3
"""Write conformance-vectors/admission/v0/inference-service-history.json, the
shared vectors for InferenceServiceHistoryCounts (go/admission) and
inference_service_history_counts (rust/capsule-engine admission),
capsule-engine#273.

Inputs, each fetched by the caller at the commit the source line names:
  --aac   agent-action-capsule vectors/inference-service-history-v0-interop-v1/vectors.json
  --mesh  Mesh-LLM/capsules-plugin crates/evidence-plugin/tests/fixtures/service_history_reply.json
  --mesh-definition  Mesh-LLM/capsules-plugin derivations/service-history-1.json

Every case gives the definition's and the artifact's JCS bytes, the
checkpoints (time and records covered) the caller reads from the embedded
history_card/1, and the requester's expected_pin. expected is the served
periods read, or null when the answer counts no served exchanges; want is a
substring of the refusal Go and Rust both give. The AAC cases' expected
values follow that corpus's own History properties: a case it accepts reads,
and a case it fails on a property is refused naming that property. The
Mesh card's checkpoints are COSE, so their times and sizes are read here with
cbor2 (the caller's card verifier reads them in a real answer); a COSE
checkpoint's log_size is the MMR size, which gives the records covered.

  uv run --with cbor2 --with rfc8785 python -I scripts/admission_ish_vectors.py --aac ... --mesh ... --mesh-definition ...
"""
import argparse
import base64
import copy
import hashlib
import json

import cbor2
import rfc8785

AAC_COMMIT = "883fcd1b"
MESH_COMMIT = "2120323927d2177f8085eb86c3c50918aa2fb70d"
# The cell card.referee_verdicts reads (capsule-engine#276, vector-only: ISH SH-12).
CORROBORATED = {"verdict": "corroborated"}
OUT = "conformance-vectors/admission/v0/inference-service-history.json"


def jcs(v):
    return rfc8785.dumps(v).decode()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def b64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def served(defn, art):
    """The served periods an accepted answer reads (the evaluator's view)."""
    units = [u["unit"] for m in defn["members"] if m["name"] == "period_counts" for u in m["units"]]

    def totals(unit, dimension=None):
        out = []
        for p in art["period_counts"]["periods"]:
            total = next((c for c in p["cells"] if c["unit"] == unit and c["dimension"] == dimension), None)
            entry = {"from": p["from"], "to": p["to"], "total": total["value"] if total else None}
            if total and total["value"] is None:
                entry["suppressed"] = total["suppressed"]
            out.append(entry)
        return out
    pol = defn["period_policy"]
    out = {"length_s": pol["length_s"], "floor": pol["floor"],
           "periods": totals("exchanges_served") if "exchanges_served" in units else []}
    if "referee_verdicts" in units:
        out["verdicts"] = totals("referee_verdicts", CORROBORATED)
        if "exchanges_served" not in units:
            out["verdicts_only"] = True
    return out


def leaves(mmr_size):
    """The leaf count n of an MMR of mmr_size nodes: 2n - popcount(n)."""
    n = next(n for n in range(mmr_size + 1) if 2 * n - bin(n).count("1") == mmr_size)
    return n


PROPERTIES = ("definition_form", "definition_binding", "artifact_form", "anchor_match", "coverage_declaration_form",
              "coverage_declaration_anchor", "units", "labels", "period_alignment", "period_anchor", "period_floor")


def case(name, defn, art, checkpoints, pin, expected=None, want=None, definition_text=None, artifact_text=None,
         prop=None, min_freshness=None):
    c = {"case": name,
         "definition": definition_text if definition_text is not None else jcs(defn),
         "artifact": artifact_text if artifact_text is not None else jcs(art),
         "card": {"checkpoints": checkpoints}, "pin": pin}
    if min_freshness is not None:
        # The requester's min_freshness ask: {"size": records} or {"at": time}. A case with none
        # and an empty pin has no ask at all.
        c["min_freshness"] = min_freshness
    if want is None:
        c["expected"] = expected
    else:
        # The property the refusal names: the sealed block records it, so
        # Go and Rust must name the same one.
        prop = prop or want.split(":")[0]
        assert prop in PROPERTIES, (name, prop)
        c["want"], c["property"] = want, prop
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aac", required=True)
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--mesh-definition", required=True)
    a = ap.parse_args()
    cases = []

    # The Mesh plugin's own reply fixture: its real service_history/1.
    mdef = json.load(open(a.mesh_definition))
    reply = json.load(open(a.mesh))
    mart = json.loads(b64(reply["artifact"]))
    assert sha(jcs(mdef)) == mart["derivation"]
    mcps = []
    for e in mart["history_card"]["history"]:
        p = cbor2.loads(cbor2.loads(bytes.fromhex(e["checkpoint"])).value[2])
        mcps.append({"at": p["issued_at"], "records": leaves(p["log_size"])})
    assert mcps[-1]["records"] == mart["coverage_declaration"]["live_set"][0]["count"]
    mpin = mart["anchor"]
    cases.append(case("mesh-service-history-1", mdef, mart, mcps, mpin, expected=served(mdef, mart)))

    def mutant(name, want=None, edit_def=None, edit_art=None, rebind=True, accept=False, prop=None, **kw):
        d, x = copy.deepcopy(mdef), copy.deepcopy(mart)
        if edit_def:
            edit_def(d)
            if rebind:
                x["derivation"] = sha(jcs(d))
        if edit_art:
            edit_art(x)
        args = dict(checkpoints=mcps, pin=mpin)
        args.update(kw)
        expected = None
        if accept:
            expected = served(d, x) if any(u["unit"] in ("exchanges_served", "referee_verdicts")
                                            for u in d["members"][1]["units"]) else None
        cases.append(case(name, d, x, args["checkpoints"], args["pin"], expected=expected, want=want, prop=prop,
                          min_freshness=args.get("min_freshness"),
                          definition_text=args.get("definition_text"), artifact_text=args.get("artifact_text")))

    mutant("mesh-unknown-profile", "definition_form: profile not known",
           edit_def=lambda d: d.update(profile="inference-service-history/1"))
    mutant("mesh-detailed-level", "definition_form: detailed level not read",
           edit_def=lambda d: d.update(level="detailed"))
    mutant("mesh-other-counts-member", "counts shape not known", prop="definition_form",
           edit_def=lambda d: d["members"][1].update(name="weekly_counts"))
    mutant("mesh-unregistered-unit", "units:",
           edit_def=lambda d: d["members"][1]["units"][0].update(unit="exchanges"))
    mutant("mesh-definition-not-bound", "definition_binding",
           edit_def=lambda d: d.update(spec=d["spec"] + "#changed"), rebind=False)
    mutant("mesh-stale-or-replayed-pin", "anchor_match", pin=sha("another checkpoint"))
    # The requester's other coverage asks (Evidence Request §3.2): the anchor must meet a min_freshness, and
    # the ask is exactly one of a pin, a size and a time. The Mesh card's anchor covers 30 records, issued at
    # 2026-01-19T00:00:00Z.
    mutant("mesh-min-freshness-size-met", accept=True, pin="", min_freshness={"size": 30})
    mutant("mesh-min-freshness-time-met", accept=True, pin="", min_freshness={"at": "2026-01-19T00:00:00Z"})
    mutant("mesh-min-freshness-size-unmet", "anchor_match: the answer's anchor covers fewer records",
           pin="", min_freshness={"size": 31})
    mutant("mesh-min-freshness-time-unmet", "anchor_match: the answer's anchor was issued before",
           pin="", min_freshness={"at": "2026-01-19T00:00:01Z"})
    mutant("mesh-min-freshness-negative-size", "anchor_match: the ask is not exactly one", pin="",
           min_freshness={"size": -1})
    mutant("mesh-pin-and-min-freshness", "anchor_match: the ask is not exactly one", min_freshness={"size": 1})
    mutant("mesh-two-min-freshness-forms", "anchor_match: the ask is not exactly one", pin="",
           min_freshness={"size": 1, "at": "2026-01-01T00:00:00Z"})
    mutant("mesh-no-ask", "anchor_match: the ask is not exactly one", pin="")
    mutant("mesh-interval-not-to-anchor", "coverage_declaration_anchor",
           edit_art=lambda x: x["coverage_declaration"]["interval"].update(to=sha("elsewhere")))
    mutant("mesh-live-set-other-count", "coverage_declaration_form",
           checkpoints=mcps[:-1] + [dict(mcps[-1], records=mcps[-1]["records"] + 1)])
    mutant("mesh-card-digest-mismatch", "artifact_form: the card does not match history_card_digest",
           edit_art=lambda x: x.update(history_card_digest=sha("another card")))
    mutant("mesh-count-labelled-peer-confirmed", "labels",
           edit_art=lambda x: x["period_counts"]["periods"][1]["cells"][2].update(label="peer_confirmed"))
    mutant("mesh-cell-below-floor", "period_floor",
           edit_art=lambda x: x["period_counts"]["periods"][1]["cells"][0].update(value=4))
    mutant("mesh-anchor-not-a-boundary", "period_anchor",
           checkpoints=mcps[:-1] + [dict(mcps[-1], at="2026-01-19T00:00:01Z")])
    mutant("mesh-week-left-out", "period_alignment",
           edit_art=lambda x: x["period_counts"]["periods"].pop(0))
    mutant("mesh-definition-not-jcs", "definition_form: the definition is not JCS bytes",
           definition_text=json.dumps(mdef))
    mutant("mesh-requested-cell-not-declared", "units:",
           edit_art=lambda x: x["period_counts"]["periods"][1]["cells"].append(
               {"dimension": None, "label": "self_reported", "unit": "exchanges_requested", "value": 7}))

    # Go's encoding/json reads null as "" where Rust refuses it; each is
    # refused in both.
    def member(i, **kv):
        return lambda d: d["members"][i].update(kv)
    mutant("mesh-null-spec", "definition_form", edit_def=lambda d: d.update(spec=None))
    mutant("mesh-null-unit-where", "definition_form", edit_def=lambda d: d["members"][1]["units"][0].update(where=None))
    mutant("mesh-null-counts-derivation", "definition_form", edit_def=member(1, derivation=None))
    mutant("mesh-null-in-excludes", "definition_form",
           edit_def=lambda d: d["excludes"].append(None))

    def null_reads(d):
        d["members"][1]["reads"] = [None]
        d["reads"] = d["members"][0]["reads"] + [None]
    mutant("mesh-null-in-reads", "definition_form", edit_def=null_reads)
    mutant("mesh-null-model", "counts shape not known: cells[0].dimension", prop="artifact_form",
           edit_art=lambda x: x["period_counts"]["periods"][1]["cells"][0].update(dimension={"model": None}))
    mutant("mesh-null-declared", "artifact_form", edit_art=lambda x: x.update(declared=None))

    # The card's checkpoints, and which of them commits the first record.
    mutant("mesh-checkpoints-go-back", "period_alignment", checkpoints=[mcps[1], mcps[0]] + mcps[2:])
    early = {"at": "2025-12-20T00:00:00Z", "records": 0}
    mutant("mesh-empty-first-checkpoint", accept=True, checkpoints=[early] + mcps)

    def leading_week(x):
        x["period_counts"]["periods"].insert(0, {"from": "2025-12-29T00:00:00Z", "to": "2026-01-05T00:00:00Z", "cells": [
            {"dimension": None, "label": "self_reported", "suppressed": "below_floor", "unit": "exchanges_served",
             "value": None}]})
    mutant("mesh-week-before-the-first-record", "period_alignment", edit_art=leading_week,
           checkpoints=[{"at": "2026-01-01T00:00:00Z", "records": 0}] + mcps)
    mutant("mesh-no-record-lists-no-week", "period_alignment",
           checkpoints=[dict(c, records=0) for c in mcps],
           edit_art=lambda x: x["coverage_declaration"]["live_set"][0].update(count=0))

    # Paths both readers take with no other case.
    def week1_total_suppressed(x):
        x["period_counts"]["periods"][0]["cells"][2].update(value=None, suppressed="below_floor")
    mutant("mesh-suppressed-total", accept=True, edit_art=week1_total_suppressed)
    mutant("mesh-total-without-models", accept=True,
           edit_art=lambda x: x["period_counts"]["periods"][1].update(cells=x["period_counts"]["periods"][1]["cells"][2:]))

    def requested_only(d):
        d["members"][1]["units"][0]["unit"] = "exchanges_requested"

    def requested_cells(x):
        for p in x["period_counts"]["periods"]:
            for c in p["cells"]:
                c["unit"] = "exchanges_requested"
    mutant("mesh-requested-only-reads-no-served", accept=True, edit_def=requested_only, edit_art=requested_cells)

    # Referee verdicts (capsule-engine#276): a unit of its own, read beside
    # the served totals and never added to them. The row reads one class, the
    # cell whose dimension is {"verdict": "corroborated"}; the classes are
    # never added, so a week of contradicted verdicts alone reads as none.
    def verdict_cells(total, **classes):
        cells = []
        for name, v in classes.items():
            cell = {"dimension": {"verdict": name}, "label": "self_reported", "unit": "referee_verdicts", "value": v}
            if v is None:
                cell["suppressed"] = "below_floor"
            cells.append(cell)
        tcell = {"dimension": None, "label": "self_reported", "unit": "referee_verdicts", "value": total}
        if total is None:
            tcell["suppressed"] = "below_floor"
        return cells + [tcell]

    def with_verdicts(*weeks):
        def edit(x):
            for pr, w in zip(x["period_counts"]["periods"], weeks, strict=True):
                pr["cells"].extend(verdict_cells(*w[0], **w[1]))
        return edit

    def declare_verdicts(d):
        d["members"][1]["units"].append({"unit": "referee_verdicts", "where": "periods[].cells[]"})
    mutant("mesh-with-referee-verdicts", accept=True, edit_def=declare_verdicts,
           edit_art=with_verdicts(((12,), {"corroborated": 7, "contradicted": 5}), ((9,), {"corroborated": 9})))
    mutant("mesh-referee-verdict-week-suppressed", accept=True, edit_def=declare_verdicts,
           edit_art=with_verdicts(((None,), {"corroborated": None}), ((9,), {"corroborated": 9})))
    mutant("mesh-referee-verdict-only-contradicted", accept=True, edit_def=declare_verdicts,
           edit_art=with_verdicts(((12,), {"contradicted": 12}), ((9,), {"corroborated": 9})))
    mutant("mesh-referee-verdict-below-floor", "period_floor", edit_def=declare_verdicts,
           edit_art=with_verdicts(((4,), {"corroborated": 4}), ((9,), {"corroborated": 9})))

    def verdicts_only_def(d):
        d["members"][1]["units"][0]["unit"] = "referee_verdicts"

    def verdicts_only_art(x):
        for pr in x["period_counts"]["periods"]:
            for c in pr["cells"]:
                c["unit"] = "referee_verdicts"
            pr["cells"][0]["dimension"] = {"verdict": "corroborated"}
    mutant("mesh-referee-verdicts-only", accept=True, edit_def=verdicts_only_def, edit_art=verdicts_only_art)

    # Arrays are read one element at a time in both, so the first bad
    # element names the property; a counterparty's member name never does.
    def bad_cell_then_number(x):
        cells = x["period_counts"]["periods"][1]["cells"]
        cells[0]["label"] = "peer_confirmed"
        cells.append(5)
    mutant("mesh-bad-cell-before-a-number", "labels", edit_art=bad_cell_then_number)

    def bad_unit_then_number(d):
        d["members"][1]["units"][0]["unit"] = "bogus"
        d["members"].append(7)
    mutant("mesh-bad-unit-before-a-number-member", "units:", edit_def=bad_unit_then_number)

    def bad_unit_then_number_unit(d):
        d["members"][1]["units"][0]["unit"] = "bogus"
        d["members"][1]["units"].append(7)
    mutant("mesh-bad-unit-before-a-number-unit", "units:", edit_def=bad_unit_then_number_unit)

    def member_named_like_a_property(d):
        d["members"].append({"name": "units: x", "reads": [], "units": [{"unit": "records", "where": "x"}]})
    mutant("mesh-member-named-like-a-property", "definition_form", edit_def=member_named_like_a_property)

    # The AAC corpus's Standard and Detailed History cases.
    aac = json.load(open(a.aac))
    for c in aac["cases"]:
        if c["kind"] != "history":
            continue
        defn = c["inputs"]["definition"]
        env = json.loads(c["inputs"]["reply"]["octets_text"])
        art = json.loads(b64(env["artifact"]))
        pin = json.loads(c["inputs"]["request"]["octets_text"])["coverage"]["expected_pin"]
        cps = [{"at": h["checkpoint"]["timestamp"], "records": h["checkpoint"]["log_size"]}
               for h in art["history_card"]["history"]]
        props = c["expected"].get("history_properties", {})
        failed = [k for k, v in props.items() if v == "FAIL"]
        name = "aac-" + c["id"]
        if defn["level"] == "detailed":
            cases.append(case(name, defn, art, cps, pin, want="detailed level not read", prop="definition_form"))
        elif failed:
            assert len(failed) == 1, (c["id"], failed)
            cases.append(case(name, defn, art, cps, pin, want=failed[0]))
        else:
            # Only a History the corpus accepts is written as accepted.
            assert c["expected"]["exchange"]["accepted"] and all(v != "FAIL" for v in
                                                                 c["expected"]["coverage"]["properties"].values())
            cases.append(case(name, defn, art, cps, pin, expected=served(defn, art)))

    out = {"source": (
        "capsule-engine admission: InferenceServiceHistoryCounts over inference-service-history/0 Standard answers "
        "(capsule-engine#273), written by scripts/admission_ish_vectors.py. mesh- cases are Mesh-LLM/capsules-plugin's "
        f"service_history/1 reply fixture and definition at {MESH_COMMIT}, and mutants of it; their card times and "
        "record count are read from its COSE checkpoints. aac- cases are agent-action-capsule "
        f"vectors/inference-service-history-v0-interop-v1 at {AAC_COMMIT}: expected where that corpus accepts the "
        "History, and want naming the property it fails otherwise. Go (go/admission) and Rust "
        "(rust/capsule-engine admission) decide every case."),
        "cases": cases}
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(len(cases), "cases")


if __name__ == "__main__":
    main()
