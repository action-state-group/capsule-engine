// SPDX-License-Identifier: Apache-2.0

//! The admission resolution (go/admission/evaluate.go Evaluate, readRow).

use std::collections::{BTreeMap, BTreeSet};

use super::band::{add, fresh, quantity, value_band, HOUR, SECOND};
use super::decision::{AskOut, Decision, Effect, PackRef, RowResult, RowStatus};
use super::pack::{OnFail, Pack, Row, SOURCE_JUDGED};
use super::{Inputs, Timestamp, Verdict, CARD_UNVERIFIABLE, CARD_VERIFIED};
use crate::canon;

/// evaluate.go SourceAboveTier, SourceMissing, SourceRows: the decision
/// sources that are not a row.
const SOURCE_ABOVE_TIER: &str = "above_tier";
const SOURCE_MISSING: &str = "missing";
const SOURCE_ROWS: &str = "rows";

/// evaluate.go Evaluate: decides admission under `p`, deterministically in
/// its inputs.
///
/// The resolution (admission design §2, §4; capsule-engine#265):
///  1. the deal's band gives its tier; a free deal is in the lowest tier,
///     and a band above every tier is above_tier;
///  2. each row that applies to the deal's tier reads pass, fail or
///     not_read;
///  3. a failing row whose on_fail is NO decides NO; a not_read one gives
///     the pack's missing default;
///  4. the tier admitted starts at the deal's tier and drops below a LIMIT
///     row's tier when that row fails or is not read (a ladder row, only to
///     the tier its earned history holds), and to a judged row's cap when it
///     fails;
///  5. GO when the tier admitted holds the deal; otherwise ASK when a LIMIT
///     row that did not pass carries an ask not yet made and the budget
///     allows; otherwise LIMIT at the tier admitted. A refused, unanswered
///     or pending ask limits and never bans.
///
/// An outcome the pack does not yield is never produced: where only the
/// evaluator could produce one, the pack's missing default decides.
pub fn evaluate(p: &Pack, input: &Inputs) -> Decision {
    let mut d = Decision {
        verdict: Verdict::No,
        source: String::new(),
        tier: None,
        band: None,
        rows: Vec::new(),
        asks: Vec::new(),
        pack: PackRef { id: p.id.clone(), digest: p.digest.clone() },
    };
    // The loader refuses a pack with no tier.
    let mut top = p.tiers.len() as i64 - 1;

    let (mut deal_tier, mut above_tier, mut missing) = (0i64, false, false);
    match value_band(&input.amount, &input.currency, &p.accept) {
        Err(_) => missing = true,
        Ok(None) => {}
        Ok(Some(band)) => {
            deal_tier = p
                .tiers
                .iter()
                .position(|t| t.max_decade.get(&band.currency).is_some_and(|ceil| band.decade <= *ceil))
                .map_or(-1, |i| i as i64);
            if deal_tier < 0 {
                // Above every tier that names the unit: the deal is held to
                // the highest of them, the most this pack offers in that
                // unit. With none naming it, it stays -1, and no row applies.
                above_tier = true;
                if let Some(i) = p.tiers.iter().rposition(|t| t.max_decade.contains_key(&band.currency)) {
                    deal_tier = i as i64;
                }
                top = deal_tier;
            }
            d.band = Some(band);
        }
    }

    let mut admitted = deal_tier;
    let (mut no_row, mut missing_row): (Option<String>, Option<String>) = (None, None);
    // Indexes into d.rows: the LIMIT rows that did not pass and carry an ask.
    let mut ask_rows: Vec<(usize, &Row)> = Vec::new();
    for r in &p.rows {
        if r.from as i64 > deal_tier {
            continue;
        }
        let mut res = read_row(r, input, p);
        res.effect = Effect::None;
        match r.on_fail {
            OnFail::Cap(cap) if r.source == SOURCE_JUDGED => {
                if res.result == RowStatus::Fail && (cap as i64) < admitted {
                    (admitted, res.effect) = (cap as i64, Effect::Cap);
                }
            }
            OnFail::Cap(_) | OnFail::Record => {}
            OnFail::No => {
                if res.result == RowStatus::Fail && no_row.is_none() {
                    no_row = Some(r.id.clone());
                    res.effect = Effect::No;
                } else if res.result == RowStatus::NotRead && !res.held && missing_row.is_none() {
                    missing_row = Some(r.id.clone());
                }
            }
            OnFail::Limit => {
                if res.result != RowStatus::Pass {
                    res.effect = Effect::Limit;
                    let mut to = r.from as i64 - 1;
                    // A ladder row limits to the tier its earned history
                    // holds, when that is above the tier below its own
                    // (Reference v3 §3A.14): history earns a limit, not a
                    // pass or a floor.
                    if let (Some(limit), Some(band)) = (res.earned, &d.band) {
                        to = to.max(earned_tier(p, &band.currency, limit, deal_tier));
                    }
                    admitted = admitted.min(to);
                    // A stale or missing card is asked for again (admission
                    // design §4, §8): a failing row asks as a not_read one
                    // does.
                    if r.ask.is_some() {
                        ask_rows.push((d.rows.len(), r));
                    }
                }
            }
        }
        d.rows.push(res);
    }

    let decide = |mut d: Decision, verdict: Verdict, source: &str, tier: i64| -> Decision {
        let (mut verdict, mut tier) = (verdict, tier);
        // Only above_tier can reach an outcome the pack does not yield (the
        // loader refuses rows that could). The missing default decides it,
        // and the source still names the cause.
        if verdict != Verdict::No && !p.outcomes.contains(&verdict) {
            verdict = p.missing;
        }
        match verdict {
            Verdict::No => tier = -1,
            Verdict::Limit if tier < 0 => tier = 0,
            _ => {}
        }
        d.verdict = verdict;
        d.source = source.to_string();
        d.tier = usize::try_from(tier).ok().and_then(|i| p.tiers.get(i)).map(|t| t.id.clone());
        d
    };
    if let Some(row) = no_row {
        return decide(d, Verdict::No, &row, -1);
    }
    if missing {
        return decide(d, p.missing, SOURCE_MISSING, -1);
    }
    if let Some(row) = missing_row {
        return decide(d, p.missing, &row, -1);
    }
    // Above the top tier: once no NO row and no missing input decided, and
    // no row lowered the tier, the decision is LIMIT at the top tier. A
    // lowered tier goes on to ASK or LIMIT like any other deal.
    if above_tier && admitted >= top {
        return decide(d, Verdict::Limit, SOURCE_ABOVE_TIER, top);
    }
    if admitted >= deal_tier {
        return decide(d, Verdict::Go, SOURCE_ROWS, deal_tier);
    }
    let mut budget = p.max_asks.wrapping_sub(input.asks_made);
    let mut asked: BTreeSet<&str> = BTreeSet::new();
    for (j, r) in ask_rows {
        let Some(derivation) = r.ask.as_deref() else { continue };
        if input.asks.contains_key(derivation) {
            continue;
        }
        // One ask answers every row that needs its derivation.
        if !asked.contains(derivation) {
            if budget <= 0 {
                continue;
            }
            budget -= 1;
            asked.insert(derivation);
            d.asks.push(AskOut { row: r.id.clone(), derivation: derivation.to_string(), deadline_s: p.deadline_s });
        }
        d.rows[j].effect = Effect::Ask;
    }
    if !d.asks.is_empty() {
        return decide(d, Verdict::Ask, SOURCE_ROWS, admitted);
    }
    // admitted is never below the lowest tier: the loader refuses a LIMIT
    // row there, and a cap names a tier.
    decide(d, Verdict::Limit, SOURCE_ROWS, admitted)
}

