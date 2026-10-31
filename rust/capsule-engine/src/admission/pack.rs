// SPDX-License-Identifier: Apache-2.0

//! The admission pack loader (go/admission/pack.go): the admission pack
//! profile v0 (evidencebook-skills#51), a pack-source.yaml with scope.kind
//! admission, compiled to pack-definition.json, which is what this module
//! reads. The loader refuses a pack that breaks the admission design
//! rules (capsule-engine#265), so a pack cannot opt out of them: only
//! platform, own_history and operator rows decide NO, a judged row only caps
//! a tier, a missing input never defaults to GO, and a pack has no field a
//! weight or score could hide in.

use std::collections::BTreeMap;

use super::band::Unit;
use super::{Error, Verdict};
use crate::canon::{self, Value};

/// pack.go Record: a row's on_fail when its result is sealed and decides
/// nothing.
const RECORD: &str = "record";

/// Row sources (admission design §3; pack.go Source*).
pub(crate) const SOURCE_PLATFORM: &str = "platform";
pub(crate) const SOURCE_CARD: &str = "card";
pub(crate) const SOURCE_OWN_HISTORY: &str = "own_history";
pub(crate) const SOURCE_OPERATOR: &str = "operator";
pub(crate) const SOURCE_VOUCH: &str = "vouch";
pub(crate) const SOURCE_STAKE: &str = "stake";
/// An artifact the counterparty returned to our own ask, signed over our
/// nonce: a selection with inclusion proofs (design §3).
pub(crate) const SOURCE_BOUND: &str = "bound";
pub(crate) const SOURCE_JUDGED: &str = "judged";

/// pack.go mayDecideNO: the sources whose failure may be NO.
const MAY_DECIDE_NO: [&str; 3] = [SOURCE_PLATFORM, SOURCE_OWN_HISTORY, SOURCE_OPERATOR];

/// evaluate.go predicates: each admission check, the source it reads and
/// the params it takes. The list is closed and versioned with the evaluator.
pub(crate) const PREDICATES: [(&str, &str, &[&str]); 20] = [
    ("platform.attestation_not_invalid", SOURCE_PLATFORM, &[]),
    ("platform.owner_signed", SOURCE_PLATFORM, &[]),
    ("card.verified", SOURCE_CARD, &[]),
    ("card.fresh", SOURCE_CARD, &["max_age_s"]),
    ("card.key_age", SOURCE_CARD, &["min_days"]),
    ("card.witnessed", SOURCE_CARD, &[]),
    ("card.claims_held", SOURCE_CARD, &["min"]),
    ("card.controller_declared", SOURCE_CARD, &[]),
    ("card.controller_trusted", SOURCE_CARD, &[]),
    ("bound.selection_verified", SOURCE_BOUND, &[]),
    ("bound.no_unanswered_dispute", SOURCE_BOUND, &["window_days"]),
    ("own.settled", SOURCE_OWN_HISTORY, &["min"]),
    ("own.held_halves", SOURCE_OWN_HISTORY, &["min"]),
    ("own.no_conflicting", SOURCE_OWN_HISTORY, &[]),
    ("own.no_adverse_adjudication", SOURCE_OWN_HISTORY, &["window_days"]),
    ("operator.not_deny_listed", SOURCE_OPERATOR, &[]),
    ("operator.allow_listed", SOURCE_OPERATOR, &[]),
    ("vouch.distinct_controllers", SOURCE_VOUCH, &["min"]),
    ("stake.covers_deal", SOURCE_STAKE, &[]),
    ("judged", SOURCE_JUDGED, &[]),
];

/// pack.go paramMax: the bound on each threshold, so its duration in
/// nanoseconds never overflows: a century of seconds or days, and a count no
/// JSON reader loses. Every param a predicate takes is one of these.
pub(crate) fn param_max(name: &str) -> Option<i64> {
    match name {
        "max_age_s" => Some(100 * 365 * 24 * 3600),
        "min_days" | "window_days" => Some(100 * 365),
        "min" => Some(canon::MAX_SAFE_INTEGER),
        _ => None,
    }
}

