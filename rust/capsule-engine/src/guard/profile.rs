// SPDX-License-Identifier: Apache-2.0

//! The operator's policy profile (go/guard/profile.go; capsule-engine
//! policy/profile.py): the values it sets over a pack's wicket defaults.

use std::collections::BTreeMap;

use serde_json::{Map, Value};

use super::checks::{
    sorted_entries, ListConfig, ListEntry, FINGERPRINT_KINDS, LIST_DISPOSITIONS, LIST_MODES, RATE_CAP_KEYS,
};
use super::Error;
use crate::canon::{self, MAX_SAFE_INTEGER};

/// profile.go ProfileFormat (policy/profile.py PROFILE_FORMAT).
pub const PROFILE_FORMAT: &str = "policy-profile/v0";

/// The values one profile sets for one pack (profile.go ProfilePack). None
/// is a value the profile does not set.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ProfilePack {
    /// caps limits, by key and action class. Engine::with_profile refuses
    /// them, as the Go port does.
    pub(crate) caps: Option<BTreeMap<String, BTreeMap<String, i64>>>,
    /// The rate_cap caps it sets, by key.
    pub(crate) rate_cap: Option<BTreeMap<String, i64>>,
    /// The counterparty list it sets; an empty disposition keeps the
    /// wicket's.
    pub(crate) list: Option<ListConfig>,
}

/// A policy-profile/v0 (profile.go Profile): its digest, the one a manifest
/// pins, and its values by pack name (publisher/name, no version).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Profile {
    digest: String,
    pub(crate) packs: BTreeMap<String, ProfilePack>,
}

fn err(m: impl std::fmt::Display) -> Error {
    Error::Decode(format!("profile: {m}"))
}

/// profile.go strictJSON for an object: refuses a key outside `allowed`.
fn object<'a>(v: &'a Value, allowed: &[&str], what: &str) -> Result<&'a Map<String, Value>, Error> {
    let m = v.as_object().ok_or_else(|| err(format!("{what} must be a mapping")))?;
    if let Some(k) = m.keys().find(|k| !allowed.contains(&k.as_str())) {
        return Err(err(format!("{what}: unknown field {k:?}")));
    }
    Ok(m)
}

/// profile.go profilePackName (profile.py PACK_NAME_RE):
/// ^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9-]*$.
fn is_pack_name(name: &str) -> bool {
    let Some((publisher, rest)) = name.split_once('/') else {
        return false;
    };
    let head = |s: &str| s.bytes().next().is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit());
    head(publisher)
        && publisher.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"_.-".contains(&c))
        && head(rest)
        && rest.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}

/// profile.go safeInt: an integer from 0 to 2^53-1; a quoted number is a
/// string and refused.
fn safe_int(v: &Value) -> Option<i64> {
    v.as_i64().filter(|n| (0..=MAX_SAFE_INTEGER).contains(n))
}

impl Profile {
    /// profile.go DecodeProfile: a policy-profile/v0 as profile.py
    /// parse_profile reads it, refusing what it refuses (an unknown key, a
    /// limit or cap that is not an integer from 0 to 2^53-1, a list with no
    /// mode, an entry whose kind and fp_alg do not go together, a
    /// counterparty listed twice, a pack named twice), and a check other
    /// than caps, rate_cap and counterparty_list. Its digest is the JCS
    /// SHA-256 of the canonical form (entries in sorted_entries order).
    pub fn decode(raw: &[u8]) -> Result<Profile, Error> {
        let doc: Value = serde_json::from_slice(raw).map_err(err)?;
        let top = object(&doc, &["format", "packs"], "the document")?;
        let format = top.get("format").and_then(Value::as_str).unwrap_or_default();
        if format != PROFILE_FORMAT {
            return Err(err(format!("format must be {PROFILE_FORMAT:?}, got {format:?}")));
        }
        let entries = top.get("packs").and_then(Value::as_array).ok_or_else(|| err("packs must be a list"))?;
        let mut packs = BTreeMap::new();
        let mut packs_canon = Vec::new();
        for entry in entries {
            let e = object(entry, &["pack", "parameters"], "packs entry")?;
            let name = match e.get("pack") {
                None | Some(Value::Null) => "",
                Some(Value::String(s)) => s.as_str(),
                Some(_) => return Err(err("packs entry: pack must be a string")),
            };
            let params = match e.get("parameters") {
                Some(Value::Object(p)) => p,
                _ => return Err(err(format!("packs[{name:?}].parameters must be a mapping"))),
            };
            if !is_pack_name(name) {
                return Err(err(format!("packs[].pack must be '<publisher>/<name>' with no version, got {name:?}")));
            }
            if packs.contains_key(name) {
                return Err(err(format!("pack {name:?} appears more than once")));
            }
            let (values, canon_params) = decode_pack(name, params)?;
            packs.insert(name.to_string(), values);
            packs_canon.push(canon::obj([("pack", canon::s(name)), ("parameters", canon_params)]));
        }
        let digest = canon::digest(&canon::obj([
            ("format", canon::s(PROFILE_FORMAT)),
            ("packs", canon::Value::Arr(packs_canon)),
        ]))
        .map_err(err)?;
        Ok(Profile { digest, packs })
    }

