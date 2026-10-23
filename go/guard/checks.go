package guard

import (
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/action-state-group/capsule-engine/go/canon"
)

func strOrNull(s *string) canon.Value {
	if s == nil {
		return canon.Null()
	}
	return canon.Str(*s)
}

// equivalenceKeyForAction is dedupe.py equivalence_key_for_action: the
// caller's equivalence_key as the digest a decision seals
// (sealedEquivalenceKey), else the digest of the default formula.
func equivalenceKeyForAction(a Action) (string, error) {
	if a.EquivalenceKey != nil {
		return sealedEquivalenceKey(*a.EquivalenceKey)
	}
	if a.TaxonomyVersion != nil && (a.ReturnedMinor != nil || a.ReversesRef != nil) {
		return refundKey(a.Operator, a.Developer, a.ActionClass, a.Target, a.ReturnedMinor, a.ReversesRef)
	}
	if a.TaxonomyVersion != nil {
		amount, _ := a.engineAmount()
		return actKey(a.Operator, a.Developer, a.ActionClass, a.Target, amount)
	}
	return canon.Digest(canon.Obj(
		canon.M("operator", canon.Str(a.Operator)),
		canon.M("developer", canon.Str(a.Developer)),
		canon.M("action_type", canon.Str(a.ActionType)),
		canon.M("verb", canon.Str(a.Verb)),
		canon.M("target", strOrNull(a.Target)),
	))
}

// sealedEquivalenceKey is action.py sealed_equivalence_key (capsule-engine
// #134): json_digest of the caller's key, what a decision seals as
// asg_payload.equivalence_key_digest and dedupe compares, never the raw key.
func sealedEquivalenceKey(key string) (string, error) {
	return canon.Digest(canon.Str(key))
}

// equivalenceKeyForRecord is dedupe.py equivalence_key_for_capsule: the
// sealed digest of the caller's key when the capsule carries one (#134),
// else the default formula over its own fields. A record sealed before the
// digest existed is keyed by the formula.
func equivalenceKeyForRecord(r Record) (string, error) {
	if r.EquivalenceKeyDigest != nil {
		return *r.EquivalenceKeyDigest, nil
	}
	if r.TaxonomyVersion != nil && (r.ReturnedMinor != nil || r.ReversesRef != nil) {
		return refundKey(r.Operator, r.Developer, r.ActionClass, r.Target, r.ReturnedMinor, r.ReversesRef)
	}
	if r.TaxonomyVersion != nil {
		return actKey(r.Operator, r.Developer, r.ActionClass, r.Target, r.keyAmount())
	}
	return canon.Digest(canon.Obj(
		canon.M("operator", canon.Str(r.Operator)),
		canon.M("developer", canon.Str(r.Developer)),
		canon.M("action_type", canon.Str(r.ActionType)),
		canon.M("verb", canon.Str(verbOf(r.ActionID))),
		canon.M("target", strOrNull(r.Target)),
	))
}

// keyAmount is the amount dedupe keys r on: the amount the engine's action
// carries. A history act's is the spend its deal bridge takes
// (_bridge_deal_check: spend_minor, 0 when the money moved in, none when
// unread), which is what the engine's history ledger writes as the act's
// amount_minor (live_history.py), so it keys like the check it repeats
// (engineAmount); never the body's amount_minor, which on a sale is the
// price. Any other record's sealed amount_minor is already the engine's.
func (r Record) keyAmount() *int64 {
	if r.live != "" {
		return r.SpendMinor
	}
	return r.AmountMinor
}

// actKey is dedupe.py _act_key for an act stated against a pinned taxonomy
// (capsule-engine f334dd5): operator, developer, the act's action_class, its
// target and its amount (the amount the engine's action carries: on a deal
// record, the spend its bridge takes). The record that states it never
// enters the key, neither its action_type nor its action_id.
func actKey(operator, developer string, class, target *string, amount *int64) (string, error) {
	amountValue := canon.Null()
	if amount != nil {
		amountValue = canon.Int(*amount)
	}
	return canon.Digest(canon.Obj(
		canon.M("operator", canon.Str(operator)),
		canon.M("developer", canon.Str(developer)),
		canon.M("action_class", strOrNull(class)),
		canon.M("target", strOrNull(target)),
		canon.M("amount_minor", amountValue),
	))
}

