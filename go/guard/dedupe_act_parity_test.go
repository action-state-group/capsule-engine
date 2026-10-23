package guard

import (
	"bytes"
	"encoding/json"
	"fmt"
	"slices"
	"testing"
)

// DEDUPE KEYS A DEAL CHECK ON THE ACT IT CARRIES (capsule-engine #106,
// f334dd5): an act stated against a pinned taxonomy (taxonomy_version set,
// as on every deal check the engine's bridge reads) is keyed on operator,
// developer, action_class, target and amount, never on the record's
// action_type or action_id, and the scan covers every action_type; any other
// action keeps the legacy key. Held against the engine side's corpus
// (testdata/parity/dedupe-act-corpus.json) run through the engine at that
// commit (generate_dedupe_act.py): per step the candidate's key and the key
// of the record its decision leaves; per case the outcome, the disposition
// decision, the failing constraints, the dedupe result and the step it
// matched; and the key formula byte for byte.
func TestDedupeKeysOnTheCheckedActMatchTheEngine(t *testing.T) {
	var py struct {
		CapsuleEngineCommit  string `json:"capsule_engine_commit"`
		PackDefinitionDigest string `json:"pack_definition_digest"`
		Decisions            map[string]struct {
			Outcome            string   `json:"outcome"`
			Decision           string   `json:"decision"`
			FailingConstraints []string `json:"failing_constraints"`
			DedupeResult       string   `json:"dedupe_result"`
			DedupeMatchedStep  *int     `json:"dedupe_matched_step"`
			Steps              []struct {
				Record             json.RawMessage `json:"record"`
				Action             json.RawMessage `json:"action"`
				CandidateKey       string          `json:"candidate_key"`
				DecisionCapsuleKey string          `json:"decision_capsule_key"`
				DecisionActionType string          `json:"decision_action_type"`
				DecisionActionID   string          `json:"decision_action_id"`
			} `json:"steps"`
		} `json:"decisions"`
		KeyVectors []struct {
			Name   string          `json:"name"`
			Action json.RawMessage `json:"action"`
			Digest string          `json:"digest"`
		} `json:"key_vectors"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "dedupe-act-parity.json"), &py); err != nil {
		t.Fatal(err)
	}
	e, p := everydayEngine(t)
	if py.CapsuleEngineCommit != parity(t, shipped).CapsuleEngineCommit || py.PackDefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors at %s for %s; the shipped pack is %s", py.CapsuleEngineCommit, py.PackDefinitionDigest, p.DefinitionDigest)
	}
	if len(py.Decisions) < 9 || len(py.KeyVectors) < 3 {
		t.Fatalf("%d cases, %d key vectors", len(py.Decisions), len(py.KeyVectors))
	}
	outcomes := map[string]string{}
	for name, want := range py.Decisions {
		var ledger []Record
		var a Action
		var d Decision
		var err error
		for i, step := range want.Steps {
			if step.Record != nil {
				a, err = DecodeRecord(step.Record, "")
			} else {
				a, err = DecodeAction(bytes.NewReader(step.Action))
			}
			if err != nil {
				t.Fatalf("%s step %d: %v", name, i, err)
			}
			if d, err = e.Check(a, ledger); err != nil {
				t.Fatalf("%s step %d: %v", name, i, err)
			}
			key, err := equivalenceKeyForAction(a)
			if err != nil {
				t.Fatal(err)
			}
			r := RecordFor(a, d)
			r.CapsuleID, r.DryRun = fmt.Sprintf("step-%d", i), boolp(true)
			recordKey, err := equivalenceKeyForRecord(r)
			if err != nil {
				t.Fatal(err)
			}
			if key != step.CandidateKey || recordKey != step.DecisionCapsuleKey {
				t.Errorf("%s step %d: keys %s / %s, engine %s / %s", name, i, key, recordKey, step.CandidateKey, step.DecisionCapsuleKey)
			}
			// The record the decision leaves has the action_type and
			// action_id the engine's decision capsule has: a deal record's
			// own, from its header, as the engine's bridge takes them.
			if r.ActionType != step.DecisionActionType || r.ActionID != step.DecisionActionID {
				t.Errorf("%s step %d: decision record %s %s, engine %s %s", name, i, r.ActionType, r.ActionID,
					step.DecisionActionType, step.DecisionActionID)
			}
			ledger = append(ledger, r)
		}
		var failing []string
		for _, c := range d.Constraints {
			if c.Result == Fail {
				failing = append(failing, c.ID)
			}
		}
		dd, _ := d.Constraint("dedupe")
		matched := -1
		if m, ok := dd.MatchedCapsuleID(); ok {
			matched = slices.IndexFunc(ledger, func(r Record) bool { return r.CapsuleID == m })
		}
		wantMatched := -1
		if want.DedupeMatchedStep != nil {
			wantMatched = *want.DedupeMatchedStep
		}
		decision := *RecordFor(a, d).Decision
		if d.Outcome != want.Outcome || e.Report(a, d).Decision != want.Outcome || decision != want.Decision ||
			!slices.Equal(failing, want.FailingConstraints) || string(dd.Result) != want.DedupeResult || matched != wantMatched {
			t.Errorf("%s: Go %s (%s), failing %v, dedupe %s matched %d; engine %s (%s), failing %v, dedupe %s matched %d", name,
				d.Outcome, decision, failing, dd.Result, matched, want.Outcome, want.Decision, want.FailingConstraints,
				want.DedupeResult, wantMatched)
		}
		outcomes[name] = d.Outcome
	}
	// A key vector is the action's own fields, without a timestamp (the key
	// never reads one), so it is decoded as is rather than through
	// DecodeAction, with the one default DecodeAction applies:
	// action_type "decide" (action.py:85).
	for _, v := range py.KeyVectors {
		var a Action
		if err := json.Unmarshal(v.Action, &a); err != nil {
			t.Fatalf("%s: %v", v.Name, err)
		}
		if a.ActionType == "" {
			a.ActionType = "decide"
		}
		if key, err := equivalenceKeyForAction(a); err != nil || key != v.Digest {
			t.Errorf("key vector %s: %s (%v), engine %s", v.Name, key, err, v.Digest)
		}
	}
	// The cases the corpus exists for, named so a regenerated file that lost
	// one fails here. The corpus names predate capsule-engine #111: the same
	// act from another deal (both checks state their x-deal-v0.deal_id, and
	// they differ) now asks the class's approver, so that case escalates.
	for name, want := range map[string]string{
		"same-act-repeated-is-refused":             Deny,
		"same-act-from-another-deal-is-refused":    Escalate,
		"earlier-act-under-decide-type-is-matched": Deny,
		"unpinned-actions-keep-the-legacy-key":     Deny,
		"another-class-passes":                     Allow,
		"another-payee-passes":                     Escalate,
		"another-amount-passes":                    Escalate,
		"outside-window-passes":                    Escalate,
		"checks-differing-in-act-never-match":      Escalate,
	} {
		if outcomes[name] != want {
			t.Errorf("%s: %q, want %s", name, outcomes[name], want)
		}
	}
}