/// evaluate.go earnedTier: the highest tier, up to `deal_tier`, that a
/// limit of decade `limit` in `unit` holds whole: every tier up to it that
/// names the unit has a ceiling at or below `limit`. It is -1 when the lowest
/// such tier's ceiling is already above `limit`, and reaches `deal_tier` only
/// for a deal above the top tier.
fn earned_tier(p: &Pack, unit: &str, limit: i64, deal_tier: i64) -> i64 {
    for (i, t) in p.tiers.iter().enumerate().take((deal_tier + 1).max(0) as usize) {
        if t.max_decade.get(unit).is_some_and(|ceil| *ceil > limit) {
            return i as i64 - 1;
        }
    }
    deal_tier
}

/// evaluate.go stakeCovers: whether the stake covers the deal, in the same
/// band unit; None (not read) when there is no stake, when its rail is not
/// pinned in the trust roots (an undeclared adapter is NOT_CHECKED, never a
/// pass; AAC#406), or when a stake or deal amount does not read or is in
/// another unit.
fn stake_covers(input: &Inputs, p: &Pack) -> Option<bool> {
    let stake = input.stake.as_ref().filter(|s| p.trust_roots.rails.contains(&s.rail))?;
    match (quantity(&stake.amount, &stake.currency, &p.accept), quantity(&input.amount, &input.currency, &p.accept)) {
        (Ok(s), Ok(deal)) if s.unit == deal.unit => Some(s.at_least(&deal)),
        _ => None,
    }
}

