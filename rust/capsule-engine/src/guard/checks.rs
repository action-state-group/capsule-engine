// SPDX-License-Identifier: Apache-2.0

//! The checks the asg/inference pack runs, each the Go port's
//! (go/guard/checks.go, rate_cap.go, counterparty_list.go,
//! required_fields.go) and so capsule-engine's (guards/checks/...), with the
//! constraint and evidence helpers they share (go/guard/outcome.go). Each
//! check's reason is the Python check's: no reason names a counterparty
//! reference or a value a list holds.

use std::collections::BTreeMap;

use super::action::{Action, History};
use super::Error;
use crate::canon::{self, int_or_null, str_or_null, Value};
use crate::instant::Instant;

/// A constraint's result (outcome.go Result).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConstraintResult {
    Pass,
    Fail,
    /// "n/a": the rule did not apply, or applied and could not be evaluated.
    NotApplicable,
}

impl ConstraintResult {
    /// The result as the engine seals it: pass, fail or n/a.
    pub fn as_str(self) -> &'static str {
        match self {
            ConstraintResult::Pass => "pass",
            ConstraintResult::Fail => "fail",
            ConstraintResult::NotApplicable => "n/a",
        }
    }
}

/// One check's result (outcome.go Constraint): its id, result, method, the
/// evidence the engine digests into evidence_digest (capsule.py:164), and
/// its reason.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Constraint {
    pub(crate) id: String,
    pub(crate) result: ConstraintResult,
    pub(crate) method: String,
    pub(crate) evidence: Value,
    pub(crate) evidence_digest: String,
    pub(crate) reason: String,
    /// An in-scope n/a that must not be allowed past (base.py
    /// CheckOutcome.fails_closed).
    pub(crate) fails_closed: bool,
    /// A failure an approver may resolve (base.py CheckOutcome.asks_approver;
    /// dedupe: the same act in another deal).
    pub(crate) asks_approver: bool,
    /// The capsule a dedupe failure matched.
    pub(crate) matched: Option<String>,
}

impl Constraint {
    /// The check's id.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// The check's result.
    pub fn result(&self) -> ConstraintResult {
        self.result
    }

    /// The method the check ran ("" where the engine names none).
    pub fn method(&self) -> &str {
        &self.method
    }

    /// outcome.go EvidenceDigest: the evidence_digest the engine seals for
    /// this result, JSON-DIGEST of its evidence.
    pub fn evidence_digest(&self) -> &str {
        &self.evidence_digest
    }

    /// Why the check decided as it did, as capsule-engine's check words it.
    pub fn reason(&self) -> &str {
        &self.reason
    }

    /// outcome.go MatchedCapsuleID: the earlier record a dedupe failure
    /// matched.
    pub fn matched_capsule_id(&self) -> Option<&str> {
        self.matched.as_deref()
    }
}

/// outcome.go decided.
fn decided(id: &str, method: &str, result: ConstraintResult, evidence: Value, reason: impl Into<String>) -> Constraint {
    Constraint {
        id: id.into(),
        result,
        method: method.into(),
        evidence,
        evidence_digest: String::new(),
        reason: reason.into(),
        fails_closed: false,
        asks_approver: false,
        matched: None,
    }
}

/// outcome.go notApplicableEvidence (capsule.py:146-160).
pub(crate) fn not_applicable_evidence(id: &str, in_scope: bool, field: Option<&str>) -> Value {
    canon::obj([
        ("constraint_id", canon::s(id)),
        ("in_scope", Value::Bool(in_scope)),
        ("missing_field", str_or_null(field)),
    ])
}

/// outcome.go outOfScope: an n/a where the rule did not apply.
pub(crate) fn out_of_scope(id: &str, method: &str, reason: &str) -> Constraint {
    decided(id, method, ConstraintResult::NotApplicable, not_applicable_evidence(id, false, None), reason)
}

/// outcome.go missingField: an n/a where the rule applied and `field` was
/// absent.
fn missing_field(id: &str, method: &str, field: &str, reason: String) -> Constraint {
    decided(id, method, ConstraintResult::NotApplicable, not_applicable_evidence(id, true, Some(field)), reason)
}

/// outcome.go notEvaluable: an n/a where the rule applied and could not be
/// evaluated, for the reason given.
pub(crate) fn not_evaluable(id: &str, method: &str, reason: String) -> Constraint {
    decided(id, method, ConstraintResult::NotApplicable, not_applicable_evidence(id, true, None), reason)
}

