// SPDX-License-Identifier: Apache-2.0

//! The shared conformance vectors for the inference pack
//! (conformance-vectors/guard/v0/inference.json), decided by this port as
//! the Go runner decides an action-ledger case (go/guard/conformance_test.go
//! ledgerSteps): each step's record enters the history before the next,
//! under the capsule id the Python engine sealed for it, since the caller
//! seals.

use std::path::{Path, PathBuf};

use capsule_engine::canon;
use capsule_engine::guard::{record_for, Action, ConstraintResult, Engine, History, Profile};
use serde_json::Value;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// One constraint in the vectors' shape.
#[derive(Debug, PartialEq)]
struct Shape {
    id: String,
    result: String,
    method: String,
    evidence_digest: String,
    reason: String,
}

/// A step's decision in the vectors' shape, or the refusal that ended the
/// case.
#[derive(Debug)]
enum Decided {
    Steps(Vec<(String, String, Vec<Shape>)>),
    Refused(String),
}

/// ledgerSteps: the case's steps decided in order. A step this port refuses
/// ends the case. An input carrying a profile is decided by an engine with
/// that profile laid over the pack (with_profile).
fn decide_case(case: &Value, input: &Value) -> Decided {
    let mut engine = Engine::inference().expect("the inference pack loads");
    let pack = case["pack"].as_str().unwrap();
    // The case names the embedded directory, inference-<version>; the input
    // names the served pack.
    let version = engine.pack_id().rsplit('/').next().unwrap();
    assert_eq!(pack, format!("inference-{version}"), "the case's pack is not the embedded one");
    if let Some(name) = input.get("pack").and_then(Value::as_str) {
        assert!(pack.starts_with(&format!("{name}-")), "the input names pack {name}, the case {pack}");
    }
    if let Some(profile) = input.get("profile") {
        let p = match Profile::decode(&serde_json::to_vec(profile).unwrap()) {
            Ok(p) => p,
            Err(e) => return Decided::Refused(e.to_string()),
        };
        // The vectors' profiles are canonical, so the digest decode computes
        // is the JCS SHA-256 of the profile as given.
        let want = canon::digest(&canon::from_json(profile).unwrap()).unwrap();
        assert_eq!(p.digest(), want, "profile digest");
        engine = match engine.with_profile(&p) {
            Ok(e) => e,
            Err(e) => return Decided::Refused(e.to_string()),
        };
    }
    let expected_steps = case["expected"]["steps"].as_array().cloned().unwrap_or_default();
    let mut history = History::new();
    let mut out = Vec::new();
    for (i, step) in input["steps"].as_array().unwrap().iter().enumerate() {
        let a = match Action::decode(&serde_json::to_vec(step).unwrap()) {
            Ok(a) => a,
            Err(e) => return Decided::Refused(e.to_string()),
        };
        let d = match engine.decide(&a, &history) {
            Ok(d) => d,
            Err(e) => return Decided::Refused(e.to_string()),
        };
        let id = expected_steps.get(i).and_then(|s| s["capsule_id"].as_str()).unwrap_or("").to_string();
        let shapes = d
            .constraints()
            .iter()
            .map(|c| Shape {
                id: c.id().into(),
                result: c.result().as_str().into(),
                method: c.method().into(),
                evidence_digest: c.evidence_digest().into(),
                reason: c.reason().into(),
            })
            .collect();
        out.push((id.clone(), d.outcome().as_str().to_string(), shapes));
        history.append(record_for(&a, &d, id));
    }
    Decided::Steps(out)
}