/// evaluate.go bandDecadeMax: a card band outside it is not counted.
const BAND_DECADE_MAX: i64 = 1000;

/// evaluate.go ladder: the per-band ladder with its two roads (Reference v3
/// §3A.14), limit = max(earned band × step-up, collateral on this deal).
fn ladder(r: &Row, input: &Inputs, p: &Pack) -> RowResult {
    if stake_covers(input, p) == Some(true) {
        return result(RowStatus::Pass);
    }
    let deal = match value_band(&input.amount, &input.currency, &p.accept) {
        Ok(Some(band)) => band,
        _ => return result(RowStatus::NotRead),
    };
    let Some(bands) = input.card.bands.as_ref().filter(|_| input.card.state == CARD_VERIFIED) else {
        return result(RowStatus::NotRead);
    };
    let mut by_decade: BTreeMap<i64, (i64, i64)> = BTreeMap::new();
    for b in bands {
        let Some(u) = p.accept.get(&b.currency) else { continue };
        if u.band != deal.currency
            || !(-BAND_DECADE_MAX..=BAND_DECADE_MAX).contains(&b.decade)
            || b.subjects < 0
            || b.adverse < 0
            || b.adverse > b.subjects
            || b.subjects > canon::MAX_SAFE_INTEGER
        {
            continue;
        }
        let s = by_decade.entry(b.decade + u.scale).or_insert((0, 0));
        s.0 = (s.0 + b.subjects).min(canon::MAX_SAFE_INTEGER);
        s.1 = (s.1 + b.adverse).min(s.0);
    }
    let (k, permille) = (param(r, "k"), param(r, "max_adverse_permille"));
    // Both products stay inside i64: subjects and adverse are at most
    // 2^53 − 1, and permille at most 1000.
    let earned = by_decade
        .iter()
        .filter(|(_, (subjects, adverse))| *subjects >= k && adverse * 1000 <= permille * subjects)
        .map(|(decade, _)| *decade)
        .max();
    match earned {
        None => result(RowStatus::Fail),
        Some(e) => {
            let limit = e + param(r, "step_decades");
            RowResult { earned: Some(limit), ..result(of(deal.decade <= limit)) }
        }
    }
}

fn of(pass: bool) -> RowStatus {
    if pass {
        RowStatus::Pass
    } else {
        RowStatus::Fail
    }
}

fn result(status: RowStatus) -> RowResult {
    RowResult {
        row: String::new(),
        result: status,
        count: None,
        denominator: None,
        effect: Effect::None,
        held: false,
        earned: None,
    }
}

/// evaluate.go counted.
fn counted(status: RowStatus, count: i64, denominator: i64) -> RowResult {
    RowResult { count: Some(count), denominator: Some(denominator), ..result(status) }
}

/// evaluate.go contains, over a trust-root list.
fn trusts(roots: &[String], who: &str) -> bool {
    roots.iter().any(|r| r == who)
}

/// Go time.Duration(n) * 24 * time.Hour, in nanoseconds. The loader bounds
/// n to a century of days (param_max), so this cannot overflow.
fn days(n: i64) -> i64 {
    n * 24 * HOUR
}

/// A row's integer param; the loader refuses a row missing one its check
/// takes.
fn param(r: &Row, k: &str) -> i64 {
    r.params.get(k).copied().unwrap_or(0)
}