/// pack.go scaleMax and decadeMax: the bounds on an accept scale and a tier
/// ceiling, so a band's decade (digits + scale) stays far inside an int.
const SCALE_MAX: i64 = 30;
const DECADE_MAX: i64 = 60;

/// pack.go maxAsksMax and deadlineSMax: the bounds on the budget, as the lint
/// holds them.
const MAX_ASKS_MAX: i64 = 1000;
const DEADLINE_S_MAX: i64 = 100 * 365 * 24 * 3600;

/// A loaded admission pack (pack.go Pack).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pack {
    pub(crate) id: String,
    pub(crate) version: String,
    pub(crate) digest: String,
    pub(crate) outcomes: Vec<Verdict>,
    pub(crate) missing: Verdict,
    pub(crate) accept: BTreeMap<String, Unit>,
    pub(crate) tiers: Vec<Tier>,
    pub(crate) max_asks: i64,
    /// How long, in seconds, an ask this pack derives waits for an answer
    /// before it is recorded as unanswered.
    pub(crate) deadline_s: i64,
    pub(crate) trust_roots: TrustRoots,
    pub(crate) rows: Vec<Row>,
}

/// The pack's trust roots (pack.go TrustRoots; Reference v3 §3A.2 item 5):
/// the controllers, witnesses and judges whose word this pack counts. An
/// empty list trusts none.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct TrustRoots {
    pub(crate) controllers: Vec<String>,
    pub(crate) witnesses: Vec<String>,
    pub(crate) judges: Vec<String>,
}

/// One rung of the pack's ladder (pack.go Tier): a deal sits in the first
/// tier whose ceiling for its band currency holds the band's decade.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Tier {
    pub(crate) id: String,
    pub(crate) max_decade: BTreeMap<String, i64>,
}

/// What a row's failure does (pack.go Row.OnFail and Row.Cap).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum OnFail {
    No,
    Limit,
    Record,
    /// A judged row caps the deal at this tier index.
    Cap(usize),
}

/// One admission row (pack.go Row).
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Row {
    pub(crate) id: String,
    pub(crate) source: String,
    pub(crate) check: String,
    pub(crate) params: BTreeMap<String, i64>,
    /// The index of the lowest tier the row applies to.
    pub(crate) from: usize,
    pub(crate) on_fail: OnFail,
    /// The Evidence Request derivation a not_read row asks for.
    pub(crate) ask: Option<String>,
}

impl Pack {
    /// The pack's id, owner/name/version.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// The pack's version.
    pub fn version(&self) -> &str {
        &self.version
    }

    /// pack.go Pack.Digest, the definition_digest: the SHA-256 of the
    /// pack-definition.json bytes as loaded, which the decision cites beside
    /// pack_id.
    pub fn digest(&self) -> &str {
        &self.digest
    }
}

// ---- strict decoding, as encoding/json decodes into sourceFile with
// DisallowUnknownFields ---------------------------------------------------------

type Decoded<T> = Result<T, String>;

/// pack.go noNulls: whether a null appears anywhere in `v`.
fn has_null(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Arr(items) => items.iter().any(has_null),
        Value::Obj(members) => members.iter().any(|(_, m)| has_null(m)),
        _ => false,
    }
}

/// A struct's fields (encoding/json decoding an object into a struct, with
/// pack.go exactKeys refusing a name not exactly one the format names). No
/// value is null here: the loader refused every null first.
struct Struct<'a> {
    what: String,
    members: &'a [(String, Value)],
    taken: Vec<&'static str>,
}

