// SPDX-License-Identifier: Apache-2.0

//! The shared admission vectors (conformance-vectors/admission/v0), decided
//! as go/admission/conformance_test.go decides them: each case's verdict,
//! source and tier, and the SHA-256 of its decision block's JCS bytes; the
//! loader's shared refusals (pack_test.go TestRefusalVectors, TestLoadPinned);
//! and the admission-baseline@1 fixtures (baseline_test.go).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use capsule_engine::admission::{
    evaluate, standard_history_counts, AdjudicationVerdict, BandCount, Card, Claims, Disputes, Inputs, Judgement,
    Operator, OwnHistory, Pack, Platform, RailPin, Selection, Stake, Timestamp, Vouch,
};
use capsule_engine::canon;
use serde_json::{Map, Value};

fn vector_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../conformance-vectors/admission/v0")
}

/// conformance_test.go's DisallowUnknownFields, for one object of a vector.
fn object<'a>(v: &'a Value, allowed: &[&str], what: &str) -> &'a Map<String, Value> {
    let m = v.as_object().unwrap_or_else(|| panic!("{what} is not an object"));
    for k in m.keys() {
        assert!(allowed.contains(&k.as_str()), "{what}: unknown field {k:?}");
    }
    m
}

fn text(m: &Map<String, Value>, k: &str) -> String {
    m.get(k).and_then(Value::as_str).unwrap_or_default().to_string()
}

fn int(m: &Map<String, Value>, k: &str) -> i64 {
    m.get(k).map_or(0, |v| v.as_i64().unwrap_or_else(|| panic!("{k} is not an integer")))
}

fn opt_bool(m: &Map<String, Value>, k: &str) -> Option<bool> {
    m.get(k).and_then(Value::as_bool)
}

/// conformance_test.go parseTime: "" is Go's zero time.
fn time(s: &str) -> Timestamp {
    if s.is_empty() {
        Timestamp::go_zero()
    } else {
        Timestamp::parse_rfc3339(s).unwrap()
    }
}

fn opt_time(s: &str) -> Option<Timestamp> {
    (!s.is_empty()).then(|| time(s))
}

fn null_or<'a>(m: &'a Map<String, Value>, k: &str) -> Option<&'a Value> {
    m.get(k).filter(|v| !v.is_null())
}

