package guard

import "github.com/action-state-group/capsule-engine/go/canon"

// Result is a constraint's result.
type Result string

const (
	Pass          Result = "pass"
	Fail          Result = "fail"
	NotApplicable Result = "n/a"
)

// Population classifies an n/a (decision_table.yaml header): "a" the rule
// applied and a named field was missing (a correct decline), "c" the rule did
// not apply (never scored). Population "b", an n/a with no evidence, has no
// constructor here: every n/a this package builds carries its facts.
type Population string

const (
	PopulationA Population = "a"
	PopulationC Population = "c"
)

// Constraint is one check's result.
type Constraint struct {
	ID     string
	Result Result
	Method string
	// evidence is the object the engine digests into evidence_digest
	// (capsule.py:164). digestable is false where this port does not rebuild
	// the engine's evidence byte for byte. Since caps/3.0.0 (whose evidence
	// names the fold by its digest) every evidence this port builds is
	// rebuilt exactly.
	evidence   canon.Value
	digestable bool
	population Population
	missing    string
	// outside is set on a population-c n/a whose action class is outside the
	// classes the check's config covers (covers).
	outside *ClassScope
	// pending is set on a population-a n/a the port cannot evaluate for a
	// reason other than a missing field: the reason.
	pending string
	// failsClosed marks an in-scope n/a that must not be allowed past
	// (base.py CheckOutcome.fails_closed; single_commitment when the sale
	// cannot be identified or its acceptance is not known): the action is
	// refused whatever else allows or asks (Decision.notEvaluated).
	failsClosed bool
	// unevaluated marks a missing-field n/a the report reads as not
	// evaluated rather than not applicable: the rule applies and its input
	// did not arrive (price_floor with a floor in force and no opening).
	unevaluated bool
	// matched is the capsule a dedupe failure matched.
	matched string
	// asksApprover marks a failure the check found an approver may resolve
	// (base.py CheckOutcome.asks_approver; dedupe: the same act in another
	// deal), so decide treats it as escalatable.
	asksApprover bool
	// read is the value the check read, when it is safe to show (never
	// outgoing content); caps carries its limits and totals in capsFacts.
	read *Read
	caps *CapsFacts
	// recorded is what recipient_role recorded in its evidence about who got
	// which classes, when the check is configured with role_classes; the
	// report words an r11/r12 reason from it (shareReason).
	recorded *recordedShare
	// scopeNote is an out-of-scope n/a's own reason, when the check gives
	// one other than its class scope (action_class_gate/1.1.0 leaving out a
	// recipient role, release_on_acceptance/1.1.0 a platform).
	scopeNote string
	// failNote is a failed check's own reason, when it composes one from
	// what it found (upfront_amount names the limits it is above).
	failNote string
	// reads are the action fields the check consulted for this action, in
	// the order it consulted them (an early return lists only those it got
	// to).
	reads []string
	// gate is set on an action_class_gate result whose action class
	// resolved: the selectors that matched it and what each selector's match
	// produces, so each rule can take only its own selector's part.
	gate *gateMatch
}

// gateMatch is the selectors an action_class_gate result matched, and every
// selector's on_match.
type gateMatch struct {
	class   string
	matched []string
	onMatch map[string]string
}

// Read is the field a check read and the value it found.
type Read struct {
	Field, Value string
}

// CapsFacts are the bounds caps compared against and what it read: the
// per-action and window limits for the action's class (minor units of the
// action's currency; caps compares integers and converts nothing), the
// amount, the window total before it, the projected total, the fold and
// the key it was read under, and the limits that tripped.
type CapsFacts struct {
	PerActionMinor          *int64
	WindowMinor             int64
	AmountMinor             int64
	WeeklySpendMinor        int64
	ProjectedMinor          int64
	Fold, FoldKey, KeyValue string
	WindowDuration          string
	Tripped                 []string
	// SpendMinorAgrees is set when the action carries spend_minor: whether
	// it equals amount_minor.
	SpendMinorAgrees *bool
	// Parts are the per-action limit (when the class has one) and the
	// window limit, each with its own verdict.
	Parts []CapsPart
	// Basis is what the per-action limit read under per_action_reads
	// (caps.py PerActionBasis), or nil.
	Basis *PerActionBasis
	// ExcludedFromWindow names the earlier acts the window left out without
	// leaving it unevaluated, or is nil: money-in acts carrying no recorded
	// decision (windowCoverage). It is this port's own, never in the
	// evidence the engine digests.
	ExcludedFromWindow *ExcludedFromWindow
	// UncheckedInWindow is the unchecked acts (Record.Unchecked) the window
	// counted as spend, or nil when there were none: WeeklySpendMinor
	// includes their Minor. It is this port's own: the engine's replay
	// counts accepted decisions only.
	UncheckedInWindow *ActsInWindow
	// ExecutedInWindow is the executed acts with no recorded decision the
	// window counted at their sealed spend (the G3 ruling), or nil when
	// there were none: WeeklySpendMinor includes their Minor. It is this
	// port's own until the engine's replay counts them
	// (the replay counts executed acts as spend).
	ExecutedInWindow *ActsInWindow
}