// refundKey is dedupe.py _act_key for an act that moves money in
// (returned_minor or reverses_ref set; capsule-engine #108): keyed on the
// amount it returns and the act it reverses in place of the amount, so two
// refunds of different amounts never collide, and the same refund of the
// same act does.
func refundKey(operator, developer string, class, target *string, returned *int64, reverses *string) (string, error) {
	return canon.Digest(canon.Obj(
		canon.M("operator", canon.Str(operator)),
		canon.M("developer", canon.Str(developer)),
		canon.M("action_class", strOrNull(class)),
		canon.M("target", strOrNull(target)),
		canon.M("returned_minor", intOrNull(returned)),
		canon.M("reverses_ref", strOrNull(reverses)),
	))
}

func intOrNull(n *int64) canon.Value {
	if n == nil {
		return canon.Null()
	}
	return canon.Int(*n)
}

// anotherDealReason is dedupe.py _ANOTHER_DEAL_REASON (capsule-engine #111):
// it names nothing from the earlier act, which may be with another
// counterparty.
const anotherDealReason = "the same act was taken in another deal inside the window: confirm it is not a duplicate"

// inAnotherDeal is dedupe.py _in_another_deal: both state a deal, and they
// differ. Unstated on either side, nothing shows the deals differ.
func inAnotherDeal(a Action, r Record) bool {
	return a.DealID != nil && r.DealID != nil && *a.DealID != *r.DealID
}

// checkDedupe: exact-match equivalence over records of the same action_type
// whose timestamp is at or after (timestamp - window_days); the first match
// in ledger order fails (dedupe.py:58-83, engine.py window, cll ScanQuery:
// since is an inclusive instant and an unparseable timestamp is left out).
func (e *Engine) checkDedupe(a Action, ledger []Record, at time.Time) (c Constraint, err error) {
	const id, method = "dedupe", "exact_match_index_v0"
	// The key's fields (or the caller's override alone), and the timestamp
	// the window is anchored on.
	rd := []string{"equivalence_key", "timestamp"}
	if a.EquivalenceKey == nil {
		rd = append(rd, "operator", "developer", "action_type", "verb", "target")
	}
	defer func() { c.reads = rd }()
	// dedupe.py check_dedupe (#107): a record that states no act is not
	// deduped and never matches.
	if a.statesNoAct {
		rd = nil
		return outOfScope(id, method), nil
	}
	key, err := equivalenceKeyForAction(a)
	if err != nil {
		return Constraint{}, err
	}
	if a.ReturnedMinor != nil || a.ReversesRef != nil {
		rd = append(rd, "returned_minor", "reverses_ref")
	}
	since := at.AddDate(0, 0, -e.dedupeWindowDays)
	var other *Record
	// dedupe.py check_dedupe (f334dd5): an act stated against a pinned
	// taxonomy is matched across every action_type (a deal check is fyi, the
	// act it carries may be recorded as decide); any other action only
	// against its own.
	for _, r := range ledger {
		// An unchecked act was never authorized: it is never the earlier
		// act a repeat duplicates (ruling (a)). The engine never
		// holds one in its ledger (the replay gives it no decision).
		if r.Unchecked {
			continue
		}
		// A statement is never an act: a live one is not counts_as_act
		// (history_state.py), and a replay's names no act a key can match.
		if r.Stated != nil {
			continue
		}
		if a.TaxonomyVersion == nil && r.ActionType != a.ActionType {
			continue
		}
		ts, err := parseInstant(r.Timestamp)
		if err != nil || ts.Before(since) {
			continue
		}
		rk, err := equivalenceKeyForRecord(r)
		if err != nil {
			return Constraint{}, err
		}
		if rk != key {
			continue
		}
		// dedupe.py check_dedupe (#111): a match in the same deal (or with a
		// deal unstated on either side) is a double commit and fails at
		// once; a match in another deal is kept, and any same-deal match in
		// the window wins over it.
		if !inAnotherDeal(a, r) {
			if a.DealID != nil || r.DealID != nil {
				rd = append(rd, "deal_id")
			}
			c := decided(id, method, Fail, canon.Obj(canon.M("equivalence_key", canon.Str(key)), canon.M("matched_capsule_id", canon.Str(r.CapsuleID))))
			c.matched = r.CapsuleID
			return c, nil
		}
		if other == nil {
			other = &r
		}
	}
	if other != nil {
		// dedupe.py _repeat_in_another_deal: an approver may confirm it is
		// not a duplicate. The earlier act's capsule id, amount, currency
		// and time are evidence only, never in the reason.
		rd = append(rd, "deal_id")
		c := decided(id, method, Fail, canon.Obj(
			canon.M("equivalence_key", canon.Str(key)),
			canon.M("repeat", canon.Str("other_deal")),
			canon.M("matched_capsule_id", canon.Str(other.CapsuleID)),
			canon.M("matched_amount_minor", intOrNull(other.keyAmount())),
			canon.M("matched_currency", strOrNull(other.Currency)),
			canon.M("matched_at", canon.Str(other.Timestamp)),
		))
		c.matched, c.asksApprover, c.failNote = other.CapsuleID, true, anotherDealReason
		return c, nil
	}
	return decided(id, method, Pass, canon.Obj(canon.M("equivalence_key", canon.Str(key)))), nil
}