/// conformance_test.go decodeInput: vectorInput as Inputs.
fn decode_input(raw: &Value) -> Inputs {
    let v = object(
        raw,
        &[
            "now",
            "amount",
            "currency",
            "platform",
            "card",
            "selection",
            "own",
            "operator",
            "vouches",
            "stake",
            "subject",
            "judged",
            "asks",
            "asks_made",
        ],
        "input",
    );
    let empty = Value::Object(Map::new());
    let platform = object(v.get("platform").unwrap_or(&empty), &["release_attestation", "owner_signed"], "platform");
    let card = object(
        v.get("card").unwrap_or(&empty),
        &[
            "state",
            "first_checkpoint_at",
            "latest_checkpoint_at",
            "witness",
            "controller",
            "claims",
            "bands",
            "disputes",
        ],
        "card",
    );
    let selection = object(v.get("selection").unwrap_or(&empty), &["state", "unanswered_disputes"], "selection");
    let operator = object(v.get("operator").unwrap_or(&empty), &["deny_listed", "allow_listed"], "operator");
    let mut input = Inputs::at(time(&text(v, "now")));
    input.amount = text(v, "amount");
    input.currency = text(v, "currency");
    input.platform = Platform {
        release_attestation: text(platform, "release_attestation"),
        owner_signed: opt_bool(platform, "owner_signed"),
    };
    input.card = Card {
        state: text(card, "state"),
        first_checkpoint_at: opt_time(&text(card, "first_checkpoint_at")),
        latest_checkpoint_at: opt_time(&text(card, "latest_checkpoint_at")),
        witness: text(card, "witness"),
        controller: text(card, "controller"),
        claims: null_or(card, "claims").map(|c| {
            let c = object(c, &["held", "total"], "card.claims");
            Claims { held: int(c, "held"), total: int(c, "total") }
        }),
        bands: null_or(card, "bands").map(|bs| {
            bs.as_array()
                .unwrap()
                .iter()
                .map(|b| {
                    let b = object(b, &["currency", "decade", "subjects", "adverse"], "card.bands[]");
                    BandCount {
                        currency: text(b, "currency"),
                        decade: int(b, "decade"),
                        subjects: int(b, "subjects"),
                        adverse: int(b, "adverse"),
                    }
                })
                .collect()
        }),
        disputes: null_or(card, "disputes").map(|d| {
            let d = object(d, &["open", "of"], "card.disputes");
            Disputes { open: int(d, "open"), of: int(d, "of") }
        }),
    };
    input.selection = Selection {
        state: text(selection, "state"),
        unanswered_disputes: null_or(selection, "unanswered_disputes")
            .map(|d| d.as_array().unwrap().iter().map(|at| time(at.as_str().unwrap())).collect())
            .unwrap_or_default(),
    };
    input.own = null_or(v, "own").map(|o| {
        let o = object(o, &["exchanges", "settled", "held_halves", "conflicting", "verdicts"], "own");
        OwnHistory {
            exchanges: int(o, "exchanges"),
            settled: int(o, "settled"),
            held_halves: int(o, "held_halves"),
            conflicting: int(o, "conflicting"),
            verdicts: null_or(o, "verdicts")
                .map(|vs| {
                    vs.as_array()
                        .unwrap()
                        .iter()
                        .map(|vd| {
                            let vd = object(vd, &["at", "adverse"], "own.verdicts[]");
                            AdjudicationVerdict {
                                at: time(&text(vd, "at")),
                                adverse: opt_bool(vd, "adverse").unwrap_or(false),
                            }
                        })
                        .collect()
                })
                .unwrap_or_default(),
        }
    });
    input.operator = Operator {
        deny_listed: opt_bool(operator, "deny_listed").unwrap_or(false),
        allow_listed: opt_bool(operator, "allow_listed"),
    };
    input.vouches = null_or(v, "vouches")
        .map(|vs| {
            vs.as_array()
                .unwrap()
                .iter()
                .map(|vc| Vouch { controller: text(object(vc, &["controller"], "vouches[]"), "controller") })
                .collect()
        })
        .unwrap_or_default();
    input.stake = null_or(v, "stake").map(|s| {
        let s = object(s, &["amount", "currency", "rail"], "stake");
        let rail = null_or(s, "rail").map_or_else(RailPin::default, |r| {
            let r = object(r, &["id", "digest"], "stake.rail");
            RailPin { id: text(r, "id"), digest: text(r, "digest") }
        });
        Stake { amount: text(s, "amount"), currency: text(s, "currency"), rail }
    });
    input.subject = text(v, "subject");
    input.judged = null_or(v, "judged")
        .map(|j| {
            // Keyed by row id, so any key; each value is {met, judge}.
            j.as_object()
                .unwrap()
                .iter()
                .map(|(row, jv)| {
                    let jv = object(jv, &["met", "judge"], "judged[]");
                    (row.clone(), Judgement { met: opt_bool(jv, "met").unwrap_or(false), judge: text(jv, "judge") })
                })
                .collect()
        })
        .unwrap_or_default();
    input.asks = null_or(v, "asks")
        .map(|a| a.as_object().unwrap().iter().map(|(k, s)| (k.clone(), s.as_str().unwrap().to_string())).collect())
        .unwrap_or_default();
    input.asks_made = int(v, "asks_made");
    input
}

