// SPDX-License-Identifier: Apache-2.0

//! The engine (go/guard/engine.go NewEngine, checkWith, decide): the checks
//! a loaded pack configures, run in the engine's order, composed into one
//! decision.

use std::collections::{BTreeMap, BTreeSet};

use super::action::{Action, History};
use super::checks::{
    check_caps_unconfigured, check_counterparty_list, check_dedupe, check_rate_cap, check_required_fields,
    check_verify_before_dispatch, not_evaluable, Constraint, ConstraintResult, ListConfig, ListEntry, RateCapConfig,
};
use super::pack::{self, Pack, Wicket};
use super::profile::{pack_name, Profile};
use super::Error;
use crate::canon::{self, Value};
use crate::instant;
use crate::yaml::{Fields, Node};

/// engine.go portedDefinitions, for the definitions the asg/inference pack
/// cites: the exact definitions this port implements, by id and digest. A
/// pack citing any other version of one of them, or any other definition,
/// is refused: a changed definition may mean changed semantics.
pub(crate) const PORTED_DEFINITIONS: [(&str, &str); 4] = [
    ("dedupe/1.0.0", "18ab5d489f1e5774d576b8f99897edd4f4b20f609b85683456a3e3b6b4912abb"),
    // asg/inference/0.1.0 (capsule-engine#263): rate_cap compares a peer's
    // advertised rates with two caps, and counterparty_list is the user's
    // list, their values set by a profile (Engine::with_profile).
    ("rate_cap/1.0.0", "a2628f677ddbeb6cdd6a62ac44403b51ac03f878b40e1c8d1627c95039c13d93"),
    ("counterparty_list/1.0.0", "d95162931a6c1f88c763f2fd221d63b238888c1bdd7c6d594123ee5671e18f65"),
    // The fold the pack cites because the loader requires one; no check of
    // the pack reads it.
    ("spend.weekly/3.1.0", "f379cd4b2b12284fa5d682845f23bcd7ab1ae8b8d1dd827cd9b5dd7069735997"),
];

/// engine.go integrityChecks and ownAskRule (engine.py _INTEGRITY_CHECKS,
/// _OWN_ASK_RULE): a declared ASK never makes one of them ask.
const INTEGRITY_CHECKS: [&str; 5] =
    ["dedupe", "verify_before_dispatch", "single_commitment", "promise_never", "release_on_acceptance"];
const OWN_ASK_RULE: [&str; 2] = ["action_class_gate", "counterparty_list"];

/// engine.go escalatable (engine.py _ESCALATABLE): failures that ask an
/// approver rather than refuse.
const ESCALATABLE: [&str; 2] = ["caps", "counterparty_seen_before"];

/// engine.go neverHeld (engine.py _NEVER_HELD_CHECKS): the checks a
/// taxonomy mismatch never holds.
const NEVER_HELD: [&str; 8] = [
    "dedupe",
    "verify_before_dispatch",
    "single_commitment",
    "promise_never",
    "release_on_acceptance",
    "credential_pattern",
    "plan_containment",
    "required_fields",
];

/// A decision's outcome (capsule.py:75-77).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Allow,
    Deny,
    Escalate,
}

impl Outcome {
    /// The outcome as the engine seals it.
    pub fn as_str(self) -> &'static str {
        match self {
            Outcome::Allow => "allow",
            Outcome::Deny => "deny",
            Outcome::Escalate => "escalate",
        }
    }
}

/// The outcome of one check run (engine.go Decision): allow, deny or
/// escalate, and the constraints in the engine's order (dedupe, caps,
/// verify_before_dispatch, the configured checks in pack order, then
/// required_fields).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Decision {
    outcome: Outcome,
    constraints: Vec<Constraint>,
    not_evaluated: Vec<String>,
}

impl Decision {
    /// The decision's outcome.
    pub fn outcome(&self) -> Outcome {
        self.outcome
    }

    /// The constraints, in the engine's order.
    pub fn constraints(&self) -> &[Constraint] {
        &self.constraints
    }