    /// The profile's digest: the JCS SHA-256 of its canonical form.
    pub fn digest(&self) -> &str {
        &self.digest
    }
}

/// profile.go decodeProfilePack: one pack's parameters, and their canonical
/// form, checks in sorted order.
fn decode_pack(name: &str, raw: &Map<String, Value>) -> Result<(ProfilePack, canon::Value), Error> {
    let mut out = ProfilePack::default();
    let mut checks = Vec::new();
    let mut names: Vec<&String> = raw.keys().collect();
    names.sort();
    for check in names {
        let ctx = format!("packs[{name:?}].parameters[{check:?}]");
        let keys = match &raw[check] {
            Value::Object(m) => Some(m),
            Value::Null => None,
            _ => return Err(err(format!("{ctx} must be a mapping"))),
        };
        let empty = Map::new();
        let keys = keys.unwrap_or(&empty);
        match check.as_str() {
            "caps" => {
                let mut members = Vec::new();
                let mut sorted: Vec<&String> = keys.keys().collect();
                sorted.sort();
                for key in sorted {
                    let (values, v) = decode_class_values(&keys[key], &format!("{ctx}[{key:?}]"))?;
                    out.caps.get_or_insert_with(BTreeMap::new).insert(key.clone(), values);
                    members.push((key.clone(), v));
                }
                checks.push((check.clone(), canon::Value::Obj(members)));
            }
            "rate_cap" => {
                if keys.is_empty() {
                    return Err(err(format!(
                        "{ctx} may set only max_input_msat_per_million and max_output_msat_per_million, and at least one"
                    )));
                }
                let mut caps = BTreeMap::new();
                let mut members = Vec::new();
                let mut sorted: Vec<&String> = keys.keys().collect();
                sorted.sort();
                for key in sorted {
                    if !RATE_CAP_KEYS.iter().any(|(_, cap)| cap == key) {
                        return Err(err(format!(
                            "{ctx} may set only max_input_msat_per_million and max_output_msat_per_million, got {key:?}"
                        )));
                    }
                    let n = safe_int(&keys[key]).ok_or_else(|| {
                        err(format!(
                            "{ctx}[{key:?}] must be an integer from 0 to {MAX_SAFE_INTEGER}, got {}",
                            keys[key]
                        ))
                    })?;
                    caps.insert(key.clone(), n);
                    members.push((key.clone(), canon::Value::Int(n)));
                }
                out.rate_cap = Some(caps);
                checks.push((check.clone(), canon::Value::Obj(members)));
            }
            "counterparty_list" => {
                let (list, v) = decode_list(keys, &ctx)?;
                out.list = Some(list);
                checks.push((check.clone(), v));
            }
            _ => return Err(err(format!("{ctx}: no wicket lets a user set {check:?}"))),
        }
    }
    Ok((out, canon::Value::Obj(checks)))
}

/// profile.go decodeClassValues: a mapping of action class to an integer
/// from 0 to 2^53-1.
fn decode_class_values(raw: &Value, ctx: &str) -> Result<(BTreeMap<String, i64>, canon::Value), Error> {
    let values =
        raw.as_object().ok_or_else(|| err(format!("{ctx} must be a mapping of action class to an integer")))?;
    let mut out = BTreeMap::new();
    let mut members = Vec::new();
    let mut sorted: Vec<&String> = values.keys().collect();
    sorted.sort();
    for class in sorted {
        let n = safe_int(&values[class]).filter(|_| !class.is_empty()).ok_or_else(|| {
            err(format!("{ctx}[{class:?}] must be an integer from 0 to {MAX_SAFE_INTEGER}, got {}", values[class]))
        })?;
        out.insert(class.clone(), n);
        members.push((class.clone(), canon::Value::Int(n)));
    }
    Ok((out, canon::Value::Obj(members)))
}

