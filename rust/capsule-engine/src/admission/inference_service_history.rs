// SPDX-License-Identifier: Apache-2.0

//! A card's served periods from an inference-service-history/0 Standard
//! answer (go/admission/inference_service_history.go
//! InferenceServiceHistoryCounts; capsule-engine#273).

use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

use super::standard_history::{count, is_jcs};
use super::Timestamp;
use crate::canon;

/// inference_service_history.go Served: the node's own count of the
/// exchanges it served in each closed period, from the first period of its
/// stream to the anchor (AAC spec/inference-service-history-profile-v0.md,
/// cited as ISH). Every count is the node's statement, labelled
/// self_reported (ISH §7.1), and the counts were never checked against
/// records, so the coverage they declare is not established as complete
/// (ISH §4.2).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Served {
    /// The definition's period_policy length_s and floor, which a verifier
    /// shows beside the counts (ISH §6.1 item 7).
    pub length_s: i64,
    pub floor: i64,
    pub periods: Vec<ServedPeriod>,
    /// inference_service_history.go Served.Verdicts: the closed periods'
    /// referee_verdicts totals (ISH §5.3); empty when the answer counts no
    /// referee verdicts. Classes are not read (SH-14) and the totals are
    /// never added to `periods` (ISH §5.1).
    pub verdicts: Option<Vec<ServedPeriod>>,
    /// inference_service_history.go Served.VerdictsOnly: the answer counts
    /// referee verdicts and no served exchanges; `periods` is then empty and
    /// not read.
    pub verdicts_only: bool,
}

/// inference_service_history.go ServedPeriod: one closed period's
/// exchanges_served total.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ServedPeriod {
    pub from: Timestamp,
    pub to: Timestamp,
    /// The published total; None when it is suppressed.
    pub total: Option<i64>,
    /// Why `total` is None: SUPPRESSED_BELOW_FLOOR or
    /// SUPPRESSED_COMPLEMENTARY (ISH §6.2). The evaluator reads any other
    /// reason for a missing total as below the floor.
    pub suppressed: String,
}

/// Why a cell is suppressed (ISH §6.2).
pub const SUPPRESSED_BELOW_FLOOR: &str = "below_floor";
pub const SUPPRESSED_COMPLEMENTARY: &str = "complementary";

/// inference_service_history.go CardCheckpoints: what the caller read from
/// the answer's embedded history_card/1 when it verified the card as a
/// direct history_card/1 answer (ISH §5.2): its checkpoints in order, the
/// stream's first to the anchor.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CardCheckpoints {
    pub checkpoints: Vec<CardCheckpoint>,
}

/// inference_service_history.go CardCheckpoint: one checkpoint's time, and
/// the number of records of the stream it covers.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CardCheckpoint {
    pub at: Timestamp,
    pub records: i64,
}

/// inference_service_history.go InferenceServiceHistoryError: why an
/// answer is refused, the History property that does not hold (one of
/// INFERENCE_SERVICE_HISTORY_PROPERTIES) and the detail. A caller records
/// `property` as `Card::reason`; the detail can echo the counterparty's own
/// strings, so it stays out of the decision.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InferenceServiceHistoryError {
    pub property: &'static str,
    pub detail: String,
}

impl std::fmt::Display for InferenceServiceHistoryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "inference-service-history/0 answer: {}: {}", self.property, self.detail)
    }
}

impl std::error::Error for InferenceServiceHistoryError {}

/// inference_service_history.go InferenceServiceHistoryProperties.
pub const INFERENCE_SERVICE_HISTORY_PROPERTIES: [&str; 11] = [
    "definition_form",
    "definition_binding",
    "artifact_form",
    "anchor_match",
    "coverage_declaration_form",
    "coverage_declaration_anchor",
    "units",
    "labels",
    "period_alignment",
    "period_anchor",
    "period_floor",
];

const PROFILE: &str = "inference-service-history/0";
const COUNTS: &str = "period_counts";
const HISTORY_CARD: &str = "history_card";
const SERVED: &str = "exchanges_served";
const VERDICTS: &str = "referee_verdicts";
const LABEL: &str = "self_reported";
/// inference_service_history.go ishPeriodsMax: the most periods one answer
/// may list, over 190 years of weeks.
pub(crate) const PERIODS_MAX: i64 = 10_000;

/// ISH §5.1's closed list of units.
const UNITS: [&str; 7] = [
    "exchanges_served",
    "exchanges_requested",
    "exchanges_unattributed",
    "checkpoints",
    "witness_receipts",
    "referee_verdicts",
    "records",
];

