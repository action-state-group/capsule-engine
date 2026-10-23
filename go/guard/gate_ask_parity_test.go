package guard

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/packs"
)

// GATE RULES DECLARED ASK (capsule-engine #104, c29e738) and TAXONOMY 4
// (#109, 5300e0b): an action_class_gate failure asks an approver only when
// every selector it failed on is bound to obligations that all declare
// default_disposition ASK, and the class names an approver_role; otherwise
// it refuses. Held against the engine side's corpora run through the engine at the
// pack's commit (generate_gate_ask.py, generate_taxonomy4.py): per case the
// outcome, the disposition decision, the failing constraints and rules, and
// the ASK selector set of the pack variant the case runs under (some
// re-declare an obligation). From taxonomy 4 every class an ASK-declared
// gate rule covers has an approver, so the gate corpus's no-approver cases
// now ask too.
func TestGateRulesDeclaredASKMatchTheEngine(t *testing.T) {
	checkGateCorpus(t, "gate-ask", map[string]string{
		"booking-create-asks":                          Escalate,
		"booking-create-r16-never-refuses":             Deny,
		"booking-cancel-no-approver-refuses":           Escalate,
		"data-delete-no-approver-refuses":              Escalate,
		"public-posting-no-approver-refuses":           Escalate,
		"personal-disclosure-no-approver-refuses":      Escalate,
		"personal-disclosure-r12-never-leaves-ask-set": Deny,
		"off-taxonomy-fails-closed":                    Deny,
		"info-query-passes":                            Allow,
		"booking-create-repeat-dedupe-refuses":         Deny,
	})
}

// TAXONOMY 4 (capsule-engine #109, 5300e0b): booking.cancel, data.delete,
// communication.publish and disclosure.personal name account_holder, so
// r17, r18, r15 and r11/r12 ask; re-declared NEVER, repeated (dedupe), or
// shared with a third party (recipient_role) they refuse. The parity file
// also pins the taxonomy the engine ran with to the vendored one. A case
// whose action cites a mandate the ledger does not hold is denied by the
// engine (verify_before_dispatch); this port has no capsule verifier and
// refuses to decide such an action at all (an error, never an allow), which
// the test asserts in its place.
func TestTaxonomy4MatchesTheEngine(t *testing.T) {
	checkTaxonomyPins(t, "taxonomy4")
	checkGateCorpus(t, "taxonomy4", map[string]string{
		"booking-cancel-asks":                           Escalate,
		"booking-cancel-never-refuses":                  Deny,
		"booking-cancel-repeat-dedupe-refuses":          Deny,
		"booking-cancel-missing-mandate-refuses":        refusedUndecided,
		"data-delete-asks":                              Escalate,
		"data-delete-never-refuses":                     Deny,
		"data-delete-repeat-dedupe-refuses":             Deny,
		"data-delete-missing-mandate-refuses":           refusedUndecided,
		"communication-publish-asks":                    Escalate,
		"communication-publish-never-refuses":           Deny,
		"communication-publish-repeat-dedupe-refuses":   Deny,
		"communication-publish-missing-mandate-refuses": refusedUndecided,
		"disclosure-personal-asks":                      Escalate,
		"disclosure-personal-never-refuses":             Deny,
		"disclosure-personal-repeat-dedupe-refuses":     Deny,
		"disclosure-personal-missing-mandate-refuses":   refusedUndecided,
		"disclosure-personal-third-party-refuses":       Deny,
		"booking-create-still-asks":                     Escalate,
		"off-taxonomy-refuses":                          Deny,
	})
}

