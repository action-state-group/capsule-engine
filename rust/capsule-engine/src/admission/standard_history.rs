// SPDX-License-Identifier: Apache-2.0

//! The ladder's inputs from a standard_history/1 card
//! (go/admission/standard_history.go StandardHistoryCounts).

use serde_json::{Map, Value};

use super::{BandCount, Disputes, Error};
use crate::canon;

const CARD: &str = "standard_history/1 card";

/// standard_history.go StandardHistoryCounts: the card's per-band counts as
/// `Card::bands`, and its received disputes as `Card::disputes` (AAC
/// spec/standard-history-v0.md §4, §5.3; evidencebook-skills#87).
///
/// `artifact` is the card's bytes from an answer the caller has verified,
/// card form included; this reads only the bands and disputes members, and
/// refuses them when they do not read. A band tally maps to one BandCount:
/// `subjects` are its decided deals (held plus did_not_hold) and `adverse`
/// those that did not hold; undecided deals are left out. A band whose
/// decade is null (zero-amount deals) is skipped. The currency is the card's
/// SR assetCode as written, with the decade in that asset's own unit.
pub fn standard_history_counts(artifact: &[u8]) -> Result<(Vec<BandCount>, Disputes), Error> {
    // The artifact must be the JCS of the card (AAC standard-history-v0 §3),
    // exactly as pack.rs requires of a pack-definition.json.
    is_jcs(artifact).map_err(|e| Error(format!("{CARD}: the artifact is not JCS bytes: {e}")))?;
    let card: Value = serde_json::from_slice(artifact).map_err(|e| Error(format!("{CARD}: {e}")))?;
    let card = card.as_object().ok_or_else(|| Error(format!("{CARD}: not an object")))?;
    match card.get("derivation") {
        Some(Value::String(d)) if d == "standard_history/1" => {}
        Some(Value::String(d)) => return Err(Error(format!("{CARD}: derivation is {d:?}"))),
        None | Some(Value::Null) => return Err(Error(format!("{CARD}: derivation is \"\""))),
        Some(_) => return Err(Error(format!("{CARD}: derivation must be a string"))),
    }
    let bands = match card.get("bands") {
        None | Some(Value::Null) => return Err(Error(format!("{CARD}: bands is missing"))),
        Some(Value::Array(items)) => items,
        Some(_) => return Err(Error(format!("{CARD}: bands must be an array"))),
    };
    let mut out = Vec::new();
    for (i, b) in bands.iter().enumerate() {
        let at = format!("{CARD}: bands[{i}]");
        let b = b.as_object().ok_or_else(|| Error(format!("{at} must be an object")))?;
        let currency = match b.get("currency") {
            Some(Value::String(c)) if !c.is_empty() => c.clone(),
            _ => return Err(Error(format!("{at}: currency must be a non-empty string"))),
        };
        let mut counts = [0i64; 3];
        for (j, k) in ["held", "did_not_hold", "undecided"].iter().enumerate() {
            counts[j] = count(b, k).map_err(|e| Error(format!("{at}.{k}: {e}")))?;
        }
        if matches!(b.get("decade"), Some(Value::Null)) {
            continue;
        }
        let decade = int(b.get("decade")).map_err(|e| Error(format!("{at}.decade: {e}")))?;
        out.push(BandCount { currency, decade, subjects: counts[0] + counts[1], adverse: counts[1] });
    }
    let received = card
        .get("disputes")
        .and_then(Value::as_object)
        .and_then(|d| d.get("received"))
        .and_then(Value::as_object)
        .ok_or_else(|| Error(format!("{CARD}: disputes.received must be an object")))?;
    let of = count(received, "of").map_err(|e| Error(format!("{CARD}: disputes.received.of: {e}")))?;
    let by_outcome = received
        .get("by_outcome")
        .and_then(Value::as_object)
        .ok_or_else(|| Error(format!("{CARD}: disputes.received.by_outcome must be an object")))?;
    let open =
        count(by_outcome, "open").map_err(|e| Error(format!("{CARD}: disputes.received.by_outcome.open: {e}")))?;
    Ok((out, Disputes { open, of }))
}

/// standard_history.go isJCS: why `src` is not exactly the JCS encoding of
/// its own parse.
fn is_jcs(src: &[u8]) -> Result<(), String> {
    let v = canon::parse_json(src).map_err(|e| e.to_string())?;
    match canon::jcs(&v) {
        Ok(jcs) if jcs.as_bytes() == src => Ok(()),
        _ => Err("not the JCS encoding of their own parse".into()),
    }
}

/// standard_history.go cardInt: a JSON integer of at most 2^53 − 1 in
/// magnitude. The artifact is JCS, so an integer is in its one canonical form.
fn int(v: Option<&Value>) -> Result<i64, String> {
    match v.and_then(Value::as_i64) {
        Some(n) if (-canon::MAX_SAFE_INTEGER..=canon::MAX_SAFE_INTEGER).contains(&n) => Ok(n),
        Some(_) => Err("must be an integer of at most 2^53 - 1 in magnitude".into()),
        None => Err("must be an integer".into()),
    }
}

/// standard_history.go cardCount: a card integer that is 0 or more.
fn count(m: &Map<String, Value>, k: &str) -> Result<i64, String> {
    let n = int(m.get(k))?;
    if n < 0 {
        return Err("must be 0 or more".into());
    }
    Ok(n)
}