    /// engine.go Decision.Constraint: the named constraint.
    pub fn constraint(&self, id: &str) -> Option<&Constraint> {
        self.constraints.iter().find(|c| c.id() == id)
    }

    /// The checks that could not be evaluated and fail closed
    /// (GuardDecision.not_evaluated_checks): the action was refused for
    /// them.
    pub fn not_evaluated(&self) -> &[String] {
        &self.not_evaluated
    }
}

/// A check the engine configures from the pack, in pack order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Check {
    RateCap,
    CounterpartyList,
}

/// Evaluates actions against one loaded pack (engine.go Engine), for the
/// asg/inference pack's checks.
#[derive(Clone, Debug)]
pub struct Engine {
    pack: Pack,
    dedupe_window_days: i64,
    order: Vec<Check>,
    rate_cap: RateCapConfig,
    list: ListConfig,
    /// The pack's action types and the fields each requires
    /// (required_fields.go requiredFieldsOf).
    required_fields: BTreeMap<String, Vec<String>>,
    /// The configured checks whose rules all declare ASK (askWickets).
    ask_checks: BTreeSet<String>,
}

fn config_err(w: &Wicket, m: String) -> Error {
    Error::Load(format!("{} config (a key this port does not implement is refused): {m}", w.id))
}

/// engine.go decodeConfig for rate_cap's config.
fn rate_cap_config(w: &Wicket) -> Result<RateCapConfig, Error> {
    let read = || -> Result<RateCapConfig, String> {
        let mut f = Fields::of(&w.config, w.id.clone())?;
        let cfg = RateCapConfig {
            action_classes: f.opt_strs("action_classes")?.unwrap_or_default(),
            max_input_msat_per_million: f.int("max_input_msat_per_million")?,
            max_output_msat_per_million: f.int("max_output_msat_per_million")?,
        };
        f.done()?;
        Ok(cfg)
    };
    read().map_err(|m| config_err(w, m))
}

/// engine.go decodeConfig for counterparty_list's config.
fn list_config(w: &Wicket) -> Result<ListConfig, Error> {
    let read = || -> Result<ListConfig, String> {
        let mut f = Fields::of(&w.config, w.id.clone())?;
        let action_classes = f.opt_strs("action_classes")?.unwrap_or_default();
        let mode = f.str("mode")?;
        let mut entries = Vec::new();
        for e in f.seq("entries")? {
            let mut ef = Fields::of(e, format!("{} entries[]", w.id))?;
            entries.push(ListEntry { kind: ef.str("kind")?, value: ef.str("value")?, fp_alg: ef.str("fp_alg")? });
            ef.done()?;
        }
        let disposition = f.str("disposition")?;
        f.done()?;
        Ok(ListConfig { action_classes, mode, entries, disposition })
    };
    read().map_err(|m| config_err(w, m))
}

/// engine.go askWickets (packs/install.py ask_wickets): the configured
/// checks bound to at least one rule, every one of which declares ASK,
/// leaving out the integrity checks and the two with their own ask rule.
fn ask_wickets(p: &Pack, order: &[&str]) -> BTreeSet<String> {
    let mut declared: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for r in &p.rules {
        let check = r.check.as_str();
        if !order.contains(&check) || INTEGRITY_CHECKS.contains(&check) || OWN_ASK_RULE.contains(&check) {
            continue;
        }
        declared.entry(check).or_default().insert(&r.default_disposition);
    }
    declared.into_iter().filter(|(_, d)| d.len() == 1 && d.contains("ASK")).map(|(c, _)| c.to_string()).collect()
}

impl Engine {
    /// The engine over the embedded asg/inference/0.1.0 pack
    /// (go/packs/inference-0.1.0), each definition it cites held to its
    /// digest.
    pub fn inference() -> Result<Engine, Error> {
        Engine::new(pack::load(&pack::INFERENCE_0_1_0)?)
    }

