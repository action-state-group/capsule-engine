// SPDX-License-Identifier: Apache-2.0

//! The admission pack loader (go/admission/pack.go): the admission pack
//! profile v0 (evidencebook-skills#51), a pack-source.yaml with scope.kind
//! admission. The loader refuses a pack that breaks the admission design
//! rules (capsule-engine#265), so a pack cannot opt out of them: only
//! platform, own_history and operator rows decide NO, a judged row only caps
//! a tier, a missing input never defaults to GO, and a pack has no field a
//! weight or score could hide in.

use std::collections::BTreeMap;

use super::band::Unit;
use super::{Error, Verdict};
use crate::canon;
use crate::yaml::{self, Node};

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

    /// pack.go Pack.Digest: the SHA-256 of the pack-source.yaml bytes as
    /// loaded, which the decision cites.
    pub fn digest(&self) -> &str {
        &self.digest
    }
}

// ---- strict decoding, as yaml.v3 decodes into sourceFile with KnownFields --

type Decoded<T> = Result<T, String>;

/// A struct's fields (yaml.v3 decoding a mapping into a struct with
/// KnownFields): null is the zero struct; a key not taken is refused.
struct Struct<'a> {
    what: String,
    members: &'a [(String, Node)],
    taken: Vec<&'static str>,
}

impl<'a> Struct<'a> {
    fn of(node: &'a Node, what: impl Into<String>) -> Decoded<Struct<'a>> {
        let what = what.into();
        match node {
            Node::Map(members) => Ok(Struct { what, members, taken: Vec::new() }),
            Node::Null => Ok(Struct { what, members: &[], taken: Vec::new() }),
            _ => Err(format!("{what}: cannot unmarshal into a mapping")),
        }
    }

    fn get(&mut self, key: &'static str) -> Option<&'a Node> {
        self.taken.push(key);
        self.members.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    fn string(&mut self, key: &'static str) -> Decoded<String> {
        let what = format!("{}.{key}", self.what);
        self.get(key).map_or(Ok(String::new()), |n| string(n, &what))
    }

    /// yaml.v3 KnownFields: "field X not found in type ...".
    fn done(self) -> Decoded<()> {
        match self.members.iter().find(|(k, _)| !self.taken.contains(&k.as_str())) {
            Some((k, _)) => Err(format!("{}: field {k} not found", self.what)),
            None => Ok(()),
        }
    }
}

/// yaml.v3 decoding a scalar into a string: its text (an integer as written,
/// which this reader holds only in plain decimal form), "" for null. A
/// boolean is refused here, where yaml.v3 would take its text: a row id or
/// tier read from `True` would otherwise change case.
fn string(n: &Node, what: &str) -> Decoded<String> {
    match n {
        Node::Str(s) => Ok(s.clone()),
        Node::Int(i) => Ok(i.to_string()),
        Node::Null => Ok(String::new()),
        Node::Bool(_) => Err(format!("{what}: a boolean is not read as a string; quote it")),
        _ => Err(format!("{what}: cannot unmarshal a collection into a string")),
    }
}

/// yaml.v3 decoding into an int: an integer; null is None (a pointer) or 0.
fn int(n: &Node, what: &str) -> Decoded<Option<i64>> {
    match n {
        Node::Int(i) => Ok(Some(*i)),
        Node::Null => Ok(None),
        _ => Err(format!("{what}: cannot unmarshal into an integer")),
    }
}

fn seq<'a>(n: Option<&'a Node>, what: &str) -> Decoded<&'a [Node]> {
    match n {
        None | Some(Node::Null) => Ok(&[]),
        Some(Node::Seq(items)) => Ok(items),
        Some(_) => Err(format!("{what}: cannot unmarshal into a sequence")),
    }
}

fn map<'a>(n: Option<&'a Node>, what: &str) -> Decoded<&'a [(String, Node)]> {
    match n {
        None | Some(Node::Null) => Ok(&[]),
        Some(Node::Map(m)) => Ok(m),
        Some(_) => Err(format!("{what}: cannot unmarshal into a mapping")),
    }
}

fn int_map(n: Option<&Node>, what: &str) -> Decoded<BTreeMap<String, i64>> {
    map(n, what)?.iter().map(|(k, v)| Ok((k.clone(), int(v, &format!("{what}.{k}"))?.unwrap_or(0)))).collect()
}

/// The source file's rows as decoded (pack.go sourceFile.Rows).
struct RawRow<'a> {
    id: String,
    statement: String,
    source: String,
    check: String,
    params: Option<BTreeMap<String, i64>>,
    from: String,
    on_fail: Option<&'a Node>,
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