// TAXONOMY 5 (capsule-engine #114, e5ec2c1): communication.send names
// account_holder (comms.external through its legacy alias), so a message to
// a new recipient fails r13 alone and asks; re-declared NEVER, DO or
// undeclared, beside a credential (r23 NEVER), repeated (dedupe), or citing
// a missing mandate it refuses. The parity file pins the taxonomy the engine
// ran with to the vendored one (from taxonomy 6 on, 6: the corpus is rerun
// at every engine commit the port mirrors).
func TestTaxonomy5MatchesTheEngine(t *testing.T) {
	checkTaxonomyPins(t, "taxonomy5")
	checkGateCorpus(t, "taxonomy5", map[string]string{
		"new-recipient-asks-legacy-alias":         Escalate,
		"new-recipient-asks-canonical":            Escalate,
		"new-recipient-r13-never-refuses":         Deny,
		"new-recipient-r13-do-refuses":            Deny,
		"new-recipient-r13-undeclared-refuses":    Deny,
		"new-recipient-beside-credential-refuses": Deny,
		"new-recipient-repeat-refuses-on-dedupe":  Deny,
		"new-recipient-missing-mandate-refuses":   refusedUndecided,
		"known-recipient-after-real-payment":      Allow,
		"communication-publish-still-asks":        Escalate,
		"off-taxonomy-refuses":                    Deny,
	})
}

// TAXONOMY 6 (capsule-engine #117, 7ae6b95): marketplace.offer,
// marketplace.sale and agreement.accept name account_holder, so a failure
// that asks on a seller class escalates where it denied. the engine side's corpus
// (taxonomy6-corpus.json) runs price_floor/2.0.0, seller.single_commitment
// and promise_never on a GuardEngine configured directly: no shipped pack
// cites them and this port runs none of them. What taxonomy 6 changes is the
// decision on the checks' results, so this holds decide, with the vendored
// table's approver for the case's class, to the engine on the engine's own
// failing checks and ASK set: below the floor alone on each seller class
// asks; beside an integrity failure (single_commitment, promise_never, a
// same-deal repeat), with price_floor not declared ASK, or on a class that
// names no approver it refuses.
func TestTaxonomy6MatchesTheEngine(t *testing.T) {
	checkTaxonomyPins(t, "taxonomy6")
	var corpus struct {
		Cases []struct {
			Name  string `json:"name"`
			Steps []struct {
				Action struct {
					ActionClass string `json:"action_class"`
				} `json:"action"`
			} `json:"steps"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "taxonomy6-corpus.json"), &corpus); err != nil {
		t.Fatal(err)
	}
	var py struct {
		CapsuleEngineCommit string `json:"capsule_engine_commit"`
		Decisions           map[string]struct {
			AskWickets         []string `json:"ask_wickets"`
			Outcome            string   `json:"outcome"`
			FailingConstraints []string `json:"failing_constraints"`
		} `json:"decisions"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "taxonomy6-parity.json"), &py); err != nil {
		t.Fatal(err)
	}
	if py.CapsuleEngineCommit != parity(t, shipped).CapsuleEngineCommit {
		t.Fatalf("taxonomy6: vectors at %s, the shipped pack's parity at %s", py.CapsuleEngineCommit, parity(t, shipped).CapsuleEngineCommit)
	}
	tax, err := engineTaxonomy()
	if err != nil {
		t.Fatal(err)
	}
	named := map[string]string{
		"below-floor-offer-asks":                          Escalate,
		"below-floor-sale-asks":                           Escalate,
		"below-floor-accept-asks":                         Escalate,
		"at-floor-allows":                                 Allow,
		"above-floor-allows":                              Allow,
		"no-opening-allows-n-a":                           Allow,
		"opening-does-not-open-commitment-asks":           Escalate,
		"below-floor-after-acceptance-in-sale-refuses":    Deny,
		"below-floor-never-representation-refuses":        Deny,
		"below-floor-repeat-same-deal-refuses":            Deny,
		"below-floor-not-declared-ask-refuses":            Deny,
		"below-floor-on-a-class-with-no-approver-refuses": Deny,
	}
	if len(corpus.Cases) != len(py.Decisions) || len(corpus.Cases) != len(named) {
		t.Fatalf("taxonomy6: %d cases, %d decisions, %d named", len(corpus.Cases), len(py.Decisions), len(named))
	}
	for _, c := range corpus.Cases {
		want, ok := py.Decisions[c.Name]
		if !ok {
			t.Fatalf("%s: no engine decision", c.Name)
		}
		var cs []Constraint
		for _, id := range want.FailingConstraints {
			cs = append(cs, Constraint{ID: id, Result: Fail})
		}
		asks := map[string]bool{}
		for _, id := range want.AskWickets {
			asks[id] = true
		}
		_, approver := tax.approver(c.Steps[len(c.Steps)-1].Action.ActionClass)
		got := decide(cs, approver, false, asks)
		if got != want.Outcome || got != named[c.Name] {
			t.Errorf("%s: Go %s, engine %s, want %s", c.Name, got, want.Outcome, named[c.Name])
		}
	}
}

