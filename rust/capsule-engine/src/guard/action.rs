// SPDX-License-Identifier: Apache-2.0

//! The normalized action a check decides (go/guard/action.go Action,
//! DecodeAction; python/capsule_engine/guards/action.py Action), the record
//! a decision leaves (Record, RecordFor) and the in-memory history the next
//! check reads (MemoryHistory).

use std::collections::BTreeMap;

use serde_json::{Map, Value};

use super::engine::{Decision, Outcome};
use super::Error;
use crate::canon;
use crate::instant;

/// One normalized candidate action (action.go Action), for the fields the
/// asg/inference pack's checks read. Absent optional fields are None.
///
/// Go's Action carries every field of every pack; this port reads only the
/// ones that bear on an inference purchase, and Action::decode refuses any
/// other field rather than ignore one Go would read.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Action {
    pub verb: String,
    pub operator: String,
    pub developer: String,
    pub action_class: Option<String>,
    pub action_id: String,
    /// "decide" when the action names none (action.py:85).
    pub action_type: String,
    pub timestamp: String,
    /// The amount the action moves, in minor units: what dedupe keys an act
    /// on when the caller gives no equivalence_key.
    pub amount_minor: Option<i64>,
    pub currency: Option<String>,
    pub target: Option<String>,
    /// A cited mandate capsule, which verify_before_dispatch would have to
    /// re-verify: such an action is refused (Engine::decide).
    pub cited_mandate_capsule_id: Option<String>,
    /// The caller's key for the exchange (the inference's own id): dedupe
    /// compares only its digest, and no decision seals the key itself.
    pub equivalence_key: Option<String>,
    /// The digest of a caller's key, as a decision seals it, for an action
    /// read back from one (action.go EquivalenceKeyDigest).
    pub equivalence_key_digest: Option<String>,
    pub model_id: Option<String>,
    pub provider: Option<String>,
    /// The selected peer's advertised rates, in millisatoshis per million
    /// input and output tokens (mesh-llm Pricing), read by rate_cap.
    pub input_msat_per_million: Option<i64>,
    pub output_msat_per_million: Option<i64>,
    pub taxonomy_version: Option<String>,
    /// What dedupe reads beside the key fields (action.go ReturnedMinor,
    /// ReversesRef, DealID).
    pub returned_minor: Option<i64>,
    pub reverses_ref: Option<String>,
    pub deal_id: Option<String>,
    /// The counterparty's keyed fingerprints by kind, and their algorithm
    /// (action.go counterpartyIDs, counterpartyFPAlg): read by
    /// counterparty_list, never sealed.
    pub(crate) counterparty_ids: BTreeMap<String, String>,
    pub(crate) counterparty_fp_alg: String,
}

/// The fields Action::decode reads, by their normalized (JSON) name.
const FIELDS: [&str; 23] = [
    "verb",
    "operator",
    "developer",
    "action_class",
    "action_id",
    "action_type",
    "timestamp",
    "amount_minor",
    "currency",
    "target",
    "cited_mandate_capsule_id",
    "equivalence_key",
    "equivalence_key_digest",
    "model_id",
    "provider",
    "input_msat_per_million",
    "output_msat_per_million",
    "taxonomy_version",
    "returned_minor",
    "reverses_ref",
    "deal_id",
    "counterparty_ids",
    "counterparty_fp_alg",
];

fn decode_err(m: impl std::fmt::Display) -> Error {
    Error::Decode(format!("action: {m}"))
}

/// A JSON string field, or None when absent or null (a Go *string).
fn opt_str(m: &Map<String, Value>, k: &str) -> Result<Option<String>, Error> {
    match m.get(k) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.clone())),
        Some(_) => Err(decode_err(format!("{k} must be a string"))),
    }
}

/// A JSON integer field in int64, or None when absent or null (a Go
/// *int64, which refuses a fraction, an exponent and a quoted number).
fn opt_int(m: &Map<String, Value>, k: &str) -> Result<Option<i64>, Error> {
    match m.get(k) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => {
            n.as_i64().map(Some).ok_or_else(|| decode_err(format!("{k} must be an integer in int64")))
        }
        Some(_) => Err(decode_err(format!("{k} must be an integer"))),
    }
}

