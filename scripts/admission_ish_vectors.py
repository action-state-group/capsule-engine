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
checkpoint times and record count the caller reads from the embedded
history_card/1, and the requester's expected_pin. expected is the served
periods read, or null when the answer counts no served exchanges; want is a
substring of the refusal Go and Rust both give. The AAC cases' expected
values follow that corpus's own History properties: a case it accepts reads,
and a case it fails on a property is refused naming that property. The
Mesh card's checkpoints are COSE, so its times and record count are read
here with cbor2 (the caller's card verifier reads them in a real answer).

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
OUT = "conformance-vectors/admission/v0/inference-service-history.json"


def jcs(v):
    return rfc8785.dumps(v).decode()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def b64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def served(defn, art):
    """The served periods an accepted answer reads (the evaluator's view)."""
    counts = art["period_counts"]["periods"]
    periods = []
    for p in counts:
        total = next(c for c in p["cells"] if c["unit"] == "exchanges_served" and c["dimension"] is None)
        entry = {"from": p["from"], "to": p["to"], "total": total["value"]}
        if total["value"] is None:
            entry["suppressed"] = total["suppressed"]
        periods.append(entry)
    pol = defn["period_policy"]
    return {"length_s": pol["length_s"], "floor": pol["floor"], "periods": periods}


def case(name, defn, art, times, records, pin, expected=None, want=None, definition_text=None, artifact_text=None):
    c = {"case": name,
         "definition": definition_text if definition_text is not None else jcs(defn),
         "artifact": artifact_text if artifact_text is not None else jcs(art),
         "card": {"times": times, "records": records}, "pin": pin}
    if want is None:
        c["expected"] = expected
    else:
        c["want"] = want
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
    mtimes, mrecords = [], None
    for e in mart["history_card"]["history"]:
        p = cbor2.loads(cbor2.loads(bytes.fromhex(e["checkpoint"])).value[2])
        mtimes.append(p["issued_at"])
    mrecords = mart["coverage_declaration"]["live_set"][0]["count"]
    mpin = mart["anchor"]
    cases.append(case("mesh-service-history-1", mdef, mart, mtimes, mrecords, mpin, expected=served(mdef, mart)))

    def mutant(name, want, edit_def=None, edit_art=None, rebind=True, **kw):
        d, x = copy.deepcopy(mdef), copy.deepcopy(mart)
        if edit_def:
            edit_def(d)
            if rebind:
                x["derivation"] = sha(jcs(d))
        if edit_art:
            edit_art(x)
        args = dict(times=mtimes, records=mrecords, pin=mpin)
        args.update(kw)
        cases.append(case(name, d, x, args["times"], args["records"], args["pin"], want=want,
                          definition_text=args.get("definition_text"), artifact_text=args.get("artifact_text")))

    mutant("mesh-unknown-profile", "definition_form: profile not known",
           edit_def=lambda d: d.update(profile="inference-service-history/1"))
    mutant("mesh-detailed-level", "definition_form: detailed level not read",
           edit_def=lambda d: d.update(level="detailed"))
    mutant("mesh-other-counts-member", "counts shape not known",
           edit_def=lambda d: d["members"][1].update(name="weekly_counts"))
    mutant("mesh-unregistered-unit", "units:",
           edit_def=lambda d: d["members"][1]["units"][0].update(unit="exchanges"))
    mutant("mesh-definition-not-bound", "definition_binding",
           edit_def=lambda d: d.update(spec=d["spec"] + "#changed"), rebind=False)
    mutant("mesh-stale-or-replayed-pin", "anchor_match", pin=mtimes and sha("another checkpoint"))
    mutant("mesh-interval-not-to-anchor", "coverage_declaration_anchor",
           edit_art=lambda x: x["coverage_declaration"]["interval"].update(to=sha("elsewhere")))
    mutant("mesh-live-set-other-count", "coverage_declaration_form", records=mrecords + 1)
    mutant("mesh-card-digest-mismatch", "artifact_form: the card does not match history_card_digest",
           edit_art=lambda x: x.update(history_card_digest=sha("another card")))
    mutant("mesh-count-labelled-peer-confirmed", "labels",
           edit_art=lambda x: x["period_counts"]["periods"][1]["cells"][2].update(label="peer_confirmed"))
    mutant("mesh-cell-below-floor", "period_floor",
           edit_art=lambda x: x["period_counts"]["periods"][1]["cells"][0].update(value=4))
    mutant("mesh-anchor-not-a-boundary", "period_anchor", times=mtimes[:-1] + ["2026-01-19T00:00:01Z"])
    mutant("mesh-week-left-out", "period_alignment",
           edit_art=lambda x: x["period_counts"]["periods"].pop(0))
    mutant("mesh-definition-not-jcs", "definition_form: the definition is not JCS bytes",
           definition_text=json.dumps(mdef))
    mutant("mesh-requested-cell-not-declared", "units:",
           edit_art=lambda x: x["period_counts"]["periods"][1]["cells"].append(
               {"dimension": None, "label": "self_reported", "unit": "exchanges_requested", "value": 7}))

    # The AAC corpus's Standard and Detailed History cases.
    aac = json.load(open(a.aac))
    for c in aac["cases"]:
        if c["kind"] != "history":
            continue
        defn = c["inputs"]["definition"]
        env = json.loads(c["inputs"]["reply"]["octets_text"])
        art = json.loads(b64(env["artifact"]))
        pin = json.loads(c["inputs"]["request"]["octets_text"])["coverage"]["expected_pin"]
        card = art["history_card"]["history"]
        times = [h["checkpoint"]["timestamp"] for h in card]
        records = card[-1]["checkpoint"]["log_size"]
        props = c["expected"].get("history_properties", {})
        failed = [k for k, v in props.items() if v == "FAIL"]
        name = "aac-" + c["id"]
        if defn["level"] == "detailed":
            cases.append(case(name, defn, art, times, records, pin, want="detailed level not read"))
        elif failed:
            assert len(failed) == 1, (c["id"], failed)
            cases.append(case(name, defn, art, times, records, pin, want=failed[0]))
        else:
            cases.append(case(name, defn, art, times, records, pin, expected=served(defn, art)))

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