/// How this port's decision differs from the expected one, or None. An
/// error agrees with an error, as the Go runner holds it (sameDecision:
/// the sides name their refusals differently). Every n/a's reason is held
/// to Python's, which the Go runner checks only where it composes one;
/// the vectors carry no reason for a pass or a fail.
fn difference(got: &Decided, expected: &Value) -> Option<String> {
    if let Some(e) = expected.get("error") {
        return match got {
            Decided::Refused(_) => None,
            Decided::Steps(_) => Some(format!("expected an error ({e}), got a decision")),
        };
    }
    let steps = match got {
        Decided::Refused(e) => return Some(format!("refused: {e}")),
        Decided::Steps(s) => s,
    };
    let want = expected["steps"].as_array().unwrap();
    if steps.len() != want.len() {
        return Some(format!("{} steps, expected {}", steps.len(), want.len()));
    }
    let mut out = String::new();
    for (i, ((id, outcome, cs), w)) in steps.iter().zip(want).enumerate() {
        if id != w["capsule_id"].as_str().unwrap() {
            out += &format!("step {i}: capsule {id}, expected {}; ", w["capsule_id"]);
        }
        if outcome != w["outcome"].as_str().unwrap() {
            out += &format!("step {i}: outcome {outcome}, expected {}; ", w["outcome"]);
        }
        let wc = w["constraints"].as_array().unwrap();
        if cs.len() != wc.len() {
            out += &format!("step {i}: {} constraints, expected {}; ", cs.len(), wc.len());
            continue;
        }
        for (c, e) in cs.iter().zip(wc) {
            let field = |k: &str| e[k].as_str().unwrap_or("").to_string();
            let want = Shape {
                id: field("id"),
                result: field("result"),
                method: field("method"),
                evidence_digest: field("evidence_digest"),
                reason: if e.get("reason").is_some() { field("reason") } else { c.reason.clone() },
            };
            if *c != want {
                out += &format!("step {i}: {c:?}, expected {want:?}; ");
            }
        }
    }
    (!out.is_empty()).then_some(out)
}

#[test]
fn inference_conformance_vectors() {
    let root = repo_root();
    let raw = std::fs::read(root.join("conformance-vectors/guard/v0/inference.json")).unwrap();
    let set: Value = serde_json::from_slice(&raw).unwrap();
    let cases = set["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 25, "the inference set's cases");
    let mut failures = Vec::new();
    for case in cases {
        let name = case["case"].as_str().unwrap();
        assert_eq!(case["kind"], "action-ledger", "{name}: only action-ledger cases are in this set");
        let input_raw = std::fs::read(root.join(case["input"].as_str().unwrap())).unwrap();
        assert_eq!(
            canon::sha256_hex(&input_raw),
            case["input_sha256"].as_str().unwrap(),
            "{name}: the input is not the one pinned"
        );
        let input: Value = serde_json::from_slice(&input_raw).unwrap();
        let got = decide_case(case, &input);
        if let Some(d) = difference(&got, &case["expected"]) {
            failures.push(format!("{name}: {d}"));
        }
    }
    assert!(failures.is_empty(), "{} of {} cases differ:\n{}", failures.len(), cases.len(), failures.join("\n"));
}

/// No known difference is pinned for an inference case: every one is held
/// to the expected decision above.
#[test]
fn no_inference_case_is_a_known_difference() {
    let raw = std::fs::read(repo_root().join("conformance-vectors/guard/v0/KNOWN-DIFFERENCES.json")).unwrap();
    let known: Value = serde_json::from_slice(&raw).unwrap();
    for k in known["cases"].as_array().unwrap() {
        let id = k["case"].as_str().unwrap();
        assert!(!id.starts_with("inference.json:"), "{id} is pinned as a known difference");
    }
}

/// AGENTS.md: a profile-keyed payee target links a merchant across a
/// profile's deals, so it never appears in a reason, whether the decision
/// passes, fails or is not applicable.
#[test]
fn a_profile_keyed_target_never_appears_in_a_reason() {
    let target = format!("payee-fp:hmac-sha256-profile-key:{}", "ab".repeat(32));
    let engine = Engine::inference().unwrap();
    for mode in ["deny", "allow"] {
        let profile = format!(
            r#"{{"format": "policy-profile/v0", "packs": [{{"pack": "asg/inference", "parameters":
            {{"counterparty_list": {{"mode": "{mode}", "entries": [{{"kind": "target", "value": "{target}"}}]}}}}}}]}}"#
        );
        let e = engine.with_profile(&Profile::decode(profile.as_bytes()).unwrap()).unwrap();
        for rates in [r#", "input_msat_per_million": 1, "output_msat_per_million": 1"#, ""] {
            let action = format!(
                r#"{{"operator": "node-a", "developer": "mesh-node@v1", "verb": "inference.purchase",
                "action_class": "money.purchase", "taxonomy_version": "6", "timestamp": "2026-10-29T12:00:00Z",
                "target": "{target}", "equivalence_key": "x"{rates}}}"#
            );
            let a = Action::decode(action.as_bytes()).unwrap();
            let mut h = History::new();
            for _ in 0..2 {
                let d = e.decide(&a, &h).unwrap();
                for c in d.constraints() {
                    assert!(!c.reason().contains(&target), "{} reason names the target: {}", c.id(), c.reason());
                    assert!(!c.reason().contains("payee-fp:"), "{} reason names a payee fingerprint", c.id());
                }
                h.append(record_for(&a, &d, "c".repeat(64)));
            }
        }
    }
}