// checkCaps is caps/3.0.0 (capsule-engine guards/checks/caps.py at 2521ee6):
// a class with no window limit is out of scope; an in-scope action with no
// amount_minor is a population-a n/a. Otherwise weekly_spend is the caps
// fold over the records sharing the action's value for the fold's key
// (developer or operator, caps.py _fold_key), and the action fails if its
// amount exceeds the class's per-action limit or weekly_spend + amount
// exceeds the window limit (caps.py _judge_two_limits; both compare with <=
// to pass). A class with a window limit and no per-action limit is judged
// on the window alone, as caps.py does. Limits are looked up by canonical
// class (caps.py cap_for).
func (e *Engine) checkCaps(a Action, ledger []Record, at time.Time, in checkInputs) (c Constraint) {
	const id = "caps"
	if e.capsFold == nil {
		// engine.py _check_caps (e0e664d): no caps definition is configured,
		// so caps is out of scope with no method.
		return outOfScope(id, "")
	}
	rd := []string{"action_class"}
	defer func() { c.reads = rd }()
	method := e.capsFold.id
	class := e.tax.canonical(a.class())
	packCap, ok := e.capsMinor[class]
	if !ok {
		return outsideScope(id, method, a.class(), sortedKeys(e.capsMinor))
	}
	// The limits in force: the operator's from activated policy, where e
	// reads it (WithOperatorLimits), else the wicket's (engine.py
	// _limits_for). A per-action limit applies only to a class with a
	// window limit, as above.
	cap, windowSource := e.limitFor(CapsKeyWindow, class, at, packCap)
	engineAmount, amountReads := a.engineAmount()
	rd = append(rd, amountReads...)
	if engineAmount == nil {
		return missingField(id, method, "amount_minor")
	}
	keyValue := a.Developer
	if e.capsFold.key == "operator" {
		keyValue = a.Operator
	}
	rd = append(rd, e.capsFold.key, "timestamp")
	spend, reversals := e.capsFold.evaluate(ledger, keyValue, at)
	// An unchecked act (ruling (a)) and an executed act with no
	// recorded decision (the G3 ruling) are spend the fold never sums
	// (neither has an accepted decision). Under spend.weekly/3.1.0 the
	// engine counts both too (#138); a cancel or refund among them is a
	// reversal citing no charge (unlinked), and one whose spend cannot be
	// read leaves the window not evaluable (WindowUnreadable).
	unchecked, executed, unlinked, unreadable := e.capsFold.outsideTheFold(ledger, keyValue, at, in.history)
	reversals.unlinkedCount += unlinked
	for _, acts := range []*ActsInWindow{unchecked, executed} {
		if acts != nil {
			spend += acts.Minor
		}
	}
	amount := *engineAmount
	projected := spend + amount
	// The evidence is caps.py's CapsEvidence (and TwoLimitCapsEvidence with a
	// per-action limit): "fold" is the fold envelope's "fold", the fold
	// definition's digest.
	members := []canon.Member{canon.M("fold", canon.Str(e.capsFold.digest)),
		canon.M("fold_key", canon.Obj(canon.M("path", canon.Str(e.capsFold.key)), canon.M("value", canon.Str(keyValue)))),
		canon.M("weekly_spend_minor", canon.Int(spend)), canon.M("amount_minor", canon.Int(amount)),
		canon.M("cap_minor", canon.Int(cap)), canon.M("projected_minor", canon.Int(projected))}
	if unreadable > 0 {
		// caps.py: the evidence names how many, read only on a per-action
		// failure, which still stands.
		members = append(members, canon.M("window_unreadable_count", canon.Int(unreadable)))
	}
	// caps.py: under a fold with a reversal clause the evidence adds
	// "reversals" (engine.py ReversalSummary); otherwise it keeps its shape.
	if e.capsFold.reversal != nil {
		members = append(members, canon.M("reversals", canon.Obj(canon.M("linked_count", canon.Int(reversals.linkedCount)),
			canon.M("linked_minor", canon.Int(reversals.linkedMinor)), canon.M("unlinked_count", canon.Int(reversals.unlinkedCount)))))
	}
	// amount is the engine's (engineAmount): on a deal record, the spend its
	// deal bridge takes, so caps decides on it as the engine does; the window
	// reads a history act's amount as the engine does too (fold.summed).
	// agrees is reported only (value.spend_minor_agrees): whether the body's
	// spend_minor equals its amount_minor; no limit reads it.
	var windowPending string
	var agrees *bool
	if a.SpendMinor != nil && a.AmountMinor != nil {
		same := *a.SpendMinor == *a.AmountMinor
		agrees = &same
	}
	if unreadable > 0 {
		windowPending = WindowUnreadable
	}
	if in.coverage != "" {
		windowPending = in.coverage
	}
	r := Pass
	facts := CapsFacts{SpendMinorAgrees: agrees, WindowMinor: cap, AmountMinor: amount, WeeklySpendMinor: spend, ProjectedMinor: projected,
		Fold: method, FoldKey: e.capsFold.key, KeyValue: keyValue, WindowDuration: e.capsFold.windowSpec, UncheckedInWindow: unchecked,
		ExecutedInWindow: executed}
	if in.moneyInExcluded > 0 {
		facts.ExcludedFromWindow = &ExcludedFromWindow{Direction: moneyIn, Count: in.moneyInExcluded}
	}
	perActionAmount := amount
	packPerAction, hasPerAction := e.perActionMinor[class]
	perAction, perActionSource := e.limitFor(CapsKeyPerAction, class, at, packPerAction)
	if hasPerAction {
		facts.PerActionMinor = &perAction
		// caps.py _per_action_basis, under per_action_reads.
		if e.perActionReads != "" {
			basis := e.perActionBasis(a, amount)
			if a.SpendAuthorizedMinor != nil {
				rd = append(rd, "spend_authorized_minor")
			}
			facts.Basis = &basis
			perActionAmount = basis.ObservedMinor
		}
		var tripped []canon.Value
		if perActionAmount > perAction {
			tripped = append(tripped, canon.Obj(canon.M("limit", canon.Str("per_action")), canon.M("threshold_minor", canon.Int(perAction)), canon.M("observed_minor", canon.Int(perActionAmount))))
			facts.Tripped = append(facts.Tripped, "per_action")
		}
		if projected > cap {
			tripped = append(tripped, canon.Obj(canon.M("limit", canon.Str("window")), canon.M("threshold_minor", canon.Int(cap)), canon.M("observed_minor", canon.Int(projected))))
			facts.Tripped = append(facts.Tripped, "window")
		}
		if len(tripped) > 0 {
			r = Fail
		}
		members = append(members, canon.M("per_action_cap_minor", canon.Int(perAction)), canon.M("tripped", canon.Arr(tripped)))
		if e.limitSources {
			members = append(members, limitSourcesMember(windowSource, &perActionSource))
		}
		if b := facts.Basis; b != nil {
			members = append(members, canon.M("per_action_basis", canon.Obj(canon.M("field", canon.Str(b.Field)),
				canon.M("fell_back", canon.Bool(b.FellBack)), canon.M("observed_minor", canon.Int(b.ObservedMinor)))))
		}
	} else {
		if e.limitSources {
			members = append(members, limitSourcesMember(windowSource, nil))
		}
		if projected > cap {
			r = Fail
			facts.Tripped = []string{"window"}
		}
	}
	if hasPerAction {
		part := capsPart("per_action", perAction, perActionAmount, "")
		part.Authorised = facts.Basis != nil && !facts.Basis.FellBack
		part.Source = perActionSource
		facts.Parts = append(facts.Parts, part)
	}
	window := capsPart("window", cap, projected, windowPending)
	window.Source = windowSource
	facts.Parts = append(facts.Parts, window)
	// The verdict is the parts': a fail on an evaluated limit stands (and is
	// the engine's fail); otherwise a limit left unevaluated leaves caps
	// unevaluated, never a pass.
	c = decided(id, method, r, canon.Obj(members...))
	failed := false
	var pending []string
	for _, p := range facts.Parts {
		failed = failed || p.Result == Fail
		if p.Pending != "" && !slices.Contains(pending, p.Pending) {
			pending = append(pending, p.Pending)
		}
	}
	if !failed && len(pending) > 0 {
		c = notEvaluable(id, method, strings.Join(pending, "; "))
		if windowPending == WindowUnreadable {
			// caps.py _window_unreadable: n/a in scope, naming spend_minor.
			c.evidence = notApplicableEvidence(id, true, "spend_minor")
		}
	}
	c.caps = &facts
	return c
}