const DEFINITION_KEYS: [&str; 12] = [
    "coverage_declaration",
    "embeds",
    "excludes",
    "kind",
    "level",
    "members",
    "name",
    "period_policy",
    "profile",
    "reads",
    "spec",
    "subjects",
];
const MEMBER_KEYS: [&str; 6] = ["claims", "derivation", "name", "reads", "required", "units"];
const POLICY_KEYS: [&str; 5] = ["epoch", "floor", "length_s", "min_length_s", "suppression"];
const NANOS: i128 = 1_000_000_000;

/// inference_service_history.go InferenceServiceHistoryCounts: a card's
/// served periods from an inference-service-history/0 Standard answer, the
/// definition document's and the artifact's JCS bytes, what the caller read
/// from the embedded card, and the expected_pin the requester sent. Every
/// History property that is a function of these is checked (ISH §4.2, §6.3,
/// §8.3), and a refusal names the one that does not hold: the card is then
/// unverifiable. The envelope and the card's own checks
/// (history_card_content) are the caller's. Only a Standard definition is
/// read, and only counts in the period_counts shape of ISH §9. None when
/// the answer counts no served exchanges.
pub fn inference_service_history_counts(
    definition: &[u8],
    artifact: &[u8],
    card: &CardCheckpoints,
    pin: &str,
) -> Result<Option<Served>, InferenceServiceHistoryError> {
    let fail = |property: &'static str, detail: String| InferenceServiceHistoryError { property, detail };
    is_jcs(definition).map_err(|e| fail("definition_form", format!("the definition is not JCS bytes: {e}")))?;
    let def: Value = serde_json::from_slice(definition).map_err(|e| fail("definition_form", e.to_string()))?;
    let def = def.as_object().ok_or_else(|| fail("definition_form", "the definition is not an object".into()))?;
    if def.get("profile").and_then(Value::as_str) != Some(PROFILE) {
        return Err(fail("definition_form", "profile not known".into()));
    }
    if def.get("level").and_then(Value::as_str) == Some("detailed") {
        return Err(fail("definition_form", "detailed level not read".into()));
    }
    let d = Definition::of(def).map_err(|(property, why)| fail(property, why))?;
    let digest = canon::sha256_hex(definition);

    is_jcs(artifact).map_err(|e| fail("artifact_form", format!("the artifact is not JCS bytes: {e}")))?;
    let art: Value = serde_json::from_slice(artifact).map_err(|e| fail("artifact_form", e.to_string()))?;
    let art = art.as_object().ok_or_else(|| fail("artifact_form", "the artifact is not an object".into()))?;
    if art.get("derivation").and_then(Value::as_str) != Some(digest.as_str()) {
        return Err(fail("definition_binding", "the artifact's derivation is not the digest of the definition".into()));
    }
    // As Go decodes it into a []string: null is empty, and anything but an
    // array of strings is refused.
    let not_sorted = || fail("artifact_form", "declared is not a sorted list of member names".into());
    let declared = art.get("declared").and_then(strings).ok_or_else(not_sorted)?;
    if !declared.windows(2).all(|w| w[0] < w[1]) {
        return Err(not_sorted());
    }
    for m in &d.members {
        if m.required && !declared.contains(&m.name.as_str()) {
            return Err(fail("artifact_form", format!("the required member {} is not declared", m.name)));
        }
    }
    // The artifact's own members (ISH §8.2), and then each declared one:
    // history_card among them, since it is required.
    let mut keys = vec![
        "anchor",
        "coverage_declaration",
        "declared",
        "derivation",
        "evidence_stream",
        "history_card_digest",
        "subject",
    ];
    for name in &declared {
        if !d.members.iter().any(|m| m.name == *name) || keys.contains(name) {
            return Err(fail("artifact_form", format!("{name} is declared and the definition does not list it")));
        }
        keys.push(name);
    }
    if !declared.contains(&COUNTS) {
        return Err(fail("artifact_form", "the answer declares no counts member".into()));
    }
    if !same_keys(art, &keys) {
        return Err(fail("artifact_form", "the artifact's members are not the ones it declares".into()));
    }
    let anchor = art.get("anchor").and_then(Value::as_str).unwrap_or("");
    let stream = art.get("evidence_stream").and_then(Value::as_str).unwrap_or("");
    if !is_hex_digest(anchor) || stream.is_empty() || art.get("subject") != Some(&json!({"full_history": null})) {
        return Err(fail("artifact_form", "anchor, evidence_stream or subject is not the profile's".into()));
    }
    let hc = art
        .get(HISTORY_CARD)
        .and_then(Value::as_object)
        .ok_or_else(|| fail("artifact_form", "history_card is not an object".into()))?;
    let card_digest =
        jcs_digest(&art[HISTORY_CARD]).ok_or_else(|| fail("artifact_form", "the card is malformed".into()))?;
    if art.get("history_card_digest").and_then(Value::as_str) != Some(card_digest.as_str()) {
        return Err(fail("artifact_form", "the card does not match history_card_digest".into()));
    }
    if hc.get("derivation").and_then(Value::as_str) != Some("history_card/1")
        || ["anchor", "evidence_stream", "subject"].iter().any(|k| hc.get(*k) != art.get(*k))
    {
        return Err(fail("artifact_form", "the card is not for this answer's subject, stream and anchor".into()));
    }
    if anchor != pin {
        return Err(fail("anchor_match", "the answer's anchor is not the expected_pin requested".into()));
    }

    // The coverage declaration (ISH §4.2): the definition's kind and rule,
    // and the members that depend on the anchor.
    let cov = art
        .get("coverage_declaration")
        .and_then(Value::as_object)
        .filter(|c| {
            same_keys(c, &["interval", "kind", "live_set", "rule"])
                && c["kind"] == json!("stream_interval")
                && c["rule"] == json!("all_records")
        })
        .ok_or_else(|| fail("coverage_declaration_form", "not the Standard declaration".into()))?;
    let interval = cov["interval"]
        .as_object()
        .filter(|i| same_keys(i, &["from", "to"]) && i["from"].is_null())
        .ok_or_else(|| fail("coverage_declaration_form", "interval is not from the stream's start".into()))?;
    if interval["to"].as_str() != Some(anchor) {
        return Err(fail("coverage_declaration_anchor", "interval.to is not the anchor".into()));
    }
    // The periods (ISH §6.3), against the card's checkpoints, which never go
    // back in time or in size.
    let cps = &card.checkpoints;
    let Some(last) = cps.last() else {
        return Err(fail("period_alignment", "the card lists no checkpoint".into()));
    };
    if cps.windows(2).any(|w| w[1].at < w[0].at || w[1].records < w[0].records) {
        return Err(fail("period_alignment", "the card's checkpoints go back in time or in size".into()));
    }
    if cov["live_set"] != json!([{"checkpoint": anchor, "count": last.records, "start": 0, "stream": stream}]) {
        return Err(fail(
            "coverage_declaration_form",
            "live_set is not the anchor's records in the answer's stream".into(),
        ));
    }

    let (anchor_s, anchor_ns) = split(last.at);
    if anchor_ns != 0 || !d.on_boundary(anchor_s) {
        return Err(fail("period_anchor", "the anchor's time is not a period boundary".into()));
    }
    let shape = || fail("artifact_form", "counts shape not known".into());
    let counts = art[COUNTS].as_object().filter(|c| same_keys(c, &["periods", "policy"])).ok_or_else(shape)?;
    if counts["policy"].as_str() != Some(d.policy_digest.as_str()) {
        return Err(fail("period_alignment", "the counts name another policy".into()));
    }
    let listed = counts["periods"].as_array().ok_or_else(shape)?;
    // Every closed period, from the one the first checkpoint that commits a
    // record commits into (the one it ends, at a boundary) to the one the
    // anchor closes, each once, in order. Where no checkpoint commits a
    // record, no period is listed.
    let end = d.period_of(anchor_s);
    let mut first = end;
    if let Some(c) = cps.iter().find(|c| c.records > 0) {
        let (first_s, first_ns) = split(c.at);
        first = d.period_of(first_s);
        if d.on_boundary(first_s) && first_ns == 0 {
            first -= 1;
        }
    }
    if end - first > PERIODS_MAX {
        return Err(fail("period_alignment", format!("more than {PERIODS_MAX} periods")));
    }
    if listed.len() as i64 != end - first {
        return Err(fail("period_alignment", "the closed periods are not each listed once".into()));
    }
    let mut served =
        Served { length_s: d.length_s, floor: d.floor, periods: Vec::new(), verdicts: None, verdicts_only: false };
    let has_served = d.counts_units.iter().any(|u| u == SERVED);
    if d.counts_units.iter().any(|u| u == VERDICTS) {
        served.verdicts = Some(Vec::new());
    }
    for (i, raw) in listed.iter().enumerate() {
        let k = first + i as i64;
        let p = raw.as_object().filter(|p| same_keys(p, &["cells", "from", "to"])).ok_or_else(shape)?;
        let (from, to) = (whole_second(&p["from"]), whole_second(&p["to"]));
        let (Some(from), Some(to)) = (from, to) else {
            return Err(fail("period_alignment", format!("periods[{i}] is not the policy's next closed period")));
        };
        if from != d.start_of(k) || to != d.start_of(k + 1) {
            return Err(fail("period_alignment", format!("periods[{i}] is not the policy's next closed period")));
        }
        let totals = d.cells(&p["cells"]).map_err(|(property, why)| fail(property, format!("periods[{i}]: {why}")))?;
        let period = |total: &Total| ServedPeriod {
            from: Timestamp::from_unix_nanos(i128::from(from) * NANOS),
            to: Timestamp::from_unix_nanos(i128::from(to) * NANOS),
            total: total.value,
            suppressed: total.suppressed.clone(),
        };
        if let Some((_, total)) = totals.iter().find(|(u, _)| *u == SERVED) {
            served.periods.push(period(total));
        }
        if let Some((_, total)) = totals.iter().find(|(u, _)| *u == VERDICTS) {
            served.verdicts.get_or_insert_with(Vec::new).push(period(total));
        }
    }
    if !has_served && served.verdicts.is_none() {
        return Ok(None);
    }
    served.verdicts_only = !has_served;
    Ok(Some(served))
}

