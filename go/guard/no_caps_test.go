package guard

import (
	"fmt"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// capsOutOfScopeDigest is capsule-engine's json_digest of
// not_applicable_evidence("caps", in_scope=False) (guards/capsule.py at
// e0e664d): the evidence caps seals when no limit applies.
const capsOutOfScopeDigest = "7a049ba37b2851287decae218ff18caac4da237c247a5188da1bd39320a2827c"

func seller012Engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadSeller012()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// noCapsTransfer is a money transfer: the one class caps/1.0.0 limits, so
// caps would read it under 0.1.1 (capsule-engine python/tests/test_pack_without_caps.py).
func noCapsTransfer(t *testing.T, n int, taxonomy string) Action {
	t.Helper()
	tv := ""
	if taxonomy != "" {
		tv = fmt.Sprintf(`,"taxonomy_version":%q`, taxonomy)
	}
	a, err := decodeAction([]byte(fmt.Sprintf(`{"verb":"send_money","operator":"no-caps","developer":"assistant@v1","action_class":"money.transfer","currency":"EUR","rail":"card","target":"payee/garden-centre","amount_minor":9999999,"action_id":"send_money/no-caps-%d","timestamp":"2026-10-09T12:%02d:00Z"%s}`, n, n, tv)), true)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// A pack may cite no caps definition (capsule-engine e0e664d, guards/
// engine.py _check_caps): the engine is built, and caps is recorded on every
// decision as n/a, out of scope, with no method and the out-of-scope
// evidence, never as a missing field and never pending. The verdicts and caps
// results are the engine's at e0e664d on these actions under 0.1.1 and 0.1.2,
// each on an empty ledger (DecisionResult.verdict: a decision refused only
// because a taxonomy mismatch left its class-keyed checks unevaluated is
// not_evaluable, which is what Report says). Under 0.1.2 a mismatch does not
// hold the absent caps: it reads nothing keyed on the class, so its evidence
// stays the out-of-scope one; under 0.1.1 the mismatch holds caps.
func TestAPackCitingNoCapsIsTaken(t *testing.T) {
	e011, _ := seller011Engine(t)
	e012, _ := seller012Engine(t)
	for _, tc := range []struct {
		name     string
		e        *Engine
		taxonomy string
		verdict  string
		capsRes  Result
		// capsEvid is the engine's caps evidence digest, or "" where caps
		// is held by the mismatch (pending, whatever evidence it carries).
		capsEvid string
	}{
		{"0.1.1 transfer", e011, "", Allow, Pass, "7b5fa931ec38657de0c625d12b2df9b2b075e35a98ced3b20284471a9a4b0736"},
		{"0.1.1 transfer under taxonomy 5", e011, "5", VerdictNotEvaluable, NotApplicable, ""},
		{"0.1.2 transfer", e012, "", Allow, NotApplicable, capsOutOfScopeDigest},
		{"0.1.2 transfer under taxonomy 5", e012, "5", VerdictNotEvaluable, NotApplicable, capsOutOfScopeDigest},
	} {
		a := noCapsTransfer(t, 1, tc.taxonomy)
		d, err := tc.e.Check(a, nil)
		if err != nil {
			t.Fatalf("%s: %v", tc.name, err)
		}
		c, ok := d.Constraint("caps")
		if !ok {
			t.Fatalf("%s: no caps recorded", tc.name)
		}
		if v := tc.e.Report(a, d).Decision; v != tc.verdict || c.Result != tc.capsRes {
			t.Errorf("%s: Go %s, caps %s; engine %s, caps %s", tc.name, v, c.Result, tc.verdict, tc.capsRes)
		}
		if tc.capsEvid == "" {
			if c.Pending() == "" {
				t.Errorf("%s: caps not held by the mismatch", tc.name)
			}
			continue
		}
		if digest, digestable, err := c.EvidenceDigest(); err != nil || !digestable || digest != tc.capsEvid {
			t.Errorf("%s: caps evidence %s (%v, %v), engine %s", tc.name, digest, digestable, err, tc.capsEvid)
		}
		if tc.e != e012 {
			continue
		}
		_, outside := c.OutsideScope()
		if c.Method != "" || c.Population() != PopulationC || c.MissingField() != "" || c.Pending() != "" || outside || len(c.reads) != 0 {
			t.Errorf("%s: caps method %q population %q missing %q pending %q outside %v reads %v", tc.name, c.Method, c.Population(), c.MissingField(), c.Pending(), outside, c.reads)
		}
	}
}

// With no caps definition there is no window to cover: a history that could
// not carry one (incomplete, too short) leaves caps and the decision exactly
// as Check records them on the same acts.
func TestNoCapsDefinitionIgnoresHistoryCoverage(t *testing.T) {
	e, _ := seller012Engine(t)
	a := noCapsTransfer(t, 2, "")
	earlier := []Record{RecordFor(noCapsTransfer(t, 1, ""), Decision{Outcome: Allow})}
	for _, h := range []InputHistory{{Days: 0, Complete: false}, {Days: 1, Complete: true}, {Days: 30, Complete: false, Acts: earlier}, {Days: 30, Complete: true, Acts: earlier}} {
		want, err := e.Check(a, MemoryHistory(h.Acts))
		if err != nil {
			t.Fatal(err)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		got, _ := d.Constraint("caps")
		gd, _, _ := got.EvidenceDigest()
		if got.Result != NotApplicable || got.Pending() != "" || gd != capsOutOfScopeDigest || d.Outcome != want.Outcome {
			t.Errorf("history %d days complete %v: caps %s %q %s, outcome %s, Check %s", h.Days, h.Complete, got.Result, got.Pending(), gd, d.Outcome, want.Outcome)
		}
	}
}
