package guard

import (
	"encoding/json"
	"os"
	"slices"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
	"gopkg.in/yaml.v3"
)

// counterparty_seen_before/5.0.0 (capsule-engine#327): on a history that may
// be missing acts, a purchase with no earlier act counted to its payee is
// not evaluated and asks, where 4.0.0 failed it as a first-time
// counterparty. A counted act still passes, a complete history with none
// still fails, and everyday 0.3.9 keeps 4.0.0. The shared vectors hold the
// decision to the engine's (emit.json:pay-history-empty-incomplete).

// checkInput decodes an emit input, with edit applied to its envelope first
// when given.
func checkInput(t *testing.T, name string, edit func(map[string]any)) (Action, InputHistory) {
	t.Helper()
	raw, err := os.ReadFile("testdata/emit/" + name + ".json")
	if err != nil {
		t.Fatal(err)
	}
	if edit != nil {
		var env map[string]any
		if err := json.Unmarshal(raw, &env); err != nil {
			t.Fatal(err)
		}
		edit(env)
		if raw, err = json.Marshal(env); err != nil {
			t.Fatal(err)
		}
	}
	a, h, err := DecodeCheckInput(raw)
	if err != nil {
		t.Fatal(err)
	}
	return a, h
}

func engineFor(t *testing.T, load func() (pack.Pack, error)) *Engine {
	t.Helper()
	p, err := load()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e
}

// ruleVerdicts is r02's and r06's report verdicts and reasons.
func ruleVerdicts(r CheckReport) map[string][2]string {
	out := map[string][2]string{}
	for _, rule := range r.Rules {
		if rule.Rule == "r02-ordinary-purchase" || rule.Rule == "r06-new-merchant" {
			out[rule.Rule] = [2]string{rule.Verdict, rule.Reason}
		}
	}
	return out
}

func TestEveryday0310CitesSeenBeforeV5(t *testing.T) {
	p, err := pack.LoadEveryday0310()
	if err != nil {
		t.Fatal(err)
	}
	if w, ok := p.Wicket("counterparty_seen_before"); !ok || w.ID != "counterparty_seen_before/5.0.0" {
		t.Fatalf("everyday 0.3.10 cites %s", w.ID)
	}
	old, err := pack.LoadEveryday039()
	if err != nil {
		t.Fatal(err)
	}
	if w, _ := old.Wicket("counterparty_seen_before"); w.ID != "counterparty_seen_before/4.0.0" {
		t.Fatalf("everyday 0.3.9 cites %s", w.ID)
	}
}

func TestAnIncompleteHistoryModeOutsideTheTwoIsRefused(t *testing.T) {
	p, err := pack.LoadEveryday0310()
	if err != nil {
		t.Fatal(err)
	}
	w, _ := p.Wicket("counterparty_seen_before")
	sb, err := newSeenBefore(w, p)
	if err != nil || sb.incompleteHistory != incompleteHistoryNotEvaluable {
		t.Fatalf("5.0.0: %q %v", sb.incompleteHistory, err)
	}
	var config yaml.Node
	if err := yaml.Unmarshal([]byte("fold_id: counterparty.seen_before/3.0.0\n"+
		"fold_digest: 73dbf8cd8a36fadca0ac823d2b17592fdc457c24081bc44667f032601e914033\n"+
		"action_classes: [money.purchase]\nmissing_target: unseen\nincomplete_history: skip\n"), &config); err != nil {
		t.Fatal(err)
	}
	w.Config = *config.Content[0]
	if _, err := newSeenBefore(w, p); err == nil {
		t.Fatal("incomplete_history skip was accepted")
	}
	w.Config.Content = w.Config.Content[:len(w.Config.Content)-2]
	if sb, err := newSeenBefore(w, p); err != nil || sb.incompleteHistory != incompleteHistoryCount {
		t.Fatalf("no incomplete_history: %q %v; the engine's default is count", sb.incompleteHistory, err)
	}
}

func TestAnIncompleteHistoryWithNoEarlierPaymentIsNotCheckedAndAsks(t *testing.T) {
	a, h := checkInput(t, "pay-history-empty-incomplete", nil)
	e := engineFor(t, pack.LoadEveryday0310)
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	c, _ := d.Constraint("counterparty_seen_before")
	if c.Result != NotApplicable || c.Pending() != historyIncompleteReason || !c.asksUnevaluated {
		t.Fatalf("counterparty_seen_before %s %q asks=%v", c.Result, c.Pending(), c.asksUnevaluated)
	}
	if d.Outcome != Escalate || !slices.Equal(d.askedUnevaluated, []string{"caps", "counterparty_seen_before"}) {
		t.Fatalf("outcome %s, asked %v", d.Outcome, d.askedUnevaluated)
	}
	want := [2]string{VerdictNotEvaluable, historyIncompleteReason}
	rules := ruleVerdicts(e.Report(a, d))
	if len(rules) != 2 {
		t.Fatalf("rules %v", rules)
	}
	for rule, got := range rules {
		if got != want {
			t.Errorf("%s: %v, want %v", rule, got, want)
		}
	}
	// 0.3.9 cites 4.0.0, which reads the merchant as new.
	old := engineFor(t, pack.LoadEveryday039)
	d, err = old.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	if c, _ := d.Constraint("counterparty_seen_before"); c.Result != Fail || d.Outcome != Escalate {
		t.Fatalf("0.3.9: %s, outcome %s", c.Result, d.Outcome)
	}
}

