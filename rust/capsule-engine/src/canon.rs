// SPDX-License-Identifier: Apache-2.0

//! RFC 8785 JCS and JSON-DIGEST as agent_action_capsule (0.6.0,
//! agent_action_capsule/canonical.py) implements them, and as the Go port's
//! `canon` package (go/canon) reproduces them: the encoding capsule-engine
//! digests pack, wicket, fold and evidence objects with.

use sha2::{Digest as _, Sha256};
use std::fmt::Write as _;

/// The ±(2^53-1) bound agent_action_capsule's JCS enforces (go/canon
/// maxSafeInteger).
pub const MAX_SAFE_INTEGER: i64 = (1 << 53) - 1;

/// The JSON value tree a canonical object is built from: only the kinds the
/// engine's JCS accepts, so no floats (go/canon Value).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    Str(String),
    Int(i64),
    Bool(bool),
    Null,
    Arr(Vec<Value>),
    /// Members in any order: JCS sorts them.
    Obj(Vec<(String, Value)>),
}

/// Why a value has no canonical encoding.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CanonError(pub String);

impl std::fmt::Display for CanonError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "canon: {}", self.0)
    }
}

impl std::error::Error for CanonError {}

/// A JSON string (go/canon Str).
pub fn s(v: impl Into<String>) -> Value {
    Value::Str(v.into())
}