/// evaluate.go readRow: one row's result; its effect is the caller's.
fn read_row(r: &Row, input: &Inputs, p: &Pack) -> RowResult {
    let not_read = || result(RowStatus::NotRead);
    let first = input.card.first_checkpoint_at.unwrap_or_else(Timestamp::go_zero);
    let latest = input.card.latest_checkpoint_at.unwrap_or_else(Timestamp::go_zero);
    let verified = input.card.state == CARD_VERIFIED;
    let mut res = match r.check.as_str() {
        "platform.attestation_not_invalid" => match input.platform.release_attestation.as_str() {
            "" => not_read(),
            "invalid" => result(RowStatus::Fail),
            _ => result(RowStatus::Pass),
        },
        "platform.owner_signed" => input.platform.owner_signed.map_or_else(not_read, |s| result(of(s))),
        "card.verified" => match input.card.state.as_str() {
            CARD_VERIFIED => result(RowStatus::Pass),
            CARD_UNVERIFIABLE => result(RowStatus::Fail),
            _ => not_read(),
        },
        "card.fresh" if !verified => not_read(),
        // The loader bounds max_age_s (param_max), so this cannot overflow.
        "card.fresh" => result(of(fresh(latest, input.now, param(r, "max_age_s") * SECOND))),
        "card.key_age" if !verified => not_read(),
        // A card that states no first checkpoint shows no age (Go IsZero).
        "card.key_age" if first == Timestamp::go_zero() => result(RowStatus::Fail),
        "card.key_age" => result(of(first <= add(input.now, -days(param(r, "min_days"))))),
        "card.witnessed" if !verified => not_read(),
        "card.witnessed" => {
            let w = &input.card.witness;
            result(of(!w.is_empty() && trusts(&p.trust_roots.witnesses, w)))
        }
        "card.claims_held" => match (&input.card.claims, verified) {
            (Some(c), true) => counted(of(c.held >= param(r, "min")), c.held, c.total),
            _ => not_read(),
        },
        "card.controller_declared" if !verified => not_read(),
        "card.controller_declared" => result(of(!input.card.controller.is_empty())),
        "card.controller_trusted" if !verified => not_read(),
        "card.controller_trusted" => {
            let c = &input.card.controller;
            result(of(!c.is_empty() && trusts(&p.trust_roots.controllers, c)))
        }
        "bound.selection_verified" => match input.selection.state.as_str() {
            CARD_VERIFIED => result(RowStatus::Pass),
            CARD_UNVERIFIABLE => result(RowStatus::Fail),
            _ => not_read(),
        },
        "bound.no_unanswered_dispute" if input.selection.state != CARD_VERIFIED => not_read(),
        "bound.no_unanswered_dispute" => {
            let since = add(input.now, -days(param(r, "window_days")));
            let open =
                input.selection.unanswered_disputes.iter().filter(|at| !(**at < since) && !(**at > input.now)).count()
                    as i64;
            // A count with no denominator: the selection states none.
            RowResult { count: Some(open), ..result(of(open == 0)) }
        }
        "own.settled" | "own.held_halves" | "own.no_conflicting" | "own.no_adverse_adjudication" => {
            let Some(own) = &input.own else { return RowResult { row: r.id.clone(), ..not_read() } };
            match r.check.as_str() {
                "own.settled" => counted(of(own.settled >= param(r, "min")), own.settled, own.exchanges),
                "own.held_halves" => counted(of(own.held_halves >= param(r, "min")), own.held_halves, own.exchanges),
                "own.no_conflicting" => counted(of(own.conflicting == 0), own.conflicting, own.exchanges),
                _ => {
                    // not_read until this node holds a verdict about the
                    // counterparty (the plugin's disputes row, base/1).
                    if own.verdicts.is_empty() {
                        RowResult { held: true, ..not_read() }
                    } else {
                        let since = add(input.now, -days(param(r, "window_days")));
                        let (mut adverse, mut in_window) = (0i64, 0i64);
                        for v in &own.verdicts {
                            if v.at < since || v.at > input.now {
                                continue;
                            }
                            in_window += 1;
                            if v.adverse {
                                adverse += 1;
                            }
                        }
                        counted(of(adverse == 0), adverse, in_window)
                    }
                }
            }
        }
        "operator.not_deny_listed" => result(of(!input.operator.deny_listed)),
        // With no allow list kept, nothing is refused by one.
        "operator.allow_listed" => result(of(input.operator.allow_listed.unwrap_or(true))),
        "vouch.distinct_controllers" => {
            // Distinct controllers, the counterparty's own excluded; a
            // voucher's weight is its own card, read elsewhere (D-26).
            let distinct: BTreeSet<&str> = input
                .vouches
                .iter()
                .map(|v| v.controller.as_str())
                .filter(|c| !c.is_empty() && *c != input.subject)
                .collect();
            let n = distinct.len() as i64;
            counted(of(n >= param(r, "min")), n, input.vouches.len() as i64)
        }
        "stake.covers_deal" => stake_covers(input, p).map_or_else(not_read, |c| result(of(c))),
        "card.band_history_or_stake" => ladder(r, input, p),
        // Open disputes stand beside the ladder, never inside its adverse
        // share (evidencebook-skills#87).
        "card.open_disputes" => match (&input.card.disputes, verified) {
            (Some(c), true) => counted(of(c.open <= param(r, "max")), c.open, c.of),
            _ => not_read(),
        },
        // A judge outside the trust roots is reported, not counted
        // (SDK-SURFACE evaluate, NOT_CHECKED).
        "judged" => match input.judged.get(&r.id) {
            Some(j) if trusts(&p.trust_roots.judges, &j.judge) => result(of(j.met)),
            _ => not_read(),
        },
        // The loader admits only the checks above (PREDICATES); Go panics
        // here. A row this reader does not know is never a pass.
        _ => not_read(),
    };
    res.row = r.id.clone();
    res
}