impl Action {
    /// action.go DecodeAction: one Action as strict JSON. action_type
    /// defaults to "decide"; the timestamp is required, so a verdict never
    /// depends on when it ran. An action carrying both equivalence_key and
    /// equivalence_key_digest is refused (errKeyAndDigest), and so is any
    /// field this port does not read.
    pub fn decode(raw: &[u8]) -> Result<Action, Error> {
        let doc: Value = serde_json::from_slice(raw).map_err(decode_err)?;
        let Value::Object(m) = doc else {
            return Err(decode_err("must be a JSON object"));
        };
        if let Some(k) = m.keys().find(|k| !FIELDS.contains(&k.as_str())) {
            return Err(decode_err(format!("unknown field {k:?}: not one this port reads")));
        }
        // takeCounterpartyFingerprints: ids map a kind to a fingerprint, and
        // fp_alg is a string; null is absent.
        let counterparty_ids = match m.get("counterparty_ids") {
            None | Some(Value::Null) => BTreeMap::new(),
            Some(Value::Object(ids)) => ids
                .iter()
                .map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect::<Option<BTreeMap<_, _>>>()
                .ok_or_else(|| decode_err("counterparty_ids must map a kind to a fingerprint"))?,
            Some(_) => return Err(decode_err("counterparty_ids must map a kind to a fingerprint")),
        };
        let counterparty_fp_alg = opt_str(&m, "counterparty_fp_alg")
            .map_err(|_| decode_err("counterparty_fp_alg must be a string"))?
            .unwrap_or_default();
        let a = Action {
            verb: opt_str(&m, "verb")?.unwrap_or_default(),
            operator: opt_str(&m, "operator")?.unwrap_or_default(),
            developer: opt_str(&m, "developer")?.unwrap_or_default(),
            action_class: opt_str(&m, "action_class")?,
            action_id: opt_str(&m, "action_id")?.unwrap_or_default(),
            action_type: opt_str(&m, "action_type")?.filter(|t| !t.is_empty()).unwrap_or_else(|| "decide".into()),
            timestamp: opt_str(&m, "timestamp")?.unwrap_or_default(),
            amount_minor: opt_int(&m, "amount_minor")?,
            currency: opt_str(&m, "currency")?,
            target: opt_str(&m, "target")?,
            cited_mandate_capsule_id: opt_str(&m, "cited_mandate_capsule_id")?,
            equivalence_key: opt_str(&m, "equivalence_key")?,
            equivalence_key_digest: opt_str(&m, "equivalence_key_digest")?,
            model_id: opt_str(&m, "model_id")?,
            provider: opt_str(&m, "provider")?,
            input_msat_per_million: opt_int(&m, "input_msat_per_million")?,
            output_msat_per_million: opt_int(&m, "output_msat_per_million")?,
            taxonomy_version: opt_str(&m, "taxonomy_version")?,
            returned_minor: opt_int(&m, "returned_minor")?,
            reverses_ref: opt_str(&m, "reverses_ref")?,
            deal_id: opt_str(&m, "deal_id")?,
            counterparty_ids,
            counterparty_fp_alg,
        };
        a.check_equivalence_key().map_err(|e| decode_err(e.message()))?;
        if a.verb.is_empty() || a.operator.is_empty() || a.developer.is_empty() {
            return Err(decode_err("verb, operator and developer are required"));
        }
        instant::parse(&a.timestamp).map_err(|e| decode_err(format!("timestamp: {e}")))?;
        Ok(a)
    }

    /// action.go checkEquivalenceKey: an action carrying both a key and its
    /// digest is refused (errKeyAndDigest).
    pub(crate) fn check_equivalence_key(&self) -> Result<(), Error> {
        if self.equivalence_key.is_some() && self.equivalence_key_digest.is_some() {
            return Err(Error::Check(
                "equivalence_key and equivalence_key_digest are both set; a caller sets the key, and only a replayed capsule carries the digest"
                    .into(),
            ));
        }
        Ok(())
    }

    /// action.go class: the action class, or "".
    pub(crate) fn class(&self) -> &str {
        self.action_class.as_deref().unwrap_or("")
    }