// WindowUnreadable is caps.py WINDOW_UNREADABLE_REASON: under a fold that
// counts executed acts, an executed act in the window has a spend that
// cannot be read, so the total is not known.
const WindowUnreadable = "an earlier act has no recorded decision, so the total cannot count it"

// capsPart is one limit's verdict: unevaluated when pending is set,
// otherwise pass or fail on observed against threshold.
func capsPart(limit string, threshold, observed int64, pending string) CapsPart {
	p := CapsPart{Limit: limit, ThresholdMinor: threshold, ObservedMinor: observed, Result: Pass, Pending: pending}
	switch {
	case pending != "":
		p.Result = NotApplicable
	case observed > threshold:
		p.Result = Fail
	}
	return p
}

// checkVerifyBeforeDispatch: an action citing no mandate is out of scope
// (verify_before_dispatch.py:24-34). Verifying a cited mandate needs the
// capsule verifier, which this port does not have, so the engine refuses
// such an action instead of deciding it (see Engine.Check).
func checkVerifyBeforeDispatch() Constraint {
	c := outOfScope("verify_before_dispatch", "agent_action_capsule.verify")
	c.reads = []string{"cited_mandate_capsule_id"}
	return c
}

// checkDestinationRail: fail when the rail is not one of the allowed rails,
// when the definition lists them, or is a watched rail (destination_rail.py
// at ba76a97). The allow-list is read first and named in the evidence only
// when the definition has one. Under seller.destination_rail/1.1.0
// (destination_rail.py at 6c5d999) the rail is first counted as its
// rail_aliases entry; a rail counted as one of in_person_rails passes only
// with handover in_person, before the allow-list is read; an action with no
// rail fails ("payment method not stated") when missing_rail maps its class
// to fail; and the evidence adds counted_as, in_person_rails and handover.
func checkDestinationRail(a Action, c railConfig) (out Constraint) {
	const id, method = "destination_rail", "set_membership_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "rail")
	// seller.destination_rail/1.1.0 (capsule-engine #201, 6c5d999): with
	// in_person_rails, rail_aliases or missing_rail configured, the
	// evidence also records how the rail was counted and the handover.
	extended := c.InPersonRails != nil || c.RailAliases != nil || c.MissingRail != nil
	watched := slices.Sorted(slices.Values(c.WatchedRails))
	extend := func(members []canon.Member, counted *string) []canon.Member {
		if c.AllowedRails != nil {
			members = append(members, canon.M("allowed_rails", canon.Strs(slices.Sorted(slices.Values(*c.AllowedRails)))))
		}
		countedAs, handover := canon.Null(), canon.Null()
		if counted != nil {
			countedAs = canon.Str(*counted)
		}
		if a.Handover != nil {
			handover = canon.Str(*a.Handover)
		}
		rd = append(rd, "handover")
		return append(members, canon.M("counted_as", countedAs),
			canon.M("in_person_rails", canon.Strs(slices.Sorted(slices.Values(c.InPersonRails)))), canon.M("handover", handover))
	}
	if a.Rail == nil {
		if c.MissingRail[a.class()] == "fail" {
			members := extend([]canon.Member{canon.M("rail", canon.Null()), canon.M("watched_rails", canon.Strs(watched))}, nil)
			out = decided(id, method, Fail, canon.Obj(append(members, canon.M("missing_field", canon.Str("rail")))...))
			out.failNote = missingRailReason
			return out
		}
		return missingField(id, method, "rail")
	}
	rail := *a.Rail
	counted := rail
	if alias, ok := c.RailAliases[rail]; ok {
		counted = alias
	}
	members := []canon.Member{canon.M("rail", canon.Str(rail)), canon.M("watched_rails", canon.Strs(watched))}
	if extended {
		members = extend(members, &counted)
	} else if c.AllowedRails != nil {
		members = append(members, canon.M("allowed_rails", canon.Strs(slices.Sorted(slices.Values(*c.AllowedRails)))))
	}
	named := rail
	if counted != rail {
		named = fmt.Sprintf("%s (counted as %s)", rail, counted)
	}
	r, note := Pass, ""
	switch {
	case slices.Contains(c.InPersonRails, counted):
		if a.Handover == nil || *a.Handover != inPersonHandover {
			how := "the handover is not stated"
			if a.Handover != nil {
				how = "the handover is " + *a.Handover
			}
			r, note = Fail, fmt.Sprintf("rail %s is allowed only when the item is handed over in person; %s", named, how)
		}
	case c.AllowedRails != nil && !slices.Contains(*c.AllowedRails, counted):
		r, note = Fail, fmt.Sprintf("rail %s is not one of the allowed rails %s", named, strings.Join(slices.Sorted(slices.Values(*c.AllowedRails)), ", "))
	}
	if r == Pass && slices.Contains(watched, counted) {
		r = Fail
	}
	out = decided(id, method, r, canon.Obj(members...))
	out.read = &Read{Field: "rail", Value: rail}
	out.failNote = note
	return out
}

