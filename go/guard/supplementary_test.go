package guard

import (
	"bytes"
	"encoding/json"
	"testing"
)

// LEG 3, SUPPLEMENTARY: what the everyday decision table does not exercise,
// compared with a live Python run of the same engine at the same commit
// (testdata/parity/generate_supplementary.py), under the shipped caps/5.0.0
// over spend.weekly/3.0.0. Real decisions, not dry runs, so they count. It
// covers:
//   - the caps window limit, reached exactly and then exceeded;
//   - the fold's operator key: a second developer of the same operator reads
//     the same total;
//   - spend pooled across money classes (a purchase after transfers);
//   - a booking.create over the per-action limit, which denies
//     (booking.create has no approver_role);
//   - a booking.cancel carrying an amount and citing no charge: out of the
//     cap's scope, and unlinked, so the next purchase still fits.
//
// Scenarios run chained from the fixture ledger's install record, as Python's
// did, and every constraint's result and evidence digest must match.
func TestLeg3SupplementaryParity(t *testing.T) {
	var p parityFile
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "supplementary-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	if p.CapsuleEngineCommit != parity(t, shipped).CapsuleEngineCommit {
		t.Fatalf("supplementary run at %s, parity run at %s", p.CapsuleEngineCommit, parity(t, shipped).CapsuleEngineCommit)
	}
	e, _ := everydayEngine(t)
	ledger := miniLedger(t, shipped)[:1]
	outcomes := map[string]string{}
	for _, s := range p.Scenarios {
		a, err := DecodeAction(bytes.NewReader(s.Action))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		d, err := e.Check(a, ledger)
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		compareToPython(t, s.Scenario, e, a, d, s)
		outcomes[s.Scenario] = d.Outcome
		ledger = append(ledger, RecordFor(a, d))
	}
	// The cases the file exists for, named so a regenerated file that lost
	// one fails here rather than passing on fewer rows.
	for name, want := range map[string]string{
		"window-fill-4-at-limit":      Allow,
		"window-over-other-developer": Escalate,
		// From taxonomy 3 (0.3.2) money.purchase has an approver, so an
		// over-limit purchase and one from a merchant with no accepted
		// history (counterparty_seen_before) ask first, as in Python at
		// c29e738.
		"window-over-pooled-purchase": Escalate,
		"after-unlinked-cancel":       Escalate,
		// The booking: over the per-purchase limit and a booking (r05
		// and r16). booking.create has an approver, and r16's gate selector
		// is declared ASK only, so both failures ask
		// (TestASKDeclaredGateRulesAskOnlyWhereTheClassHasAnApprover).
		"booking-over-per-action": Escalate,
		// A cancellation fails action_class_gate (r17), declared ASK, and
		// from taxonomy 4 booking.cancel has an approver: it asks.
		"cancel-with-amount": Escalate,
	} {
		if got, ok := outcomes[name]; !ok || got != want {
			t.Errorf("%s: %q, want %s", name, got, want)
		}
	}
}