/// rate_cap.py and counterparty_list.py: an action of a class the rule is
/// not configured for.
const NOT_CONFIGURED: &str = "the rule is not configured for this action class";

// ---- dedupe -----------------------------------------------------------------

const DEDUPE_METHOD: &str = "exact_match_index_v0";

/// checks.go sealedEquivalenceKey (action.py sealed_equivalence_key, #134):
/// json_digest of the caller's key, what a decision seals and dedupe
/// compares, never the raw key.
fn sealed_equivalence_key(key: &str) -> Result<String, Error> {
    canon::digest(&canon::s(key)).map_err(|e| Error::Check(e.to_string()))
}

fn digest(v: &Value) -> Result<String, Error> {
    canon::digest(v).map_err(|e| Error::Check(e.to_string()))
}

/// checks.go actKey (dedupe.py _act_key) for an act stated against a pinned
/// taxonomy: operator, developer, the act's class, its target and amount.
fn act_key(
    operator: &str,
    developer: &str,
    class: Option<&str>,
    target: Option<&str>,
    amount: Option<i64>,
) -> Result<String, Error> {
    digest(&canon::obj([
        ("operator", canon::s(operator)),
        ("developer", canon::s(developer)),
        ("action_class", str_or_null(class)),
        ("target", str_or_null(target)),
        ("amount_minor", int_or_null(amount)),
    ]))
}

/// checks.go refundKey (dedupe.py _act_key for an act moving money in).
fn refund_key(
    operator: &str,
    developer: &str,
    class: Option<&str>,
    target: Option<&str>,
    returned: Option<i64>,
    reverses: Option<&str>,
) -> Result<String, Error> {
    digest(&canon::obj([
        ("operator", canon::s(operator)),
        ("developer", canon::s(developer)),
        ("action_class", str_or_null(class)),
        ("target", str_or_null(target)),
        ("returned_minor", int_or_null(returned)),
        ("reverses_ref", str_or_null(reverses)),
    ]))
}

/// checks.go verbOf (dedupe.py:27-29): the action_id prefix before the
/// first "/".
fn verb_of(action_id: &str) -> &str {
    action_id.split('/').next().unwrap_or("")
}

/// checks.go equivalenceKeyForAction (dedupe.py
/// equivalence_key_for_action): the caller's key as the digest a decision
/// seals, else the default formula.
fn key_for_action(a: &Action) -> Result<String, Error> {
    if let Some(k) = &a.equivalence_key {
        return sealed_equivalence_key(k);
    }
    if let Some(d) = &a.equivalence_key_digest {
        return Ok(d.clone());
    }
    if a.taxonomy_version.is_some() && (a.returned_minor.is_some() || a.reverses_ref.is_some()) {
        return refund_key(
            &a.operator,
            &a.developer,
            a.action_class.as_deref(),
            a.target.as_deref(),
            a.returned_minor,
            a.reverses_ref.as_deref(),
        );
    }
    if a.taxonomy_version.is_some() {
        // action.go engineAmount: a normalized action's amount_minor.
        return act_key(&a.operator, &a.developer, a.action_class.as_deref(), a.target.as_deref(), a.amount_minor);
    }
    digest(&canon::obj([
        ("operator", canon::s(&a.operator)),
        ("developer", canon::s(&a.developer)),
        ("action_type", canon::s(&a.action_type)),
        ("verb", canon::s(&a.verb)),
        ("target", str_or_null(a.target.as_deref())),
    ]))
}

/// checks.go equivalenceKeyForRecord (dedupe.py
/// equivalence_key_for_capsule): the sealed digest of the caller's key, else
/// the default formula over the record's own fields.
fn key_for_record(r: &super::action::Record) -> Result<String, Error> {
    if let Some(d) = &r.equivalence_key_digest {
        return Ok(d.clone());
    }
    if r.taxonomy_version.is_some() && (r.returned_minor.is_some() || r.reverses_ref.is_some()) {
        return refund_key(
            &r.operator,
            &r.developer,
            r.action_class.as_deref(),
            r.target.as_deref(),
            r.returned_minor,
            r.reverses_ref.as_deref(),
        );
    }
    if r.taxonomy_version.is_some() {
        return act_key(&r.operator, &r.developer, r.action_class.as_deref(), r.target.as_deref(), r.amount_minor);
    }
    digest(&canon::obj([
        ("operator", canon::s(&r.operator)),
        ("developer", canon::s(&r.developer)),
        ("action_type", canon::s(&r.action_type)),
        ("verb", canon::s(verb_of(&r.action_id))),
        ("target", str_or_null(r.target.as_deref())),
    ]))
}