/// pack.go Load's YAML pre-pass: the document parsed, then refused for a
/// plain YAML 1.1 boolean (yaml11Boolean), and then for a plain scalar
/// outside the plain scalar rule, an anchor, an alias or a tag
/// (plainScalars), so a pack reads one way in every reader (the lint, the Go
/// loader and this one) or not at all.
pub(crate) fn pre_pass(text: &str) -> Result<yaml::Document, String> {
    let doc = yaml::parse_admission(text)?;
    if let Some(v) = &doc.yaml11_boolean {
        // YAML 1.1 readers (PyYAML, so the pack's own lint) read a plain NO
        // as false; YAML 1.2 readers read it as "NO". A pack reads the same
        // in both or not at all.
        return Err(format!("the plain scalar {v:?} is a boolean in YAML 1.1; quote it"));
    }
    if let Some(e) = &doc.plain_rule {
        return Err(e.clone());
    }
    Ok(doc)
}

impl Pack {
    /// pack.go Load: an admission pack from its pack-source.yaml bytes. A
    /// pinned pack is loaded with load_pinned, which also checks the digest.
    pub fn load(src: &[u8]) -> Result<Pack, Error> {
        let fail = |m: String| Error(format!("admission pack: {m}"));
        let text = std::str::from_utf8(src).map_err(|e| fail(e.to_string()))?;
        let doc = pre_pass(text).map_err(fail)?;
        if doc.node == Node::Null {
            return Err(fail("EOF".into()));
        }
        let (f, rows) = decode(&doc.node).map_err(fail)?;
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

/// pack.go sourceFile, as yaml.v3 decodes it with KnownFields.
struct SourceFile {
    pack_id: String,
    pack_version: String,
    scope_kind: String,
    outcomes: Vec<String>,
    missing: String,
    /// Each currency's band and scale (None where the file gives none).
    accept: Vec<(String, String, Option<i64>)>,
    tiers: Vec<(String, BTreeMap<String, i64>)>,
    max_asks: Option<i64>,
    deadline_s: Option<i64>,
    trust_roots: TrustRoots,
}

fn strings(n: Option<&Node>, what: &str) -> Decoded<Vec<String>> {
    seq(n, what)?.iter().map(|i| string(i, &format!("{what}[]"))).collect()
}

fn decode(node: &Node) -> Decoded<(SourceFile, Vec<RawRow<'_>>)> {
    let mut top = Struct::of(node, "the pack")?;
    let pack_id = top.string("pack_id")?;
    let pack_version = top.string("pack_version")?;
    let scope_kind = {
        let mut s = Struct::of(top.get("scope").unwrap_or(&Node::Null), "scope")?;
        let k = s.string("kind")?;
        s.done()?;
        k
    };
    // source names where the pack came from; the evaluator does not read it.
    top.string("source")?;
    let outcomes =
        seq(top.get("outcomes"), "outcomes")?.iter().map(|o| string(o, "outcomes[]")).collect::<Decoded<_>>()?;
    let missing = top.string("missing")?;
    let mut accept = Vec::new();
    for (cur, u) in map(top.get("accept"), "accept")? {
        let mut s = Struct::of(u, format!("accept.{cur}"))?;
        let band = s.string("band")?;
        let scale = s.get("scale").map_or(Ok(None), |n| int(n, &format!("accept.{cur}.scale")))?;
        s.done()?;
        accept.push((cur.clone(), band, scale));
    }
    let mut tiers = Vec::new();
    for (i, t) in seq(top.get("tiers"), "tiers")?.iter().enumerate() {
        let mut s = Struct::of(t, format!("tiers[{i}]"))?;
        let id = s.string("id")?;
        let max_decade = int_map(s.get("max_decade"), &format!("tiers[{i}].max_decade"))?;
        s.done()?;
        tiers.push((id, max_decade));
    }
    let (max_asks, deadline_s) = {
        let mut s = Struct::of(top.get("budget").unwrap_or(&Node::Null), "budget")?;
        let v = s.get("max_asks").map_or(Ok(None), |n| int(n, "budget.max_asks"))?;
        let d = s.get("deadline_s").map_or(Ok(None), |n| int(n, "budget.deadline_s"))?;
        s.done()?;
        (v, d)
    };
    let trust_roots = {
        let mut s = Struct::of(top.get("trust_roots").unwrap_or(&Node::Null), "trust_roots")?;
        let t = TrustRoots {
            controllers: strings(s.get("controllers"), "trust_roots.controllers")?,
            witnesses: strings(s.get("witnesses"), "trust_roots.witnesses")?,
            judges: strings(s.get("judges"), "trust_roots.judges")?,
        };
        s.done()?;
        t
    };
    let mut rows = Vec::new();
    for (i, r) in seq(top.get("rows"), "rows")?.iter().enumerate() {
        let mut s = Struct::of(r, format!("rows[{i}]"))?;
        let row = RawRow {
            id: s.string("id")?,
            statement: s.string("statement")?,
            source: s.string("source")?,
            check: s.string("check")?,
            params: match s.get("params") {
                None | Some(Node::Null) => None,
                p => Some(int_map(p, &format!("rows[{i}].params"))?),
            },
            from: s.string("from")?,
            // A key given as null is a scalar node, as yaml.v3 holds it.
            on_fail: s.get("on_fail"),
            ask: match s.get("ask") {
                None | Some(Node::Null) => None,
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
            pack_id,
            pack_version,
            scope_kind,
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

    if f.scope_kind != "admission" {
        bad(format!("scope.kind must be admission, got {:?}", f.scope_kind));
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
        for (band, d) in max_decade {
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
        tiers.push(Tier { id: id.clone(), max_decade: max_decade.clone() });
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
        Some(n) if n >= 0 => n,
        _ => {
            bad("budget.max_asks must be a non-negative integer".into());
            0
        }
    };
    let deadline_s = match f.deadline_s {
        Some(n) if n > 0 => n,
        _ => {
            bad("budget.deadline_s must be a positive integer".into());
            0
        }
    };

    let mut seen: Vec<&str> = Vec::new();
    let mut rows = Vec::new();
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
        let params = r.params.clone().unwrap_or_default();
        match PREDICATES.iter().find(|(c, _, _)| *c == r.check) {
            None => bad(format!("{at}: check {:?} is not an admission predicate", r.check)),
            Some((_, source, _)) if *source != r.source => {
                bad(format!("{at}: check {} reads {source}, not {}", r.check, r.source))
            }
            Some((_, _, wants)) => {
                for k in *wants {
                    if !params.contains_key(*k) {
                        bad(format!("{at}: check {} needs params.{k}", r.check));
                    }
                }
                for (k, v) in &params {
                    match param_max(k) {
                        Some(max) if wants.contains(&k.as_str()) => {
                            if *v < 0 || *v > max {
                                bad(format!("{at}: params.{k} {v} must be from 0 to {max}"));
                            }
                        }
                        _ => bad(format!("{at}: check {} takes no params.{k}", r.check)),
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
            let cap = match r.on_fail {
                Some(Node::Map(m)) => match &m[..] {
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
            let o = match r.on_fail {
                Some(n @ (Node::Str(_) | Node::Int(_) | Node::Null)) => string(n, "on_fail").unwrap_or_default(),
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

#[cfg(test)]
mod tests {
    use super::pre_pass;

    /// plain_scalars_test.go TestPlainScalarRule: the shared probes in
    /// conformance-vectors/admission/v0/plain-scalars.json, the documents
    /// every reader of an admission pack (this loader, the Go one and the
    /// lint) must accept or refuse alike. A refused one may be refused while
    /// it is parsed, before the rule is reached.
    #[test]
    fn plain_scalar_rule() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../conformance-vectors/admission/v0/plain-scalars.json");
        let probes: serde_json::Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        let docs =
            |k: &str| probes[k].as_array().unwrap().iter().map(|d| d.as_str().unwrap().to_string()).collect::<Vec<_>>();
        let (accepted, refused) = (docs("accepted"), docs("refused"));
        assert!(!accepted.is_empty() && !refused.is_empty());
        let mut failures = Vec::new();
        for doc in &accepted {
            if let Err(e) = pre_pass(doc) {
                failures.push(format!("{doc:?}: refused ({e}); want it accepted"));
            }
        }
        for doc in &refused {
            if pre_pass(doc).is_ok() {
                failures.push(format!("{doc:?}: accepted; want it refused"));
            }
        }
        assert!(failures.is_empty(), "{}", failures.join("\n"));
    }

    /// The refusal names the scalar to quote, in Go's words.
    #[test]
    fn the_rule_names_what_to_quote() {
        assert_eq!(
            pre_pass("a: x\nb: 1e3\n").err().as_deref(),
            Some("line 2: quote \"1e3\": YAML readers do not all read it as the same string")
        );
        assert_eq!(pre_pass("v: !!int \"3\"\n").err().as_deref(), Some("line 1: tags are not read"));
        assert!(pre_pass("v: ! T0\n").is_ok());
        assert!(pre_pass("v: ! 1e3\n").is_err());
    }
}