struct Member {
    name: String,
    required: bool,
}

struct Definition {
    members: Vec<Member>,
    counts_units: Vec<String>,
    epoch: i64,
    length_s: i64,
    floor: i64,
    policy_digest: String,
}

#[derive(Clone)]
struct Total {
    value: Option<i64>,
    suppressed: String,
}

impl Definition {
    /// inference_service_history.go ishDefinitionOf: definition_form for a
    /// Standard definition (ISH §4.1, §8.3).
    fn of(def: &Map<String, Value>) -> Result<Definition, (&'static str, String)> {
        let bad = |why: String| ("definition_form", why);
        if !same_keys(def, &DEFINITION_KEYS) {
            return Err(bad(String::from("not exactly the members of a Standard definition")));
        }
        let name_ok = def["name"].as_str().is_some_and(is_definition_name);
        if def["level"] != json!("standard")
            || def["kind"] != json!("projection")
            || def["embeds"] != json!(["history_card/1"])
            || def["subjects"] != json!(["full_history"])
            || def["coverage_declaration"] != json!({"kind": "stream_interval", "rule": "all_records"})
            || !name_ok
            || !def["spec"].is_string()
        {
            return Err(bad(String::from("a member does not have the value the profile fixes")));
        }
        let excludes = strings(&def["excludes"]);
        if !excludes.is_some_and(|e| e.contains(&"requester identity")) {
            return Err(bad(String::from("excludes does not name requester identity")));
        }
        let members =
            def["members"].as_array().filter(|m| !m.is_empty()).ok_or_else(|| bad(String::from("no members")))?;
        let mut d = Definition {
            members: Vec::new(),
            counts_units: Vec::new(),
            epoch: 0,
            length_s: 0,
            floor: 0,
            policy_digest: String::new(),
        };
        let mut union: Vec<&str> = Vec::new();
        let mut has_counts = false;
        for (i, m) in members.iter().enumerate() {
            let m = m
                .as_object()
                .filter(|m| m.keys().all(|k| MEMBER_KEYS.contains(&k.as_str())))
                .ok_or_else(|| bad(format!("members[{i}] is not a member")))?;
            let name = m
                .get("name")
                .and_then(Value::as_str)
                .filter(|n| !n.is_empty() && !d.members.iter().any(|o| o.name == *n))
                .ok_or_else(|| bad(format!("members[{i}] has no name, or one listed twice")))?;
            let required =
                m.get("required").and_then(Value::as_bool).ok_or_else(|| bad(format!("{name} has no required")))?;
            let reads = m.get("reads").and_then(strings).ok_or_else(|| bad(format!("{name} has no reads")))?;
            for r in reads {
                if !union.contains(&r) {
                    union.push(r);
                }
            }
            let units = m.get("units").and_then(Value::as_array).filter(|u| !u.is_empty());
            let units = units.ok_or_else(|| bad(format!("{name} has no units")))?;
            let mut member_units = Vec::new();
            for (j, u) in units.iter().enumerate() {
                let u = u.as_object().filter(|u| same_keys(u, &["unit", "where"]) && u["where"].is_string());
                let unit = u
                    .and_then(|u| u["unit"].as_str())
                    .ok_or_else(|| bad(format!("{name}: units[{j}] is not {{where, unit}}")))?;
                if !UNITS.contains(&unit) {
                    return Err(("units", format!("{name} counts in {unit:?}, which is not a unit of the profile")));
                }
                member_units.push(unit.to_string());
            }
            if m.get("derivation").is_some_and(|v| !v.is_string()) {
                return Err(bad(format!("{name}: derivation is not a string")));
            }
            match name {
                HISTORY_CARD => {
                    if !required || m.get("derivation") != Some(&json!("history_card/1")) {
                        return Err(bad(String::from("history_card is not a required history_card/1 member")));
                    }
                }
                COUNTS => {
                    d.counts_units = member_units;
                    has_counts = true;
                }
                _ => return Err(bad(format!("counts shape not known: member {name}"))),
            }
            d.members.push(Member { name: name.to_string(), required });
        }
        if !d.members.iter().any(|m| m.name == HISTORY_CARD) {
            return Err(bad(String::from("history_card is not a required history_card/1 member")));
        }
        if !has_counts {
            return Err(bad(String::from("no counts member")));
        }
        if strings(&def["reads"]) != Some(union) {
            return Err(bad(String::from("reads is not the union of the members' reads")));
        }
        let p = def["period_policy"]
            .as_object()
            .filter(|p| same_keys(p, &POLICY_KEYS) && p["suppression"] == json!("complementary"))
            .ok_or_else(|| bad(String::from("period_policy is not the profile's")))?;
        d.epoch = whole_second(&p["epoch"])
            .ok_or_else(|| bad(String::from("period_policy: epoch is not a whole-second UTC time")))?;
        let mut min_length = 0;
        for (k, v) in [("length_s", &mut d.length_s), ("min_length_s", &mut min_length), ("floor", &mut d.floor)] {
            *v = count(p, k)
                .ok()
                .filter(|n| *n > 0)
                .ok_or_else(|| bad(format!("period_policy: {k} is not a positive integer")))?;
        }
        if d.length_s < min_length {
            return Err(bad(String::from("period_policy: length_s is under min_length_s")));
        }
        d.policy_digest =
            jcs_digest(&def["period_policy"]).ok_or_else(|| bad(String::from("period_policy is malformed")))?;
        Ok(d)
    }