/// checks.go anotherDealReason (dedupe.py _ANOTHER_DEAL_REASON): it names
/// nothing from the earlier act, which may be with another counterparty.
const ANOTHER_DEAL_REASON: &str =
    "the same act was taken in another deal inside the window: confirm it is not a duplicate";

/// checks.go checkDedupe (dedupe.py check_dedupe): exact-match equivalence
/// over the records of the same action_type (of every action_type when the
/// action's taxonomy is pinned) whose timestamp is at or after the window's
/// start; the first match in log order in the same deal fails, and a match
/// only in another deal fails asking an approver.
pub(crate) fn check_dedupe(a: &Action, h: &History, at: Instant, window_days: i64) -> Result<Constraint, Error> {
    const ID: &str = "dedupe";
    let key = key_for_action(a)?;
    let since = at.minus_days(window_days);
    let mut other: Option<&super::action::Record> = None;
    for r in h.records() {
        // history.go Query: the action_type where the taxonomy is not pinned,
        // and a time at or after since, both read to the microsecond. A
        // record's time is read as Go's parseInstant reads it (instant::parse
        // accepts exactly what time.Parse(RFC3339Nano) does, a comma fraction
        // included), and one Go cannot read does not match there either
        // (history.go matches, checks.go checkDedupe), so it is passed over
        // here as Go passes it over.
        if a.taxonomy_version.is_none() && r.action_type != a.action_type {
            continue;
        }
        let Ok(ts) = crate::instant::parse(&r.timestamp) else { continue };
        if ts.micros() < since.micros() || ts < since {
            continue;
        }
        if key_for_record(r)? != key {
            continue;
        }
        let in_another_deal = matches!((&a.deal_id, &r.deal_id), (Some(x), Some(y)) if x != y);
        if !in_another_deal {
            // dedupe.py _repeat_in_this_deal: the reason names the earlier
            // act only when both state the same deal, or neither states one.
            let reason = if a.deal_id == r.deal_id {
                let short: String = r.capsule_id.chars().take(16).collect();
                format!("equivalent action already recorded ({short}…)")
            } else {
                "equivalent action already recorded".to_string()
            };
            let evidence =
                canon::obj([("equivalence_key", canon::s(&key)), ("matched_capsule_id", canon::s(&r.capsule_id))]);
            let mut c = decided(ID, DEDUPE_METHOD, ConstraintResult::Fail, evidence, reason);
            c.matched = Some(r.capsule_id.clone());
            return Ok(c);
        }
        other.get_or_insert(r);
    }
    if let Some(o) = other {
        // dedupe.py _repeat_in_another_deal: the earlier act's capsule id,
        // amount, currency and time are evidence only, never in the reason.
        let evidence = canon::obj([
            ("equivalence_key", canon::s(&key)),
            ("repeat", canon::s("other_deal")),
            ("matched_capsule_id", canon::s(&o.capsule_id)),
            ("matched_amount_minor", int_or_null(o.amount_minor)),
            ("matched_currency", str_or_null(o.currency.as_deref())),
            ("matched_at", canon::s(&o.timestamp)),
        ]);
        let mut c = decided(ID, DEDUPE_METHOD, ConstraintResult::Fail, evidence, ANOTHER_DEAL_REASON);
        c.matched = Some(o.capsule_id.clone());
        c.asks_approver = true;
        return Ok(c);
    }
    Ok(decided(
        ID,
        DEDUPE_METHOD,
        ConstraintResult::Pass,
        canon::obj([("equivalence_key", canon::s(key))]),
        "no equivalent action in window",
    ))
}

// ---- caps and verify_before_dispatch ------------------------------------------

/// checks.go checkCaps with no caps definition (engine.py _check_caps,
/// e0e664d): out of scope, with no method.
pub(crate) fn check_caps_unconfigured() -> Constraint {
    out_of_scope("caps", "", "no caps definition is configured")
}