/// A rate past the safe-integer range has no canonical evidence, so the
/// decision is refused rather than returned with an evidence digest the
/// engine could not seal.
#[test]
fn an_unencodable_rate_is_refused() {
    let engine = Engine::inference().unwrap();
    let a = Action::decode(
        br#"{"operator": "node-a", "developer": "mesh-node@v1", "verb": "inference.purchase",
        "action_class": "money.purchase", "taxonomy_version": "6", "timestamp": "2026-10-29T12:00:00Z",
        "target": "node-b", "equivalence_key": "x", "input_msat_per_million": 9007199254740992,
        "output_msat_per_million": 1}"#,
    )
    .unwrap();
    assert!(engine.decide(&a, &History::new()).is_err());
}

/// An action at another taxonomy version is not evaluated against this
/// table: the class-keyed checks are held, and a would-allow is refused.
#[test]
fn another_taxonomy_version_is_held() {
    let engine = Engine::inference().unwrap();
    let a = Action::decode(
        br#"{"operator": "node-a", "developer": "mesh-node@v1", "verb": "inference.purchase",
        "action_class": "money.purchase", "taxonomy_version": "5", "timestamp": "2026-10-29T12:00:00Z",
        "target": "node-b", "equivalence_key": "x", "input_msat_per_million": 1, "output_msat_per_million": 1}"#,
    )
    .unwrap();
    let d = engine.decide(&a, &History::new()).unwrap();
    assert_eq!(d.outcome().as_str(), "deny");
    for id in ["rate_cap", "counterparty_list"] {
        let c = d.constraint(id).unwrap();
        assert_eq!(c.result(), ConstraintResult::NotApplicable);
        assert!(c.reason().starts_with("taxonomy_version 5 on the action"), "{}", c.reason());
    }
    assert_eq!(d.constraint("dedupe").unwrap().result(), ConstraintResult::Pass);
    assert_eq!(d.constraint("required_fields").unwrap().result(), ConstraintResult::Pass);
}

