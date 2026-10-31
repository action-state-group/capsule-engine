// SPDX-License-Identifier: Apache-2.0

//! RFC 3339 instants, read as the Go port's parseInstant reads them
//! (time.Parse(time.RFC3339Nano, ts), which takes Go's strict parseRFC3339
//! path for that layout; go/admission's time.Parse(time.RFC3339, ts) takes
//! the same path and reads a fraction the same way).

/// An instant: nanoseconds since the Unix epoch.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub(crate) struct Instant(pub(crate) i128);

const NANOS: i128 = 1_000_000_000;

impl Instant {
    /// The instant `days` days earlier (Go time.AddDate(0, 0, -days) on a
    /// time in a fixed zone, which has no daylight saving).
    pub(crate) fn minus_days(self, days: i64) -> Instant {
        Instant(self.0 - i128::from(days) * 86_400 * NANOS)
    }

    /// The instant truncated to the microsecond (Go Time.Truncate(
    /// time.Microsecond), toward the zero time of year 1, which for a
    /// whole second count is flooring).
    pub(crate) fn micros(self) -> Instant {
        Instant(self.0.div_euclid(1_000) * 1_000)
    }
}

fn is_leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn days_in(month: i64, year: i64) -> i64 {
    match month {
        2 if is_leap(year) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Days since 1970-01-01 of a proleptic Gregorian date.
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// parseInstant: Go's parseRFC3339 for the RFC3339Nano layout. The forms
/// only Go's lenient fallback parser accepts (a one-digit hour, a comma
/// before the fraction) are refused here.
pub(crate) fn parse(ts: &str) -> Result<Instant, String> {
    let bad = || format!("parsing time {ts:?} as RFC 3339: not a valid instant");
    let b = ts.as_bytes();
    if b.len() < 19 {
        return Err(bad());
    }
    let num = |from: usize, to: usize, min: i64, max: i64| -> Option<i64> {
        let mut x: i64 = 0;
        for &c in &b[from..to] {
            if !c.is_ascii_digit() {
                return None;
            }
            x = x * 10 + i64::from(c - b'0');
        }
        (min..=max).contains(&x).then_some(x)
    };
    if !(b[4] == b'-' && b[7] == b'-' && b[10] == b'T' && b[13] == b':' && b[16] == b':') {
        return Err(bad());
    }
    let year = num(0, 4, 0, 9999).ok_or_else(bad)?;
    let month = num(5, 7, 1, 12).ok_or_else(bad)?;
    let day = num(8, 10, 1, days_in(month, year)).ok_or_else(bad)?;
    let hour = num(11, 13, 0, 23).ok_or_else(bad)?;
    let minute = num(14, 16, 0, 59).ok_or_else(bad)?;
    let second = num(17, 19, 0, 59).ok_or_else(bad)?;
    let mut rest = &b[19..];
    let mut nanos: i128 = 0;
    if rest.len() >= 2 && rest[0] == b'.' && rest[1].is_ascii_digit() {
        let n = 1 + rest[1..].iter().take_while(|c| c.is_ascii_digit()).count();
        // Go parseNanoseconds: at most nine digits are read.
        let digits = &rest[1..n.min(10)];
        let mut v: i128 = 0;
        for &c in digits {
            v = v * 10 + i128::from(c - b'0');
        }
        for _ in digits.len()..9 {
            v *= 10;
        }
        nanos = v;
        rest = &rest[n..];
    }
    let offset_secs: i64 = if rest == b"Z" {
        0
    } else {
        if rest.len() != 6 || !(rest[0] == b'+' || rest[0] == b'-') || rest[3] != b':' {
            return Err(bad());
        }
        let digits = |r: &[u8], max: i64| -> Option<i64> {
            if !r.iter().all(u8::is_ascii_digit) {
                return None;
            }
            let x = i64::from(r[0] - b'0') * 10 + i64::from(r[1] - b'0');
            (x <= max).then_some(x)
        };
        let hh = digits(&rest[1..3], 23).ok_or_else(bad)?;
        let mm = digits(&rest[4..6], 59).ok_or_else(bad)?;
        let off = (hh * 60 + mm) * 60;
        if rest[0] == b'-' {
            -off
        } else {
            off
        }
    };
    let secs = days_from_civil(year, month, day) * 86_400 + hour * 3_600 + minute * 60 + second - offset_secs;
    Ok(Instant(i128::from(secs) * NANOS + nanos))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_rfc3339() {
        assert_eq!(parse("1970-01-01T00:00:00Z").unwrap(), Instant(0));
        assert_eq!(parse("1970-01-01T01:00:00+01:00").unwrap(), Instant(0));
        assert_eq!(parse("2026-10-29T12:13:00Z").unwrap(), Instant(1_793_275_980 * NANOS));
        assert_eq!(parse("1970-01-01T00:00:00.5Z").unwrap(), Instant(500_000_000));
        assert_eq!(parse("1970-01-01T00:00:00.1234567891Z").unwrap(), Instant(123_456_789));
        assert_eq!(parse("2024-02-29T00:00:00Z").unwrap().0 % NANOS, 0);
    }

    #[test]
    fn refuses_what_go_refuses() {
        for ts in [
            "",
            "2026-10-29",
            "2026-10-29 12:00:00Z",
            "2026-13-01T00:00:00Z",
            "2025-02-29T00:00:00Z",
            "2026-10-29T24:00:00Z",
            "2026-10-29T12:00:60Z",
            "2026-10-29T12:00:00",
            "2026-10-29T12:00:00+24:00",
            "2026-10-29T12:00:00+01:60",
            "2026-10-29T12:00:00.Z",
        ] {
            assert!(parse(ts).is_err(), "accepted {ts:?}");
        }
    }

    #[test]
    fn truncates_and_steps() {
        let t = Instant(1_500);
        assert_eq!(t.micros(), Instant(1_000));
        assert_eq!(Instant(-1).micros(), Instant(-1_000));
        assert_eq!(Instant(0).minus_days(1), Instant(-86_400 * NANOS));
    }
}
