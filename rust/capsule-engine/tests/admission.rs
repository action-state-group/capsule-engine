// SPDX-License-Identifier: Apache-2.0

//! The shared admission vectors (conformance-vectors/admission/v0), decided
//! as go/admission/conformance_test.go decides them: each case's verdict,
//! source and tier, and the SHA-256 of its decision block's JCS bytes; and
//! the loader's refusals (pack_test.go TestLoadRefuses, TestLoadPinned).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use capsule_engine::admission::{
    evaluate, AdjudicationVerdict, Card, Claims, Inputs, Judgement, Operator, OwnHistory, Pack, Platform, Selection,
    Stake, Timestamp, Vouch,
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
        &["state", "first_checkpoint_at", "latest_checkpoint_at", "witness", "controller", "claims"],
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
        let s = object(s, &["amount", "currency"], "stake");
        Stake { amount: text(s, "amount"), currency: text(s, "currency") }
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

fn ladder_source() -> String {
    std::fs::read_to_string(vector_dir().join("packs/ladder.yaml")).unwrap()
}

// pack_test.go TestLoadPinned.
#[test]
fn load_pinned() {
    let src = ladder_source();
    let p = Pack::load(src.as_bytes()).unwrap();
    assert!(Pack::load_pinned(src.as_bytes(), p.id(), p.digest()).is_ok(), "the pinned bytes");
    assert!(
        Pack::load_pinned(format!("{src}\n").as_bytes(), p.id(), p.digest()).is_err(),
        "changed bytes under the same pin load"
    );
    assert!(
        Pack::load_pinned(src.as_bytes(), "asg/other/0.1.0", p.digest()).is_err(),
        "another pack id loads under this pin"
    );
    assert_eq!(p.digest(), canon::sha256_hex(src.as_bytes()));
}