/// profile.go decodeProfileList (profile.py _parse_list_parameters): mode
/// and entries, and disposition when given (the wicket's otherwise).
fn decode_list(keys: &Map<String, Value>, ctx: &str) -> Result<(ListConfig, canon::Value), Error> {
    if let Some(k) = keys.keys().find(|k| !["mode", "entries", "disposition"].contains(&k.as_str())) {
        return Err(err(format!("{ctx} must set 'mode' and 'entries' and may set 'disposition', got {k:?}")));
    }
    // A key given as null is present, and then refused for its value, as
    // json.RawMessage holds "null" for it.
    let present = |k: &str| keys.get(k);
    let (Some(mode), Some(entries)) = (present("mode"), present("entries")) else {
        return Err(err(format!("{ctx} must set 'mode' and 'entries'")));
    };
    let mode = mode
        .as_str()
        .filter(|m| LIST_MODES.contains(m))
        .ok_or_else(|| err(format!("{ctx}['mode'] must be one of {LIST_MODES:?}, got {mode}")))?
        .to_string();
    let raw_entries = entries.as_array().ok_or_else(|| err(format!("{ctx}['entries'] must be a list")))?;
    let mut list = ListConfig { mode, ..ListConfig::default() };
    for re in raw_entries {
        let entry = decode_entry(re).map_err(|e| err(format!("{ctx}['entries']: {e}")))?;
        entry.check().map_err(|e| err(format!("{ctx}['entries']: {e}")))?;
        if list.entries.contains(&entry) {
            return Err(err(format!("{ctx}['entries'] lists a counterparty more than once")));
        }
        list.entries.push(entry);
    }
    list.entries = sorted_entries(&list.entries).map_err(err)?;
    let mut members = vec![
        ("mode".to_string(), canon::s(&list.mode)),
        ("entries".to_string(), canon::Value::Arr(list.entries.iter().map(ListEntry::canon).collect())),
    ];
    if let Some(d) = present("disposition") {
        list.disposition = d
            .as_str()
            .filter(|d| LIST_DISPOSITIONS.contains(d))
            .ok_or_else(|| err(format!("{ctx}['disposition'] must be one of {LIST_DISPOSITIONS:?}, got {d}")))?
            .to_string();
        members.push(("disposition".to_string(), canon::s(&list.disposition)));
    }
    Ok((list, canon::Value::Obj(members)))
}

/// profile.go decodeListEntry (profile.py _parse_list_entry): {kind:
/// target, value} or {kind: <fingerprint kind>, fp_alg, value}, with exactly
/// those keys and non-empty string values.
fn decode_entry(raw: &Value) -> Result<ListEntry, String> {
    let fields = raw.as_object().ok_or("an entry must be a mapping")?;
    let text = |key: &str| -> Result<String, String> {
        match fields.get(key) {
            Some(Value::String(s)) if !s.is_empty() => Ok(s.clone()),
            other => Err(format!(
                "entry {key} must be a non-empty string, got {}",
                other.map_or_else(String::new, Value::to_string)
            )),
        }
    };
    let kind = text("kind")?;
    let want: &[&str] =
        if FINGERPRINT_KINDS.contains(&kind.as_str()) { &["fp_alg", "kind", "value"] } else { &["kind", "value"] };
    let mut got: Vec<&str> = fields.keys().map(String::as_str).collect();
    got.sort_unstable();
    if got != want {
        return Err(format!("entry of kind {kind:?} must have exactly the keys {want:?}, got {got:?}"));
    }
    let value = text("value")?;
    let fp_alg = if want.len() == 3 { text("fp_alg")? } else { String::new() };
    Ok(ListEntry { kind, value, fp_alg })
}

/// profile.go packName (profile.py pack_name): asg/inference/0.1.0 ->
/// asg/inference.
pub(crate) fn pack_name(pack_id: &str) -> &str {
    pack_id.rsplit_once('/').map_or(pack_id, |(name, _)| name)
}
