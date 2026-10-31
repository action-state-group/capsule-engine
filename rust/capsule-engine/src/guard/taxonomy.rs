// SPDX-License-Identifier: Apache-2.0

//! The action-class table (go/guard/taxonomy.go; capsule-engine
//! guards/action_taxonomy.json, read by guards/classes.py), for the parts
//! the inference pack reads: each class's approver_role, and legacy aliases.

use std::collections::BTreeMap;

use super::Error;
use crate::canon;

/// One row of the table (taxonomy.go actionClass).
#[derive(Clone, Debug)]
struct ActionClass {
    approver_role: Option<String>,
    legacy_aliases: Vec<String>,
}

/// taxonomy.go taxonomy: the classes by canonical name, and legacy name to
/// canonical name.
#[derive(Clone, Debug)]
pub(crate) struct Taxonomy {
    pub(crate) version: String,
    /// The SHA-256 over the table's JCS bytes, what a pack's taxonomy pin
    /// names (go/pack VendoredTaxonomy).
    pub(crate) digest: String,
    classes: BTreeMap<String, ActionClass>,
    aliases: BTreeMap<String, String>,
}

/// taxonomy.go rowKeys (classes.py _ROW_KEYS): every row declares every key.
const ROW_KEYS: [&str; 6] =
    ["name", "trigger_class", "consequential", "fail_open_allowed", "approver_role", "legacy_aliases"];

impl Taxonomy {
    /// taxonomy.go loadTaxonomy: the table as classes.py load_taxonomy_table
    /// parses it, for the parts this port reads.
    pub(crate) fn load(raw: &str) -> Result<Taxonomy, Error> {
        let err = |m: String| Error::Load(format!("action taxonomy: {m}"));
        let doc: serde_json::Value = serde_json::from_str(raw).map_err(|e| err(e.to_string()))?;
        let digest =
            canon::digest(&canon::from_json(&doc).map_err(|e| err(e.to_string()))?).map_err(|e| err(e.to_string()))?;
        let version = doc.get("taxonomy_version").and_then(|v| v.as_str()).unwrap_or_default().to_string();
        let actions = doc.get("actions").and_then(|v| v.as_array()).cloned().unwrap_or_default();
        if version.is_empty() || actions.is_empty() {
            return Err(err("no taxonomy_version or no actions".into()));
        }
        let mut classes = BTreeMap::new();
        for (i, row) in actions.iter().enumerate() {
            let keys = row.as_object().ok_or_else(|| err(format!("actions[{i}] is not an object")))?;
            if keys.len() != ROW_KEYS.len() || ROW_KEYS.iter().any(|k| !keys.contains_key(*k)) {
                return Err(err(format!("actions[{i}] must declare exactly {ROW_KEYS:?}")));
            }
            let name = keys["name"].as_str().unwrap_or_default().to_string();
            let approver_role = match &keys["approver_role"] {
                serde_json::Value::Null => None,
                serde_json::Value::String(s) => Some(s.clone()),
                _ => return Err(err(format!("actions[{i}].approver_role must be a string or null"))),
            };
            let legacy_aliases = keys["legacy_aliases"]
                .as_array()
                .and_then(|a| a.iter().map(|v| v.as_str().map(str::to_string)).collect::<Option<Vec<_>>>());
            if name.is_empty() || !keys["consequential"].is_boolean() || !keys["fail_open_allowed"].is_boolean() {
                return Err(err(format!("actions[{i}] is under-declared")));
            }
            let Some(legacy_aliases) = legacy_aliases else {
                return Err(err(format!("actions[{i}] is under-declared")));
            };
            if classes.insert(name.clone(), ActionClass { approver_role, legacy_aliases }).is_some() {
                return Err(err(format!("action class {name:?} declared more than once")));
            }
        }
        let mut aliases = BTreeMap::new();
        for (name, row) in &classes {
            for alias in &row.legacy_aliases {
                if classes.contains_key(alias) || aliases.insert(alias.clone(), name.clone()).is_some() {
                    return Err(err(format!("legacy alias {alias:?} on {name:?} collides with another name")));
                }
            }
        }
        Ok(Taxonomy { version, digest, classes, aliases })
    }

    /// taxonomy.go canonical: a legacy name maps to its row's name; any
    /// other name is kept as written.
    fn canonical<'a>(&'a self, class: &'a str) -> &'a str {
        self.aliases.get(class).map_or(class, String::as_str)
    }

    /// taxonomy.go approver: classify(class).approver_role; an absent or
    /// unknown class is the fail-closed default, which has none.
    pub(crate) fn has_approver(&self, class: &str) -> bool {
        self.classes.get(self.canonical(class)).is_some_and(|r| r.approver_role.is_some())
    }
}