#[test]
fn admission_conformance_vectors() {
    let raw = std::fs::read(vector_dir().join("cases.json")).unwrap();
    let set: Value = serde_json::from_slice(&raw).unwrap();
    let cases = set["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "no admission vectors");
    let mut packs: BTreeMap<String, Pack> = BTreeMap::new();
    let mut failures = Vec::new();
    for c in cases {
        let name = c["case"].as_str().unwrap();
        let pack_path = c["pack"].as_str().unwrap();
        let p = packs.entry(pack_path.to_string()).or_insert_with(|| {
            let src = std::fs::read(vector_dir().join(pack_path)).unwrap();
            Pack::load(&src).unwrap_or_else(|e| panic!("{pack_path}: {e}"))
        });
        let d = evaluate(p, &decode_input(&c["input"]));
        let jcs = canon::jcs(&d.block()).unwrap();
        let got = canon::sha256_hex(jcs.as_bytes());
        let e = &c["expected"];
        let tier = d.tier.as_deref();
        if d.verdict.as_str() != e["verdict"] || d.source != e["source"].as_str().unwrap() || tier != e["tier"].as_str()
        {
            failures.push(format!(
                "{name}: got {} from {} at {tier:?}; want {} from {} at {}\n{jcs}",
                d.verdict.as_str(),
                d.source,
                e["verdict"],
                e["source"],
                e["tier"]
            ));
            continue;
        }
        if got != e["block_sha256"].as_str().unwrap() {
            failures.push(format!("{name}: block_sha256 {got}, want {}\n{jcs}", e["block_sha256"]));
        }
    }
    assert!(failures.is_empty(), "{} of {} cases differ:\n{}", failures.len(), cases.len(), failures.join("\n"));
}

fn ladder_source() -> Vec<u8> {
    std::fs::read(vector_dir().join("packs/ladder.json")).unwrap()
}

// pack_test.go TestLoadPinned.
#[test]
fn load_pinned() {
    let src = ladder_source();
    let p = Pack::load(&src).unwrap();
    assert!(Pack::load_pinned(&src, p.id(), p.digest()).is_ok(), "the pinned bytes");
    let other = String::from_utf8(src.clone()).unwrap().replacen(r#""deadline_s":86400"#, r#""deadline_s":86401"#, 1);
    assert_ne!(other.as_bytes(), &src[..]);
    assert!(Pack::load_pinned(other.as_bytes(), p.id(), p.digest()).is_err(), "changed bytes under the same pin load");
    assert!(Pack::load_pinned(&src, "asg/other/0.1.0", p.digest()).is_err(), "another pack id loads under this pin");
    assert_eq!(p.digest(), canon::sha256_hex(&src));
}

/// pack_test.go applyOp: sets or deletes the member at `path` (object keys
/// and array indexes) in `doc`.
fn apply_op(doc: &mut Value, path: &[Value], value: Option<&Value>) {
    let (last, parents) = path.split_last().expect("refusals.json: an empty path");
    let mut node = doc;
    for step in parents {
        node = match step {
            Value::String(k) => node.get_mut(k.as_str()).expect("refusals.json: a path through a missing key"),
            Value::Number(n) => node
                .get_mut(n.as_u64().expect("an array index") as usize)
                .expect("refusals.json: an index out of range"),
            _ => panic!("refusals.json: a path step that is neither a key nor an index"),
        };
    }
    match (last, value) {
        (Value::String(k), None) => {
            node.as_object_mut().expect("refusals.json: a delete through a scalar").remove(k);
        }
        (Value::String(k), Some(v)) => {
            node.as_object_mut().expect("refusals.json: a set through a scalar").insert(k.clone(), v.clone());
        }
        (Value::Number(n), Some(v)) => node[n.as_u64().expect("an array index") as usize] = v.clone(),
        _ => panic!("refusals.json: an op this test does not apply"),
    }
}

// pack_test.go TestRefusalVectors: conformance-vectors/admission/v0/
// refusals.json, the admission design rules (capsule-engine#265) as the
// loader holds them, and the exact-JCS rule. Each case applies its ops to
// the parsed base pack and re-encodes it as JCS, or gives its bytes outright;
// load must refuse it with an error containing want.
#[test]
fn refusal_vectors() {
    let raw = std::fs::read(vector_dir().join("refusals.json")).unwrap();
    let v: Value = serde_json::from_slice(&raw).unwrap();
    let (cases, raws) = (v["cases"].as_array().unwrap(), v["raw"].as_array().unwrap());
    assert!(!cases.is_empty() && !raws.is_empty(), "no refusal vectors");
    let mut failures = Vec::new();
    let mut refused = |name: &str, src: &[u8], want: &str| match Pack::load(src) {
        Err(e) if e.to_string().contains(want) => {}
        got => failures
            .push(format!("{name}: got {:?}; want an error containing {want:?}", got.map(|p| p.id().to_string()))),
    };
    for c in cases {
        let name = c["case"].as_str().unwrap();
        let base = std::fs::read(vector_dir().join(c["base"].as_str().unwrap())).unwrap();
        let mut doc: Value = serde_json::from_slice(&base).unwrap();
        for op in c["ops"].as_array().unwrap() {
            match (op.get("delete"), op.get("set")) {
                (Some(Value::Array(path)), _) => apply_op(&mut doc, path, None),
                (_, Some(Value::Array(path))) => apply_op(&mut doc, path, Some(&op["value"])),
                _ => panic!("{name}: an op that neither sets nor deletes"),
            }
        }
        let jcs = canon::jcs(&canon::from_json(&doc).unwrap()).unwrap();
        refused(name, jcs.as_bytes(), c["want"].as_str().unwrap());
    }
    for c in raws {
        refused(c["case"].as_str().unwrap(), c["bytes"].as_str().unwrap().as_bytes(), c["want"].as_str().unwrap());
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// baseline_test.go TestBaselineFixtures: admission-baseline@1 and its
/// fixtures (evidencebook-skills#51), copied byte for byte with the commit
/// they came from in PROVENANCE.json. The fixtures' verdict, source and
/// tier are written by hand in evidencebook-skills and carry no block
/// digest; this evaluator and the Go one decide each the same.
#[test]
fn baseline_fixtures() {
    let dir = vector_dir().join("admission-baseline-1.0.0");
    let prov: Value = serde_json::from_slice(&std::fs::read(dir.join("PROVENANCE.json")).unwrap()).unwrap();
    let read = |name: &str| {
        let b = std::fs::read(dir.join(name)).unwrap();
        assert_eq!(
            Some(canon::sha256_hex(&b).as_str()),
            prov["files"][name].as_str(),
            "{name} is not the copy PROVENANCE.json pins (evidencebook-skills {})",
            prov["commit"]
        );
        b
    };
    let p = Pack::load(&read("pack-definition.json")).unwrap_or_else(|e| panic!("{e}"));
    let set: Value = serde_json::from_slice(&read("cases.json")).unwrap();
    let cases = set["cases"].as_array().unwrap();
    assert!(
        set["pack"].as_str() == Some(p.id()) && !cases.is_empty(),
        "the fixtures name {} with {} cases; the pack is {}",
        set["pack"],
        cases.len(),
        p.id()
    );
    let mut failures = Vec::new();
    for c in cases {
        let d = evaluate(&p, &decode_input(&c["input"]));
        let e = &c["expected"];
        let tier = d.tier.as_deref();
        if d.verdict.as_str() != e["verdict"] || d.source != e["source"].as_str().unwrap() || tier != e["tier"].as_str()
        {
            failures.push(format!(
                "{}: got {} from {} at {tier:?}; want {} from {} at {}",
                c["case"],
                d.verdict.as_str(),
                d.source,
                e["verdict"],
                e["source"],
                e["tier"]
            ));
        }
    }
    assert!(failures.is_empty(), "{} of {} cases differ:\n{}", failures.len(), cases.len(), failures.join("\n"));
}

/// evaluate.go card.key_age: a verified card that states no first
/// checkpoint (Go's zero time) shows no age, and fails, whatever min_days is.
#[test]
fn key_age_fails_without_a_first_checkpoint() {
    let p = Pack::load(&ladder_source()).unwrap();
    let now = Timestamp::parse_rfc3339("2026-10-30T12:00:00Z").unwrap();
    for first in [None, Some(Timestamp::go_zero())] {
        let mut input = Inputs::at(now);
        // 50,000 sat is decade 4, T1, where the key-age row applies.
        input.amount = "50000".into();
        input.currency = "sat".into();
        input.card = Card { state: "verified".into(), first_checkpoint_at: first, ..Card::default() };
        let d = evaluate(&p, &input);
        let row = d.rows.iter().find(|r| r.row == "key-age").unwrap();
        assert_eq!(row.result.as_str(), "fail", "{first:?}");
    }
}

/// standard_history_test.go TestStandardHistoryVectors: the shared
/// standard_history/1 mapping vectors.
#[test]
fn standard_history_vectors() {
    let raw = std::fs::read(vector_dir().join("standard-history.json")).unwrap();
    let set: Value = serde_json::from_slice(&raw).unwrap();
    let cases = set["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "no standard_history/1 vectors");
    for c in cases {
        let name = c["case"].as_str().unwrap();
        let got = standard_history_counts(c["artifact"].as_str().unwrap().as_bytes());
        match c.get("expected") {
            None => {
                let want = c["want"].as_str().unwrap();
                match got {
                    Err(e) => assert!(e.0.contains(want), "{name}: error {e}, want one containing {want:?}"),
                    Ok(_) => panic!("{name}: read, want an error containing {want:?}"),
                }
            }
            Some(e) => {
                let (bands, disputes) = got.unwrap_or_else(|err| panic!("{name}: {err}"));
                let want: Vec<BandCount> = e["bands"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|b| BandCount {
                        currency: b["currency"].as_str().unwrap().into(),
                        decade: b["decade"].as_i64().unwrap(),
                        subjects: b["subjects"].as_i64().unwrap(),
                        adverse: b["adverse"].as_i64().unwrap(),
                    })
                    .collect();
                assert_eq!(bands, want, "{name}: bands");
                let d = &e["disputes"];
                assert_eq!(
                    disputes,
                    Disputes { open: d["open"].as_i64().unwrap(), of: d["of"].as_i64().unwrap() },
                    "{name}: disputes"
                );
            }
        }
    }
}