impl<'a> Struct<'a> {
    fn of(v: &'a Value, what: impl Into<String>) -> Decoded<Struct<'a>> {
        let what = what.into();
        match v {
            Value::Obj(members) => Ok(Struct { what, members, taken: Vec::new() }),
            _ => Err(format!("cannot unmarshal into {what}, an object")),
        }
    }

    fn get(&mut self, key: &'static str) -> Option<&'a Value> {
        self.taken.push(key);
        self.members.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    fn string(&mut self, key: &'static str) -> Decoded<String> {
        let what = format!("{}.{key}", self.what);
        self.get(key).map_or(Ok(String::new()), |v| string(v, &what))
    }

    /// An integer member, None when absent.
    fn int(&mut self, key: &'static str) -> Decoded<Option<i64>> {
        let what = format!("{}.{key}", self.what);
        self.get(key).map(|v| int(v, &what)).transpose()
    }

    /// encoding/json DisallowUnknownFields: `unknown field "x"`.
    fn done(self) -> Decoded<()> {
        match self.members.iter().find(|(k, _)| !self.taken.contains(&k.as_str())) {
            Some((k, _)) => Err(format!("unknown field {k:?}")),
            None => Ok(()),
        }
    }
}

/// encoding/json decoding into a string.
fn string(v: &Value, what: &str) -> Decoded<String> {
    match v {
        Value::Str(s) => Ok(s.clone()),
        _ => Err(format!("cannot unmarshal into {what}, a string")),
    }
}

/// encoding/json decoding into an int.
fn int(v: &Value, what: &str) -> Decoded<i64> {
    match v {
        Value::Int(i) => Ok(*i),
        _ => Err(format!("cannot unmarshal into {what}, an integer")),
    }
}

fn array<'a>(v: Option<&'a Value>, what: &str) -> Decoded<&'a [Value]> {
    match v {
        None => Ok(&[]),
        Some(Value::Arr(items)) => Ok(items),
        Some(_) => Err(format!("cannot unmarshal into {what}, an array")),
    }
}

fn object<'a>(v: Option<&'a Value>, what: &str) -> Decoded<&'a [(String, Value)]> {
    match v {
        None => Ok(&[]),
        Some(Value::Obj(m)) => Ok(m),
        Some(_) => Err(format!("cannot unmarshal into {what}, an object")),
    }
}

fn strings(v: Option<&Value>, what: &str) -> Decoded<Vec<String>> {
    array(v, what)?.iter().map(|i| string(i, &format!("{what}[]"))).collect()
}

/// A map of name to integer (max_decade, params).
fn int_map(v: Option<&Value>, what: &str) -> Decoded<BTreeMap<String, i64>> {
    object(v, what)?.iter().map(|(k, n)| Ok((k.clone(), int(n, &format!("{what}.{k}"))?))).collect()
}

/// The pack's rows as decoded (pack.go sourceFile.Rows).
struct RawRow<'a> {
    id: String,
    statement: String,
    source: String,
    check: String,
    params: BTreeMap<String, i64>,
    from: String,
    /// The on_fail value as given (json.RawMessage), None when absent.
    on_fail: Option<&'a Value>,
    ask: Option<String>,
}

/// pack.go rowIDPattern, ^[a-z][a-z0-9_-]*$.
fn is_row_id(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b[0].is_ascii_lowercase()
        && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"_-".contains(c))
}

/// pack.go packIDPattern, ^[a-z][a-z0-9_]*/[a-z][a-z0-9_-]*/\d+\.\d+\.\d+$.
fn is_pack_id(s: &str) -> bool {
    let parts: Vec<&str> = s.split('/').collect();
    let [owner, name, version] = parts[..] else { return false };
    let lead = |p: &str| p.as_bytes().first().is_some_and(u8::is_ascii_lowercase);
    let body =
        |p: &str, extra: &[u8]| p.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || extra.contains(&c));
    let numbers = version.split('.').collect::<Vec<_>>();
    lead(owner)
        && body(owner, b"_")
        && lead(name)
        && body(name, b"_-")
        && numbers.len() == 3
        && numbers.iter().all(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
}

fn verdict_of(s: &str) -> Option<Verdict> {
    match s {
        "GO" => Some(Verdict::Go),
        "ASK" => Some(Verdict::Ask),
        "LIMIT" => Some(Verdict::Limit),
        "NO" => Some(Verdict::No),
        _ => None,
    }
}

/// pack.go CompiledFormat: the format a pack-definition.json names.
pub const COMPILED_FORMAT: &str = "admission-pack/v0";