/// checks.go checkVerifyBeforeDispatch (verify_before_dispatch.py:24-34):
/// an action citing no mandate is out of scope. One citing a mandate is
/// refused before any check runs (Engine::decide).
pub(crate) fn check_verify_before_dispatch() -> Constraint {
    out_of_scope("verify_before_dispatch", "agent_action_capsule.verify", "action cites no mandate capsule")
}

// ---- rate_cap ---------------------------------------------------------------

const RATE_CAP_METHOD: &str = "rate_threshold_v0";

/// rate_cap.go rateCapKeys (rate_cap.py RATE_FIELDS; profile.py
/// RATE_CAP_KEYS): each rate field and the cap it is compared with, in the
/// order the check reads them.
pub(crate) const RATE_CAP_KEYS: [(&str, &str); 2] = [
    ("input_msat_per_million", "max_input_msat_per_million"),
    ("output_msat_per_million", "max_output_msat_per_million"),
];

/// rate_cap.go rateCapConfig: rate_cap's config.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct RateCapConfig {
    pub(crate) action_classes: Vec<String>,
    pub(crate) max_input_msat_per_million: i64,
    pub(crate) max_output_msat_per_million: i64,
}

impl RateCapConfig {
    fn cap_for(&self, key: &str) -> i64 {
        if key == "max_input_msat_per_million" {
            self.max_input_msat_per_million
        } else {
            self.max_output_msat_per_million
        }
    }
}

fn rate_for(a: &Action, field: &str) -> Option<i64> {
    if field == "input_msat_per_million" {
        a.input_msat_per_million
    } else {
        a.output_msat_per_million
    }
}

/// rate_cap.go checkRateCap (rate_cap.py check_rate_cap): a rate over its
/// cap fails, at its cap it passes; an action missing either rate is n/a,
/// in scope, naming the first one missing, and fails closed.
pub(crate) fn check_rate_cap(a: &Action, cfg: &RateCapConfig) -> Constraint {
    const ID: &str = "rate_cap";
    if !cfg.action_classes.iter().any(|c| c == a.class()) {
        return out_of_scope(ID, RATE_CAP_METHOD, NOT_CONFIGURED);
    }
    let mut rates = BTreeMap::new();
    for (field, _) in RATE_CAP_KEYS {
        let Some(r) = rate_for(a, field) else {
            let mut c = missing_field(
                ID,
                RATE_CAP_METHOD,
                field,
                format!("the action carries no {field}; the price cannot be read, so it is refused"),
            );
            c.fails_closed = true;
            return c;
        };
        rates.insert(field, r);
    }
    let mut over = Vec::new();
    let mut notes = Vec::new();
    for (field, cap) in RATE_CAP_KEYS {
        let (limit, observed) = (cfg.cap_for(cap), rates[field]);
        if observed > limit {
            over.push(canon::obj([
                ("cap", canon::s(cap)),
                ("cap_msat_per_million", Value::Int(limit)),
                ("observed_msat_per_million", Value::Int(observed)),
            ]));
            notes.push(format!("{cap} {limit}, observed {observed}"));
        }
    }
    let evidence = canon::obj([
        ("input_msat_per_million", Value::Int(rates["input_msat_per_million"])),
        ("output_msat_per_million", Value::Int(rates["output_msat_per_million"])),
        ("max_input_msat_per_million", Value::Int(cfg.max_input_msat_per_million)),
        ("max_output_msat_per_million", Value::Int(cfg.max_output_msat_per_million)),
        ("over", Value::Arr(over)),
    ]);
    if notes.is_empty() {
        decided(ID, RATE_CAP_METHOD, ConstraintResult::Pass, evidence, "both rates are at or under their caps")
    } else {
        decided(ID, RATE_CAP_METHOD, ConstraintResult::Fail, evidence, notes.join("; "))
    }
}

// ---- counterparty_list --------------------------------------------------------

const LIST_METHOD: &str = "exact_reference_match_v0";

/// counterparty_list.go's closed sets (counterparty_list.py MODES,
/// DISPOSITIONS, CLEAR_KINDS, FINGERPRINT_KINDS).
pub(crate) const LIST_MODES: [&str; 2] = ["allow", "deny"];
pub(crate) const LIST_DISPOSITIONS: [&str; 2] = ["ask", "deny"];
const CLEAR_KINDS: [&str; 1] = ["target"];
pub(crate) const FINGERPRINT_KINDS: [&str; 2] = ["domain", "payee"];

