package guard

import (
	"bytes"
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// ASK-DECLARED CHECKS ASK (capsule-engine #113, 8d7bff2): a failing check
// asks an approver when every rule bound to it declares default_disposition
// ASK and the action's class names an approver_role; a NEVER-declared rule
// beside it, a second rule on the same check declaring NEVER, an integrity
// failure (a cited mandate, a same-deal dedupe hit) or a class with no
// approver refuses. Held against the engine side's 48 vectors run through the engine
// at the pinned commit (generate_ask_declared_wickets.py): per case the
// outcome, the disposition decision and verdict class, the failing
// constraints and rules, and the ASK check and gate-selector sets of the
// pack variant the case runs under. A case whose action cites a mandate is
// denied by the engine (verify_before_dispatch); this port has no capsule
// verifier and refuses to decide it (an error, never an allow).
func TestASKDeclaredChecksMatchTheEngine(t *testing.T) {
	var corpus struct {
		PackID string `json:"pack_id"`
		Cases  []struct {
			Name           string            `json:"name"`
			Redeclare      map[string]string `json:"redeclare"`
			RedeclarePfx   string            `json:"redeclare_rule_prefix"`
			AddObligations []struct {
				ID                 string `json:"id"`
				Statement          string `json:"statement"`
				CheckOfRule        string `json:"check_of_rule"`
				DefaultDisposition string `json:"default_disposition"`
			} `json:"add_obligations"`
			Steps []struct {
				Action              json.RawMessage `json:"action"`
				DryRun              bool            `json:"dry_run"`
				TaskAuthorityRecord json.RawMessage `json:"task_authority_record"`
			} `json:"steps"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "ask-declared-wickets-corpus.json"), &corpus); err != nil {
		t.Fatal(err)
	}
	var py struct {
		CapsuleEngineCommit  string `json:"capsule_engine_commit"`
		PackDefinitionDigest string `json:"pack_definition_digest"`
		Decisions            map[string]struct {
			AskWickets         []string `json:"ask_wickets"`
			AskGateSelectors   []string `json:"ask_gate_selectors"`
			Outcome            string   `json:"outcome"`
			Decision           string   `json:"decision"`
			VerdictClass       string   `json:"verdict_class"`
			FailingConstraints []string `json:"failing_constraints"`
			FailingRules       []string `json:"failing_rules"`
		} `json:"decisions"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "ask-declared-wickets-parity.json"), &py); err != nil {
		t.Fatal(err)
	}
	_, shippedPack := everydayEngine(t)
	if py.CapsuleEngineCommit != parity(t, shipped).CapsuleEngineCommit || py.PackDefinitionDigest != shippedPack.DefinitionDigest ||
		corpus.PackID != shippedPack.ID {
		t.Fatalf("vectors at %s for %s %s; the shipped pack is %s %s", py.CapsuleEngineCommit, corpus.PackID,
			py.PackDefinitionDigest, shippedPack.ID, shippedPack.DefinitionDigest)
	}
	if len(corpus.Cases) != 48 || len(py.Decisions) != 48 {
		t.Fatalf("%d cases, %d decisions; the engine side's set is 48", len(corpus.Cases), len(py.Decisions))
	}
	// The engine seals the decision's verdict class (capsule.py); this port
	// seals no decision, so it is held to the engine's mapping here.
	verdictClass := map[string]string{Deny: "blocked", Escalate: "hitl_dispatched"}
	outcomes := map[string]string{}
	for _, c := range corpus.Cases {
		want, ok := py.Decisions[c.Name]
		if !ok {
			t.Fatalf("%s: no engine decision", c.Name)
		}
		p := shippedPack
		p.Rules = slices.Clone(p.Rules)
		for i, r := range p.Rules {
			if d, ok := c.Redeclare[r.ID]; ok {
				p.Rules[i].DefaultDisposition = d
			}
			if c.RedeclarePfx != "" && strings.HasPrefix(r.ID, c.RedeclarePfx+"-") {
				p.Rules[i].DefaultDisposition = "NEVER"
			}
		}
		for _, add := range c.AddObligations {
			// The engine appends the obligation after the pack's own, on the
			// check of the rule it names.
			last := -1
			var of pack.Rule
			for i, r := range p.Rules {
				if r.Check != "" || r.Declared {
					last = i
				}
				if strings.HasPrefix(r.ID, add.CheckOfRule+"-") {
					of = r
				}
			}
			extra := pack.Rule{ID: add.ID, Check: of.Check, Statement: add.Statement, DefaultDisposition: add.DefaultDisposition,
				ForwardVerdict: of.ForwardVerdict, Selector: of.Selector}
			p.Rules = slices.Insert(p.Rules, last+1, extra)
		}
		e, err := NewEngine(p)
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		if got := e.AskWickets(); !slices.Equal(got, want.AskWickets) {
			t.Errorf("%s: ask checks %v, engine %v", c.Name, got, want.AskWickets)
		}
		if got := e.AskGateSelectors(); !slices.Equal(got, want.AskGateSelectors) {
			t.Errorf("%s: ask gate selectors %v, engine %v", c.Name, got, want.AskGateSelectors)
		}
		var ledger []Record
		var a Action
		var d Decision
		refused := false
		for _, step := range c.Steps {
			if a, err = DecodeAction(bytes.NewReader(step.Action)); err != nil {
				t.Fatalf("%s: %v", c.Name, err)
			}
			if d, err = e.CheckWithTaskAuthority(a, ledger, step.TaskAuthorityRecord); err != nil {
				if a.CitedMandateCapsuleID == nil || want.Outcome != Deny || !slices.Contains(want.FailingConstraints, "verify_before_dispatch") {
					t.Fatalf("%s: %v", c.Name, err)
				}
				refused = true
				break
			}
			r := RecordFor(a, d)
			r.DryRun = &step.DryRun
			ledger = append(ledger, r)
		}
		if refused {
			outcomes[c.Name] = refusedUndecided
			continue
		}
		var failingConstraints, failingRules []string
		for _, con := range d.Constraints {
			if con.Result == Fail {
				failingConstraints = append(failingConstraints, con.ID)
			}
		}
		rep := e.Report(a, d)
		for _, r := range rep.Rules {
			if r.Verdict == VerdictFail && r.Rule != referenceRule {
				failingRules = append(failingRules, r.Rule)
			}
		}
		decision := *RecordFor(a, d).Decision
		if d.Outcome != want.Outcome || rep.Decision != want.Outcome || decision != want.Decision || verdictClass[d.Outcome] != want.VerdictClass ||
			!slices.Equal(failingConstraints, want.FailingConstraints) || !slices.Equal(failingRules, want.FailingRules) {
			t.Errorf("%s: Go %s/%s (%s), failing %v %v; engine %s (%s, %s), failing %v %v", c.Name, d.Outcome, rep.Decision, decision,
				failingConstraints, failingRules, want.Outcome, want.Decision, want.VerdictClass, want.FailingConstraints, want.FailingRules)
		}
		outcomes[c.Name] = d.Outcome
	}
	// The cases the ruling turns on, named so a regenerated file that lost
	// one fails here.
	for name, want := range map[string]string{
		"booking-558-80-non-refundable-asks":                        Escalate,
		"booking-558-80-r08-never-refuses":                          Deny,
		"r08-non-refundable-asks":                                   Escalate,
		"r08-non-refundable-beside-credential-refuses":              Deny,
		"r08-non-refundable-citing-missing-mandate-refuses":         refusedUndecided,
		"r08-non-refundable-same-deal-repeat-refuses":               Deny,
		"r08-non-refundable-mixed-binding-refuses":                  Deny,
		"r09-material-terms-changed-asks":                           Escalate,
		"r10-offer-differs-asks":                                    Escalate,
		"r21-channel-change-asks":                                   Escalate,
		"r22-unusual-deposit-asks":                                  Escalate,
		"r07-recurring-charge-asks":                                 Escalate,
		"r20-watched-rail-asks":                                     Escalate,
		"r19-payee-account-changed-asks":                            Escalate,
		"r26-outside-task-asks":                                     Escalate,
		"r26-never-refuses":                                         Deny,
		"r13-new-recipient-no-approver-refuses":                     Escalate,
		"r22-unusual-deposit-same-deal-repeat-refuses":              Deny,
		"r09-material-terms-changed-citing-missing-mandate-refuses": refusedUndecided,
	} {
		if outcomes[name] != want {
			t.Errorf("%s: %q, want %s", name, outcomes[name], want)
		}
	}
}

// An integrity check, or one with its own ask rule, never joins the ASK
// checks whatever its rules declare (ASK_RULE_EXCLUDED_CHECKS at 8d7bff2).
func TestIntegrityChecksNeverAsk(t *testing.T) {
	_, p := everydayEngine(t)
	p.Rules = slices.Clone(p.Rules)
	for i := range p.Rules {
		p.Rules[i].DefaultDisposition = "ASK"
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	for _, check := range slices.Concat(integrityChecks, ownAskRule) {
		if slices.Contains(e.AskWickets(), check) {
			t.Errorf("%s asks when its rules declare ASK", check)
		}
	}
	if !slices.Contains(e.AskWickets(), "credential_pattern") {
		t.Errorf("credential_pattern re-declared ASK does not ask: %v", e.AskWickets())
	}
}
