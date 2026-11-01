// SPDX-License-Identifier: Apache-2.0

//! The admission decision and the block a caller seals
//! (go/admission/decision.go, evaluate.go Decision).

use super::{Band, Verdict};
use crate::canon::{self, Value};

/// A row's result, as the plugin's admission_decision rows read (evaluate.go
/// Pass, Fail, NotRead).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RowStatus {
    Pass,
    Fail,
    NotRead,
}

impl RowStatus {
    /// The result as the block names it.
    pub fn as_str(self) -> &'static str {
        match self {
            RowStatus::Pass => "pass",
            RowStatus::Fail => "fail",
            RowStatus::NotRead => "not_read",
        }
    }
}

/// What a row did to the decision (evaluate.go RowResult.Effect).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Effect {
    None,
    No,
    Limit,
    Ask,
    Cap,
}

impl Effect {
    /// The effect as the block names it: none, NO, LIMIT, ASK or cap.
    pub fn as_str(self) -> &'static str {
        match self {
            Effect::None => "none",
            Effect::No => "NO",
            Effect::Limit => "LIMIT",
            Effect::Ask => "ASK",
            Effect::Cap => "cap",
        }
    }
}

/// One row as the decision records it (evaluate.go RowResult).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RowResult {
    pub row: String,
    pub result: RowStatus,
    /// Set on a row that counts; the denominator where the count has one
    /// (bound.no_unanswered_dispute counts with none).
    pub count: Option<i64>,
    pub denominator: Option<i64>,
    pub effect: Effect,
    /// A not_read row whose input was there but held nothing to read (no
    /// verdict yet about the counterparty): not a missing input.
    pub(crate) held: bool,
    /// On a ladder row that read the card's bands and found one earned: the
    /// highest decade, in the deal's band unit, the earned history covers
    /// (the earned band plus the step).
    pub(crate) earned: Option<i64>,
    /// Set on a card.verified row that fails with a reason from
    /// INFERENCE_SERVICE_HISTORY_PROPERTIES.
    pub reason: String,
    /// Set on a card.self_reported_served_periods row that read the card:
    /// the periods of its window not counted, by why. Its counts are the
    /// node's own statement (self_reported).
    pub not_counted: Option<NotCounted>,
}

/// evaluate.go NotCounted: the periods of a served periods row's window that
/// were not counted: their total suppressed below the floor or as a
/// complement (ISH §6.2), or not listed because the node's stream is younger
/// than the window. None of these is a fail on its own.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct NotCounted {
    pub below_floor: i64,
    pub complementary: i64,
    pub not_listed: i64,
}

/// evaluate.go Coverage: the coverage a card's counts declare, and whether
/// its population was checked.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Coverage {
    pub kind: String,
    pub population: String,
}

/// One Evidence Request a decision derives from a row that did not pass
/// (evaluate.go AskOut).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AskOut {
    pub row: String,
    pub derivation: String,
    /// The pack's budget.deadline_s: after it, an unanswered ask is recorded
    /// as absence, never failure (design §8).
    pub deadline_s: i64,
}

/// The pack a decision cites (evaluate.go PackRef).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PackRef {
    pub id: String,
    pub digest: String,
}

/// An admission decision (evaluate.go Decision).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Decision {
    pub verdict: Verdict,
    /// What decided: a row id, or above_tier, missing or rows.
    pub source: String,
    /// The tier admitted at; None when there is none.
    pub tier: Option<String>,
    /// None for a free deal or one whose band could not be read.
    pub band: Option<Band>,
    pub rows: Vec<RowResult>,
    pub asks: Vec<AskOut>,
    pub pack: PackRef,
    /// Set when the card carries served periods: the counts declare the
    /// node's stream up to the anchor, and were never checked against
    /// records, so completeness is not established (ISH §4.2).
    pub coverage: Option<Coverage>,
}

impl Decision {
    /// decision.go Decision.Block: the decision as the admission_decision
    /// block a caller seals (Mesh-LLM/capsules-plugin's admission.rs BLOCK).
    /// A card.verified row that fails with a reason records it, a served
    /// periods row its label (self_reported) and the periods it did not
    /// count, and a decision on a card with served periods its coverage,
    /// population NOT_CHECKED. reads holds the policy entry only; the caller appends what it read
    /// and names the counterparty by its own reference. The block is
    /// local-only: it is about one counterparty and is never exported for
    /// another party.
    pub fn block(&self) -> Value {
        let rows = self
            .rows
            .iter()
            .map(|r| {
                let mut ms = vec![
                    ("row".to_string(), canon::s(&r.row)),
                    ("result".to_string(), canon::s(r.result.as_str())),
                    ("effect".to_string(), canon::s(r.effect.as_str())),
                ];
                if let Some(c) = r.count {
                    ms.push(("count".to_string(), Value::Int(c)));
                }
                if let Some(d) = r.denominator {
                    ms.push(("denominator".to_string(), Value::Int(d)));
                }
                if !r.reason.is_empty() {
                    ms.push(("reason".to_string(), canon::s(&r.reason)));
                }
                if let Some(nc) = r.not_counted {
                    ms.push(("label".to_string(), canon::s("self_reported")));
                    ms.push((
                        "not_counted".to_string(),
                        canon::obj([
                            ("below_floor", Value::Int(nc.below_floor)),
                            ("complementary", Value::Int(nc.complementary)),
                            ("not_listed", Value::Int(nc.not_listed)),
                        ]),
                    ));
                }
                Value::Obj(ms)
            })
            .collect();
        let asks = self
            .asks
            .iter()
            .map(|a| {
                canon::obj([
                    ("row", canon::s(&a.row)),
                    ("derivation", canon::s(&a.derivation)),
                    ("deadline_s", Value::Int(a.deadline_s)),
                ])
            })
            .collect();
        let policy = canon::obj([
            ("kind", canon::s("policy")),
            ("policy", canon::s(&self.pack.id)),
            ("digest", canon::obj([("alg", canon::s("SHA-256")), ("digest", canon::s(&self.pack.digest))])),
        ]);
        let mut block = vec![
            ("verdict", canon::s(self.verdict.as_str())),
            ("source", canon::s(&self.source)),
            ("tier", canon::str_or_null(self.tier.as_deref())),
            (
                "band",
                self.band.as_ref().map_or(Value::Null, |b| {
                    canon::obj([("currency", canon::s(&b.currency)), ("decade", Value::Int(b.decade))])
                }),
            ),
            ("reads", Value::Arr(vec![policy])),
            ("rows", Value::Arr(rows)),
            ("asks", Value::Arr(asks)),
        ];
        // coverage is written only for a card with served periods, so a
        // decision on any other card keeps its bytes.
        if let Some(c) = &self.coverage {
            block
                .push(("coverage", canon::obj([("kind", canon::s(&c.kind)), ("population", canon::s(&c.population))])));
        }
        Value::Obj(block.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }
}