// pack_test.go TestLoadRefuses: the admission design rules
// (capsule-engine#265) as the loader holds them. Each edit of the ladder
// pack must be refused, naming what broke the rule.
#[test]
fn load_refuses() {
    let cases: &[(&str, &str, &str, &str)] = &[
        (
            "a card row decides NO",
            "    check: card.verified\n    from: T1\n    on_fail: LIMIT",
            "    check: card.verified\n    from: T1\n    on_fail: \"NO\"",
            "a card row may not decide NO",
        ),
        ("a vouch row decides NO", "    from: T2\n    on_fail: LIMIT", "    from: T2\n    on_fail: \"NO\"", "a vouch row may not decide NO"),
        ("a judged row decides NO", "    on_fail: {cap: T1}", "    on_fail: \"NO\"", "a judged row's on_fail must be {cap: <tier>}"),
        ("a missing input defaults to GO", "missing: LIMIT", "missing: GO", "missing must not be GO"),
        ("a score field", "budget:\n", "score_weights: {settled: 3}\nbudget:\n", "score_weights"),
        (
            "a judged row in a pack without LIMIT",
            r#"outcomes: ["GO", "ASK", "LIMIT", "NO"]"#,
            r#"outcomes: ["GO", "ASK", "NO"]"#,
            "a judged row caps a tier, so LIMIT must be in outcomes",
        ),
        ("a cap that lowers nothing", "    on_fail: {cap: T1}", "    on_fail: {cap: T2}", "cap T2 must be a tier below from T2"),
        (
            "an ask on a NO row",
            "    check: platform.attestation_not_invalid\n    from: T0\n    on_fail: \"NO\"",
            "    check: platform.attestation_not_invalid\n    from: T0\n    on_fail: \"NO\"\n    ask: {derivation: history_card/1}",
            "only a LIMIT row may carry an ask",
        ),
        (
            "a ceiling in a unit accept does not name",
            "max_decade: {USD: 2}",
            "max_decade: {USD: 2, Sat: 9}",
            "max_decade.Sat is not a band unit in accept",
        ),
        (
            "a ceiling that falls across a skipped tier",
            "max_decade: {USD: 2}",
            "max_decade: {USD: 2, sat: 3}",
            "max_decade.sat 3 does not rise above the tier below (4)",
        ),
        (
            "a cap with another key",
            "    on_fail: {cap: T1}",
            "    on_fail: {cap: T1, weight: 2}",
            "a judged row's on_fail must be {cap: <tier>}",
        ),
        (
            "a unit no tier names",
            "  USD: {band: USD, scale: 0}\n",
            "  USD: {band: USD, scale: 0}\n  EUR: {band: EUR, scale: 0}\n",
            "band unit EUR is named by no tier",
        ),
        ("a negative threshold", "params: {min: 1}\n    from: T1", "params: {min: -1}\n    from: T1", "params.min -1 must be from 0"),
        (
            "a threshold whose duration wraps",
            "params: {max_age_s: 604800}",
            "params: {max_age_s: 99999999999}",
            "params.max_age_s 99999999999 must be from 0",
        ),
        ("a float", "scale: -3}", "scale: -3.5}", "quote \"-3.5\""),
        ("a hex integer", "max_asks: 2", "max_asks: 0x2", "quote \"0x2\""),
        ("a boolean id", "  - id: attestation\n", "  - id: True\n", "quote \"True\""),
        ("an exponent with no dot", "  - id: attestation\n", "  - id: 1e3\n", "quote \"1e3\""),
        ("an octal 0o", "  - id: attestation\n", "  - id: 0o10\n", "quote \"0o10\""),
        ("a one-digit date", "  - id: attestation\n", "  - id: 2026-1-2\n", "quote \"2026-1-2\""),
        ("a base-60 number", "  - id: attestation\n", "  - id: 1:30\n", "quote \"1:30\""),
        ("a tag on a quoted scalar", "max_asks: 2", "max_asks: !!int \"2\"", "tags are not read"),
        ("a merge key", "  - id: T0\n", "  - <<: {x: 1}\n    id: T0\n", "quote \"<<\""),
        ("an anchor", "  - id: T0\n", "  - &t0 id: T0\n", "anchors and aliases"),
        ("no ask deadline", "  deadline_s: 86400\n", "", "budget.deadline_s must be a positive integer"),
        ("a row weight", "    check: own.settled\n", "    check: own.settled\n    weight: 2\n", "weight"),
        (
            "LIMIT from the lowest tier",
            "    params: {min: 1}\n    from: T0\n    on_fail: record",
            "    params: {min: 1}\n    from: T0\n    on_fail: LIMIT",
            "cannot LIMIT",
        ),
        ("an unknown predicate", "check: own.settled", "check: own.reputation", "not an admission predicate"),
        (
            "a predicate under the wrong source",
            "    source: own_history\n    check: own.settled",
            "    source: card\n    check: own.settled",
            "reads own_history",
        ),
        ("a missing threshold", "    params: {min: 1}\n    from: T1", "    from: T1", "needs params.min"),
        ("decades that do not rise", "max_decade: {sat: 4, USD: 0}", "max_decade: {sat: 3, USD: 0}", "does not rise"),
        (
            "an outcome the pack does not yield",
            r#"outcomes: ["GO", "ASK", "LIMIT", "NO"]"#,
            r#"outcomes: ["GO", "LIMIT", "NO"]"#,
            "ASK is not in outcomes",
        ),
        ("an unquoted NO", "    from: T0\n    on_fail: \"NO\"", "    from: T0\n    on_fail: NO", "is a boolean in YAML 1.1"),
        ("another scope kind", "kind: admission", "kind: live", "scope.kind must be admission"),
        ("a version that is not the id's", "pack_version: 0.1.0", "pack_version: 0.2.0", "does not end in pack_version"),
    ];
    let src = ladder_source();
    let mut failures = Vec::new();
    for (name, old, new, want) in cases {
        assert!(src.contains(old), "{name}: the ladder pack has no {old:?}");
        match Pack::load(src.replacen(old, new, 1).as_bytes()) {
            Err(e) if e.to_string().contains(want) => {}
            got => failures.push(format!("{name}: got {got:?}; want an error naming {want:?}")),
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// The reader is the trust boundary for an authored pack: what it cannot
/// read exactly is refused, never guessed at.
#[test]
fn load_refuses_what_the_reader_does_not_read() {
    let src = ladder_source();
    for (old, new) in [
        ("    params: {min: 1}\n    from: T1", "    params: {min: 1.5}\n    from: T1"),
        ("    params: {min: 1}\n    from: T1", "    params: {min: \"1\"}\n    from: T1"),
        ("    params: {min: 1}\n    from: T1", "    params: {min: 0x1}\n    from: T1"),
        ("  msat: {band: sat, scale: -3}", "  msat: {band: sat, scale: -3, scale: -3}"),
        ("  msat: {band: sat, scale: -3}", "  msat: {band: {unit: sat}, scale: -3}"),
        ("  msat: {band: sat, scale: -3}", "  msat: &m {band: sat, scale: -3}"),
        ("    on_fail: {cap: T1}", "    on_fail: {cap: T1, weight: 2}"),
        ("kind: admission", "kind: yes"),
        ("kind: admission", "Off: admission"),
        ("statement: The counterparty is not", "statement: note: The counterparty is not"),
    ] {
        assert!(src.contains(old), "the ladder pack has no {old:?}");
        let edited = src.replacen(old, new, 1);
        assert!(Pack::load(edited.as_bytes()).is_err(), "loaded {new:?}");
    }
    // The error is Go's: the rule and the value, the line it is on.
    let err = Pack::load(
        src.replacen("    params: {min: 1}\n    from: T1", "    params: {min: 9007199254740992}\n    from: T1", 1)
            .as_bytes(),
    )
    .unwrap_err()
    .to_string();
    assert!(err.contains("params.min 9007199254740992 must be from 0 to 9007199254740991"), "{err}");
    // Quoted, the same words are strings.
    let quoted = src.replacen("kind: admission", "kind: \"yes\"", 1);
    let err = Pack::load(quoted.as_bytes()).unwrap_err().to_string();
    assert!(err.contains("scope.kind must be admission, got \"yes\""), "{err}");
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
    let p = Pack::load(&read("pack-source.yaml")).unwrap_or_else(|e| panic!("{e}"));
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
    let src = ladder_source();
    let p = Pack::load(src.as_bytes()).unwrap();
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