// Before taxonomy 6 the seller classes named no approver, so the same
// below-floor failure refused: the three cases that ask under 6 deny under
// the v5 table (v6 with those three approver_role values and the version
// put back).
func TestASellerClassFailureRefusedUnderTaxonomy5(t *testing.T) {
	v5 := string(packs.ActionTaxonomy)
	v5 = strings.Replace(v5, `"taxonomy_version": "6"`, `"taxonomy_version": "5"`, 1)
	for _, class := range []string{"agreement.accept", "marketplace.offer", "marketplace.sale"} {
		row := `{"name": "` + class + `", "trigger_class": "COMMIT", "consequential": true, "fail_open_allowed": false, "approver_role": `
		if !strings.Contains(v5, row+`"account_holder"`) {
			t.Fatalf("%s: no account_holder row in the vendored table", class)
		}
		v5 = strings.Replace(v5, row+`"account_holder"`, row+"null", 1)
	}
	sum := sha256.Sum256([]byte(v5))
	if got := hex.EncodeToString(sum[:]); got != "7d31e892cdea7352d0485c89430dba028bf02ce91fb0e932f4752c1f4066022f" {
		t.Fatalf("rebuilt v5 sha256 %s, not e5ec2c1's", got)
	}
	tax, err := loadTaxonomy([]byte(v5))
	if err != nil {
		t.Fatal(err)
	}
	floor := []Constraint{{ID: "price_floor", Result: Fail}}
	for _, class := range []string{"agreement.accept", "marketplace.offer", "marketplace.sale"} {
		_, approver := tax.approver(class)
		if got := decide(floor, approver, false, map[string]bool{"price_floor": true}); got != Deny {
			t.Errorf("%s under taxonomy 5: %s, want deny", class, got)
		}
	}
}

