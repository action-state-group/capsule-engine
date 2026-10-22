package guard

import (
	"maps"
	"slices"
	"testing"
)

// LEG 2, a regression snapshot: gold_labels.yaml. Its generated rows carry
// identities, not verdicts: the action set, the pack and wicket digests the
// verdict ran under, and the evaluator tier. The Go engine must match them.
func TestLeg2GoldLabels(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg2GoldLabels(t, fx) })
	}
}

func testLeg2GoldLabels(t *testing.T, fx fixtureSet) {
	e, p := fx.engine(t)
	got := goReplay(t, e, fx)
	wickets := map[string]string{}
	for _, w := range p.Wickets {
		wickets[w.ID] = w.Digest
	}
	var generated []string
	for _, row := range goldLabels(t, fx) {
		if row.HandAuthored {
			continue
		}
		generated = append(generated, row.ActionID)
		if _, ok := got[row.ActionID]; !ok {
			t.Errorf("gold row %s has no Go decision", row.ActionID)
		}
		if row.ConfigIdentity.PackID != p.ID || row.ConfigIdentity.PackDigest != p.DefinitionDigest {
			t.Errorf("%s: gold pack %s@%s, Go %s@%s", row.ActionID, row.ConfigIdentity.PackID, row.ConfigIdentity.PackDigest, p.ID, p.DefinitionDigest)
		}
		if !maps.Equal(row.ConfigIdentity.Wickets, wickets) {
			t.Errorf("%s: gold wickets %v, Go %v", row.ActionID, row.ConfigIdentity.Wickets, wickets)
		}
		// All six checks are deterministic predicates: every row is recomputed.
		if row.EvaluatorTier != "recomputed" {
			t.Errorf("%s: evaluator tier %s", row.ActionID, row.EvaluatorTier)
		}
	}
	if !slices.Equal(slices.Sorted(slices.Values(generated)), slices.Sorted(maps.Keys(got))) {
		t.Fatalf("gold generated rows %v, Go decisions %v", generated, slices.Sorted(maps.Keys(got)))
	}
}

// The hand-authored row is the population-b record shape (an n/a sealed with
// no evidence). It belongs to another pack; what it pins here is that the Go
// engine never emits that shape: its verify_before_dispatch n/a always carries
// the out-of-scope evidence.
func TestLeg2HandAuthoredPopulationBIsNeverProduced(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg2HandAuthoredPopulationBIsNeverProduced(t, fx) })
	}
}

func testLeg2HandAuthoredPopulationBIsNeverProduced(t *testing.T, fx fixtureSet) {
	var hand []goldRow
	for _, row := range goldLabels(t, fx) {
		if row.HandAuthored {
			hand = append(hand, row)
		}
	}
	if len(hand) != 1 || hand[0].DeclaredPopulation != "b" || hand[0].Record == nil || hand[0].Record.Result != "n/a" {
		t.Fatalf("expected one hand-authored population-b n/a row, got %+v", hand)
	}
	e, _ := fx.engine(t)
	for id, d := range goReplay(t, e, fx) {
		c, ok := d.Constraint(hand[0].Record.ID)
		if !ok || c.Result != NotApplicable {
			t.Fatalf("%s: %s missing or not n/a", id, hand[0].Record.ID)
		}
		if _, ok, err := c.EvidenceDigest(); !ok || err != nil {
			t.Fatalf("%s: %s n/a without evidence", id, c.ID)
		}
	}
}