    /// engine.go NewEngine: an engine over a loaded pack, refusing one whose
    /// cited definitions are not exactly the ported ones.
    fn new(p: Pack) -> Result<Engine, Error> {
        let ported = |id: &str, digest: &str| PORTED_DEFINITIONS.iter().any(|(i, d)| *i == id && *d == digest);
        for w in &p.wickets {
            if !ported(&w.id, &w.digest) {
                return Err(Error::Load(format!(
                    "wicket {} at digest {} is not a definition this port implements or pins",
                    w.id, w.digest
                )));
            }
        }
        for f in &p.folds {
            if !ported(&f.id, &f.digest) {
                return Err(Error::Load(format!(
                    "fold {} at digest {} is not a definition this port implements or pins",
                    f.id, f.digest
                )));
            }
        }
        let mut required_fields = BTreeMap::new();
        for s in &p.action_semantics {
            for f in &s.required_fields {
                if Action::default().field_set(f).is_none() {
                    return Err(Error::Load(format!(
                        "action type {} requires {f:?}, which this port's Action does not carry",
                        s.action_type
                    )));
                }
            }
            required_fields.insert(s.action_type.clone(), s.required_fields.clone());
        }
        let dedupe = p.wicket("dedupe").ok_or_else(|| Error::Load("pack cites no dedupe wicket".into()))?;
        let (method, window_days) = {
            let read = || -> Result<(String, i64), String> {
                let mut f = Fields::of(&dedupe.config, dedupe.id.clone())?;
                let v = (f.str("method")?, f.int("window_days")?);
                f.done()?;
                Ok(v)
            };
            read().map_err(|m| config_err(dedupe, m))?
        };
        if method != "exact_match_index_v0" || window_days <= 0 {
            return Err(Error::Load(format!(
                "dedupe method {method:?} window {window_days} is not what this port implements"
            )));
        }
        let mut order = Vec::new();
        let mut order_names = Vec::new();
        let mut rate_cap = RateCapConfig::default();
        let mut list = ListConfig::default();
        for w in &p.wickets {
            match w.check.as_str() {
                // Run by the engine in its own order, whether a pack cites
                // them or not.
                "dedupe" | "verify_before_dispatch" => continue,
                "rate_cap" => {
                    rate_cap = rate_cap_config(w)?;
                    order.push(Check::RateCap);
                }
                "counterparty_list" => {
                    list = list_config(w)?;
                    list.check().map_err(Error::Load)?;
                    order.push(Check::CounterpartyList);
                }
                other => {
                    return Err(Error::Load(format!(
                        "wicket {} configures check {other:?}, which this port does not implement",
                        w.id
                    )))
                }
            }
            order_names.push(w.check.as_str());
        }
        let ask_checks = ask_wickets(&p, &order_names);
        Ok(Engine { dedupe_window_days: window_days, order, rate_cap, list, required_fields, ask_checks, pack: p })
    }

    /// The pack's id, e.g. asg/inference/0.1.0.
    pub fn pack_id(&self) -> &str {
        &self.pack.id
    }

    /// The pack's definition digest, the one capsule-engine computes.
    pub fn definition_digest(&self) -> &str {
        &self.pack.definition_digest
    }