/// counterparty_list.go ListEntry (counterparty_list.py ListEntry): fp_alg
/// is set exactly when kind is a fingerprint kind.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ListEntry {
    pub(crate) kind: String,
    pub(crate) value: String,
    pub(crate) fp_alg: String,
}

impl ListEntry {
    /// counterparty_list.go canon: the entry as the engine digests it,
    /// fp_alg only on a fingerprint.
    pub(crate) fn canon(&self) -> Value {
        let mut members =
            vec![("kind".to_string(), canon::s(&self.kind)), ("value".to_string(), canon::s(&self.value))];
        if !self.fp_alg.is_empty() {
            members.push(("fp_alg".to_string(), canon::s(&self.fp_alg)));
        }
        Value::Obj(members)
    }

    /// counterparty_list.go ListEntry.check.
    pub(crate) fn check(&self) -> Result<(), String> {
        if CLEAR_KINDS.contains(&self.kind.as_str()) {
            if !self.fp_alg.is_empty() {
                return Err(format!("counterparty_list entry of kind {:?} carries an fp_alg", self.kind));
            }
        } else if FINGERPRINT_KINDS.contains(&self.kind.as_str()) {
            if self.fp_alg.is_empty() {
                return Err(format!("counterparty_list entry of kind {:?} needs an fp_alg", self.kind));
            }
        } else {
            return Err(format!("counterparty_list entry kind {:?} must be one of [target domain payee]", self.kind));
        }
        if self.value.is_empty() {
            return Err(format!("counterparty_list entry of kind {:?} has no value", self.kind));
        }
        Ok(())
    }
}

/// counterparty_list.go listConfig: the classes it applies to, and the list
/// a profile may replace.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ListConfig {
    pub(crate) action_classes: Vec<String>,
    pub(crate) mode: String,
    pub(crate) entries: Vec<ListEntry>,
    pub(crate) disposition: String,
}

impl ListConfig {
    /// counterparty_list.go listConfig.check: an unknown mode or
    /// disposition, or an entry whose kind and fp_alg do not go together.
    pub(crate) fn check(&self) -> Result<(), String> {
        if !LIST_MODES.contains(&self.mode.as_str()) {
            return Err(format!("counterparty_list mode {:?} must be one of {LIST_MODES:?}", self.mode));
        }
        if !LIST_DISPOSITIONS.contains(&self.disposition.as_str()) {
            return Err(format!(
                "counterparty_list disposition {:?} must be one of {LIST_DISPOSITIONS:?}",
                self.disposition
            ));
        }
        self.entries.iter().try_for_each(ListEntry::check)
    }
}

