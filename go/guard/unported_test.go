package guard

import (
	"maps"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
	"gopkg.in/yaml.v3"
)

// NO RULE OF THE EVERYDAY PACK IS "NOT YET RUN": every check asg/everyday/0.3.7
// cites is one this port runs, none at a definition pinned as unported (those
// are asg/seller/0.1.0's), and no decision over the fixture scenarios or over
// an action of any taxonomy class carries a not-yet-run constraint or rule.
func TestNoEverydayRuleIsNotYetRun(t *testing.T) {
	e, p := everydayEngine(t)
	if len(e.unported) != 0 {
		t.Fatalf("configured unported %v", e.unported)
	}
	for _, w := range p.Wickets {
		if _, pinned := unportedDefinitions[w.ID]; pinned || !slices.Contains(implementedChecks, w.Check) {
			t.Errorf("everyday cites %s (%s), which this port does not run", w.ID, w.Check)
		}
	}
	for _, f := range p.Folds {
		if _, pinned := unportedFolds[f.ID]; pinned {
			t.Errorf("everyday cites fold %s, pinned as unported", f.ID)
		}
	}
	if n := NotYetRunRules(p); len(n) != 0 {
		t.Fatalf("rules not yet run: %v", n)
	}
	assertRun := func(name string, a Action, d Decision) {
		t.Helper()
		if anyNotYetRun(d) {
			t.Errorf("%s: a constraint is not yet run", name)
		}
		for _, r := range e.Report(a, d).Rules {
			if r.Reason == NotYetRun {
				t.Errorf("%s: %s is not yet run", name, r.Rule)
			}
		}
	}
	for _, fx := range fixtureSets {
		fe, _ := fx.engine(t)
		for id, d := range goReplay(t, fe, fx) {
			if anyNotYetRun(d) {
				t.Errorf("%s %s: a constraint is not yet run", fx.name, id)
			}
		}
	}
	for _, class := range slices.Sorted(maps.Keys(e.tax.classes)) {
		a := Action{Verb: "act", Operator: "op", Developer: "dev", ActionClass: strp(class), ActionID: "act/" + class,
			ActionType: "decide", Timestamp: "2026-10-08T10:00:00Z", Target: strp("shop/a"), AmountMinor: i64p(100)}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatal(err)
		}
		assertRun(class, a, d)
	}
}

// futurePack is the shipped pack plus a rule citing future_check/1.0.0, a
// check this port does not implement, pinned (for the test's duration) as
// unported, scoped to money.purchase: what a later pack citing a check the
// plugin has not ported looks like.
func futurePack(t *testing.T) pack.Pack {
	t.Helper()
	const id, digest = "future_check/1.0.0", "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0"
	unportedDefinitions[id] = digest
	t.Cleanup(func() { delete(unportedDefinitions, id) })
	var w struct {
		Config yaml.Node `yaml:"config"`
	}
	if err := yaml.Unmarshal([]byte("config:\n  action_classes: [money.purchase]\n"), &w); err != nil {
		t.Fatal(err)
	}
	_, p := everydayEngine(t)
	p.Wickets = append(slices.Clone(p.Wickets), pack.Wicket{ID: id, Check: "future_check", Digest: digest, Config: w.Config})
	p.Rules = append(slices.Clone(p.Rules), pack.Rule{ID: "r99-future", Check: "future_check", Statement: "A later rule.",
		DefaultDisposition: "ASK", ForwardVerdict: pack.ForwardDeterministic})
	return p
}

// THE FALLBACK, kept for a future unported check: in its scope it is
// not_evaluable, "not yet run by this plugin", in the engine's decision and
// the machine output, never a pass; out of its scope it is out of scope
// (n/a, not applicable), as an engine check is. NotYetRunRules names it.
func TestAFutureUnportedCheckIsNotYetRun(t *testing.T) {
	p := futurePack(t)
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	if n := NotYetRunRules(p); len(n) != 1 || !n["r99-future"] {
		t.Fatalf("not yet run: %v", n)
	}
	for class, want := range map[string]string{"money.purchase": VerdictNotEvaluable, "info.query": VerdictNotApplicable} {
		a := Action{Verb: "act", Operator: "op", Developer: "dev", ActionClass: strp(class), ActionID: "act/future",
			ActionType: "decide", Timestamp: "2026-10-08T10:00:00Z", Target: strp("shop/a"), AmountMinor: i64p(100)}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatal(err)
		}
		con, _ := d.Constraint("future_check")
		if con.Result == Pass || con.Result == Fail {
			t.Fatalf("%s: the port decided %s", class, con.Result)
		}
		if inScope := want == VerdictNotEvaluable; notYetRun(con) != inScope {
			t.Errorf("%s: not yet run = %v", class, notYetRun(con))
		}
		var got RuleResult
		for _, r := range e.Report(a, d).Rules {
			if r.Rule == "r99-future" {
				got = r
			}
		}
		if got.Verdict != want || (want == VerdictNotEvaluable && got.Reason != NotYetRun) {
			t.Errorf("r99-future on %s: %s %q, want %s", class, got.Verdict, got.Reason, want)
		}
	}
}

// A cited-but-unported wicket is accepted only at its pinned digest: any
// other digest is refused, as for a ported one.
func TestUnportedWicketAtAnotherDigestIsRefused(t *testing.T) {
	p := futurePack(t)
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.Check == "future_check" })
	p.Wickets[i].Digest = strings.Repeat("0", 64)
	if _, err := NewEngine(p); err == nil {
		t.Fatal("future_check/1.0.0 at another digest was accepted")
	}
}

// The declared rules (r14, r24, r25) are not_evaluable with their own
// reason, distinct from not yet run.
func TestDeclaredRulesHaveTheirOwnReason(t *testing.T) {
	e, p := everydayEngine(t)
	a := Action{Verb: "act", Operator: "op", Developer: "dev", ActionClass: strp("info.query"), ActionID: "act/q",
		ActionType: "decide", Timestamp: "2026-10-08T10:00:00Z"}
	d, err := e.Check(a, nil)
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, r := range e.Report(a, d).Rules {
		for _, pr := range p.Rules {
			if pr.ID == r.Rule && pr.Declared {
				n++
				if r.Verdict != VerdictNotEvaluable || r.Reason != DeclaredNotMeasured || r.Check != "" {
					t.Errorf("%s: %s %q %q", r.Rule, r.Verdict, r.Reason, r.Check)
				}
			}
		}
	}
	if n != 3 {
		t.Fatalf("%d declared rules reported, want 3", n)
	}
}