    /// profile.go WithProfile (policy/resolve.py configured_wickets): an
    /// engine deciding with the profile's values laid over its pack's
    /// wickets. Each starts from the wicket's own config, so what the
    /// profile omits is the wicket's default. It refuses values for a pack
    /// other than this engine's and for a check the pack does not cite, and
    /// caps limits, which this port does not apply from a profile.
    pub fn with_profile(&self, p: &Profile) -> Result<Engine, Error> {
        let name = pack_name(&self.pack.id);
        if let Some(n) = p.packs.keys().find(|n| n.as_str() != name) {
            return Err(Error::Check(format!("profile sets values for pack {n:?}, which this engine does not run")));
        }
        let values = p.packs.get(name).cloned().unwrap_or_default();
        if values.caps.is_some() {
            return Err(Error::Check("profile sets caps limits, which this port does not apply from a profile".into()));
        }
        let mut rate_cap = self.rate_cap.clone();
        let mut list = self.list.clone();
        if let Some(w) = self.pack.wicket("rate_cap") {
            rate_cap = rate_cap_config(w)?;
        }
        if let Some(w) = self.pack.wicket("counterparty_list") {
            list = list_config(w)?;
        }
        if let Some(caps) = &values.rate_cap {
            let w = self
                .pack
                .wicket("rate_cap")
                .ok_or_else(|| Error::Check("profile sets rate_cap, which no wicket of this pack runs".into()))?;
            for (key, v) in caps {
                let declared = matches!(&w.config, Node::Map(m) if m.iter().any(|(k, _)| k == key));
                if !declared {
                    return Err(Error::Check(format!(
                        "profile sets rate_cap.{key}, which wicket {} declares no default for",
                        w.id
                    )));
                }
                if key == "max_input_msat_per_million" {
                    rate_cap.max_input_msat_per_million = *v;
                } else {
                    rate_cap.max_output_msat_per_million = *v;
                }
            }
        }
        if let Some(l) = &values.list {
            if self.pack.wicket("counterparty_list").is_none() {
                return Err(Error::Check("profile sets counterparty_list, which no wicket of this pack runs".into()));
            }
            list.mode.clone_from(&l.mode);
            list.entries.clone_from(&l.entries);
            if !l.disposition.is_empty() {
                list.disposition.clone_from(&l.disposition);
            }
            list.check().map_err(Error::Check)?;
        }
        Ok(Engine { rate_cap, list, ..self.clone() })
    }

    /// engine.go Check and checkWith: decides `a` against the history of
    /// earlier records: dedupe, caps and verify_before_dispatch in the
    /// engine's order, then the configured checks in pack order, then
    /// required_fields. An action citing a mandate capsule, or carrying both
    /// an equivalence key and its digest, is refused, and so is one whose
    /// evidence has no canonical encoding (an integer outside ±(2^53-1)).
    pub fn decide(&self, a: &Action, h: &History) -> Result<Decision, Error> {
        if a.cited_mandate_capsule_id.is_some() {
            return Err(Error::Check(
                "the action cites a mandate capsule; verify_before_dispatch needs the capsule verifier, which this port does not have"
                    .into(),
            ));
        }
        a.check_equivalence_key()?;
        let at = instant::parse(&a.timestamp).map_err(|e| Error::Check(format!("timestamp: {e}")))?;
        let mut cs = vec![
            check_dedupe(a, h, at, self.dedupe_window_days)?,
            // engine.go runCheck parses the timestamp for caps too; with no
            // caps definition it reads nothing else.
            check_caps_unconfigured(),
            check_verify_before_dispatch(),
        ];
        for check in &self.order {
            cs.push(match check {
                Check::RateCap => check_rate_cap(a, &self.rate_cap),
                Check::CounterpartyList => check_counterparty_list(a, &self.list),
            });
        }
        if let Some(c) = check_required_fields(a, &self.required_fields) {
            cs.push(c);
        }
        let mut cs = self.compare_taxonomy(a, cs);
        let approver = self.pack.taxonomy.has_approver(a.class());
        let mut asks = self.ask_checks.clone();
        if self.list.disposition == "ask" {
            // engine.py: a counterparty_list configured disposition ask joins
            // the failures that ask an approver.
            asks.insert("counterparty_list".into());
        }
        let mut outcome = decide(&cs, approver, &asks);
        // A would-allow held only by its record's taxonomy is refused.
        if outcome == Outcome::Allow && a.taxonomy_version.as_deref().is_some_and(|v| v != self.pack.taxonomy.version) {
            outcome = Outcome::Deny;
        }
        // engine.py check (#137): a check that fails closed and could not be
        // evaluated refuses the action, whatever else would allow or ask.
        let not_evaluated: Vec<String> = cs
            .iter()
            .filter(|c| c.fails_closed && c.result == ConstraintResult::NotApplicable)
            .map(|c| c.id.clone())
            .collect();
        if !not_evaluated.is_empty() {
            outcome = Outcome::Deny;
        }
        for c in &mut cs {
            c.evidence_digest =
                canon::digest(&c.evidence).map_err(|e| Error::Check(format!("{} evidence: {e}", c.id)))?;
        }
        Ok(Decision { outcome, constraints: cs, not_evaluated })
    }