    fn period_of(&self, t: i64) -> i64 {
        (t - self.epoch).div_euclid(self.length_s)
    }

    fn start_of(&self, k: i64) -> i64 {
        self.epoch + k * self.length_s
    }

    fn on_boundary(&self, t: i64) -> bool {
        (t - self.epoch).rem_euclid(self.length_s) == 0
    }

    /// inference_service_history.go cells: one period's cells (ISH §6.2,
    /// §7.3, §5.1), and each declared unit's total.
    fn cells(&self, raw: &Value) -> Result<Vec<(String, Total)>, (&'static str, String)> {
        let cells = raw.as_array().ok_or(("artifact_form", "counts shape not known".to_string()))?;
        // (unit, dimension, total) per cell; the dimension is "" on a total.
        let mut seen: Vec<(String, String, Total)> = Vec::new();
        for (j, c) in cells.iter().enumerate() {
            let shape = || ("artifact_form", format!("counts shape not known: cells[{j}]"));
            let c = c.as_object().ok_or_else(shape)?;
            let unit = c.get("unit").and_then(Value::as_str).ok_or_else(shape)?;
            if !UNITS.contains(&unit) || !self.counts_units.iter().any(|u| u == unit) {
                return Err(("units", format!("cells[{j}] counts in {unit:?}, which its member does not declare")));
            }
            if c.get("label").and_then(Value::as_str) != Some(LABEL) {
                return Err(("labels", format!("cells[{j}] is labelled other than self_reported")));
            }
            // A dimension is null on the unit's total, or one stated value.
            let dim = match c.get("dimension") {
                Some(Value::Null) => String::new(),
                Some(v @ Value::Object(m)) if m.len() == 1 && m.values().all(Value::is_string) => {
                    canon::from_json(v).ok().and_then(|v| canon::jcs(&v).ok()).ok_or_else(shape)?
                }
                _ => return Err(("artifact_form", format!("counts shape not known: cells[{j}].dimension"))),
            };
            if seen.iter().any(|(u, d, _)| u == unit && *d == dim) {
                return Err(("period_floor", format!("cells[{j}] is listed twice")));
            }
            let suppressed = c.get("suppressed").and_then(Value::as_str);
            let total = match suppressed {
                Some(why)
                    if same_keys(c, &["dimension", "label", "suppressed", "unit", "value"])
                        && c["value"].is_null()
                        && (why == SUPPRESSED_BELOW_FLOOR || why == SUPPRESSED_COMPLEMENTARY) =>
                {
                    Total { value: None, suppressed: why.to_string() }
                }
                None if same_keys(c, &["dimension", "label", "unit", "value"]) => {
                    let n = count(c, "value").map_err(|e| ("period_floor", format!("cells[{j}].value {e}")))?;
                    if n < self.floor {
                        return Err(("period_floor", format!("cells[{j}] is published below the floor")));
                    }
                    Total { value: Some(n), suppressed: String::new() }
                }
                _ => return Err(("period_floor", format!("cells[{j}] is neither published nor suppressed"))),
            };
            seen.push((unit.to_string(), dim, total));
        }
        let mut totals = Vec::new();
        for unit in &self.counts_units {
            let of_unit: Vec<&(String, String, Total)> = seen.iter().filter(|(u, _, _)| u == unit).collect();
            let Some((_, _, total)) = of_unit.iter().find(|(_, d, _)| d.is_empty()) else {
                return Err(("artifact_form", format!("counts shape not known: no {unit} total")));
            };
            totals.push((unit.clone(), total.clone()));
            let Some(total) = total.value else { continue };
            let dims: Vec<&Total> = of_unit.iter().filter(|(_, d, _)| !d.is_empty()).map(|(_, _, t)| t).collect();
            let withheld = dims.iter().filter(|t| t.value.is_none()).count();
            // Each value is at most 2^53 − 1, so capping the sum just past
            // it keeps it inside i64 and still above any total.
            let published =
                dims.iter().filter_map(|t| t.value).fold(0i64, |s, n| (s + n).min(canon::MAX_SAFE_INTEGER + 1));
            if withheld == 1 {
                return Err(("period_floor", format!("a suppressed {unit} cell can be derived from the total")));
            }
            if published > total || (withheld == 0 && !dims.is_empty() && published != total) {
                return Err(("period_floor", format!("the {unit} cells do not add up to the total")));
            }
        }
        Ok(totals)
    }
}