/// Action::decode refuses what DecodeAction refuses, and what this port does
/// not read.
#[test]
fn decode_refusals() {
    let base = r#""operator": "node-a", "developer": "mesh-node@v1", "verb": "inference.purchase", "timestamp": "2026-10-29T12:00:00Z""#;
    for extra in [
        r#", "equivalence_key": "k", "equivalence_key_digest": "d""#,
        r#", "input_msat_per_million": 1.5"#,
        r#", "input_msat_per_million": "1""#,
        r#", "spend_minor": 1"#,
        r#", "counterparty_ids": {"payee": 1}"#,
    ] {
        let doc = format!("{{{base}{extra}}}");
        assert!(Action::decode(doc.as_bytes()).is_err(), "accepted {doc}");
    }
    assert!(Action::decode(br#"{"operator": "a", "developer": "b", "verb": "v", "timestamp": "2026-10-29"}"#).is_err());
    assert!(Action::decode(br#"{"operator": "a", "developer": "b", "timestamp": "2026-10-29T12:00:00Z"}"#).is_err());
    let a = Action::decode(format!("{{{base}}}").as_bytes()).unwrap();
    assert_eq!(a.action_type, "decide");
}

/// Engine::with_profile refuses what WithProfile refuses, and leaves the
/// engine it was called on as it was.
#[test]
fn profile_refusals() {
    let engine = Engine::inference().unwrap();
    let decode = |params: &str, pack: &str| {
        Profile::decode(
            format!(r#"{{"format": "policy-profile/v0", "packs": [{{"pack": "{pack}", "parameters": {params}}}]}}"#)
                .as_bytes(),
        )
    };
    for params in [
        r#"{"rate_cap": {}}"#,
        r#"{"rate_cap": {"max_input_msat_per_million": -1}}"#,
        r#"{"rate_cap": {"max_input_msat_per_million": 9007199254740992}}"#,
        r#"{"rate_cap": {"max_input_msat_per_million": "5"}}"#,
        r#"{"rate_cap": {"max_tokens": 5}}"#,
        r#"{"counterparty_list": {"entries": []}}"#,
        r#"{"counterparty_list": {"mode": "deny", "entries": [], "disposition": null}}"#,
        r#"{"counterparty_list": {"mode": "deny", "entries": [{"kind": "payee", "value": "x"}]}}"#,
        r#"{"counterparty_list": {"mode": "deny", "entries": [{"kind": "target", "value": "x"}, {"kind": "target", "value": "x"}]}}"#,
        r#"{"dedupe": {"window_days": 1}}"#,
    ] {
        assert!(decode(params, "asg/inference").is_err(), "decoded {params}");
    }
    assert!(decode("{}", "asg/inference/0.1.0").is_err());
    let caps = decode(r#"{"caps": {"caps_minor": {"money.purchase": 5}}}"#, "asg/inference").unwrap();
    assert!(engine.with_profile(&caps).is_err());
    let other = decode(r#"{"rate_cap": {"max_input_msat_per_million": 5}}"#, "asg/everyday").unwrap();
    assert!(engine.with_profile(&other).is_err());
}

/// A history record whose time Go's time.Parse(RFC3339Nano) reads with a
/// comma before the fraction is matched by dedupe, as Go matches it; one
/// neither reads is passed over by both.
#[test]
fn dedupe_reads_a_record_time_as_go_does() {
    let engine = Engine::inference().unwrap();
    let a = Action::decode(
        br#"{"operator": "node-a", "developer": "mesh-node@v1", "verb": "inference.purchase",
        "action_class": "money.purchase", "taxonomy_version": "6", "timestamp": "2026-10-29T12:00:00Z",
        "target": "node-b", "equivalence_key": "x", "input_msat_per_million": 1, "output_msat_per_million": 1}"#,
    )
    .unwrap();
    let first = engine.decide(&a, &History::new()).unwrap();
    for (ts, result) in [
        ("2026-10-29T11:00:00,5Z", ConstraintResult::Fail),
        ("2026-10-29T1:00:00Z", ConstraintResult::Fail),
        ("2026-10-29 11:00:00Z", ConstraintResult::Pass),
    ] {
        let mut r = record_for(&a, &first, "c".repeat(64));
        r.timestamp = ts.into();
        let d = engine.decide(&a, &History::from(vec![r])).unwrap();
        assert_eq!(d.constraint("dedupe").unwrap().result(), result, "{ts}");
    }
}