func TestAnIncompleteHistoryWithAnEarlierPaymentPasses(t *testing.T) {
	a, h := checkInput(t, "pay-history-incomplete", nil)
	if h.Complete {
		t.Fatal("the input's history is complete")
	}
	d, err := engineFor(t, pack.LoadEveryday0310).CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	if c, _ := d.Constraint("counterparty_seen_before"); c.Result != Pass {
		t.Fatalf("counterparty_seen_before %s %q", c.Result, c.Pending())
	}
	if slices.Contains(d.askedUnevaluated, "counterparty_seen_before") {
		t.Fatalf("asked %v", d.askedUnevaluated)
	}
}

func TestACompleteHistoryWithNoEarlierPaymentFailsAsBefore(t *testing.T) {
	a, h := checkInput(t, "pay-history-empty-complete", nil)
	d, err := engineFor(t, pack.LoadEveryday0310).CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	if c, _ := d.Constraint("counterparty_seen_before"); c.Result != Fail || c.asksUnevaluated {
		t.Fatalf("counterparty_seen_before %s asks=%v", c.Result, c.asksUnevaluated)
	}
	if d.Outcome != Escalate || len(d.askedUnevaluated) != 0 {
		t.Fatalf("outcome %s, asked %v", d.Outcome, d.askedUnevaluated)
	}
}

// A complete history holding an act whose item_ref cannot be read may be
// missing an act (InputHistory.saleUnknown, as live_history.py writes its
// incomplete record), while this port's caps window reads Complete alone and
// is evaluated: counterparty_seen_before then asks on its own, with nothing
// failed, and the report labels it as the engine does.
func TestOnlySeenBeforeAsksWhenTheCapsWindowWasEvaluated(t *testing.T) {
	a, h := checkInput(t, "pay-history-other-merchant", func(env map[string]any) {
		env["history"].([]any)[0].(map[string]any)["item_ref"] = nil
	})
	if !h.Complete || !h.saleUnknown() {
		t.Fatalf("complete=%v saleUnknown=%v", h.Complete, h.saleUnknown())
	}
	e := engineFor(t, pack.LoadEveryday0310)
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	if caps, _ := d.Constraint("caps"); caps.Result != Pass {
		t.Fatalf("caps %s %q", caps.Result, caps.Pending())
	}
	if d.anyFailed() || d.Outcome != Escalate || !slices.Equal(d.askedUnevaluated, []string{"counterparty_seen_before"}) {
		t.Fatalf("failed=%v outcome %s asked %v", d.anyFailed(), d.Outcome, d.askedUnevaluated)
	}
	if r := e.Report(a, d); r.Decision != Escalate || r.Refused {
		t.Fatalf("report %s refused=%v", r.Decision, r.Refused)
	}
}

// With no approver for the class, the same lone unevaluated check is
// refused, for that check and not for caps. The shipped wicket covers only
// money.purchase, which names an approver, so the engine's seen-before
// config is widened to a capped class that names none.
func TestALoneUnevaluatedSeenBeforeWithNoApproverIsRefused(t *testing.T) {
	a, h := checkInput(t, "pay-history-other-merchant", func(env map[string]any) {
		env["history"].([]any)[0].(map[string]any)["item_ref"] = nil
	})
	class := "external_commitment.other" // capped, with no approver_role
	a.ActionClass = &class
	e := engineFor(t, pack.LoadEveryday0310)
	e.seenBefore.actionClasses = append(slices.Clone(e.seenBefore.actionClasses), class)
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	if caps, _ := d.Constraint("caps"); caps.Result == NotApplicable {
		t.Fatalf("caps %s %q: want it evaluated", caps.Result, caps.Pending())
	}
	if d.anyFailed() || d.Outcome != Deny || !slices.Equal(d.askedUnevaluated, []string{"counterparty_seen_before"}) {
		t.Fatalf("failed=%v outcome %s asked %v", d.anyFailed(), d.Outcome, d.askedUnevaluated)
	}
	if r := e.Report(a, d); r.Decision != VerdictNotEvaluable || !r.Refused || r.RefusedFor != RefusedUnevaluated {
		t.Fatalf("report %s refused=%v for %q", r.Decision, r.Refused, r.RefusedFor)
	}
}

// A check held by the record's taxonomy is no longer n/a for its own reason,
// so it asks nothing; the taxonomy refuses it (compareTaxonomy).
func TestATaxonomyHeldSeenBeforeAsksNothing(t *testing.T) {
	a, h := checkInput(t, "pay-history-empty-incomplete", nil)
	other := "1"
	a.TaxonomyVersion = &other
	d, err := engineFor(t, pack.LoadEveryday0310).CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	if c, _ := d.Constraint("counterparty_seen_before"); c.Result != NotApplicable || c.asksUnevaluated {
		t.Fatalf("counterparty_seen_before %s asks=%v", c.Result, c.asksUnevaluated)
	}
	if len(d.askedUnevaluated) != 0 || d.Outcome != Deny {
		t.Fatalf("asked %v outcome %s", d.askedUnevaluated, d.Outcome)
	}
}

// A pending reason alone never asks: task_authority and single_commitment
// set one without asking.
func TestOnlyAMarkedCheckAsksWhenUnevaluated(t *testing.T) {
	cs := []Constraint{notEvaluable("task_authority", "m", "no record")}
	if got := asksUnevaluated(cs); len(got) != 0 {
		t.Fatalf("asked %v", got)
	}
	marked := notEvaluable("counterparty_seen_before", "m", historyIncompleteReason)
	marked.asksUnevaluated = true
	if got := asksUnevaluated(append(cs, marked)); !slices.Equal(got, []string{"counterparty_seen_before"}) {
		t.Fatalf("asked %v", got)
	}
}