// missingRailReason is destination_rail.py MISSING_RAIL_REASON: why an
// action whose class missing_rail maps to fail, and which states no rail,
// fails (what the user is asked about).
const missingRailReason = "payment method not stated"

// inPersonHandover is the handover an in-person rail needs.
const inPersonHandover = "in_person"

// checkCounterpartyIdentityChange compares the account against the latest
// accepted record for the same target and action_type that recorded one
// (counterparty_identity_change.py:41-95). No such record: out of scope.
func checkCounterpartyIdentityChange(a Action, ledger []Record, c counterpartyConfig) (out Constraint) {
	const id, method = "counterparty_identity_change", "prior_recorded_value_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "target", "counterparty_account_ref", "action_type")
	if a.Target == nil {
		return missingField(id, method, "target")
	}
	if a.CounterpartyAccountRef == nil {
		return missingField(id, method, "counterparty_account_ref")
	}
	var prior *Record
	for i, r := range ledger {
		if r.ActionType != a.ActionType || r.Target == nil || *r.Target != *a.Target || !r.accepted() || r.CounterpartyAccountRef == nil {
			continue
		}
		prior = &ledger[i]
	}
	if prior == nil {
		return outOfScope(id, method)
	}
	evidence := canon.Obj(
		canon.M("target", canon.Str(*a.Target)),
		canon.M("counterparty_account_ref", canon.Str(*a.CounterpartyAccountRef)),
		canon.M("prior_capsule_id", canon.Str(prior.CapsuleID)),
		canon.M("prior_counterparty_account_ref", canon.Str(*prior.CounterpartyAccountRef)),
	)
	r := Pass
	if *a.CounterpartyAccountRef != *prior.CounterpartyAccountRef {
		r = Fail
	}
	out = decided(id, method, r, evidence)
	out.read = &Read{Field: "counterparty_account_ref", Value: *a.CounterpartyAccountRef}
	return out
}