impl Pack {
    /// pack.go Load: an admission pack from its pack-definition.json bytes,
    /// the pinned artifact evidencebook-skills' pack_compile.py writes from
    /// a pack-source.yaml. The evaluator reads no YAML: two YAML readers read
    /// one source two ways, so the compiler's is the only one. The bytes must
    /// be exactly the JCS (RFC 8785) encoding of their own parse, which
    /// refuses duplicate keys, other whitespace or key order and
    /// non-integer numbers; they are then decoded strictly. A pinned pack is
    /// loaded with load_pinned, which also checks the digest.
    pub fn load(src: &[u8]) -> Result<Pack, Error> {
        let fail = |m: String| Error(format!("admission pack: {m}"));
        let v = canon::parse_json(src).map_err(|e| fail(e.to_string()))?;
        if canon::jcs(&v).ok().as_deref().map(str::as_bytes) != Some(src) {
            return Err(fail("the bytes are not the JCS encoding of their own parse".into()));
        }
        if has_null(&v) {
            // pack.go noNulls: no member of the format is nullable, and a
            // null would otherwise decode to a zero value (an empty
            // trust-root name, a threshold of 0).
            return Err(fail("null is not a pack value".into()));
        }
        let (f, rows) = decode(&v).map_err(fail)?;
        validate(src, f, rows)
    }

    /// pack.go LoadPinned: load for a pack pinned as pack_id@version plus
    /// digest: the bytes must be the ones pinned.
    pub fn load_pinned(src: &[u8], pack_id: &str, digest: &str) -> Result<Pack, Error> {
        let p = Pack::load(src)?;
        if p.id != pack_id {
            return Err(Error(format!("admission pack: pinned {pack_id}, loaded {}", p.id)));
        }
        if p.digest != digest {
            return Err(Error(format!("admission pack {pack_id}: pinned digest {digest}, loaded {}", p.digest)));
        }
        Ok(p)
    }
}

/// pack.go sourceFile: pack-definition.json, every optional field written
/// out. An Option marks a number that may be 0 but not absent (the loader
/// has refused every null).
struct SourceFile {
    format: String,
    pack_id: String,
    pack_version: String,
    outcomes: Vec<String>,
    missing: String,
    /// Each currency's band and scale.
    accept: Vec<(String, String, Option<i64>)>,
    tiers: Vec<(String, BTreeMap<String, i64>)>,
    max_asks: Option<i64>,
    deadline_s: Option<i64>,
    trust_roots: TrustRoots,
}

/// An absent struct member: the zero struct.
static EMPTY: Value = Value::Obj(Vec::new());

fn decode(v: &Value) -> Decoded<(SourceFile, Vec<RawRow<'_>>)> {
    let mut top = Struct::of(v, "the pack")?;
    let format = top.string("format")?;
    let pack_id = top.string("pack_id")?;
    let pack_version = top.string("pack_version")?;
    // source names where the pack came from; the evaluator does not read it.
    top.string("source")?;
    let outcomes = strings(top.get("outcomes"), "outcomes")?;
    let missing = top.string("missing")?;
    let mut accept = Vec::new();
    for (cur, u) in object(top.get("accept"), "accept")? {
        let mut s = Struct::of(u, format!("accept.{cur}"))?;
        let band = s.string("band")?;
        let scale = s.int("scale")?;
        s.done()?;
        accept.push((cur.clone(), band, scale));
    }
    let mut tiers = Vec::new();
    for (i, t) in array(top.get("tiers"), "tiers")?.iter().enumerate() {
        let mut s = Struct::of(t, format!("tiers[{i}]"))?;
        let id = s.string("id")?;
        let max_decade = int_map(s.get("max_decade"), &format!("tiers[{i}].max_decade"))?;
        s.done()?;
        tiers.push((id, max_decade));
    }
    let (max_asks, deadline_s) = {
        let mut s = Struct::of(top.get("budget").unwrap_or(&EMPTY), "budget")?;
        let v = (s.int("max_asks")?, s.int("deadline_s")?);
        s.done()?;
        v
    };
    let trust_roots = {
        let mut s = Struct::of(top.get("trust_roots").unwrap_or(&EMPTY), "trust_roots")?;
        let t = TrustRoots {
            controllers: strings(s.get("controllers"), "trust_roots.controllers")?,
            witnesses: strings(s.get("witnesses"), "trust_roots.witnesses")?,
            judges: strings(s.get("judges"), "trust_roots.judges")?,
        };
        s.done()?;
        t
    };
    let mut rows = Vec::new();
    for (i, r) in array(top.get("rows"), "rows")?.iter().enumerate() {
        let mut s = Struct::of(r, format!("rows[{i}]"))?;
        let row = RawRow {
            id: s.string("id")?,
            statement: s.string("statement")?,
            source: s.string("source")?,
            check: s.string("check")?,
            params: int_map(s.get("params"), &format!("rows[{i}].params"))?,
            from: s.string("from")?,
            on_fail: s.get("on_fail"),
            ask: match s.get("ask") {
                None => None,
                Some(a) => {
                    let mut af = Struct::of(a, format!("rows[{i}].ask"))?;
                    let d = af.string("derivation")?;
                    af.done()?;
                    Some(d)
                }
            },
        };
        s.done()?;
        rows.push(row);
    }
    top.done()?;
    Ok((
        SourceFile {
            format,
            pack_id,
            pack_version,
            outcomes,
            missing,
            accept,
            tiers,
            max_asks,
            deadline_s,
            trust_roots,
        },
        rows,
    ))
}

