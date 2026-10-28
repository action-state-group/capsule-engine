package guard

import (
	"encoding/json"
	"fmt"
	"slices"
	"testing"
)

// A REFUND DEDUPES ON WHAT IT RETURNS AND THE ACT IT REVERSES; A REPEAT IN
// ANOTHER DEAL ASKS (capsule-engine #108 at 49b2fe8 and #111 at 35874b8).
// A check that moves money in is keyed on its returned amount and the digest
// of its typed reverses_ref, never a bare string; a key match where both
// checks state their x-deal-v0.deal_id and they differ fails asking the
// class's approver, and any other match is refused. Held against the engine side's
// vectors plus the plugin's own cases
// (testdata/parity/dedupe-refund-cross-deal-corpus.json) run through the
// engine (generate_dedupe_refund_cross_deal.py): per step the decision, the
// failing constraints, the dedupe result, its evidence digest and the step
// it matched, both keys, and the refund and deal fields the decision record
// carries.
func TestDedupeRefundKeyAndCrossDealRepeatMatchTheEngine(t *testing.T) {
	var py struct {
		CapsuleEngineCommit  string `json:"capsule_engine_commit"`
		PackDefinitionDigest string `json:"pack_definition_digest"`
		Cases                map[string][]struct {
			Record               json.RawMessage `json:"record"`
			Outcome              string          `json:"outcome"`
			FailingConstraints   []string        `json:"failing_constraints"`
			DedupeResult         string          `json:"dedupe_result"`
			DedupeReason         string          `json:"dedupe_reason"`
			DedupeEvidenceDigest *string         `json:"dedupe_evidence_digest"`
			DedupeMatchedStep    *int            `json:"dedupe_matched_step"`
			CandidateKey         string          `json:"candidate_key"`
			DecisionCapsuleKey   string          `json:"decision_capsule_key"`
			SealedReturnedMinor  *int64          `json:"sealed_returned_minor"`
			SealedReversesRef    *string         `json:"sealed_reverses_ref"`
			SealedDealID         *string         `json:"sealed_deal_id"`
			DecisionCapsuleID    string          `json:"decision_capsule_id"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "dedupe-refund-cross-deal-parity.json"), &py); err != nil {
		t.Fatal(err)
	}
	e, p := everyday037Engine(t)
	if py.CapsuleEngineCommit != parity(t, fixtures037).CapsuleEngineCommit || py.PackDefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors at %s for %s; Go runs %s", py.CapsuleEngineCommit, py.PackDefinitionDigest, p.DefinitionDigest)
	}
	if len(py.Cases) < 22 {
		t.Fatalf("%d cases", len(py.Cases))
	}
	last := map[string]string{}
	for name, steps := range py.Cases {
		var ledger []Record
		for i, want := range steps {
			a, err := DecodeRecord(want.Record, "")
			if err != nil {
				t.Fatalf("%s step %d: %v", name, i, err)
			}
			d, err := e.Check(a, MemoryHistory(ledger))
			if err != nil {
				t.Fatalf("%s step %d: %v", name, i, err)
			}
			r := RecordFor(a, d)
			// The engine's decision capsule id, which a later match's
			// evidence names.
			r.CapsuleID, r.DryRun = want.DecisionCapsuleID, boolp(true)
			key, err := equivalenceKeyForAction(a)
			if err != nil {
				t.Fatal(err)
			}
			recordKey, err := equivalenceKeyForRecord(r)
			if err != nil {
				t.Fatal(err)
			}
			if key != want.CandidateKey || recordKey != want.DecisionCapsuleKey {
				t.Errorf("%s step %d: keys %s / %s, engine %s / %s", name, i, key, recordKey, want.CandidateKey, want.DecisionCapsuleKey)
			}
			if !eqPtr(r.ReturnedMinor, want.SealedReturnedMinor) || !eqPtr(r.ReversesRef, want.SealedReversesRef) || !eqPtr(r.DealID, want.SealedDealID) {
				t.Errorf("%s step %d: record carries returned %v reverses %v deal %v; engine seals %v %v %v", name, i,
					shown(r.ReturnedMinor), shown(r.ReversesRef), shown(r.DealID),
					shown(want.SealedReturnedMinor), shown(want.SealedReversesRef), shown(want.SealedDealID))
			}
			var failing []string
			for _, c := range d.Constraints {
				if c.Result == Fail {
					failing = append(failing, c.ID)
				}
			}
			dd, _ := d.Constraint("dedupe")
			digest, _, err := dd.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			matched := -1
			if m, ok := dd.MatchedCapsuleID(); ok {
				matched = slices.IndexFunc(ledger, func(r Record) bool { return r.CapsuleID == m })
			}
			wantMatched := -1
			if want.DedupeMatchedStep != nil {
				wantMatched = *want.DedupeMatchedStep
			}
			if d.Outcome != want.Outcome || e.Report(a, d).Decision != want.Outcome || !slices.Equal(failing, want.FailingConstraints) ||
				string(dd.Result) != want.DedupeResult || want.DedupeEvidenceDigest == nil || digest != *want.DedupeEvidenceDigest || matched != wantMatched {
				t.Errorf("%s step %d: Go %s, failing %v, dedupe %s %s matched %d; engine %s, failing %v, dedupe %s %v matched %d", name, i,
					d.Outcome, failing, dd.Result, digest, matched, want.Outcome, want.FailingConstraints, want.DedupeResult,
					shown(want.DedupeEvidenceDigest), wantMatched)
			}
			// r27 names the engine's reason for a repeat in another deal,
			// which says nothing of the earlier act.
			if dd.Result == Fail && dd.asksApprover {
				if rule := ruleResultFor(e, a, d, "r27-no-commitment-beyond-task-bounds"); rule.Verdict != VerdictFail || rule.Reason != want.DedupeReason {
					t.Errorf("%s step %d: r27 %s %q, engine reason %q", name, i, rule.Verdict, rule.Reason, want.DedupeReason)
				}
			}
			ledger = append(ledger, r)
			last[name] = d.Outcome
		}
	}
	// The cases the corpus exists for, named so a regenerated file that lost
	// one fails here: the outcome of each case's last step.
	for name, want := range map[string]string{
		"refund/same-refund-same-act":                     Deny,
		"refund/equal-refunds-different-acts":             Allow,
		"refund/two-partial-refunds-different-amounts":    Allow,
		"cross-deal/same-deal-repeat":                     Deny,
		"cross-deal/other-deal-repeat-with-approver":      Escalate,
		"cross-deal/other-deal-repeat-no-approver":        Deny,
		"cross-deal/same-deal-match-wins-over-other-deal": Deny,
		"cross-deal/deal-unstated-now":                    Deny,
		"plugin/bare-string-reverses-ref-is-absent":       Deny,
		"plugin/typed-record-refs-different-acts-pass":    Allow,
		"plugin/partial-cancels-key-on-cancelled-amount":  Deny,
		"plugin/explicit-returned-minor-wins":             Deny,
		"plugin/same-refund-same-act-other-deal":          Deny,
	} {
		if last[name] != want {
			t.Errorf("%s: %q, want %s", name, last[name], want)
		}
	}
}

func eqPtr[T comparable](a, b *T) bool {
	return (a == nil) == (b == nil) && (a == nil || *a == *b)
}

// shown is *p for a failure message, or "absent".
func shown[T any](p *T) string {
	if p == nil {
		return "absent"
	}
	return fmt.Sprint(*p)
}