type compiledPattern struct {
	id string
	re *regexp.Regexp
}

// checkCredentialPattern: fail when outgoing content matches any pattern; the
// evidence carries pattern ids only (credential_pattern.py:39-73). No
// outgoing content: out of scope, for every action class.
func checkCredentialPattern(a Action, patterns []compiledPattern) (out Constraint) {
	const id, method = "credential_pattern", "regex_match_v0"
	defer func() { out.reads = []string{"outgoing_content"} }()
	if a.OutgoingContent == nil {
		return outOfScope(id, method)
	}
	var matched, all []string
	for _, p := range patterns {
		all = append(all, p.id)
		if p.re.MatchString(*a.OutgoingContent) {
			matched = append(matched, p.id)
		}
	}
	slices.Sort(matched)
	slices.Sort(all)
	if matched == nil {
		matched = []string{}
	}
	evidence := canon.Obj(canon.M("matched_pattern_ids", canon.Strs(matched)), canon.M("pattern_ids", canon.Strs(all)))
	if len(matched) > 0 {
		// The pattern ids, never the content.
		c := decided(id, method, Fail, evidence)
		c.read = &Read{Field: "matched_pattern_ids", Value: strings.Join(matched, ",")}
		return c
	}
	return decided(id, method, Pass, evidence)
}

// checkRecurringCharge: fail when the declared recurrence is not a one-time
// value (recurring_charge.py:33-49).
func checkRecurringCharge(a Action, c recurringConfig) (out Constraint) {
	const id, method = "recurring_charge", "structured_field_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "recurrence")
	if a.Recurrence == nil {
		return missingField(id, method, "recurrence")
	}
	evidence := canon.Obj(canon.M("recurrence", canon.Str(*a.Recurrence)), canon.M("one_time_values", canon.Strs(slices.Sorted(slices.Values(c.OneTimeValues)))))
	r := Fail
	if slices.Contains(c.OneTimeValues, *a.Recurrence) {
		r = Pass
	}
	out = decided(id, method, r, evidence)
	out.read = &Read{Field: "recurrence", Value: *a.Recurrence}
	return out
}

