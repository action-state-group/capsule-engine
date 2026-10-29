package guard

import (
	"fmt"
	"slices"
	"sort"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// counterparty_list is capsule-engine guards/checks/counterparty_list.py
// (counterparty_list/1.0.0, capsule-engine#263): is the action's counterparty
// on a list the user supplied? The wicket ships an empty deny list; a policy
// profile replaces mode, entries and disposition (WithProfile). Mode deny
// fails a listed counterparty, allow an unlisted one. An entry names its
// kind: target (the action's clear Target), or a fingerprint kind (domain,
// payee) with its fp_alg, matched against the counterparty ids a record seals.
// Matching is exact. Every kind the list names must be readable on the
// action, or the result is n/a, in scope, naming what was missing.
// Disposition is read by the engine: ask joins the failures that ask.

const counterpartyListMethod = "exact_reference_match_v0"

// Closed sets of counterparty_list.py: MODES, DISPOSITIONS, CLEAR_KINDS,
// FINGERPRINT_KINDS.
var (
	listModes        = []string{"allow", "deny"}
	listDispositions = []string{"ask", "deny"}
	clearKinds       = []string{"target"}
	fingerprintKinds = []string{"domain", "payee"}
)

// ListEntry is one reference on a counterparty list (counterparty_list.py
// ListEntry): FPAlg is set exactly when Kind is a fingerprint kind.
type ListEntry struct {
	Kind  string `yaml:"kind"`
	Value string `yaml:"value"`
	FPAlg string `yaml:"fp_alg,omitempty"`
}

// canon is the entry as the engine digests it: fp_alg only on a fingerprint.
func (l ListEntry) canon() canon.Value {
	members := []canon.Member{canon.M("kind", canon.Str(l.Kind)), canon.M("value", canon.Str(l.Value))}
	if l.FPAlg != "" {
		members = append(members, canon.M("fp_alg", canon.Str(l.FPAlg)))
	}
	return canon.Obj(members...)
}

// listConfig is counterparty_list's config: the classes it applies to, and
// the list a profile may replace.
type listConfig struct {
	ActionClasses []string    `yaml:"action_classes"`
	Mode          string      `yaml:"mode"`
	Entries       []ListEntry `yaml:"entries"`
	Disposition   string      `yaml:"disposition"`
}

// check refuses what counterparty_list.py and the engine refuse when the
// engine is built or the check runs: an unknown mode or disposition, and an
// entry whose kind and fp_alg do not go together.
func (c listConfig) check() error {
	if !slices.Contains(listModes, c.Mode) {
		return fmt.Errorf("counterparty_list mode %q must be one of %v", c.Mode, listModes)
	}
	if !slices.Contains(listDispositions, c.Disposition) {
		return fmt.Errorf("counterparty_list disposition %q must be one of %v", c.Disposition, listDispositions)
	}
	for _, e := range c.Entries {
		if err := e.check(); err != nil {
			return err
		}
	}
	return nil
}

func (l ListEntry) check() error {
	switch {
	case slices.Contains(clearKinds, l.Kind):
		if l.FPAlg != "" {
			return fmt.Errorf("counterparty_list entry of kind %q carries an fp_alg", l.Kind)
		}
	case slices.Contains(fingerprintKinds, l.Kind):
		if l.FPAlg == "" {
			return fmt.Errorf("counterparty_list entry of kind %q needs an fp_alg", l.Kind)
		}
	default:
		return fmt.Errorf("counterparty_list entry kind %q must be one of %v", l.Kind, slices.Concat(clearKinds, fingerprintKinds))
	}
	if l.Value == "" {
		return fmt.Errorf("counterparty_list entry of kind %q has no value", l.Kind)
	}
	return nil
}

// sortedEntries is counterparty_list.py sorted_entries: the entries ordered
// by the digest of each, so no digest depends on the order a user typed them.
func sortedEntries(entries []ListEntry) ([]ListEntry, error) {
	type keyed struct {
		digest string
		entry  ListEntry
	}
	ks := make([]keyed, 0, len(entries))
	for _, e := range entries {
		d, err := canon.Digest(e.canon())
		if err != nil {
			return nil, err
		}
		ks = append(ks, keyed{d, e})
	}
	sort.SliceStable(ks, func(i, j int) bool { return ks[i].digest < ks[j].digest })
	out := make([]ListEntry, len(ks))
	for i, k := range ks {
		out[i] = k.entry
	}
	return out, nil
}

func entriesValue(entries []ListEntry) canon.Value {
	items := make([]canon.Value, len(entries))
	for i, e := range entries {
		items[i] = e.canon()
	}
	return canon.Arr(items)
}

// readReference is counterparty_list.py _read: the action's reference of
// kind, in the form the list's entries of that kind use, or the field that is
// missing.
func readReference(a Action, kind string, fpAlgs []string) (*ListEntry, string) {
	if slices.Contains(clearKinds, kind) {
		if a.Target == nil || *a.Target == "" {
			return nil, "target"
		}
		return &ListEntry{Kind: kind, Value: *a.Target}, ""
	}
	if len(a.counterpartyIDs) == 0 || a.counterpartyFPAlg == "" {
		return nil, "counterparty"
	}
	if len(fpAlgs) != 1 || fpAlgs[0] != a.counterpartyFPAlg {
		return nil, "counterparty.fp_alg"
	}
	v := a.counterpartyIDs[kind]
	if v == "" {
		return nil, "counterparty.ids." + kind
	}
	return &ListEntry{Kind: kind, FPAlg: a.counterpartyFPAlg, Value: v}, ""
}

// checkCounterpartyList is counterparty_list.py check_counterparty_list. Its
// reason never names a reference: the evidence holds what was read and
// matched, as the engine seals it, and no reason or read value carries it
// (AGENTS.md, local-only payload fields).
func checkCounterpartyList(a Action, cfg listConfig) (c Constraint) {
	const id = "counterparty_list"
	rd := []string{"action_class"}
	defer func() { c.reads = rd }()
	if !slices.Contains(cfg.ActionClasses, a.class()) {
		return outsideScope(id, counterpartyListMethod, a.class(), cfg.ActionClasses)
	}
	listed, err := sortedEntries(cfg.Entries)
	if err != nil {
		return notEvaluable(id, counterpartyListMethod, err.Error())
	}
	if len(listed) == 0 && (a.Target == nil || *a.Target == "") && len(a.counterpartyIDs) == 0 {
		return missingField(id, counterpartyListMethod, "counterparty")
	}
	var kinds []string
	for _, e := range listed {
		if !slices.Contains(kinds, e.Kind) {
			kinds = append(kinds, e.Kind)
		}
	}
	slices.Sort(kinds)
	var read []ListEntry
	for _, kind := range kinds {
		var fpAlgs []string
		for _, e := range listed {
			if e.Kind == kind && e.FPAlg != "" && !slices.Contains(fpAlgs, e.FPAlg) {
				fpAlgs = append(fpAlgs, e.FPAlg)
			}
		}
		if slices.Contains(clearKinds, kind) {
			rd = append(rd, "target")
		} else {
			rd = append(rd, "counterparty")
		}
		ref, missing := readReference(a, kind, fpAlgs)
		if ref == nil {
			return missingField(id, counterpartyListMethod, missing)
		}
		read = append(read, *ref)
	}
	var matched *ListEntry
	for _, e := range listed {
		if slices.Contains(read, e) {
			matched = &e
			break
		}
	}
	listDigest, err := canon.Digest(entriesValue(listed))
	if err != nil {
		return notEvaluable(id, counterpartyListMethod, err.Error())
	}
	matchedValue := canon.Null()
	if matched != nil {
		matchedValue = matched.canon()
	}
	evidence := canon.Obj(canon.M("match_rule", canon.Str("exact")), canon.M("mode", canon.Str(cfg.Mode)),
		canon.M("list_digest", canon.Str(listDigest)), canon.M("list_size", canon.Int(int64(len(listed)))),
		canon.M("read", entriesValue(read)), canon.M("matched_entry", matchedValue))
	// deny: a match fails. allow: a miss fails.
	r := Pass
	if (matched != nil) == (cfg.Mode == "deny") {
		r = Fail
	}
	c = decided(id, counterpartyListMethod, r, evidence)
	if r == Fail {
		verdict := "is not on"
		if matched != nil {
			verdict = "is on"
		}
		c.failNote = fmt.Sprintf("the counterparty %s the %s list (consulted %d entries)", verdict, cfg.Mode, len(listed))
	}
	return c
}