/// pack.go Load's rules over the decoded file, each refusal collected and
/// joined (errors.Join: one per line).
fn validate(src: &[u8], f: SourceFile, raw_rows: Vec<RawRow<'_>>) -> Result<Pack, Error> {
    let mut errs: Vec<String> = Vec::new();
    let mut bad = |m: String| errs.push(m);

    if f.format != COMPILED_FORMAT {
        bad(format!("format must be {COMPILED_FORMAT}, got {:?}", f.format));
    }
    if !is_pack_id(&f.pack_id) {
        bad(format!("pack_id {:?} is not owner/name/version", f.pack_id));
    } else if !f.pack_id.ends_with(&format!("/{}", f.pack_version)) {
        bad(format!("pack_id {:?} does not end in pack_version {:?}", f.pack_id, f.pack_version));
    }

    if f.outcomes.is_empty() {
        bad("outcomes must name the outcomes this pack yields".into());
    }
    let mut outcomes = Vec::new();
    for o in &f.outcomes {
        match verdict_of(o) {
            Some(v) => outcomes.push(v),
            None => bad(format!("outcomes: {o:?} is not GO, ASK, LIMIT or NO")),
        }
    }
    let yields = |s: &str| f.outcomes.iter().any(|o| o == s);
    if !yields("GO") {
        bad("outcomes must include GO".into());
    }
    if f.missing == "GO" {
        bad("missing must not be GO: a missing input is never a silent GO".into());
    } else if !(f.missing == "LIMIT" || f.missing == "NO") {
        // LIMIT is to the lowest tier; there is nothing an ASK could name.
        bad(format!("missing must be LIMIT or NO, got {:?}", f.missing));
    } else if !yields(&f.missing) {
        bad(format!("missing {} is not in outcomes", f.missing));
    }

    if f.accept.is_empty() {
        bad("accept must name at least one currency".into());
    }
    let mut accept = BTreeMap::new();
    // In sorted order (pack.go sortedKeys), so the errors are too.
    let mut entries: Vec<&(String, String, Option<i64>)> = f.accept.iter().collect();
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    for (cur, band, scale) in entries {
        match scale {
            Some(scale) if !band.is_empty() => {
                if !(-SCALE_MAX..=SCALE_MAX).contains(scale) {
                    bad(format!("accept.{cur}: scale {scale} must be from {} to {SCALE_MAX}", -SCALE_MAX));
                }
                accept.insert(cur.clone(), Unit { band: band.clone(), scale: *scale });
            }
            _ => bad(format!("accept.{cur} needs band and scale")),
        }
    }

    let bands: Vec<&str> = accept.values().map(|u: &Unit| u.band.as_str()).collect();
    if f.tiers.is_empty() {
        bad("tiers must not be empty".into());
    }
    let mut tier_index: BTreeMap<String, usize> = BTreeMap::new();
    let mut tiers = Vec::new();
    // below holds, per band unit, the ceiling of the highest tier so far
    // that names it, so a unit a tier skips is still compared across the gap.
    let mut below: BTreeMap<&str, i64> = BTreeMap::new();
    for (i, (id, max_decade)) in f.tiers.iter().enumerate() {
        if id.is_empty() {
            bad(format!("tiers[{i}] needs an id"));
        }
        if tier_index.contains_key(id) {
            bad(format!("tier {id:?} is listed twice"));
        }
        tier_index.insert(id.clone(), i);
        // The keys in sorted order (pack.go sortedKeys): a BTreeMap's.
        let mut decades = BTreeMap::new();
        for (band, d) in max_decade {
            decades.insert(band.clone(), *d);
            if !(-DECADE_MAX..=DECADE_MAX).contains(d) {
                bad(format!("tier {id}: max_decade.{band} {d} must be from {} to {DECADE_MAX}", -DECADE_MAX));
            }
            if !bands.contains(&band.as_str()) {
                bad(format!("tier {id}: max_decade.{band} is not a band unit in accept"));
            }
            if let Some(prev) = below.get(band.as_str()) {
                if d <= prev {
                    bad(format!("tier {id}: max_decade.{band} {d} does not rise above the tier below ({prev})"));
                }
            }
            below.insert(band, *d);
        }
        tiers.push(Tier { id: id.clone(), max_decade: decades });
    }
    // A unit no tier names would leave a deal in it with no tier at all.
    let mut units: Vec<&str> = bands.clone();
    units.sort_unstable();
    units.dedup();
    for band in units {
        if !below.contains_key(band) {
            bad(format!("accept: band unit {band} is named by no tier's max_decade"));
        }
    }

    let max_asks = match f.max_asks {
        Some(n) if (0..=MAX_ASKS_MAX).contains(&n) => n,
        _ => {
            bad(format!("budget.max_asks must be from 0 to {MAX_ASKS_MAX}"));
            0
        }
    };
    let deadline_s = match f.deadline_s {
        Some(n) if (1..=DEADLINE_S_MAX).contains(&n) => n,
        _ => {
            bad(format!("budget.deadline_s must be from 1 to {DEADLINE_S_MAX}"));
            0
        }
    };

    // No empty or whitespace-only name: it would name no controller,
    // witness or judge.
    for (kind, names) in [
        ("controllers", &f.trust_roots.controllers),
        ("witnesses", &f.trust_roots.witnesses),
        ("judges", &f.trust_roots.judges),
    ] {
        for n in names {
            if n.trim().is_empty() {
                bad(format!("trust_roots.{kind}: a name must not be empty"));
            }
        }
    }

    let mut seen: Vec<&str> = Vec::new();
    let mut rows = Vec::new();
    if raw_rows.is_empty() {
        bad("rows must not be empty".into());
    }
    for (i, r) in raw_rows.iter().enumerate() {
        let at = format!("rows[{i}] ({})", r.id);
        if !is_row_id(&r.id) {
            bad(format!("{at}: id must be lowercase letters, digits, - or _"));
        }
        if seen.contains(&r.id.as_str()) {
            bad(format!("{at}: id is listed twice"));
        }
        seen.push(&r.id);
        if r.statement.is_empty() {
            bad(format!("{at}: statement is required"));
        }
        let mut params = BTreeMap::new();
        match PREDICATES.iter().find(|(c, _, _)| *c == r.check) {
            None => bad(format!("{at}: check {:?} is not an admission predicate", r.check)),
            Some((_, source, _)) if *source != r.source => {
                bad(format!("{at}: check {} reads {source}, not {}", r.check, r.source))
            }
            Some((_, _, wants)) => {
                for k in *wants {
                    if !r.params.contains_key(*k) {
                        bad(format!("{at}: check {} needs params.{k}", r.check));
                    }
                }
                // In sorted order (pack.go sortedKeys): a BTreeMap's.
                for (k, v) in &r.params {
                    match param_max(k).filter(|_| wants.contains(&k.as_str())) {
                        None => bad(format!("{at}: check {} takes no params.{k}", r.check)),
                        Some(max) if *v < 0 || *v > max => bad(format!("{at}: params.{k} {v} must be from 0 to {max}")),
                        Some(_) => {
                            params.insert(k.clone(), *v);
                        }
                    }
                }
            }
        }
        let from = tier_index.get(&r.from).copied();
        if from.is_none() {
            bad(format!("{at}: from {:?} is not a tier", r.from));
        }
        let on_fail = if r.source == SOURCE_JUDGED {
            // A judged row caps a tier and never decides NO (D-23).
            // pack.go: on_fail decoded as a map of string to string, with
            // exactly one member, cap, not empty.
            let cap = match r.on_fail {
                Some(Value::Obj(m)) => match &m[..] {
                    [(k, v)] if k == "cap" => string(v, "on_fail.cap").ok().filter(|c| !c.is_empty()),
                    _ => None,
                },
                _ => None,
            };
            let on_fail = match cap {
                None => {
                    bad(format!("{at}: a judged row's on_fail must be {{cap: <tier>}}"));
                    None
                }
                Some(c) => match tier_index.get(&c) {
                    None => {
                        bad(format!("{at}: cap {c:?} is not a tier"));
                        None
                    }
                    // pack.go compares with from's index, 0 when from is not
                    // a tier. A cap at or above the row's own tier could
                    // never lower one.
                    Some(idx) if *idx >= from.unwrap_or(0) => {
                        bad(format!("{at}: cap {c} must be a tier below from {}", r.from));
                        None
                    }
                    Some(idx) => Some(OnFail::Cap(*idx)),
                },
            };
            // A cap is a LIMIT: a pack that yields none would turn it into
            // the missing default, a judged row deciding NO (D-23).
            if !yields("LIMIT") {
                bad(format!("{at}: a judged row caps a tier, so LIMIT must be in outcomes"));
            }
            // A judged row refused above keeps no on_fail (pack.go: OnFail
            // and Cap stay zero); the pack is refused.
            on_fail.unwrap_or(OnFail::Record)
        } else {
            // pack.go: on_fail decoded as a string; an absent one does not
            // decode.
            let o = match r.on_fail {
                Some(Value::Str(o)) => o.clone(),
                _ => {
                    bad(format!("{at}: on_fail must be NO, LIMIT or record"));
                    String::new()
                }
            };
            let on_fail = match o.as_str() {
                RECORD => OnFail::Record,
                "NO" => {
                    if !MAY_DECIDE_NO.contains(&r.source.as_str()) {
                        bad(format!(
                            "{at}: a {} row may not decide NO; only platform, own_history and operator rows do",
                            r.source
                        ));
                    }
                    OnFail::No
                }
                "LIMIT" => {
                    // The lowest tier is the floor: there is no tier below it
                    // to limit to (admission design §4, T0 "LIMIT: never").
                    if from == Some(0) {
                        bad(format!("{at}: a row from the lowest tier cannot LIMIT; it can decide NO or be recorded"));
                    }
                    OnFail::Limit
                }
                _ => {
                    bad(format!("{at}: on_fail must be NO, LIMIT or record, got {o:?}"));
                    OnFail::Record
                }
            };
            if o != RECORD && !o.is_empty() && !yields(&o) {
                bad(format!("{at}: on_fail {o} is not in outcomes"));
            }
            on_fail
        };
        if let Some(d) = &r.ask {
            // Only a LIMIT row asks (resolution step 6); an ask anywhere else
            // would never be made.
            if on_fail != OnFail::Limit {
                bad(format!("{at}: only a LIMIT row may carry an ask"));
            }
            if d.is_empty() {
                bad(format!("{at}: ask needs a derivation"));
            }
            if !yields("ASK") {
                bad(format!("{at}: carries an ask, but ASK is not in outcomes"));
            }
        }
        rows.push(Row {
            id: r.id.clone(),
            source: r.source.clone(),
            check: r.check.clone(),
            params,
            from: from.unwrap_or(0),
            on_fail,
            ask: r.ask.clone().filter(|d| !d.is_empty()),
        });
    }
    if !errs.is_empty() {
        return Err(Error(format!("admission pack {}: {}", f.pack_id, errs.join("\n"))));
    }
    let missing = verdict_of(&f.missing).unwrap_or(Verdict::No);
    Ok(Pack {
        id: f.pack_id,
        version: f.pack_version,
        digest: canon::sha256_hex(src),
        outcomes,
        missing,
        accept,
        tiers,
        max_asks,
        deadline_s,
        trust_roots: f.trust_roots,
        rows,
    })
}
