package guard

import (
	"fmt"
	"slices"
	"strings"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// Verdicts in the machine output. A rule whose scope excludes the action is
// VerdictNotApplicable; a rule that applies and could not run is
// VerdictNotEvaluable. Each carries its reason, and neither is a pass.
const (
	VerdictPass          = "pass"
	VerdictFail          = "fail"
	VerdictNotApplicable = "not_applicable"
	VerdictNotEvaluable  = "not_evaluable"
)

// CheckReport is a check's per-rule report: the pack, the decision, and
// every rule of the pack with its verdict, then the engine reference check.
// Field order is the struct order; there is no timestamp (the caller seals
// time). Schema is the caller's id for the output it writes the report as:
// Report leaves it empty, and the caller sets it.
type CheckReport struct {
	Schema   string       `json:"schema"`
	Pack     ReportPack   `json:"pack"`
	Decision string       `json:"decision"`
	Rules    []RuleResult `json:"rules"`
	Inputs   Inputs       `json:"inputs"`
	// Refused is set when the check is refused though no rule failed: a
	// would-allow held only by a record sealed at another action taxonomy,
	// which the engine seals reject. Decision is then the engine's verdict
	// label, not_evaluable; what a caller shows the user is a refusal. It is
	// not part of the written report.
	Refused bool `json:"-"`
	// RefusedFor is why the check is refused when Refused is set, one of the
	// Refused* causes, so a caller can name the reason; "" otherwise. Like
	// Refused, it is not part of the written report.
	RefusedFor string `json:"-"`
}

// The causes of a refusal with no rule failed (CheckReport.RefusedFor).
const (
	// RefusedHeldCaps: caps could not be evaluated over the history, and
	// the class has no approver to ask (capsule-engine #216).
	RefusedHeldCaps = "held_caps_no_approver"
	// RefusedNotEvaluated: a check that fails closed could not be evaluated
	// (capsule-engine #137).
	RefusedNotEvaluated = "not_evaluated"
	// RefusedTaxonomy: the record was sealed at another action taxonomy
	// than the rules'.
	RefusedTaxonomy = "taxonomy"
	// RefusedDealConflict: the record is in no single deal (capsule-engine
	// #127), and caps, held, asked.
	RefusedDealConflict = "deal_id_conflict"
)

// Inputs are the action body's fields: those a rule consulted for this
// action, and those it carried that no rule read. Both sorted; never null.
// spend_minor is read only to test that it agrees with amount_minor
// (value.spend_minor_agrees): no limit is compared with it.
type Inputs struct {
	Read    []string `json:"read"`
	Ignored []string `json:"ignored"`
}

// ReportPack names the pack the rules are from.
type ReportPack struct {
	ID               string `json:"id"`
	DefinitionDigest string `json:"definition_digest"`
}

// RuleResult is one rule's verdict. Reason is set on every verdict but a
// pass. Limit, Value and Window are set where the check has them.
type RuleResult struct {
	Rule    string      `json:"rule"`
	Check   string      `json:"check"`
	Verdict string      `json:"verdict"`
	Reason  string      `json:"reason,omitempty"`
	Limit   *RuleLimit  `json:"limit,omitempty"`
	Value   *RuleValue  `json:"value,omitempty"`
	Window  *RuleWindow `json:"window,omitempty"`
	Parts   []RulePart  `json:"parts,omitempty"`
}

// RulePart is one of caps' limits with its own verdict: "per_action" (the
// per-purchase limit) or "window". LimitSource names where ThresholdMinor
// came from: operator_profile (a limit the person set, in the policy
// profile ProfileDigest names) or definition_default (the pack's own), as
// the engine's caps evidence does (limit_sources). PendingRaise is a higher
// limit the person set that is still inside its cooling-off.
type RulePart struct {
	Limit          string            `json:"limit"`
	Verdict        string            `json:"verdict"`
	Reason         string            `json:"reason,omitempty"`
	ThresholdMinor int64             `json:"threshold_minor"`
	ObservedMinor  int64             `json:"observed_minor"`
	LimitSource    string            `json:"limit_source"`
	ProfileDigest  string            `json:"profile_digest,omitempty"`
	PendingRaise   *RulePendingRaise `json:"pending_raise,omitempty"`
}

// RulePendingRaise is caps.py's pending_raise: the higher value, when it
// takes effect, and where it comes from.
type RulePendingRaise struct {
	ValueMinor    int64  `json:"value_minor"`
	EffectiveAt   string `json:"effective_at"`
	LimitSource   string `json:"limit_source"`
	ProfileDigest string `json:"profile_digest,omitempty"`
}

// RuleLimit is the bound a check compared against: for caps, the
// per-action and window limits, in minor units of the action's currency
// (caps compares integers and converts no currency).
type RuleLimit struct {
	PerActionMinor *int64  `json:"per_action_minor,omitempty"`
	WindowMinor    *int64  `json:"window_minor,omitempty"`
	Currency       *string `json:"currency,omitempty"`
}

// RuleValue is what a check read: the field, and its value. For caps,
// Minor is the amount, with the window total before it and the projected
// total.
type RuleValue struct {
	Field            string `json:"field"`
	Minor            *int64 `json:"minor,omitempty"`
	WeeklySpendMinor *int64 `json:"weekly_spend_minor,omitempty"`
	ProjectedMinor   *int64 `json:"projected_minor,omitempty"`
	SpendMinorAgrees *bool  `json:"spend_minor_agrees,omitempty"`
	// PerActionBasis is what a caps/5.0.0 per-action limit read.
	PerActionBasis *RuleBasis `json:"per_action_basis,omitempty"`
	Currency       *string    `json:"currency,omitempty"`
	Text           *string    `json:"text,omitempty"`
}

// RuleBasis is caps/5.0.0's per_action_basis: the field the per-action limit
// read (spend_authorized_minor, or spend_minor for the capture), whether it
// fell back to the capture, and the value it compared.
type RuleBasis struct {
	Field         string `json:"field"`
	FellBack      bool   `json:"fell_back"`
	ObservedMinor int64  `json:"observed_minor"`
}

// RuleWindow is the fold a check's total was read from.
type RuleWindow struct {
	Fold     string `json:"fold"`
	Key      string `json:"key"`
	KeyValue string `json:"key_value"`
	Duration string `json:"duration,omitempty"`
}

// referenceRule names the engine's own reference check when no pack rule
// cites it (the text output's label). asg/seller/0.1.0 cites it for rule s12,
// whose row it then is.
const referenceRule = "(engine reference check)"

// citesReference is whether a rule of the engine's pack is measured by the
// engine's reference check, verify_before_dispatch: its result is then that
// rule's, and there is no separate reference row.
func (e *Engine) citesReference() bool {
	return slices.ContainsFunc(e.pack.Rules, func(r pack.Rule) bool { return r.Check == "verify_before_dispatch" })
}

// Report is the machine output for decision d of action a: every rule of
// the pack in pack order (a declared_not_measured rule, which no check
// measures, is not_evaluable with that reason), then the engine reference
// check. The decision is the engine's (capsule-engine 365eb92
// guards/engine.py _decide: allow unless a constraint fails; an n/a, in scope
// or not, never changes it). So a rule whose check the engine reads as n/a
// for a field the action does not state is not_applicable, naming the field,
// and does not change the decision. Only where the engine itself would not
// decide this way is an allow not_evaluable: a rule left unevaluated for a
// reason that is not a missing field (a spend window the history supplied
// does not cover, a taxonomy version that is not the engine's, no or an
// unknown action class, a check not yet run). A rule the pack declares and no check measures is not_evaluable
// but does not change the decision.
func (e *Engine) Report(a Action, d Decision) CheckReport {
	r := CheckReport{Pack: ReportPack{ID: e.pack.ID, DefinitionDigest: e.pack.DefinitionDigest}, Decision: d.Outcome}
	blocked := false
	for _, rule := range e.pack.Rules {
		if rule.Check == "" {
			r.Rules = append(r.Rules, RuleResult{Rule: rule.ID, Check: "", Verdict: VerdictNotEvaluable,
				Reason: DeclaredNotMeasured})
			continue
		}
		c, ok := d.Constraint(rule.Check)
		if !ok {
			r.Rules = append(r.Rules, RuleResult{Rule: rule.ID, Check: rule.Check, Verdict: VerdictNotEvaluable,
				Reason: "the engine produced no result for this check"})
			blocked = true
			continue
		}
		res, blocks := e.ruleResult(rule.ID, a, c)
		if rule.Selector != "" {
			res = selectorResult(rule.Selector, res, c)
		}
		blocked = blocked || blocks
		r.Rules = append(r.Rules, res)
	}
	if c, ok := d.Constraint("verify_before_dispatch"); ok && !e.citesReference() {
		res, blocks := e.ruleResult(referenceRule, a, c)
		blocked = blocked || blocks
		r.Rules = append(r.Rules, res)
	}
	r.Inputs = inputs(a, d)
	if r.Decision == Allow && blocked {
		r.Decision = VerdictNotEvaluable
	}
	if len(d.askedUnevaluated) > 0 && !d.anyFailed() && (d.Outcome != Escalate || a.dealIDConflict) {
		// GuardDecision.verdict (capsule-engine #217): held only because
		// caps could not be evaluated, with nothing failed. A class with an
		// approver asks, and the escalation is the label; a class with no
		// approver is refused (#216), labelled not_evaluable, which Refused
		// carries. A record in no deal is refused by the engine whatever
		// caps asks (#127), so its label stays not_evaluable.
		r.Decision, r.Refused = VerdictNotEvaluable, d.Outcome == Deny
		if r.Refused {
			r.RefusedFor = RefusedHeldCaps
			if a.dealIDConflict {
				r.RefusedFor = RefusedDealConflict
			}
		}
	}
	if len(d.notEvaluated) > 0 && !d.anyFailed() {
		// GuardDecision.verdict (#137): refused only because a check that
		// fails closed could not be evaluated; the engine seals a reject,
		// which Refused carries to the plugin.
		r.Decision, r.Refused, r.RefusedFor = VerdictNotEvaluable, true, RefusedNotEvaluated
	}
	if d.refusedForTaxonomy {
		// The engine's verdict label for a would-allow refused only by the
		// record's taxonomy (DecisionResult.verdict); its sealed decision is
		// a reject, which Refused carries to the plugin.
		r.Decision, r.Refused, r.RefusedFor = VerdictNotEvaluable, true, RefusedTaxonomy
	}
	if a.dealIDConflict {
		// capsule-engine #127: a would-allow on a record in no deal is
		// refused (sealed deny, verdict not_evaluable); the plugin seals
		// nothing, so it is not_evaluable. A deny or escalate stands.
		r.Rules = append(r.Rules, dealIDConflictResult())
		if r.Decision == Allow {
			r.Decision = VerdictNotEvaluable
		}
	}
	return r
}

// selectorResult is capsule-engine packs/obligation_results.py at a5e9e30
// for a rule measured by one selector of its check: when the check matched
// selectors, the rule takes that selector's on_match if it matched and is
// not applicable if it did not. A check that matched none (n/a), could not
// be evaluated, or failed closed on a class it cannot resolve leaves the
// rule its whole result: a class that fails closed fails every selector's
// rule, since none of them can be ruled out.
func selectorResult(selector string, res RuleResult, c Constraint) RuleResult {
	g := c.gate
	if g == nil || (c.Result != Pass && c.Result != Fail) {
		return res
	}
	if !slices.Contains(g.matched, selector) {
		res.Verdict = VerdictNotApplicable
		res.Reason = fmt.Sprintf("action class %s did not match selector %s", g.class, selector)
		return res
	}
	if g.onMatch[selector] == "fail" {
		res.Verdict = VerdictFail
		res.Reason = fmt.Sprintf("action class %s matched selector %s, which the pack asks about", g.class, selector)
		return res
	}
	res.Verdict, res.Reason = VerdictPass, ""
	return res
}

// checkName is the wicket the check is configured by, as id/version, or
// the check's own name when no wicket configures it.
func (e *Engine) checkName(check string) string {
	if w, ok := e.pack.Wicket(check); ok {
		return w.ID
	}
	return check
}

// ruleResult is one constraint as a rule result, and whether it is an
// applicable rule left unevaluated.
func (e *Engine) ruleResult(rule string, a Action, c Constraint) (RuleResult, bool) {
	res := RuleResult{Rule: rule, Check: e.checkName(c.ID)}
	if f, ok := c.Caps(); ok {
		res.Limit = &RuleLimit{PerActionMinor: f.PerActionMinor, WindowMinor: &f.WindowMinor, Currency: a.Currency}
		res.Value = &RuleValue{Field: "amount_minor", Minor: &f.AmountMinor, WeeklySpendMinor: &f.WeeklySpendMinor,
			ProjectedMinor: &f.ProjectedMinor, Currency: a.Currency, SpendMinorAgrees: f.SpendMinorAgrees}
		if b := f.Basis; b != nil {
			res.Value.PerActionBasis = &RuleBasis{Field: b.Field, FellBack: b.FellBack, ObservedMinor: b.ObservedMinor}
		}
		res.Window = &RuleWindow{Fold: f.Fold, Key: f.FoldKey, KeyValue: f.KeyValue, Duration: f.WindowDuration}
		for _, p := range f.Parts {
			part := RulePart{Limit: p.Limit, ThresholdMinor: p.ThresholdMinor, ObservedMinor: p.ObservedMinor,
				LimitSource: p.Source.Source, ProfileDigest: p.Source.ProfileDigest}
			if r := p.Source.PendingRaise; r != nil {
				part.PendingRaise = &RulePendingRaise{ValueMinor: r.ValueMinor, EffectiveAt: r.EffectiveAt, LimitSource: r.Source,
					ProfileDigest: r.ProfileDigest}
			}
			switch p.Result {
			case Pass:
				part.Verdict = VerdictPass
			case Fail:
				part.Verdict, part.Reason = VerdictFail, overReason(p, f.WindowDuration, a.Currency)
			default:
				part.Verdict, part.Reason = VerdictNotEvaluable, p.Pending
			}
			res.Parts = append(res.Parts, part)
		}
	} else if rd, ok := c.ReadValue(); ok {
		v := rd.Value
		res.Value = &RuleValue{Field: rd.Field, Text: &v}
	}
	switch c.Result {
	case Pass:
		res.Verdict = VerdictPass
		return res, false
	case Fail:
		res.Verdict = VerdictFail
		res.Reason = failReason(c, a.Currency)
		// A purchase naming no payee (counterparty_seen_before/4.0.0) was
		// not looked for, so its reason is the check's own, never how the
		// merchant was looked for.
		if ruleFailLimits[rule] && c.ID == "counterparty_seen_before" && c.failNote == "" {
			res.Reason = merchantLimit(a)
		}
		return res, false
	}
	switch {
	case c.unevaluated:
		res.Verdict, res.Reason = VerdictNotEvaluable, c.Pending()
		return res, true
	case c.MissingField() != "":
		// The engine's n/a, in scope, missing_field (capsule.py
		// not_applicable_evidence): it decides without the rule.
		res.Verdict, res.Reason = VerdictNotApplicable, statesNo(a, c)
		return res, false
	case c.Pending() != "":
		res.Verdict, res.Reason = VerdictNotEvaluable, c.Pending()
		return res, true
	}
	if o, ok := c.OutsideScope(); ok {
		if o.Class == "" {
			res.Verdict = VerdictNotEvaluable
			res.Reason = "the action declares no action_class, so no limit can be picked for it"
			return res, true
		}
		if _, known := e.tax.resolve(o.Class); !known {
			res.Verdict = VerdictNotEvaluable
			res.Reason = fmt.Sprintf("action_class %s is not in the action-class table, so no limit can be picked for it", o.Class)
			return res, true
		}
		res.Verdict = VerdictNotApplicable
		res.Reason = "covers " + strings.Join(o.Covers, ", ") + " only"
		return res, false
	}
	res.Verdict = VerdictNotApplicable
	res.Reason = e.outOfScopeReason(a, c)
	return res, false
}

// statesNo is the reason for a rule the engine reads as n/a because the
// action does not state a field it needs: "the deal states no <field>" for a
// deal record ("the action" for a normalized --action), with the check's own
// detail after it when it has one (a task-authority record not supplied, or
// not the one the ref names).
func statesNo(a Action, c Constraint) string {
	subject := "the action"
	if a.fromRecord {
		subject = "the deal"
	}
	reason := subject + " states no " + c.MissingField()
	if c.Pending() != "" {
		reason += ": " + c.Pending()
	}
	return reason
}

// outOfScopeReason says why a check did not apply, from its own config.
// It never names the action's class: an action with none is said to have
// none once, on caps.
func (e *Engine) outOfScopeReason(a Action, c Constraint) string {
	if c.scopeNote != "" {
		return c.scopeNote
	}
	check := c.ID
	// An unported check's scope is its pinned definition's, not the config
	// of the check of that name this port runs for another definition.
	if u, ok := e.unported[check]; ok {
		return "covers " + strings.Join(u.actionClasses, ", ") + " only"
	}
	classes := map[string][]string{"destination_rail": e.rail.ActionClasses, "counterparty_identity_change": e.counterparty.ActionClasses,
		"recurring_charge": e.recurring.ActionClasses, "counterparty_seen_before": e.seenBefore.actionClasses,
		"channel_change": e.channel.ActionClasses, "material_fields_changed": e.material.ActionClasses,
		"offer_fields_changed":      e.offer.ActionClasses,
		"recipient_seen_before":     e.recipientSeenBefore.actionClasses,
		"refundability":             e.refund.ActionClasses,
		"upfront_amount":            e.upfront.ActionClasses,
		"recipient_role":            e.role.ActionClasses,
		"task_authority":            e.authority.ActionClasses,
		"offer_expiry":              e.offerExpiry.ActionClasses,
		"price_floor":               e.priceFloor.ActionClasses,
		"single_commitment":         e.singleCommitment.CommitClasses,
		"release_on_acceptance":     e.release.ActionClasses,
		"promise_never":             e.never.ActionClasses,
		"promise_requires_approval": e.approval.ActionClasses,
		"required_disclosure":       e.disclosure.actionClasses}
	if cs, ok := classes[check]; ok && !slices.Contains(cs, a.class()) {
		return "covers " + strings.Join(slices.Sorted(slices.Values(cs)), ", ") + " only"
	}
	switch check {
	case "dedupe":
		if a.statesNoAct {
			return "the record states no act"
		}
	case "counterparty_identity_change":
		return "no earlier accepted action to this target recorded an account to compare with"
	case "credential_pattern":
		return "the action carries no outgoing content"
	case "verify_before_dispatch":
		return "the action cites no mandate"
	case "action_class_gate":
		return "no selector names this action's class"
	}
	return "not in this rule's scope for this action"
}

// overReason is a failed limit, in the action's currency's minor units.
func overReason(p CapsPart, window string, currency *string) string {
	units := "minor units"
	if currency != nil {
		units = *currency + " minor units"
	}
	// A limit the person set says so; the
	// pack's own keeps the words it had.
	whose := ""
	if p.Source.Source == LimitSourceProfile {
		whose = " you set"
	}
	if p.Limit == "per_action" && p.Authorised {
		return fmt.Sprintf("authorised maximum %d (spend_authorized_minor) is over the per-purchase limit %d%s (%s)", p.ObservedMinor, p.ThresholdMinor, whose, units)
	}
	if p.Limit == "per_action" {
		return fmt.Sprintf("amount %d is over the per-purchase limit %d%s (%s)", p.ObservedMinor, p.ThresholdMinor, whose, units)
	}
	return fmt.Sprintf("%s total with this amount, %d, is over the %s limit %d%s (%s)", window, p.ObservedMinor, window, p.ThresholdMinor, whose, units)
}

// ruleFailLimits are the rules whose failure reason states a limit of how
// they are measured (the wording ruled for these reasons): r02 and r06 recognise a
// merchant by the payee fingerprint the check read (merchantLimit).
var ruleFailLimits = map[string]bool{
	"r02-ordinary-purchase": true,
	"r06-new-merchant":      true,
}

// The two ways r02 and r06 look for a merchant. A per-deal fingerprint
// (capsule-cli's x-deal-v0.counterparty block) is keyed by deal, so a
// merchant paid in an earlier deal reads as new. A profile-key fingerprint
// (counterparty_profile, AMENDMENT 3) is keyed by the profile, so one paid
// in any earlier deal of this profile is recognised.
const (
	perDealMerchant = "no earlier payment to this merchant in this deal (a merchant paid in an earlier deal is not recognised)"
	profileMerchant = "no earlier payment to this merchant from this profile"
)

// merchantLimit is r02's and r06's failure reason, from the target the check
// read. It names how the merchant was looked for, never the fingerprint.
func merchantLimit(a Action) string {
	if a.Target != nil && strings.HasPrefix(*a.Target, ProfileTargetPrefix) {
		return profileMerchant
	}
	return perDealMerchant
}

// failReason names what failed, from the facts the check recorded.
func failReason(c Constraint, currency *string) string {
	if f, ok := c.Caps(); ok {
		var parts []string
		for _, p := range f.Parts {
			if p.Result == Fail {
				parts = append(parts, overReason(p, f.WindowDuration, currency))
			}
		}
		return strings.Join(parts, "; ")
	}
	if c.failNote != "" {
		return c.failNote
	}
	switch c.ID {
	case "dedupe":
		return "repeats an action already recorded"
	case "destination_rail":
		return "the payment goes over a watched rail"
	case "counterparty_identity_change":
		return "the payee's account differs from the one last recorded for it"
	case "credential_pattern":
		return "outgoing content matches a credential pattern"
	case "recurring_charge":
		return "the payment sets up a repeating charge"
	case "refundability":
		return "the payment is not refundable"
	case "counterparty_seen_before":
		return "a first-time counterparty for this operator"
	case "recipient_seen_before":
		return "a first-time recipient for this operator"
	case "action_class_gate":
		return "the action's class is one the pack asks about"
	case "channel_change":
		return "the counterparty moved off the channel the relationship started on"
	case "material_fields_changed":
		return "material terms changed from what was agreed"
	case "offer_fields_changed":
		return "the offer differs from what the user stated"
	}
	return c.ID + " failed"
}

// inputs splits the fields the action carried into those some check
// consulted for this action and those none did. A field a check consulted
// that the action did not carry is not listed: a reason names it where it
// mattered.
func inputs(a Action, d Decision) Inputs {
	consulted := map[string]bool{}
	// party_role is read to pick the pack, by no check.
	if a.PartyRole != nil {
		f := "party_role"
		if name, ok := a.sourceName[f]; ok {
			f = name
		}
		consulted[f] = true
	}
	for _, c := range d.Constraints {
		for _, f := range c.reads {
			if name, ok := a.sourceName[f]; ok {
				f = name
			}
			consulted[f] = true
		}
	}
	in := Inputs{Read: []string{}, Ignored: []string{}}
	for _, f := range a.present {
		if consulted[f] {
			in.Read = append(in.Read, f)
		} else {
			in.Ignored = append(in.Ignored, f)
		}
	}
	return in
}

// ReportUnevaluable is the machine output for a record that cannot be
// evaluated at all (Action.Unevaluable): every rule not_evaluable with the
// reasons, and nothing read.
func (e *Engine) ReportUnevaluable(a Action) CheckReport {
	reason := strings.Join(a.unevaluable, "; ")
	r := CheckReport{Pack: ReportPack{ID: e.pack.ID, DefinitionDigest: e.pack.DefinitionDigest}, Decision: VerdictNotEvaluable,
		Inputs: Inputs{Read: []string{}, Ignored: slices.Clone(a.present)}}
	if r.Inputs.Ignored == nil {
		r.Inputs.Ignored = []string{}
	}
	for _, rule := range e.pack.Rules {
		check := ""
		if rule.Check != "" {
			check = e.checkName(rule.Check)
		}
		r.Rules = append(r.Rules, RuleResult{Rule: rule.ID, Check: check, Verdict: VerdictNotEvaluable, Reason: reason})
	}
	if !e.citesReference() {
		r.Rules = append(r.Rules, RuleResult{Rule: referenceRule, Check: "verify_before_dispatch", Verdict: VerdictNotEvaluable, Reason: reason})
	}
	if a.dealIDConflict {
		r.Rules = append(r.Rules, dealIDConflictResult())
	}
	return r
}

// dealIDConflictResult is the marker row of a record in no deal.
func dealIDConflictResult() RuleResult {
	return RuleResult{Rule: DealIDConflictRow, Verdict: VerdictNotEvaluable, Reason: DealIDConflict}
}
