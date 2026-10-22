package guard

import (
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// counterparty_seen_before/1.0.0, /2.0.0, /3.0.0 and /4.0.0 (capsule-engine
// guards/checks/counterparty_seen_before.py and the wicket definitions,
// c7fc3ac; 3.0.0 at 97a2aff). Every version runs the same check; they differ
// only in the fold they cite: counterparty.seen_before/2.0.0 leaves dry runs
// out of the count, and counterparty.seen_before/3.0.0 also counts a record
// whose disposition.decision is carried_out (an act whose check asked, was
// approved to proceed and was executed) beside an accepted one.
//
// 4.0.0 (c878edf0, #135) is 3.0.0 with config missing_target: unseen. An
// in-scope action naming no target, or one that is empty or only
// whitespace, then fails as a first-time counterparty ("no payee named")
// with no fold run, where versions 1 to 3 (missing_target absent, read as
// n/a) record n/a for an absent target and count a blank one under "".
//
// recipient_seen_before/1.0.0 (365eb92, check_recipient_seen_before) is the
// same check under its own id, for a message: the same _seen_before over
// counterparty.seen_before/2.0.0, scoped to the message classes its wicket
// lists. The fold has no class filter, so an accepted earlier action of any
// class addressed to the recipient makes it known.

type seenBeforeConfig struct {
	FoldID        string   `yaml:"fold_id"`
	FoldDigest    string   `yaml:"fold_digest"`
	ActionClasses []string `yaml:"action_classes"`
	MissingTarget *string  `yaml:"missing_target"`
}

// What an in-scope action naming no target records (the engine's
// MISSING_TARGET_MODES): n/a naming the field, or a failure as an unseen
// counterparty. Absent is n/a.
const (
	missingTargetNA     = "n/a"
	missingTargetUnseen = "unseen"
)

type seenBefore struct {
	// id is the check: counterparty_seen_before or recipient_seen_before.
	id            string
	fold          fold
	actionClasses []string
	// missingTarget is the wicket's missing_target mode.
	missingTarget string
}

// newSeenBefore resolves the wicket's fold by id and digest from the fold
// catalog, as the engine reads it from its core fold catalog
// (seen_before_fold), whether or not the pack cites it: asg/everyday/0.3.6
// cites counterparty.seen_before/3.0.0 while recipient_seen_before/1.0.0
// still names /2.0.0. The wicket is pinned by digest (portedDefinitions),
// and its config names the fold's digest, so the fold is pinned with it.
func newSeenBefore(w pack.Wicket, p pack.Pack) (seenBefore, error) {
	cfg, err := decodeConfig[seenBeforeConfig](w)
	if err != nil {
		return seenBefore{}, err
	}
	f, ok := p.CatalogFold(cfg.FoldID)
	if !ok || f.Digest != cfg.FoldDigest {
		return seenBefore{}, fmt.Errorf("%s cites fold %s at %s, which the fold catalog does not resolve to that digest", w.ID, cfg.FoldID, cfg.FoldDigest)
	}
	fl, err := newFold(f)
	if err != nil {
		return seenBefore{}, err
	}
	if fl.reducer != "count" || fl.key != "asg_payload.target" || fl.window != 0 {
		return seenBefore{}, fmt.Errorf("%s: fold %s is not the per-target count the check reads", w.ID, f.ID)
	}
	mode := missingTargetNA
	if cfg.MissingTarget != nil {
		mode = *cfg.MissingTarget
	}
	if mode != missingTargetNA && mode != missingTargetUnseen {
		return seenBefore{}, fmt.Errorf("%s: missing_target %q is not one of %s, %s", w.ID, mode, missingTargetNA, missingTargetUnseen)
	}
	return seenBefore{id: w.Check, fold: fl, actionClasses: cfg.ActionClasses, missingTarget: mode}, nil
}

// checkSeenBefore is _seen_before, run as sb.id: out of scope for a class
// the wicket does not list (the raw class, as the Python compares it); under
// missing_target unseen, an absent or blank target fails as no payee named;
// otherwise a missing target is a population-a n/a; otherwise the fold's count of the
// operator's accepted prior actions addressed to this target, failing at
// zero (a first-time counterparty or recipient) and passing at one or more.
func checkSeenBefore(a Action, ledger []Record, sb seenBefore) (c Constraint) {
	id := sb.id
	rd := []string{"action_class"}
	defer func() { c.reads = rd }()
	method := sb.fold.id
	if a.ActionClass == nil || !slices.Contains(sb.actionClasses, *a.ActionClass) {
		return outOfScope(id, method)
	}
	rd = append(rd, "target", "operator")
	// Under unseen a blank target names nobody either: counted under "" it
	// would let one blank-target act make every later one a repeat.
	if sb.missingTarget == missingTargetUnseen && (a.Target == nil || strings.TrimSpace(*a.Target) == "") {
		return noPayee(id, method, a)
	}
	if a.Target == nil {
		return missingField(id, method, "target")
	}
	// ScanQuery(counterparty=action.operator): this operator's records only.
	var mine []Record
	for _, r := range ledger {
		if r.Operator == a.Operator {
			mine = append(mine, r)
		}
	}
	prior, _ := sb.fold.evaluate(mine, *a.Target, time.Time{})
	members := []canon.Member{canon.M("fold", canon.Str(sb.fold.digest)),
		canon.M("fold_key", canon.Obj(canon.M("path", canon.Str(sb.fold.key)), canon.M("value", canon.Str(*a.Target)))),
		canon.M("operator", canon.Str(a.Operator)), canon.M("prior_count", canon.Int(prior)), canon.M("seen_before", canon.Bool(prior > 0))}
	// The inputs the target was built without, by name, never the value
	// (capsule-engine #115, 4ac9fb0: evidence["ignored_inputs"]).
	if len(a.ignoredInputs) > 0 {
		members = append(members, canon.M("ignored_inputs", canon.Strs(a.ignoredInputs)))
	}
	evidence := canon.Obj(members...)
	if prior > 0 {
		return decided(id, method, Pass, evidence)
	}
	return decided(id, method, Fail, evidence)
}

// noPayee is the engine's _no_payee: an in-scope action naming no target,
// read as an unseen counterparty. No fold runs, as there is no key to count
// under. The evidence names the missing field, the operator and
// seen_before false, and any input the target was built without.
func noPayee(id, method string, a Action) Constraint {
	noun := map[string]string{"counterparty_seen_before": "counterparty", "recipient_seen_before": "recipient"}[id]
	members := []canon.Member{canon.M("missing_field", canon.Str("target")), canon.M("operator", canon.Str(a.Operator)),
		canon.M("seen_before", canon.Bool(false))}
	if len(a.ignoredInputs) > 0 {
		members = append(members, canon.M("ignored_inputs", canon.Strs(a.ignoredInputs)))
	}
	c := decided(id, method, Fail, canon.Obj(members...))
	c.failNote = "no payee named: the action names no target, so the " + noun + " is read as first-time"
	return c
}