/// A JSON object (go/canon Obj).
pub fn obj<const N: usize>(members: [(&str, Value); N]) -> Value {
    Value::Obj(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

/// A JSON array of strings (go/canon Strs).
pub fn strs<S: AsRef<str>>(items: &[S]) -> Value {
    Value::Arr(items.iter().map(|i| s(i.as_ref())).collect())
}

/// `v`, or null (go/guard strOrNull).
pub fn str_or_null(v: Option<&str>) -> Value {
    v.map_or(Value::Null, s)
}

/// `v`, or null (go/guard intOrNull).
pub fn int_or_null(v: Option<i64>) -> Value {
    v.map_or(Value::Null, Value::Int)
}

/// go/canon FromJSON: one JSON document as a Value. Numbers must be integers
/// in int64; JCS formats any other number by ECMAScript rules this module
/// does not implement, so they are refused rather than digested differently.
/// serde_json keeps the last of two members with the same name, so a
/// duplicate key is not refused here as go/canon refuses it.
pub fn from_json(v: &serde_json::Value) -> Result<Value, CanonError> {
    Ok(match v {
        serde_json::Value::Null => Value::Null,
        serde_json::Value::Bool(b) => Value::Bool(*b),
        serde_json::Value::String(t) => Value::Str(t.clone()),
        serde_json::Value::Number(n) => match n.as_i64() {
            Some(i) => Value::Int(i),
            None => {
                return Err(CanonError(format!(
                    "{n} is not an integer in int64; JCS for other numbers is not implemented"
                )))
            }
        },
        serde_json::Value::Array(items) => Value::Arr(items.iter().map(from_json).collect::<Result<_, _>>()?),
        serde_json::Value::Object(members) => {
            Value::Obj(members.iter().map(|(k, v)| Ok((k.clone(), from_json(v)?))).collect::<Result<_, CanonError>>()?)
        }
    })
}

/// go/canon writeJCSString: RFC 8785 string serialization as
/// agent_action_capsule implements it: the two-character escapes, \u00xx for
/// other controls, and nothing else escaped.
fn write_string(out: &mut String, v: &str) {
    out.push('"');
    for c in v.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{08}' => out.push_str("\\b"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\u{0c}' => out.push_str("\\f"),
            '\r' => out.push_str("\\r"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// go/canon utf16Less: keys compare by their UTF-16 code units.
fn utf16_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    a.encode_utf16().cmp(b.encode_utf16())
}

fn write_jcs(out: &mut String, v: &Value) -> Result<(), CanonError> {
    match v {
        Value::Str(t) => write_string(out, t),
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Int(i) => {
            if *i > MAX_SAFE_INTEGER || *i < -MAX_SAFE_INTEGER {
                return Err(CanonError(format!("integer {i} outside the safe range")));
            }
            let _ = write!(out, "{i}");
        }
        Value::Arr(items) => {
            out.push('[');
            for (n, item) in items.iter().enumerate() {
                if n > 0 {
                    out.push(',');
                }
                write_jcs(out, item)?;
            }
            out.push(']');
        }
        Value::Obj(members) => {
            let mut sorted: Vec<&(String, Value)> = members.iter().collect();
            sorted.sort_by(|x, y| utf16_cmp(&x.0, &y.0));
            out.push('{');
            for (n, (k, val)) in sorted.iter().map(|m| (&m.0, &m.1)).enumerate() {
                if n > 0 {
                    out.push(',');
                    if *k == sorted[n - 1].0 {
                        return Err(CanonError(format!("duplicate object key {k:?}")));
                    }
                }
                write_string(out, k);
                out.push(':');
                write_jcs(out, val)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

/// go/canon JCS: v's RFC 8785 serialization, as agent_action_capsule's jcs
/// writes it; an integer outside the safe range has none.
pub fn jcs(v: &Value) -> Result<String, CanonError> {
    let mut out = String::new();
    write_jcs(&mut out, v)?;
    Ok(out)
}

/// go/canon Digest: agent_action_capsule's JSON-DIGEST, the lowercase-hex
/// SHA-256 of the JCS bytes.
pub fn digest(v: &Value) -> Result<String, CanonError> {
    Ok(sha256_hex(jcs(v)?.as_bytes()))
}

/// The lowercase-hex SHA-256 of `bytes`.
pub fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    // go/canon json_test.go TestFromJSONDigestsAsJCS: the expected value is
    // sha256 over Python's json.dumps(sort_keys=True, separators=(",", ":"),
    // ensure_ascii=False), which is JCS for strings, integers, booleans and
    // null.
    #[test]
    fn from_json_digests_as_jcs() {
        let doc: serde_json::Value =
            serde_json::from_str(r#"{"b": [1, true, null, "é"], "a": {"z": -3, "y": "x"}}"#).unwrap();
        let got = digest(&from_json(&doc).unwrap()).unwrap();
        assert_eq!(got, "79ffbd09966fa56ba9bfb8808a72ba05b82db317304bd302f1f5c5a26d4a6240");
    }

    // go/canon json_test.go TestFromJSONRefuses, for what serde_json reads
    // as a number this module refuses.
    #[test]
    fn from_json_refuses_non_integers() {
        for doc in [r#"{"a": 1.5}"#, r#"{"a": 1e3}"#, r#"{"a": 9223372036854775808}"#] {
            let v: serde_json::Value = serde_json::from_str(doc).unwrap();
            assert!(from_json(&v).is_err(), "accepted {doc}");
        }
    }

    #[test]
    fn jcs_escapes_and_sorts_by_utf16() {
        let v = Value::Obj(vec![
            ("\u{e000}".into(), Value::Int(1)),
            ("\u{1f600}".into(), Value::Int(2)),
            ("a\u{1}\"".into(), Value::Int(3)),
        ]);
        // U+1F600 encodes as a surrogate pair (0xD83D ...), which sorts before
        // U+E000 in UTF-16 though after it by code point.
        assert_eq!(jcs(&v).unwrap(), "{\"a\\u0001\\\"\":3,\"\u{1f600}\":2,\"\u{e000}\":1}");
    }

    #[test]
    fn jcs_refuses_unsafe_integers_and_duplicates() {
        assert!(jcs(&Value::Int(MAX_SAFE_INTEGER)).is_ok());
        assert!(jcs(&Value::Int(MAX_SAFE_INTEGER + 1)).is_err());
        assert!(jcs(&Value::Int(-MAX_SAFE_INTEGER - 1)).is_err());
        let dup = Value::Obj(vec![("k".into(), Value::Null), ("k".into(), Value::Null)]);
        assert!(jcs(&dup).is_err());
    }
}
