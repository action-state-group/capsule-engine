// SPDX-License-Identifier: Apache-2.0

//! The value band and the card's freshness (go/admission/band.go): exact
//! decimal arithmetic on strings, never floats.

use std::collections::BTreeMap;

use super::Timestamp;

/// A deal's value band, (currency, decade) (band.go Band; Reference v3
/// §3A.14): the decade is the power of ten of the amount's leading digit, in
/// the band currency's unit. 5 sat is decade 0, 1,000 sat decade 3, 0.25 USD
/// decade -1.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Band {
    pub currency: String,
    pub decade: i64,
}

/// One row of a pack's accept table (band.go Unit): the currency a band is
/// counted in and the power of ten that turns an amount in the stated
/// currency into that unit (msat counted as sat has scale -3). There is no
/// conversion across currencies.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unit {
    pub band: String,
    pub scale: i64,
}

/// band.go amountQuantity: an amount in its band unit, exactly: digits ×
/// 10^exp, digits without leading zeros ("" for zero).
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Quantity {
    pub(crate) unit: String,
    digits: String,
    exp: i64,
}

/// band.go amountPattern, ^(0|[1-9][0-9]*)(\.[0-9]+)?$: a non-negative
/// decimal with no exponent, sign or redundant leading zero. The integer
/// and fraction parts, or None.
fn split_amount(amount: &str) -> Option<(&str, &str)> {
    let (int, frac) = match amount.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (amount, None),
    };
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if !digits(int) || (int.len() > 1 && int.starts_with('0')) {
        return None;
    }
    match frac {
        None => Some((int, "")),
        Some(f) if digits(f) => Some((int, f)),
        Some(_) => None,
    }
}

/// band.go quantity: `amount` in `currency` as a quantity of its band unit
/// under `accept`, or why it cannot be read.
pub(crate) fn quantity(amount: &str, currency: &str, accept: &BTreeMap<String, Unit>) -> Result<Quantity, String> {
    let unit =
        accept.get(currency).ok_or_else(|| format!("currency {currency:?} is not in the pack's accept table"))?;
    let (int, frac) = split_amount(amount).ok_or_else(|| format!("amount {amount:?} is not a plain decimal"))?;
    let digits = format!("{int}{frac}").trim_start_matches('0').to_string();
    // Go int arithmetic: 64-bit, wrapping.
    Ok(Quantity { unit: unit.band.clone(), digits, exp: unit.scale.wrapping_sub(frac.len() as i64) })
}

/// band.go ValueBand: the band of `amount` in `currency` under `accept`. A
/// zero amount has no band (Ok(None)): it is free and sits in the lowest
/// tier. A currency the table does not name, or an amount that is not a
/// plain decimal, is an error, which the caller treats as a missing input.
pub(crate) fn value_band(
    amount: &str,
    currency: &str,
    accept: &BTreeMap<String, Unit>,
) -> Result<Option<Band>, String> {
    let q = quantity(amount, currency, accept)?;
    if q.digits.is_empty() {
        return Ok(None);
    }
    Ok(Some(Band { currency: q.unit, decade: (q.digits.len() as i64 - 1).wrapping_add(q.exp) }))
}

impl Quantity {
    /// band.go amountQuantity.atLeast: whether self ≥ other, both in the
    /// same unit. Go writes both over the smaller exponent and compares them
    /// as integer strings; this compares the same numbers without writing
    /// the zeros out, so an extreme scale cannot exhaust memory.
    pub(crate) fn at_least(&self, other: &Quantity) -> bool {
        match (self.digits.is_empty(), other.digits.is_empty()) {
            (_, true) => return true,
            (true, false) => return false,
            _ => {}
        }
        // Without leading zeros, the longer integer is the larger: its length
        // over the smaller exponent is len(digits) + exp - min(exp).
        let (a, b) =
            (self.digits.len() as i128 + i128::from(self.exp), other.digits.len() as i128 + i128::from(other.exp));
        if a != b {
            return a > b;
        }
        // The same length: compare digit by digit, each padded with zeros.
        let (x, y) = (self.digits.as_bytes(), other.digits.as_bytes());
        for i in 0..x.len().max(y.len()) {
            let (cx, cy) = (x.get(i).copied().unwrap_or(b'0'), y.get(i).copied().unwrap_or(b'0'));
            if cx != cy {
                return cx > cy;
            }
        }
        true
    }
}

/// Go time.Duration units, in int64 nanoseconds. The loader bounds every
/// param a duration is made from (pack.rs param_max), so none overflows.
pub(crate) const SECOND: i64 = 1_000_000_000;
pub(crate) const HOUR: i64 = 3_600 * SECOND;
#[cfg(test)]
const DAY: i64 = 24 * HOUR;