// checkRefundability: fail when the payment declares it cannot be refunded,
// recording the rail beside the value (refundability.py at 365eb92).
func checkRefundability(a Action, c classConfig) (out Constraint) {
	const id, method = "refundability", "declared_boolean_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "refundable")
	if a.Refundable == nil {
		return missingField(id, method, "refundable")
	}
	if a.Rail != nil {
		rd = append(rd, "rail")
	}
	evidence := canon.Obj(canon.M("refundable", canon.Bool(*a.Refundable)), canon.M("rail", strOrNull(a.Rail)))
	r := Fail
	if *a.Refundable {
		r = Pass
	}
	out = decided(id, method, r, evidence)
	out.read = &Read{Field: "refundable", Value: strconv.FormatBool(*a.Refundable)}
	return out
}

// checkRecipientRole: pass when the disclosure recipient's role is one of
// the allowed roles; a role outside the closed set fails even if allowed
// names it (recipient_role.py at 7339028). A role in role_classes
// (recipient_role/1.1.0: platform) passes only when the disclosure names at
// least one class of data and every one is listed for it. It records the
// role, and under role_classes the class names it read, never who the
// recipient is or the data.
func checkRecipientRole(a Action, c roleConfig) (out Constraint) {
	const id, method = "recipient_role", "set_membership_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "recipient_role")
	if a.RecipientRole == nil {
		return missingField(id, method, "recipient_role")
	}
	role := *a.RecipientRole
	recognised := slices.Contains(c.Roles, role)
	allowed := slices.Sorted(slices.Values(c.AllowedRoles))
	limited := map[string][]string{}
	for r, classes := range c.RoleClasses {
		limited[r] = slices.Sorted(slices.Values(classes))
	}
	members := []canon.Member{canon.M("recipient_role", canon.Str(role)), canon.M("recognised", canon.Bool(recognised)),
		canon.M("allowed_roles", canon.Strs(allowed))}
	if c.RoleClasses != nil {
		rd = append(rd, "disclosed_classes")
		var rc []canon.Member
		for _, r := range slices.Sorted(maps.Keys(limited)) {
			rc = append(rc, canon.M(r, canon.Strs(limited[r])))
		}
		disclosed := canon.Null()
		if a.DisclosedClasses != nil {
			disclosed = canon.Strs(a.DisclosedClasses)
		}
		members = append(members, canon.M("role_classes", canon.Obj(rc...)), canon.M("disclosed_classes", disclosed))
	}
	evidence := canon.Obj(members...)
	passing := passingRoles(allowed, limited)
	result := func(r Result, note string) Constraint {
		out := decided(id, method, r, evidence)
		out.read = &Read{Field: "recipient_role", Value: role}
		if r == Fail {
			out.failNote = note
		}
		return out
	}
	if !recognised {
		return result(Fail, fmt.Sprintf("recipient_role %s is not a role this rule knows; personal data passes only to %s",
			pyRepr(role), passing))
	}
	if slices.Contains(allowed, role) {
		return result(Pass, "")
	}
	if classes, ok := limited[role]; ok {
		if a.DisclosedClasses == nil {
			return result(Fail, fmt.Sprintf("the recipient is a %s, which may be given only %s; the disclosure names no class of data",
				role, either(classes)))
		}
		var others []string
		for _, cl := range a.DisclosedClasses {
			if !slices.Contains(classes, cl) {
				others = append(others, cl)
			}
		}
		if len(others) > 0 {
			return result(Fail, fmt.Sprintf("the recipient is a %s, which may be given only %s; this disclosure also gives %s",
				role, either(classes), either(others)))
		}
		return result(Pass, "")
	}
	return result(Fail, fmt.Sprintf("the recipient is a %s; personal data passes only to %s", role, passing))
}

// either is recipient_role.py _either: "a", "a or b", "a, b or c"; "no
// role" for none.
func either(words []string) string {
	switch len(words) {
	case 0:
		return "no role"
	case 1:
		return words[0]
	}
	return strings.Join(words[:len(words)-1], ", ") + " or " + words[len(words)-1]
}