// checkTaxonomyPins holds the taxonomy <name>-parity.json says the engine ran
// with to the vendored table: version, JCS digest and file sha256.
func checkTaxonomyPins(t *testing.T, name string) {
	t.Helper()
	var pins struct {
		Version    string `json:"taxonomy_version"`
		JCS        string `json:"taxonomy_jcs_digest"`
		FileSHA256 string `json:"taxonomy_file_sha256"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", name+"-parity.json"), &pins); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(packs.ActionTaxonomy)
	v, err := canon.FromJSON(packs.ActionTaxonomy)
	if err != nil {
		t.Fatal(err)
	}
	jcs, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	e, _ := everydayEngine(t)
	if pins.Version != e.tax.version || pins.JCS != jcs || pins.FileSHA256 != hex.EncodeToString(sum[:]) {
		t.Fatalf("%s: the engine ran taxonomy %s %s %s; vendored %s %s %x", name, pins.Version, pins.JCS, pins.FileSHA256, e.tax.version, jcs, sum)
	}
}

// refusedUndecided is the outcome recorded for a case this port refuses to
// decide (an action citing a mandate: no capsule verifier here).
const refusedUndecided = "refused, undecided"

// checkGateCorpus runs <name>-corpus.json against <name>-parity.json, then
// holds the named cases to their outcomes.
func checkGateCorpus(t *testing.T, name string, named map[string]string) {
	t.Helper()
	var corpus struct {
		PackID string `json:"pack_id"`
		Cases  []struct {
			Name      string            `json:"name"`
			Redeclare map[string]string `json:"redeclare"`
			Steps     []struct {
				Action json.RawMessage `json:"action"`
				DryRun bool            `json:"dry_run"`
			} `json:"steps"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", name+"-corpus.json"), &corpus); err != nil {
		t.Fatal(err)
	}
	var py struct {
		CapsuleEngineCommit  string `json:"capsule_engine_commit"`
		PackDefinitionDigest string `json:"pack_definition_digest"`
		Decisions            map[string]struct {
			AskGateSelectors   []string `json:"ask_gate_selectors"`
			Outcome            string   `json:"outcome"`
			Decision           string   `json:"decision"`
			FailingConstraints []string `json:"failing_constraints"`
			FailingRules       []string `json:"failing_rules"`
		} `json:"decisions"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", name+"-parity.json"), &py); err != nil {
		t.Fatal(err)
	}
	_, shippedPack := everydayEngine(t)
	if py.CapsuleEngineCommit != parity(t, shipped).CapsuleEngineCommit || py.PackDefinitionDigest != shippedPack.DefinitionDigest ||
		corpus.PackID != shippedPack.ID {
		t.Fatalf("%s: vectors at %s for %s %s; the shipped pack is %s %s", name, py.CapsuleEngineCommit, corpus.PackID,
			py.PackDefinitionDigest, shippedPack.ID, shippedPack.DefinitionDigest)
	}
	if len(corpus.Cases) != len(py.Decisions) || len(corpus.Cases) != len(named) {
		t.Fatalf("%s: %d cases, %d decisions, %d named", name, len(corpus.Cases), len(py.Decisions), len(named))
	}
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
		}
		e, err := NewEngine(p)
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
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
			if d, err = e.Check(a, ledger); err != nil {
				// Only an action citing a mandate is refused, and only where
				// the engine denies it.
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
		if d.Outcome != want.Outcome || rep.Decision != want.Outcome || decision != want.Decision ||
			!slices.Equal(failingConstraints, want.FailingConstraints) || !slices.Equal(failingRules, want.FailingRules) {
			t.Errorf("%s: Go %s/%s (%s), failing %v %v; engine %s (%s), failing %v %v", c.Name, d.Outcome, rep.Decision, decision,
				failingConstraints, failingRules, want.Outcome, want.Decision, want.FailingConstraints, want.FailingRules)
		}
		outcomes[c.Name] = d.Outcome
	}
	// The cases each corpus exists for, named so a regenerated file that
	// lost one fails here.
	for c, want := range named {
		if outcomes[c] != want {
			t.Errorf("%s: %q, want %s", c, outcomes[c], want)
		}
	}
}

// A gate that failed closed (a class with no taxonomy row) names no
// selector and never counts as asking (_gate_failures_ask: "a failure
// naming no selector never asks"), while a booking's gate failure does.
// Through the decision this cannot show: a class with no row has no
// approver, so it denies either way.
func TestAFailClosedGateNeverAsks(t *testing.T) {
	e, _ := everydayEngine(t)
	for class, want := range map[string]bool{"not.a.class": false, "booking.create": true} {
		a := Action{Verb: "act", Operator: "op", Developer: "dev", ActionClass: strp(class), ActionID: "act/" + class,
			ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z"}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatal(err)
		}
		if g, _ := d.Constraint("action_class_gate"); g.Result != Fail {
			t.Fatalf("%s: gate %s", class, g.Result)
		}
		if got := e.gateFailuresAsk(d.Constraints); got != want {
			t.Errorf("%s: gate failure asks %v, want %v", class, got, want)
		}
	}
}