/// Go Time.Sub: the duration from `b` to `a`, saturating at the int64
/// bounds.
pub(crate) fn sub(a: Timestamp, b: Timestamp) -> i64 {
    let d = a.unix_nanos() - b.unix_nanos();
    d.clamp(i128::from(i64::MIN), i128::from(i64::MAX)) as i64
}

/// Go Time.Add: `t` moved by `d`.
pub(crate) fn add(t: Timestamp, d: i64) -> Timestamp {
    Timestamp::from_unix_nanos(t.unix_nanos() + i128::from(d))
}

/// band.go Fresh: whether a card whose latest checkpoint states `at` is
/// within `max_age` (nanoseconds) of `now`. Freshness is measured from the
/// holder's checkpoint, never from when the card arrived; a checkpoint
/// stated after now is not fresh.
pub(crate) fn fresh(at: Timestamp, now: Timestamp, max_age: i64) -> bool {
    if at > now {
        return false;
    }
    sub(now, at) <= max_age
}

#[cfg(test)]
mod tests {
    use super::*;

    fn accept() -> BTreeMap<String, Unit> {
        [("msat", "sat", -3), ("sat", "sat", 0), ("USDC", "USD", 0), ("USD", "USD", 0)]
            .into_iter()
            .map(|(c, b, s)| (c.to_string(), Unit { band: b.to_string(), scale: s }))
            .collect()
    }

    // band_test.go TestValueBand.
    #[test]
    fn value_band_cases() {
        for (amount, currency, band, decade) in [
            ("5", "sat", "sat", 0),
            ("9", "sat", "sat", 0),
            ("10", "sat", "sat", 1),
            ("1000", "sat", "sat", 3),
            ("9999", "sat", "sat", 3),
            ("1000000", "msat", "sat", 3),
            ("999", "msat", "sat", -1),
            ("1", "msat", "sat", -3),
            ("0.25", "USDC", "USD", -1),
            ("0.0099", "USD", "USD", -3),
            ("1.0", "USD", "USD", 0),
            ("12.50", "USD", "USD", 1),
            ("100", "USD", "USD", 2),
        ] {
            let got = value_band(amount, currency, &accept()).unwrap();
            assert_eq!(got, Some(Band { currency: band.into(), decade }), "{amount} {currency}");
        }
    }

    // band_test.go TestValueBandZeroHasNoBand.
    #[test]
    fn zero_has_no_band() {
        for a in ["0", "0.0", "0.000"] {
            assert_eq!(value_band(a, "USD", &accept()), Ok(None), "{a}");
        }
    }

    // band_test.go TestValueBandRefuses.
    #[test]
    fn value_band_refuses() {
        for (a, c) in [
            ("10", "EUR"),
            ("-1", "USD"),
            ("1e3", "USD"),
            ("01", "USD"),
            (".5", "USD"),
            ("5.", "USD"),
            ("", "USD"),
            (" 5", "USD"),
        ] {
            assert!(value_band(a, c, &accept()).is_err(), "{a:?} {c}");
        }
    }

    #[test]
    fn at_least_compares_exactly() {
        let q = |a: &str, c: &str| quantity(a, c, &accept()).unwrap();
        assert!(q("1", "sat").at_least(&q("1000", "msat")));
        assert!(!q("1", "sat").at_least(&q("1001", "msat")));
        assert!(q("1.5", "USD").at_least(&q("1.50", "USDC")));
        assert!(!q("1.49", "USD").at_least(&q("1.5", "USD")));
        assert!(q("0", "USD").at_least(&q("0.00", "USD")));
        assert!(!q("0", "USD").at_least(&q("0.01", "USD")));
        assert!(q("0.01", "USD").at_least(&q("0", "USD")));
        assert!(q("10", "USD").at_least(&q("9.99", "USD")));
        // An extreme scale compares without writing its zeros out.
        let mut wide = accept();
        wide.insert("big".into(), Unit { band: "sat".into(), scale: i64::MAX / 2 });
        assert!(quantity("1", "big", &wide).unwrap().at_least(&q("5", "sat")));
    }

    // band_test.go TestFresh.
    #[test]
    fn freshness() {
        let now = Timestamp::parse_rfc3339("2026-10-30T12:00:00Z").unwrap();
        assert!(fresh(add(now, -DAY), now, DAY), "a checkpoint exactly max_age old is fresh");
        assert!(!fresh(add(now, -DAY - SECOND), now, DAY), "a checkpoint past max_age is stale");
        assert!(!fresh(add(now, SECOND), now, DAY), "a checkpoint stated after now is not fresh");
    }
}