    /// required_fields.go fieldSet (required_fields.py _unset, negated) for
    /// a field this port's Action carries, or None for one it does not: set
    /// is not None and not "" (equivalence_key also when only its sealed
    /// digest is, _SEALED_AS).
    pub(crate) fn field_set(&self, name: &str) -> Option<bool> {
        let text = |v: &Option<String>| v.as_deref().is_some_and(|s| !s.is_empty());
        Some(match name {
            "verb" => !self.verb.is_empty(),
            "operator" => !self.operator.is_empty(),
            "developer" => !self.developer.is_empty(),
            "action_id" => !self.action_id.is_empty(),
            "action_type" => !self.action_type.is_empty(),
            "timestamp" => !self.timestamp.is_empty(),
            "action_class" => text(&self.action_class),
            "currency" => text(&self.currency),
            "target" => text(&self.target),
            "cited_mandate_capsule_id" => text(&self.cited_mandate_capsule_id),
            "equivalence_key" => text(&self.equivalence_key_digest) || text(&self.equivalence_key),
            "equivalence_key_digest" => text(&self.equivalence_key_digest),
            "model_id" => text(&self.model_id),
            "provider" => text(&self.provider),
            "taxonomy_version" => text(&self.taxonomy_version),
            "reverses_ref" => text(&self.reverses_ref),
            "deal_id" => text(&self.deal_id),
            "amount_minor" => self.amount_minor.is_some(),
            "input_msat_per_million" => self.input_msat_per_million.is_some(),
            "output_msat_per_million" => self.output_msat_per_million.is_some(),
            "returned_minor" => self.returned_minor.is_some(),
            _ => return None,
        })
    }
}

/// One prior record, as the checks read it (action.go Record): the fields
/// of a decision capsule dedupe consults. The equivalence key digest is a
/// local-only field (AGENTS.md): a record lives in this machine's history
/// and is never exported by this crate.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Record {
    pub capsule_id: String,
    pub action_id: String,
    pub action_type: String,
    pub operator: String,
    pub developer: String,
    pub timestamp: String,
    /// disposition.decision: accept, reject or needs_input.
    pub decision: Option<String>,
    pub target: Option<String>,
    pub amount_minor: Option<i64>,
    pub action_class: Option<String>,
    pub taxonomy_version: Option<String>,
    pub returned_minor: Option<i64>,
    pub reverses_ref: Option<String>,
    pub deal_id: Option<String>,
    pub currency: Option<String>,
    /// asg_payload.equivalence_key_digest: the digest of the caller's key,
    /// never the key (capsule-engine #134).
    pub equivalence_key_digest: Option<String>,
}

/// engine.go RecordFor: the ledger record a decision leaves, as the checks
/// read it later (disposition per capsule.py:81-85), under the capsule id
/// the caller sealed it as (the Go and Rust guards decide; their caller
/// seals).
pub fn record_for(a: &Action, d: &Decision, capsule_id: impl Into<String>) -> Record {
    let decision = match d.outcome() {
        Outcome::Allow => "accept",
        Outcome::Deny => "reject",
        Outcome::Escalate => "needs_input",
    };
    Record {
        capsule_id: capsule_id.into(),
        action_id: a.action_id.clone(),
        action_type: a.action_type.clone(),
        operator: a.operator.clone(),
        developer: a.developer.clone(),
        timestamp: a.timestamp.clone(),
        decision: Some(decision.into()),
        target: a.target.clone(),
        amount_minor: a.amount_minor,
        action_class: a.action_class.clone(),
        taxonomy_version: a.taxonomy_version.clone(),
        returned_minor: a.returned_minor,
        reverses_ref: a.reverses_ref.clone(),
        deal_id: a.deal_id.clone(),
        currency: a.currency.clone(),
        equivalence_key_digest: sealed_key_of(a),
    }
}

/// engine.go sealedKeyOf: the equivalence key digest the engine's decision
/// on `a` seals (capsule.py _payload_extension, #134), or None when `a`
/// carries no key.
fn sealed_key_of(a: &Action) -> Option<String> {
    match &a.equivalence_key {
        // A JSON string always digests.
        Some(k) => canon::digest(&canon::s(k)).ok(),
        None => a.equivalence_key_digest.clone(),
    }
}

/// history.go MemoryHistory: earlier records held in memory, in log order,
/// what the next check reads.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct History {
    records: Vec<Record>,
}

impl History {
    /// An empty history.
    pub fn new() -> History {
        History::default()
    }

    /// Appends a record, the newest.
    pub fn append(&mut self, r: Record) {
        self.records.push(r);
    }

    /// The records, in log order.
    pub fn records(&self) -> &[Record] {
        &self.records
    }
}

impl From<Vec<Record>> for History {
    fn from(records: Vec<Record>) -> History {
        History { records }
    }
}