// passingRoles is recipient_role.py _passing: the roles that pass, once,
// "fulfilling_merchant or self, or a platform given only email".
func passingRoles(allowed []string, limited map[string][]string) string {
	var phrases []string
	for _, r := range slices.Sorted(maps.Keys(limited)) {
		phrases = append(phrases, fmt.Sprintf("a %s given only %s", r, either(limited[r])))
	}
	if len(allowed) == 0 {
		return either(phrases)
	}
	out := either(allowed)
	for _, p := range phrases {
		out += ", or " + p
	}
	return out
}

// pyRepr is Python's repr of a str, as the engine's reasons quote a value
// with !r: single quotes unless the text holds one and no double quote.
func pyRepr(s string) string {
	q := byte('\'')
	if strings.Contains(s, "'") && !strings.Contains(s, "\"") {
		q = '"'
	}
	var b strings.Builder
	b.WriteByte(q)
	for _, r := range s {
		switch {
		case r == '\\':
			b.WriteString("\\\\")
		case r == rune(q):
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\n':
			b.WriteString("\\n")
		case r == '\r':
			b.WriteString("\\r")
		case r == '\t':
			b.WriteString("\\t")
		case r < 0x20 || r == 0x7f:
			fmt.Fprintf(&b, "\\x%02x", r)
		case !unicode.IsPrint(r) && r > 0x7f:
			switch {
			case r <= 0xff:
				fmt.Fprintf(&b, "\\x%02x", r)
			case r <= 0xffff:
				fmt.Fprintf(&b, "\\u%04x", r)
			default:
				fmt.Fprintf(&b, "\\U%08x", r)
			}
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte(q)
	return b.String()
}

// upfrontBps is 100% in basis points (upfront_amount.py _BPS).
const upfrontBps = 10000

// checkUpfrontAmount: fail when the stated deposit is above the absolute
// limit, or above the share limit of the total in basis points, compared
// in integers as upfront * 10000 > total * bps; with no total, the
// absolute limit alone (upfront_amount.py at 365eb92). The total is the
// action's amount_minor; on a deal record it is what the engine's deal
// bridge takes as the action's amount: spend_minor, and 0 for money moving
// in.
func checkUpfrontAmount(a Action, c upfrontConfig) (out Constraint) {
	const id, method = "upfront_amount", "upfront_threshold_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	rd = append(rd, "upfront_amount_minor")
	if a.UpfrontAmountMinor == nil {
		return missingField(id, method, "upfront_amount_minor")
	}
	upfront := *a.UpfrontAmountMinor
	total, totalReads := a.engineAmount()
	rd = append(rd, totalReads...)
	overAbsolute := upfront > c.UpfrontMaxMinor
	overShare := total != nil && upfront*upfrontBps > *total*c.UpfrontMaxBps
	totalValue := canon.Null()
	if total != nil {
		totalValue = canon.Int(*total)
	}
	evidence := canon.Obj(canon.M("upfront_amount_minor", canon.Int(upfront)), canon.M("amount_minor", totalValue),
		canon.M("upfront_max_minor", canon.Int(c.UpfrontMaxMinor)), canon.M("upfront_max_bps", canon.Int(c.UpfrontMaxBps)),
		canon.M("over_absolute", canon.Bool(overAbsolute)), canon.M("over_share", canon.Bool(overShare)))
	r := Pass
	if overAbsolute || overShare {
		r = Fail
	}
	out = decided(id, method, r, evidence)
	out.read = &Read{Field: "upfront_amount_minor", Value: strconv.FormatInt(upfront, 10)}
	if r == Fail {
		var which []string
		if overAbsolute {
			which = append(which, "the absolute limit")
		}
		if overShare {
			which = append(which, "the share limit")
		}
		out.failNote = fmt.Sprintf("the up-front amount %d is above %s", upfront, strings.Join(which, " and "))
	}
	return out
}

// perActionBasis is caps.py _per_action_basis: the authorised maximum when
// one was declared and is not below the capture, the capture otherwise (an
// authorised maximum below the capture is not a maximum). On a deal record,
// as the engine's deal reader does (report/replay.py _bridge_deal_check), an
// authorised maximum on money moving in is dropped; a normalized --action is
// read as check_caps reads it, which knows no direction.
func (e *Engine) perActionBasis(a Action, capture int64) PerActionBasis {
	authorized := a.SpendAuthorizedMinor
	if a.fromRecord && a.Direction != nil && *a.Direction == "in" {
		authorized = nil
	}
	if authorized == nil || *authorized < capture {
		return PerActionBasis{Field: "spend_minor", FellBack: true, ObservedMinor: capture}
	}
	return PerActionBasis{Field: "spend_authorized_minor", FellBack: false, ObservedMinor: *authorized}
}
