// SPDX-License-Identifier: Apache-2.0

//! The admission evaluator (go/admission; capsule-engine#265): a pinned
//! admission pack and what was received about a counterparty give a
//! Decision, GO, ASK, LIMIT or NO, with the tier and the rows that decided
//! it. The evaluator does the math (the value band, the tier, the card's
//! freshness, counts and denominators); a pack states thresholds only. The
//! Go package and this module decide conformance-vectors/admission/v0 the
//! same.
//!
//! Like the guard, the evaluator decides and its caller seals. The decision
//! block is local-only: it is about one counterparty and this crate never
//! exports it for another party.
//!
//! ```
//! use capsule_engine::admission::{evaluate, Inputs, Pack, Timestamp, Verdict};
//! use capsule_engine::canon;
//!
//! // A pack-definition.json is the JCS encoding of the compiled pack.
//! let def = serde_json::json!({
//!     "format": "admission-pack/v0", "pack_id": "asg/example/0.1.0", "pack_version": "0.1.0",
//!     "source": "an example", "outcomes": ["GO", "NO"], "missing": "NO",
//!     "accept": {"msat": {"band": "sat", "scale": -3}},
//!     "tiers": [{"id": "T0", "max_decade": {"sat": 6}}],
//!     "budget": {"max_asks": 0, "deadline_s": 86400},
//!     "trust_roots": {"controllers": [], "witnesses": [], "judges": []},
//!     "rows": [{"id": "deny-list", "statement": "The peer is not on the operator's deny list.",
//!               "source": "operator", "check": "operator.not_deny_listed", "params": {},
//!               "from": "T0", "on_fail": "NO"}],
//! });
//! let src = canon::jcs(&canon::from_json(&def).unwrap()).unwrap();
//! let pack = Pack::load(src.as_bytes()).unwrap();
//! let mut inputs = Inputs::at(Timestamp::parse_rfc3339("2026-10-30T12:00:00Z").unwrap());
//! inputs.amount = "5000".into();
//! inputs.currency = "msat".into();
//! let decision = evaluate(&pack, &inputs);
//! assert_eq!(decision.verdict, Verdict::Go);
//! let _block = decision.block(); // what the caller seals
//! ```

mod band;
mod decision;
mod evaluate;
mod pack;
mod standard_history;

use std::collections::BTreeMap;

pub use band::{Band, Unit};
pub use decision::{AskOut, Decision, Effect, PackRef, RowResult, RowStatus};
pub use evaluate::evaluate;
pub use pack::{Pack, RailPin, COMPILED_FORMAT};
pub use standard_history::standard_history_counts;

use crate::instant::Instant;

/// An outcome, in the order a decision ranks them (pack.go GO, ASK, LIMIT,
/// NO).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Go,
    Ask,
    Limit,
    No,
}

impl Verdict {
    /// The outcome as a pack and the decision block name it.
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Go => "GO",
            Verdict::Ask => "ASK",
            Verdict::Limit => "LIMIT",
            Verdict::No => "NO",
        }
    }
}

/// A pack the loader refused, naming each rule it broke.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error(pub String);

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

/// An instant, to the nanosecond (Go time.Time without a location).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Timestamp(Instant);

/// Go's zero time.Time, 0001-01-01T00:00:00Z: what an absent card
/// checkpoint is read as, in seconds since the Unix epoch.
const GO_ZERO_TIME_SECS: i128 = -62_135_596_800;

impl Timestamp {
    /// An RFC 3339 instant, as Go's time.Parse(time.RFC3339, s) reads one.
    pub fn parse_rfc3339(s: &str) -> Result<Timestamp, String> {
        crate::instant::parse(s).map(Timestamp)
    }

    /// The instant `nanos` nanoseconds after the Unix epoch.
    pub fn from_unix_nanos(nanos: i128) -> Timestamp {
        Timestamp(Instant(nanos))
    }

    /// Nanoseconds since the Unix epoch.
    pub fn unix_nanos(self) -> i128 {
        self.0 .0
    }

    /// Go's zero time.Time, 0001-01-01T00:00:00Z, which an absent card
    /// checkpoint is read as.
    pub fn go_zero() -> Timestamp {
        Timestamp::from_unix_nanos(GO_ZERO_TIME_SECS * 1_000_000_000)
    }
}

impl From<std::time::SystemTime> for Timestamp {
    fn from(t: std::time::SystemTime) -> Timestamp {
        let nanos = match t.duration_since(std::time::UNIX_EPOCH) {
            Ok(d) => d.as_nanos() as i128,
            Err(e) => -(e.duration().as_nanos() as i128),
        };
        Timestamp::from_unix_nanos(nanos)
    }
}