/// An instant as whole Unix seconds and the nanoseconds past them (Go
/// Time.Unix and Time.Nanosecond).
fn split(t: Timestamp) -> (i64, i64) {
    let n = t.unix_nanos();
    (n.div_euclid(NANOS) as i64, n.rem_euclid(NANOS) as i64)
}

/// inference_service_history.go ishTime: a profile time, a whole second in
/// UTC written YYYY-MM-DDTHH:MM:SSZ, as Unix seconds.
fn whole_second(v: &Value) -> Option<i64> {
    let s = v.as_str()?;
    let b = s.as_bytes();
    let shape = b.len() == 20
        && b.iter().enumerate().all(|(i, c)| match i {
            4 | 7 => *c == b'-',
            10 => *c == b'T',
            13 | 16 => *c == b':',
            19 => *c == b'Z',
            _ => c.is_ascii_digit(),
        });
    if !shape {
        return None;
    }
    Timestamp::parse_rfc3339(s).ok().map(|t| split(t).0)
}

/// A definition name: lowercase ASCII letters, digits and `_`, then `/` and
/// a version number (ISH §4.1).
fn is_definition_name(name: &str) -> bool {
    let Some((stem, version)) = name.split_once('/') else { return false };
    !stem.is_empty()
        && stem.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
        && !version.is_empty()
        && version.bytes().all(|c| c.is_ascii_digit())
}

fn is_hex_digest(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// An array of strings, or None.
fn strings(v: &Value) -> Option<Vec<&str>> {
    v.as_array()?.iter().map(Value::as_str).collect()
}

fn same_keys(m: &Map<String, Value>, keys: &[&str]) -> bool {
    m.len() == keys.len() && keys.iter().all(|k| m.contains_key(*k))
}

/// The SHA-256 of a value's JCS bytes. The artifact is JCS, so these are its
/// own bytes for that member.
fn jcs_digest(v: &Value) -> Option<String> {
    let text = canon::jcs(&canon::from_json(v).ok()?).ok()?;
    Some(hex::encode(Sha256::digest(text.as_bytes())))
}