    /// engine.go compareTaxonomy: an action at a taxonomy_version other than
    /// the vendored table's leaves every check that reads action_class not
    /// evaluable, naming both versions (TaxonomyHeldEvidence).
    fn compare_taxonomy(&self, a: &Action, cs: Vec<Constraint>) -> Vec<Constraint> {
        let engine = &self.pack.taxonomy.version;
        let Some(version) = a.taxonomy_version.as_deref().filter(|v| *v != engine) else {
            return cs;
        };
        let reason = format!(
            "taxonomy_version {version} on the action; the engine's action taxonomy is version {engine}: action_class is not evaluated against a different table"
        );
        cs.into_iter()
            .map(|c| {
                // With no caps definition, caps reads nothing keyed on the
                // class, so a mismatch does not hold it.
                if NEVER_HELD.contains(&c.id.as_str()) || c.id == "caps" {
                    return c;
                }
                // engine.py _taxonomy_held: the n/a evidence with the
                // versions it was held on.
                let mut n = not_evaluable(&c.id, &c.method, reason.clone());
                n.evidence = canon::obj([
                    ("constraint_id", canon::s(&c.id)),
                    ("in_scope", Value::Bool(true)),
                    ("missing_field", Value::Null),
                    (
                        "taxonomy_mismatch",
                        canon::obj([
                            ("record_taxonomy_version", canon::s(version)),
                            ("engine_taxonomy_version", canon::s(engine)),
                        ]),
                    ),
                ]);
                n
            })
            .collect()
    }
}

/// engine.go decide (engine.py _decide): a clean run allows; when every
/// failure is escalatable and the action's class has an approver_role, it
/// escalates; any other failure denies.
fn decide(cs: &[Constraint], has_approver: bool, asks: &BTreeSet<String>) -> Outcome {
    let failed: Vec<&Constraint> = cs.iter().filter(|c| c.result == ConstraintResult::Fail).collect();
    if failed.is_empty() {
        return Outcome::Allow;
    }
    let all_escalatable =
        failed.iter().all(|c| ESCALATABLE.contains(&c.id.as_str()) || asks.contains(&c.id) || c.asks_approver);
    if all_escalatable && has_approver {
        Outcome::Escalate
    } else {
        Outcome::Deny
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// go/guard/testdata/engine-definitions.json is the engine's digest of
    /// every catalog definition and the current version of each served
    /// pack (scripts/generate_go_packs.py). Every definition this port
    /// implements is one of them at that digest, and the pack it embeds is
    /// the current inference pack.
    #[test]
    fn ported_definitions_are_the_engine_s() {
        let doc: serde_json::Value =
            serde_json::from_str(include_str!("../../../../go/guard/testdata/engine-definitions.json")).unwrap();
        for (id, digest) in PORTED_DEFINITIONS {
            let kind = if id.starts_with("spend.") { "folds" } else { "wickets" };
            assert_eq!(doc[kind][id].as_str(), Some(digest), "{id}");
        }
        assert_eq!(doc["current"]["inference"].as_str(), Some(pack::INFERENCE_0_1_0.dir));
    }

    #[test]
    fn the_inference_pack_loads_at_its_pinned_digest() {
        let e = Engine::inference().unwrap();
        assert_eq!(e.pack_id(), "asg/inference/0.1.0");
        assert_eq!(e.definition_digest(), "b8410fcf29faa0c027012a76bd275d397e210b788a6aa64098e96c4cd09098ed");
        assert_eq!(e.order, [Check::RateCap, Check::CounterpartyList]);
        assert!(e.ask_checks.is_empty());
        assert_eq!(e.rate_cap.max_input_msat_per_million, canon::MAX_SAFE_INTEGER);
        assert_eq!(e.list.mode, "deny");
        assert_eq!(e.required_fields["inference.purchase"], ["target", "equivalence_key"]);
    }
}