/// counterparty_list.go sortedEntries (counterparty_list.py
/// sorted_entries): the entries ordered by the digest of each, stably.
pub(crate) fn sorted_entries(entries: &[ListEntry]) -> Result<Vec<ListEntry>, String> {
    let mut keyed = entries
        .iter()
        .map(|e| canon::digest(&e.canon()).map(|d| (d, e.clone())).map_err(|e| e.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    keyed.sort_by(|x, y| x.0.cmp(&y.0));
    Ok(keyed.into_iter().map(|(_, e)| e).collect())
}

/// counterparty_list.go readReference (counterparty_list.py _read): the
/// action's reference of `kind`, in the form the list's entries of that kind
/// use, or the field that is missing.
fn read_reference(a: &Action, kind: &str, fp_algs: &[&str]) -> Result<ListEntry, String> {
    if CLEAR_KINDS.contains(&kind) {
        return match a.target.as_deref() {
            Some(t) if !t.is_empty() => Ok(ListEntry { kind: kind.into(), value: t.into(), fp_alg: String::new() }),
            _ => Err("target".into()),
        };
    }
    if a.counterparty_ids.is_empty() || a.counterparty_fp_alg.is_empty() {
        return Err("counterparty".into());
    }
    if fp_algs != [a.counterparty_fp_alg.as_str()] {
        return Err("counterparty.fp_alg".into());
    }
    match a.counterparty_ids.get(kind) {
        Some(v) if !v.is_empty() => {
            Ok(ListEntry { kind: kind.into(), value: v.clone(), fp_alg: a.counterparty_fp_alg.clone() })
        }
        _ => Err(format!("counterparty.ids.{kind}")),
    }
}

/// counterparty_list.go checkCounterpartyList (counterparty_list.py
/// check_counterparty_list): deny fails a listed counterparty, allow an
/// unlisted one; every kind the list names must be readable on the action,
/// or the result is n/a, in scope, naming what was missing. Its reason never
/// names a reference (AGENTS.md, local-only payload fields).
pub(crate) fn check_counterparty_list(a: &Action, cfg: &ListConfig) -> Constraint {
    const ID: &str = "counterparty_list";
    if !cfg.action_classes.iter().any(|c| c == a.class()) {
        return out_of_scope(ID, LIST_METHOD, NOT_CONFIGURED);
    }
    let unusable = |field: &str| {
        missing_field(
            ID,
            LIST_METHOD,
            field,
            format!("the action carries no usable {field}; the counterparty list could not be checked"),
        )
    };
    let listed = match sorted_entries(&cfg.entries) {
        Ok(l) => l,
        Err(e) => return not_evaluable(ID, LIST_METHOD, e),
    };
    if listed.is_empty() && a.target.as_deref().unwrap_or("").is_empty() && a.counterparty_ids.is_empty() {
        return unusable("counterparty");
    }
    let mut kinds: Vec<&str> = Vec::new();
    for e in &listed {
        if !kinds.contains(&e.kind.as_str()) {
            kinds.push(&e.kind);
        }
    }
    kinds.sort_unstable();
    let mut read = Vec::new();
    for kind in kinds {
        let mut fp_algs: Vec<&str> = Vec::new();
        for e in &listed {
            if e.kind == kind && !e.fp_alg.is_empty() && !fp_algs.contains(&e.fp_alg.as_str()) {
                fp_algs.push(&e.fp_alg);
            }
        }
        match read_reference(a, kind, &fp_algs) {
            Ok(r) => read.push(r),
            Err(missing) => return unusable(&missing),
        }
    }
    let matched = listed.iter().find(|e| read.contains(e));
    let list_digest = match canon::digest(&Value::Arr(listed.iter().map(ListEntry::canon).collect())) {
        Ok(d) => d,
        Err(e) => return not_evaluable(ID, LIST_METHOD, e.to_string()),
    };
    let evidence = canon::obj([
        ("match_rule", canon::s("exact")),
        ("mode", canon::s(&cfg.mode)),
        ("list_digest", canon::s(list_digest)),
        ("list_size", Value::Int(listed.len() as i64)),
        ("read", Value::Arr(read.iter().map(ListEntry::canon).collect())),
        ("matched_entry", matched.map_or(Value::Null, ListEntry::canon)),
    ]);
    // deny: a match fails. allow: a miss fails.
    let fails = matched.is_some() == (cfg.mode == "deny");
    let verdict = if matched.is_some() { "is on" } else { "is not on" };
    let reason = format!("the counterparty {verdict} the {} list (consulted {} entries)", cfg.mode, listed.len());
    let result = if fails { ConstraintResult::Fail } else { ConstraintResult::Pass };
    decided(ID, LIST_METHOD, result, evidence, reason)
}

// ---- required_fields ----------------------------------------------------------

const REQUIRED_FIELDS_METHOD: &str = "action_semantics_v0";

/// required_fields.go checkRequiredFields (required_fields.py
/// check_required_fields), or None when the action's verb names no action
/// type in `required`: with every field set it passes; with one missing it
/// is n/a, in scope, naming the first in the pack's order, and fails closed.
pub(crate) fn check_required_fields(a: &Action, required: &BTreeMap<String, Vec<String>>) -> Option<Constraint> {
    const ID: &str = "required_fields";
    let fields = required.get(&a.verb)?;
    for f in fields {
        // Engine::new refuses a pack requiring a field Action does not carry.
        if !a.field_set(f).unwrap_or(false) {
            let mut c = missing_field(
                ID,
                REQUIRED_FIELDS_METHOD,
                f,
                format!("the action carries no {f}, which action type {} requires; it is refused", a.verb),
            );
            c.fails_closed = true;
            return Some(c);
        }
    }
    Some(decided(
        ID,
        REQUIRED_FIELDS_METHOD,
        ConstraintResult::Pass,
        canon::obj([("action_type", canon::s(&a.verb)), ("required_fields", canon::strs(fields))]),
        format!("the action carries every field action type {} requires", a.verb),
    ))
}
