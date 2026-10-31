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

/// parseInstant: Go's time.Parse(time.RFC3339Nano, ts), which tries the
/// strict parseRFC3339 path and, when that fails, the general layout parser.
/// What the second accepts beyond the first is read here too, as Go reads it
/// (probed against Go): a one-digit hour, a comma before the fraction, and
/// an offset hour of 24 or minute of 60. Every other form is refused.
pub(crate) fn parse(ts: &str) -> Result<Instant, String> {
    let bad = || format!("parsing time {ts:?} as RFC 3339: not a valid instant");
    let b = ts.as_bytes();
    let mut at = 0usize;
    // `n` digits exactly, from `at`.
    let fixed = |at: &mut usize, n: usize| -> Option<i64> {
        let d = b.get(*at..*at + n)?;
        if !d.iter().all(u8::is_ascii_digit) {
            return None;
        }
        *at += n;
        Some(d.iter().fold(0i64, |x, c| x * 10 + i64::from(c - b'0')))
    };
    let lit = |at: &mut usize, c: u8| -> Option<()> { (b.get(*at) == Some(&c)).then(|| *at += 1) };
    let year = fixed(&mut at, 4).ok_or_else(bad)?;
    lit(&mut at, b'-').ok_or_else(bad)?;
    let month = fixed(&mut at, 2).filter(|m| (1..=12).contains(m)).ok_or_else(bad)?;
    lit(&mut at, b'-').ok_or_else(bad)?;
    let day = fixed(&mut at, 2).ok_or_else(bad)?;
    lit(&mut at, b'T').ok_or_else(bad)?;
    // Go stdHour: one digit, or two.
    let hour = match b.get(at + 1) {
        Some(c) if c.is_ascii_digit() => fixed(&mut at, 2),
        _ => fixed(&mut at, 1),
    }
    .filter(|h| (0..24).contains(h))
    .ok_or_else(bad)?;
    lit(&mut at, b':').ok_or_else(bad)?;
    let minute = fixed(&mut at, 2).filter(|m| (0..60).contains(m)).ok_or_else(bad)?;
    lit(&mut at, b':').ok_or_else(bad)?;
    let second = fixed(&mut at, 2).filter(|s| (0..60).contains(s)).ok_or_else(bad)?;
    if !(1..=days_in(month, year)).contains(&day) {
        return Err(bad());
    }
    let mut nanos: i128 = 0;
    if matches!(b.get(at), Some(b'.' | b',')) && b.get(at + 1).is_some_and(u8::is_ascii_digit) {
        let n = b[at + 1..].iter().take_while(|c| c.is_ascii_digit()).count();
        // Go parseNanoseconds: at most nine digits are read.
        let digits = &b[at + 1..at + 1 + n.min(9)];
        let mut v: i128 = 0;
        for &c in digits {
            v = v * 10 + i128::from(c - b'0');
        }
        for _ in digits.len()..9 {
            v *= 10;
        }
        nanos = v;
        at += 1 + n;
    }
    let rest = &b[at..];
    let offset_secs: i64 = if rest == b"Z" {
        0
    } else {
        if rest.len() != 6 || !(rest[0] == b'+' || rest[0] == b'-') || rest[3] != b':' {
            return Err(bad());
        }
        let two = |r: &[u8], max: i64| -> Option<i64> {
            if !r.iter().all(u8::is_ascii_digit) {
                return None;
            }
            let x = i64::from(r[0] - b'0') * 10 + i64::from(r[1] - b'0');
            (x <= max).then_some(x)
        };
        // The general parser's offset range is > 24 and > 60, not >= (Go
        // format.go: "some people do write offsets of 24 hours or 60
        // minutes").
        let hh = two(&rest[1..3], 24).ok_or_else(bad)?;
        let mm = two(&rest[4..6], 60).ok_or_else(bad)?;
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
            "2026-10-29T12:00:00+25:00",
            "2026-10-29T12:00:00+01:61",
            "2026-10-29T12:00:00.Z",
            "2026-10-29T12:00:00,Z",
            "2026-10-29T12:0:00Z",
            "2026-10-29T12:00:0Z",
            "2026-1-29T12:00:00Z",
            "2026-10-9T12:00:00Z",
            "2026-10-29t12:00:00Z",
            "2026-10-29T12:00:00z",
            "2026-10-29T12:00:00+0100",
            "2026-10-29T12:00:00+01:00:00",
            "2026-10-29T9:5:00Z",
            "2026-10-29T12:00:00Zx",
            "+2026-10-29T12:00:00Z",
        ] {
            assert!(parse(ts).is_err(), "accepted {ts:?}");
        }
    }

    /// What Go's time.Parse(RFC3339Nano) accepts beyond its strict path, at
    /// the instants Go gives (probed with Go 1.27).
    #[test]
    fn accepts_what_go_s_general_parser_accepts() {
        for (ts, unix_nanos) in [
            ("2026-10-29T12:00:00,5Z", 1_793_275_200_500_000_000i128),
            ("2026-10-29T1:00:00Z", 1_793_235_600_000_000_000),
            ("2026-10-29T12:00:00+24:00", 1_793_188_800_000_000_000),
            ("2026-10-29T12:00:00+01:60", 1_793_268_000_000_000_000),
            ("2026-10-29T12:00:00,1234567891Z", 1_793_275_200_123_456_789),
            ("2026-10-29T12:00:00,5+24:00", 1_793_188_800_500_000_000),
            ("2026-10-29T00:00:00-00:00", 1_793_232_000_000_000_000),
        ] {
            assert_eq!(parse(ts), Ok(Instant(unix_nanos)), "{ts}");
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
