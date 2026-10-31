// SPDX-License-Identifier: Apache-2.0

//! The guard (go/guard): load the asg/inference pack, lay the operator's
//! policy profile over it, and decide each inference purchase against the
//! history of earlier decisions.
//!
//! ```
//! use capsule_engine::guard::{record_for, Action, Engine, History};
//!
//! let engine = Engine::inference().unwrap();
//! let action = Action::decode(br#"{"operator": "node-a", "developer": "mesh-node@v1",
//!     "verb": "inference.purchase", "action_class": "money.purchase", "taxonomy_version": "6",
//!     "timestamp": "2026-10-29T12:00:00Z", "target": "node-b", "equivalence_key": "exchange-1",
//!     "input_msat_per_million": 1000000, "output_msat_per_million": 4000000}"#).unwrap();
//! let mut history = History::new();
//! let decision = engine.decide(&action, &history).unwrap();
//! assert_eq!(decision.outcome().as_str(), "allow");
//! // The caller seals the decision, then records it under that capsule id.
//! history.append(record_for(&action, &decision, "capsule-id-the-caller-sealed"));
//! ```

mod action;
mod checks;
mod engine;
mod pack;
mod profile;
mod taxonomy;

pub use action::{record_for, Action, History, Record};
pub use checks::{Constraint, ConstraintResult};
pub use engine::{Decision, Engine, Outcome};
pub use profile::{Profile, PROFILE_FORMAT};

/// Why the guard refused: a pack or definition that does not load, an
/// input it cannot read, or an action it will not decide.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Error {
    /// The embedded pack, a definition it cites, or the taxonomy does not
    /// load at its pinned digest.
    Load(String),
    /// An action or a profile is not one this guard reads.
    Decode(String),
    /// An action or a profile this guard reads and will not decide or apply.
    Check(String),
}

impl Error {
    /// The refusal's message.
    pub fn message(&self) -> &str {
        match self {
            Error::Load(m) | Error::Decode(m) | Error::Check(m) => m,
        }
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Load(m) => write!(f, "load: {m}"),
            Error::Decode(m) => write!(f, "decode: {m}"),
            Error::Check(m) => write!(f, "check: {m}"),
        }
    }
}

impl std::error::Error for Error {}
