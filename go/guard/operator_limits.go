package guard

import (
	"fmt"
	"maps"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// Where a caps limit's value came from (capsule-engine guards/checks/caps.py
// LIMIT_SOURCE_PROFILE / LIMIT_SOURCE_DEFAULT, #95): the operator's own
// value from a policy profile an activation binds, or the wicket's default.
const (
	LimitSourceProfile = "operator_profile"
	LimitSourceDefault = "definition_default"
)

// The caps config keys a policy profile may set (capsule-engine
// policy/profile.py CONFIGURABLE["caps"]): the window limit and the
// per-action limit, each per action class in minor units.
const (
	CapsKeyWindow    = "caps_minor"
	CapsKeyPerAction = "per_action_minor"
)

// LimitSource is caps.py's LimitSource: where one applied limit's value came
// from, the digest of the profile that set it (operator_profile only), and
// any higher value activated but still inside its cooling-off.
type LimitSource struct {
	Source        string
	ProfileDigest string
	PendingRaise  *PendingRaise
}

// PendingRaise is caps.py's PendingRaise: a higher value activated for a
// limit and not yet in force, when it takes effect, and where it comes from.
type PendingRaise struct {
	ValueMinor    int64
	EffectiveAt   string
	Source        string
	ProfileDigest string
}

// Words names a limit's source as a person reads it: the limits a person
// set (limits show, the check's text, the closing message and the rules
// page all say it so) or the pack's own.
func (s LimitSource) Words() string {
	if s.Source == LimitSourceProfile {
		return "set by you, profile " + s.ProfileDigest
	}
	return "the rules' default"
}

// LimitInForce is one limit's value at a decision, with its source.
type LimitInForce struct {
	ValueMinor int64
	Source     LimitSource
}

// OperatorLimits are the caps limits read from activated policy
// (capsule-engine policy/limits.py CapsLimits): for a caps key and a
// canonical action class, the value in force for an action stamped at, and
// where it came from. ok is false when the pack sets no such limit for the
// class. An implementation applies the cooling-off itself.
type OperatorLimits interface {
	InForce(key, class string, at time.Time) (LimitInForce, bool)
}

// WithOperatorLimits makes e read its caps limits from l on every decision
// (capsule-engine build_engine's caps_limits), and name each limit's source
// in the caps evidence and the report. Without it every limit is the
// wicket's default, as before.
func (e *Engine) WithOperatorLimits(l OperatorLimits) *Engine {
	e.operatorLimits = l
	return e
}

// defaultSource is the source of a limit that is the wicket's own.
var defaultSource = LimitSource{Source: LimitSourceDefault}

// limitFor is the limit for key and class at: the operator's in force when
// e reads activated policy, else def from the pack.
func (e *Engine) limitFor(key, class string, at time.Time, def int64) (int64, LimitSource) {
	if e.operatorLimits == nil {
		return def, defaultSource
	}
	if l, ok := e.operatorLimits.InForce(key, class, at); ok {
		return l.ValueMinor, l.Source
	}
	return def, defaultSource
}

// canon is caps.py's LimitSource as sealed in the evidence.
func (s LimitSource) canon() canon.Value {
	members := []canon.Member{canon.M("limit_source", canon.Str(s.Source))}
	if s.ProfileDigest != "" {
		members = append(members, canon.M("profile_digest", canon.Str(s.ProfileDigest)))
	}
	if p := s.PendingRaise; p != nil {
		raise := []canon.Member{canon.M("value_minor", canon.Int(p.ValueMinor)), canon.M("effective_at", canon.Str(p.EffectiveAt)),
			canon.M("limit_source", canon.Str(p.Source))}
		if p.ProfileDigest != "" {
			raise = append(raise, canon.M("profile_digest", canon.Str(p.ProfileDigest)))
		}
		members = append(members, canon.M("pending_raise", canon.Obj(raise...)))
	}
	return canon.Obj(members...)
}

// limitSourcesMember is caps.py's limit_sources: window, and per_action when
// a per-action limit is applied.
func limitSourcesMember(window LimitSource, perAction *LimitSource) canon.Member {
	sources := []canon.Member{canon.M("window", window.canon())}
	if perAction != nil {
		sources = append(sources, canon.M("per_action", perAction.canon()))
	}
	return canon.M("limit_sources", canon.Obj(sources...))
}

// CapsDefaults are the pack's own caps limits, the defaults a policy profile
// may replace: caps key -> canonical action class -> minor units. Empty
// when the pack cites no caps definition.
func (e *Engine) CapsDefaults() map[string]map[string]int64 {
	return map[string]map[string]int64{CapsKeyWindow: maps.Clone(e.capsMinor), CapsKeyPerAction: maps.Clone(e.perActionMinor)}
}

// profileLimits is the caps limits of one policy profile, keyed by canonical
// class: each is the person's, with the profile's digest as its source.
type profileLimits struct {
	caps   map[string]map[string]int64
	source LimitSource
}

func (l profileLimits) InForce(key, class string, _ time.Time) (LimitInForce, bool) {
	v, ok := l.caps[key][class]
	return LimitInForce{ValueMinor: v, Source: l.source}, ok
}

// WithPersonProfile makes e decide as a host does that applies the policy
// profile p itself: p's caps limits for e's pack become person-set limits
// (source LimitSourceProfile, p's digest), and its rate_cap and counterparty
// list go through WithProfile. A class is read by its canonical name
// (caps.py resolve_caps_minor). It refuses, as resolve.py _check_profile does,
// a caps key or class the pack declares no default for (a profile replaces a
// default, it never adds a limit). An entry for another pack is ignored. The values are limits
// already in force; the caller applies any cooling-off. On a refusal e is
// left as it was.
func (e *Engine) WithPersonProfile(p Profile) (*Engine, error) {
	mine, ok := p.Packs[packName(e.pack.ID)]
	if !ok {
		return e, nil
	}
	defaults := e.CapsDefaults()
	caps := map[string]map[string]int64{}
	for key, byClass := range mine.Caps {
		declared, ok := defaults[key]
		if !ok {
			return nil, fmt.Errorf("profile sets caps.%s, which no pack limit takes", key)
		}
		resolved, err := e.tax.resolveLimits(byClass)
		if err != nil {
			return nil, err
		}
		for class := range resolved {
			if _, ok := declared[class]; !ok {
				return nil, fmt.Errorf("profile sets caps.%s for class %q, which the pack declares no default for", key, class)
			}
		}
		caps[key] = resolved
	}
	rest := ProfilePack{RateCap: mine.RateCap, List: mine.List}
	if rest.RateCap != nil || rest.List != nil {
		if _, err := e.WithProfile(Profile{Digest: p.Digest, Packs: map[string]ProfilePack{packName(e.pack.ID): rest}}); err != nil {
			return nil, err
		}
	}
	if len(caps) > 0 {
		e.operatorLimits = profileLimits{caps: caps, source: LimitSource{Source: LimitSourceProfile, ProfileDigest: p.Digest}}
	}
	return e, nil
}