/// What was received about a counterparty, as the caller verified it, plus
/// the deal (evaluate.go Inputs). Nothing here is fetched or verified by the
/// evaluator: signatures, witness receipts and the staple's presenter are
/// checked before a card reaches Inputs, and `now` is the caller's clock.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Inputs {
    pub now: Timestamp,
    /// The deal's amount as a plain decimal in `currency`; "" when the deal
    /// states none, which is a missing input.
    pub amount: String,
    pub currency: String,
    pub platform: Platform,
    pub card: Card,
    /// This node's own history with the counterparty; None when it has not
    /// been read.
    pub own: Option<OwnHistory>,
    pub operator: Operator,
    /// Verified third-party vouches; `subject` is the counterparty's own
    /// controller, whose vouches do not count.
    pub vouches: Vec<Vouch>,
    pub subject: String,
    /// Collateral posted on this deal; None when none is.
    pub stake: Option<Stake>,
    pub selection: Selection,
    /// A judged row's result by row id; absent is not judged. A result from
    /// a judge outside the pack's trust roots is not counted.
    pub judged: BTreeMap<String, Judgement>,
    /// The state of an ask already made (pending, refused, unanswered or
    /// answered), by its derivation (history_card/1).
    pub asks: BTreeMap<String, String>,
    /// How many asks this counterparty has had in the pack's budget window.
    pub asks_made: i64,
}

impl Inputs {
    /// Inputs at `now` with nothing received: no amount, no card, no own
    /// history, no vouch, no stake, nothing judged and no ask made.
    pub fn at(now: Timestamp) -> Inputs {
        Inputs {
            now,
            amount: String::new(),
            currency: String::new(),
            platform: Platform::default(),
            card: Card::default(),
            own: None,
            operator: Operator::default(),
            vouches: Vec::new(),
            subject: String::new(),
            stake: None,
            selection: Selection::default(),
            judged: BTreeMap::new(),
            asks: BTreeMap::new(),
            asks_made: 0,
        }
    }
}

/// The platform's own facts about the counterparty (evaluate.go Platform).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Platform {
    /// "valid", "invalid", or "" when unknown.
    pub release_attestation: String,
    /// None when unknown.
    pub owner_signed: Option<bool>,
}

/// Card and selection states (admission design §5; evaluate.go Card*).
pub const CARD_VERIFIED: &str = "verified";
pub const CARD_UNVERIFIABLE: &str = "unverifiable";
pub const CARD_REFUSED: &str = "refused";
pub const CARD_UNANSWERED: &str = "unanswered";

/// The counterparty's history_card/1 or served_summary/1 as the caller
/// verified it (evaluate.go Card). An empty state is absent. An absent
/// checkpoint is read as Go's zero time, 0001-01-01T00:00:00Z.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Card {
    pub state: String,
    pub first_checkpoint_at: Option<Timestamp>,
    pub latest_checkpoint_at: Option<Timestamp>,
    /// The witness whose receipt on the latest checkpoint the caller
    /// verified, or "".
    pub witness: String,
    /// The controller the card's key declares itself bound to, or "" when
    /// it declares none.
    pub controller: String,
    /// The claims the card holds, with their denominator; None when the card
    /// carries no counts (history_card/1 carries none today).
    pub claims: Option<Claims>,
    /// The card's per-band counts; None when the card carries none, and
    /// empty when it carries them and every band is empty. A
    /// standard_history/1 card carries them (`standard_history_counts`).
    pub bands: Option<Vec<BandCount>>,
    /// The card's received disputes, open out of all; None when the card
    /// carries no dispute tally. `standard_history_counts` fills `bands` and
    /// `disputes` from a standard_history/1 card.
    pub disputes: Option<Disputes>,
}

/// The disputes others raised about the counterparty that its card shows:
/// those still open, out of all of them (evaluate.go Disputes).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Disputes {
    pub open: i64,
    pub of: i64,
}

/// A card's count in one value band (evaluate.go BandCount; Reference v3
/// §3A.14): the corroborated, net-settled subjects whose net amount falls in
/// `decade` of `currency`, each subject banded once after netting, and how
/// many of them were resolved against the counterparty.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct BandCount {
    pub currency: String,
    pub decade: i64,
    pub subjects: i64,
    pub adverse: i64,
}

/// A card's claims held out of claims made, in its window (evaluate.go
/// Claims).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Claims {
    pub held: i64,
    pub total: i64,
}

/// A bound selection: the counterparty's answer to our own ask, over our
/// nonce, with inclusion proofs, as the caller verified it (evaluate.go
/// Selection). An empty state is absent.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Selection {
    pub state: String,
    /// The times of disputes with anyone that the selection shows left
    /// unanswered.
    pub unanswered_disputes: Vec<Timestamp>,
}

/// A judged row's result and the judge that gave it (evaluate.go
/// Judgement).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Judgement {
    pub met: bool,
    pub judge: String,
}

/// This node's own book about the counterparty, as counts (evaluate.go
/// OwnHistory).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct OwnHistory {
    pub exchanges: i64,
    pub settled: i64,
    pub held_halves: i64,
    pub conflicting: i64,
    pub verdicts: Vec<AdjudicationVerdict>,
}

/// One adjudication about the counterparty this node holds (evaluate.go
/// Verdict).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AdjudicationVerdict {
    pub at: Timestamp,
    pub adverse: bool,
}

/// What the operator's own config says about the counterparty (evaluate.go
/// Operator).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Operator {
    pub deny_listed: bool,
    /// None when the operator keeps no allow list, which refuses no one.
    pub allow_listed: Option<bool>,
}

/// Collateral posted on a deal (evaluate.go Stake), with no bond terms.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Stake {
    pub amount: String,
    pub currency: String,
    /// The adapter declaration of the rail the stake is posted on; a stake
    /// counts only on a rail the pack's trust roots pin.
    pub rail: RailPin,
}

/// One verified vouch: the controller its signer is bound to (evaluate.go
/// Vouch).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Vouch {
    pub controller: String,
}
