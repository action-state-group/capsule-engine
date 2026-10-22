package guard

import (
	"fmt"
	"regexp"
	"slices"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// offerExpiryConfig is offer_expiry/1.0.1's config: the classes it applies
// to and the oldest a proposal may be, in seconds.
//
// NoProposalClasses (offer_expiry/1.1.0) are the classes whose action may
// answer no proposal (Action.NoProposal); nil under 1.0.1, which never reads
// no_proposal.
type offerExpiryConfig struct {
	ActionClasses     []string `yaml:"action_classes"`
	MaxAgeSeconds     int64    `yaml:"max_age_seconds"`
	NoProposalClasses []string `yaml:"no_proposal_classes"`
}

// utcTimestamp is offer_expiry.py _UTC: an RFC 3339 timestamp in UTC, "Z",
// with at most six fractional digits. Python's \d also matches non-ASCII
// digits, which datetime.fromisoformat then refuses, so both read such a
// value as unreadable; \z, unlike Python's $, admits no trailing newline,
// which fromisoformat refuses too.
var utcTimestamp = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z\z`)

// utcInstant is offer_expiry.py _utc: the instant, or false when the value
// is not utcTimestamp or names no instant datetime.fromisoformat reads (a
// day the month does not have, an hour past 23, a 60th second, year 0).
// fromisoformat reads an hour of 24 as the next midnight from Python 3.14
// on; capsule-engine's CI runs 3.12, where it is unreadable, and so is it
// here.
func utcInstant(v string) (time.Time, bool) {
	if !utcTimestamp.MatchString(v) {
		return time.Time{}, false
	}
	at, err := time.Parse(time.RFC3339Nano, v)
	if err != nil || at.Year() < 1 {
		return time.Time{}, false
	}
	return at, true
}

// checkOfferExpiry is capsule-engine guards/checks/offer_expiry.py (ba76a97):
// the proposal's age, the action's timestamp less proposal_at in whole
// seconds (floored), passes within max_age_seconds and fails past it. An age
// it cannot establish (no proposal_at or timestamp, either unreadable, or a
// proposal later than the action) fails too, marked expiry_unverified with
// the field it could not read: never a pass.
func checkOfferExpiry(a Action, c offerExpiryConfig) (out Constraint) {
	const id, method = "offer_expiry", "age_threshold_v0"
	rd := []string{"action_class"}
	defer func() { out.reads = rd }()
	if !slices.Contains(c.ActionClasses, a.class()) {
		return outOfScope(id, method)
	}
	if c.NoProposalClasses != nil && a.NoProposal != nil && *a.NoProposal {
		// offer_expiry.py at d3066f5 (capsule-engine #202): an action that
		// says it answers no proposal and also carries one contradicts
		// itself; one in no_proposal_classes with none has nothing to go
		// stale, in scope.
		rd = append(rd, "no_proposal", "proposal_at")
		if a.ProposalAt != nil {
			out := decided(id, method, Fail, canon.Obj(canon.M("expiry_unverified", canon.Bool(true)),
				canon.M("no_proposal_beside", canon.Str("proposal_at"))))
			out.failNote = "the action says it answers no proposal (no_proposal) but also carries a proposal_at, so the offer's expiry could not be established"
			return out
		}
		if slices.Contains(c.NoProposalClasses, a.class()) {
			return Constraint{ID: id, Result: NotApplicable, Method: method, population: PopulationC, digestable: true,
				evidence: notApplicableEvidence(id, true, ""), scopeNote: "the action answers no proposal (no_proposal), so there is none to go stale"}
		}
	}
	unverified := func(field, note string) Constraint {
		out := decided(id, method, Fail, canon.Obj(canon.M("expiry_unverified", canon.Bool(true)), canon.M("missing_field", canon.Str(field))))
		out.failNote = note + "; the offer's expiry could not be established"
		return out
	}
	rd = append(rd, "proposal_at")
	if a.ProposalAt == nil {
		return unverified("proposal_at", "the action carries no proposal_at")
	}
	rd = append(rd, "timestamp")
	if a.Timestamp == "" {
		return unverified("timestamp", "the action carries no timestamp")
	}
	decidedAt, ok := utcInstant(a.Timestamp)
	if !ok {
		return unverified("timestamp", "the timestamp is not an RFC 3339 UTC timestamp")
	}
	proposedAt, ok := utcInstant(*a.ProposalAt)
	if !ok {
		return unverified("proposal_at", "proposal_at is not an RFC 3339 UTC timestamp")
	}
	// timedelta // timedelta(seconds=1) floors: half a second later is -1.
	diff := decidedAt.Sub(proposedAt)
	age := int64(diff / time.Second)
	if diff%time.Second < 0 {
		age--
	}
	if age < 0 {
		return unverified("proposal_at", "proposal_at is later than the action")
	}
	expired := age > c.MaxAgeSeconds
	out = decided(id, method, Pass, canon.Obj(
		canon.M("proposal_at", canon.Str(*a.ProposalAt)),
		canon.M("decided_at", canon.Str(a.Timestamp)),
		canon.M("age_seconds", canon.Int(age)),
		canon.M("max_age_seconds", canon.Int(c.MaxAgeSeconds)),
		canon.M("expired", canon.Bool(expired)),
	))
	if expired {
		out.Result = Fail
		out.failNote = fmt.Sprintf("the proposal is %ds old, past the %ds limit; a new proposed action is required", age, c.MaxAgeSeconds)
	}
	return out
}