// ActsInWindow is how many acts of one kind the window counted beside the
// fold, and their total.
type ActsInWindow struct {
	Count int
	Minor int64
}

// ExcludedFromWindow is the kind of act the window did not count, by its
// direction, and how many there were.
type ExcludedFromWindow struct {
	Direction string
	Count     int
}

// PerActionBasis is caps.py's PerActionBasis: the field the per-action limit
// read (spend_authorized_minor, or spend_minor for the capture), whether it
// fell back to the capture, and the value compared.
type PerActionBasis struct {
	Field         string
	FellBack      bool
	ObservedMinor int64
}

// CapsPart is one of caps' limits: "per_action" or "window", its verdict
// (Pass, Fail, or NotApplicable with Pending set when it could not be
// evaluated), the threshold and the value compared with it.
type CapsPart struct {
	Limit string
	// Authorised is set on a per_action part that read the authorised
	// maximum (caps/5.0.0) rather than the capture.
	Authorised     bool
	Result         Result
	Pending        string
	ThresholdMinor int64
	ObservedMinor  int64
	// Source is where ThresholdMinor came from: the operator's policy
	// profile or the wicket's default (caps.py limit_sources).
	Source LimitSource
}

// notEvaluable is an n/a where the rule applied and the port cannot evaluate
// it, for the reason given (population a, no missing field).
func notEvaluable(id, method, reason string) Constraint {
	return Constraint{ID: id, Result: NotApplicable, Method: method, population: PopulationA, pending: reason, digestable: true,
		evidence: notApplicableEvidence(id, true, "")}
}

// Pending is the reason a population-a n/a could not be evaluated, when it
// is not a missing field.
func (c Constraint) Pending() string { return c.pending }

// ReadValue is the value the check read, when it is safe to show.
func (c Constraint) ReadValue() (Read, bool) {
	if c.read == nil {
		return Read{}, false
	}
	return *c.read, true
}

// Caps is caps' facts, for a caps pass or fail.
func (c Constraint) Caps() (CapsFacts, bool) {
	if c.caps == nil {
		return CapsFacts{}, false
	}
	return *c.caps, true
}

// ClassScope is an action class a check's config does not cover, and the
// classes it does.
type ClassScope struct {
	Class  string
	Covers []string
}

// outOfScope is an n/a where the rule did not apply (population c).
func outOfScope(id, method string) Constraint {
	return Constraint{ID: id, Result: NotApplicable, Method: method, population: PopulationC, digestable: true,
		evidence: notApplicableEvidence(id, false, "")}
}

// outsideScope is an out-of-scope n/a because the action's class is not one
// the check's config covers. The evidence is outOfScope's: only the reason
// printed beside it is new.
func outsideScope(id, method, class string, covers []string) Constraint {
	c := outOfScope(id, method)
	c.outside = &ClassScope{Class: class, Covers: covers}
	return c
}

// missingField is an n/a where the rule applied and field was absent
// (population a).
func missingField(id, method, field string) Constraint {
	return Constraint{ID: id, Result: NotApplicable, Method: method, population: PopulationA, missing: field, digestable: true,
		evidence: notApplicableEvidence(id, true, field)}
}

func decided(id, method string, r Result, evidence canon.Value) Constraint {
	return Constraint{ID: id, Result: r, Method: method, evidence: evidence, digestable: true}
}

// notApplicableEvidence is capsule.py:146-160 not_applicable_evidence.
func notApplicableEvidence(id string, inScope bool, field string) canon.Value {
	mf := canon.Null()
	if field != "" {
		mf = canon.Str(field)
	}
	return canon.Obj(canon.M("constraint_id", canon.Str(id)), canon.M("in_scope", canon.Bool(inScope)), canon.M("missing_field", mf))
}

// Population is the n/a population, or "" for a pass or fail.
func (c Constraint) Population() Population { return c.population }

// OutsideScope is the class and the configured classes of an n/a caused by
// the action class being outside the check's configured scope.
func (c Constraint) OutsideScope() (ClassScope, bool) {
	if c.outside == nil {
		return ClassScope{}, false
	}
	return *c.outside, true
}

// MissingField names the absent field of a population-a n/a.
func (c Constraint) MissingField() string { return c.missing }

// EvidenceDigest is the evidence_digest the engine seals for this result, and
// false where this port does not rebuild that evidence.
func (c Constraint) EvidenceDigest() (string, bool, error) {
	if !c.digestable {
		return "", false, nil
	}
	d, err := canon.Digest(c.evidence)
	return d, err == nil, err
}

// MatchedCapsuleID is the earlier record a dedupe failure matched.
func (c Constraint) MatchedCapsuleID() (string, bool) {
	return c.matched, c.matched != ""
}
