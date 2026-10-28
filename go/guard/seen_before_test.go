package guard

import (
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// seenBeforeEngine is the shipped engine: asg/everyday/0.3.8 cites
// counterparty_seen_before/4.0.0, which counts as 3.0.0 does (an action that
// names a target), over counterparty.seen_before/3.0.0.
func seenBeforeEngine(t *testing.T, v string) *Engine {
	t.Helper()
	if v != "2.0.0" {
		t.Fatalf("counterparty_seen_before/%s is not pinned", v)
	}
	e, p := everydayEngine(t)
	if w, ok := p.Wicket("counterparty_seen_before"); !ok || w.ID != "counterparty_seen_before/4.0.0" {
		t.Fatalf("the shipped pack does not cite counterparty_seen_before/4.0.0")
	}
	return e
}

func purchase(target string) Action {
	return Action{Verb: "buy", Operator: "op", Developer: "dev", ActionClass: strp("money.purchase"), ActionID: "buy/x",
		ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z", Target: strp(target), AmountMinor: i64p(100)}
}

func i64p(i int64) *int64 { return &i }
func boolp(b bool) *bool  { return &b }

func prior(op, target, decision string, dryRun *bool) Record {
	return Record{CapsuleID: "c-" + op + "-" + target + "-" + decision, Operator: op, Developer: "dev", Timestamp: "2026-10-07T09:00:00Z",
		Decision: strp(decision), Target: strp(target), AmountMinor: i64p(100), DryRun: dryRun}
}

func seenResult(t *testing.T, e *Engine, a Action, ledger []Record) Constraint {
	t.Helper()
	d, err := e.Check(a, MemoryHistory(ledger))
	if err != nil {
		t.Fatal(err)
	}
	c, ok := d.Constraint("counterparty_seen_before")
	if !ok {
		t.Fatal("no counterparty_seen_before constraint")
	}
	return c
}

// The check, at both versions: the operator's accepted prior actions to this
// target, counted; zero fails, one or more passes.
func TestSeenBeforeCountsAcceptedPriorsOfThisOperator(t *testing.T) {
	for _, v := range []string{"2.0.0"} {
		e := seenBeforeEngine(t, v)
		cases := []struct {
			name   string
			ledger []Record
			want   Result
		}{
			{"no history", nil, Fail},
			{"accepted prior to this merchant", []Record{prior("op", "shop/a", "accept", nil)}, Pass},
			{"prior to another merchant", []Record{prior("op", "shop/b", "accept", nil)}, Fail},
			{"another operator's prior", []Record{prior("other", "shop/a", "accept", nil)}, Fail},
			{"a rejected prior", []Record{prior("op", "shop/a", "reject", nil)}, Fail},
		}
		for _, c := range cases {
			if got := seenResult(t, e, purchase("shop/a"), c.ledger); got.Result != c.want {
				t.Errorf("v%s %s: %s, want %s", v, c.name, got.Result, c.want)
			}
		}
		other := purchase("shop/a")
		other.ActionClass = strp("money.transfer")
		if got := seenResult(t, e, other, nil); got.Result != NotApplicable || got.Population() != PopulationC {
			t.Errorf("v%s money.transfer: %s %s, want out of scope", v, got.Result, got.Population())
		}
		noTarget := purchase("shop/a")
		noTarget.Target = nil
		// 4.0.0 (missing_target: unseen): a purchase naming no payee fails
		// as a first-time merchant, with no fold run.
		if got := seenResult(t, e, noTarget, nil); got.Result != Fail || !strings.HasPrefix(got.failNote, "no payee named") {
			t.Errorf("v%s no target: %s %q, want fail, no payee named", v, got.Result, got.failNote)
		}
	}
	// The frozen 0.3.4 cites 3.0.0, where it is n/a naming the field.
	p, err := pack.LoadEveryday034()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	noTarget := purchase("shop/a")
	noTarget.Target = nil
	if got := seenResult(t, e, noTarget, nil); got.Result != NotApplicable || got.MissingField() != "target" {
		t.Errorf("0.3.4 no target: %s missing %q", got.Result, got.MissingField())
	}
}

// THE v2 CHANGE: a dry run never ran, so under counterparty.seen_before/2.0.0
// it does not make a merchant known (1.0.0 counted it).
func TestSeenBeforeV2LeavesDryRunsOut(t *testing.T) {
	ledger := []Record{prior("op", "shop/a", "accept", boolp(true))}
	if got := seenResult(t, seenBeforeEngine(t, "2.0.0"), purchase("shop/a"), ledger); got.Result != Fail {
		t.Errorf("v2 with a dry-run prior: %s, want fail (a dry run is not history)", got.Result)
	}
	explicit := []Record{prior("op", "shop/a", "accept", boolp(false))}
	if got := seenResult(t, seenBeforeEngine(t, "2.0.0"), purchase("shop/a"), explicit); got.Result != Pass {
		t.Errorf("v2 with dry_run=false: %s, want pass", got.Result)
	}
}

// A first-time merchant on its own asks an approver when the class has one,
// as caps does. From taxonomy 3 money.purchase has one (account_holder), so
// it escalates; under taxonomy 2 it had none and denied.
func TestSeenBeforeFailureDecides(t *testing.T) {
	e := seenBeforeEngine(t, "2.0.0")
	d, err := e.Check(purchase("shop/new"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if d.Outcome != Escalate {
		t.Fatalf("outcome %s, want escalate (money.purchase has approver_role account_holder)", d.Outcome)
	}
}
