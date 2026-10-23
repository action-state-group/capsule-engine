package guard

import (
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// realSellerDir is capsule-engine's real seller fixture (#131, f256abc):
// three bundles and five check inputs capsulectl 831afeeb wrote, and the
// engine's decisions on them (testdata/real-seller-131).
var realSellerDir = filepath.Join("testdata", "real-seller-131", "real-seller")

type realSellerDecision struct {
	Source            string            `json:"source"`
	CapsuleID         string            `json:"capsule_id"`
	Verb              string            `json:"verb"`
	ActionClass       *string           `json:"action_class"`
	StatedAmountMinor *int64            `json:"stated_amount_minor"`
	ProposalAt        *string           `json:"proposal_at"`
	Outcome           string            `json:"outcome"`
	FailingChecks     []string          `json:"failing_checks"`
	Rules             map[string]string `json:"rules"`
}

type realSellerExpected struct {
	Pack struct {
		PackID           string `json:"pack_id"`
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Replayed  []string             `json:"replayed"`
	Decisions []realSellerDecision `json:"decisions"`
}

// realSellerGot is one decision in the expected document's terms.
func realSellerGot(source, capsuleID string, a Action, d Decision, p pack.Pack) realSellerDecision {
	got := realSellerDecision{Source: source, CapsuleID: capsuleID, Verb: a.Verb, ActionClass: a.ActionClass,
		StatedAmountMinor: a.StatedAmountMinor, ProposalAt: a.ProposalAt, Outcome: d.Outcome, FailingChecks: []string{},
		Rules: map[string]string{}}
	for _, c := range d.Constraints {
		if c.Result == Fail {
			got.FailingChecks = append(got.FailingChecks, c.ID)
		}
	}
	slices.Sort(got.FailingChecks)
	for _, r := range p.Rules {
		if c, ok := d.Constraint(r.Check); ok {
			got.Rules[r.ID] = string(c.Result)
		}
	}
	return got
}

// show is a decision as one comparable line.
func (d realSellerDecision) show() string {
	b, err := json.Marshal(d)
	if err != nil {
		return err.Error()
	}
	return string(b)
}

// CROSS-IMPLEMENTATION on REAL records (capsule-engine #131, f256abc): the
// engine's decisions on capsulectl's real seller fixture, replayed and live,
// under the seller pack. The engine's expectations name asg/seller/0.1.2,
// which runs here, and so does 0.1.1, 0.1.2 with caps/1.0.0 cited (it limits
// money.transfer, which no record here states): both give every expectation.
//
// The replay reads every record of the three bundles in order, each through
// DecodeRecord as check --record reads it from its bundle: a record that
// states no act (an x-deal-v0 record other than a check, or a typed
// task-authority, evaluation, approval, record, outcome or report) gets no
// decision, and a typed commit is dated by the offer its buyer accepted. The
// ledger is the engine's own decisions, as dry runs. The live path reads
// each check input as check --emit does, on an empty ledger, with its
// task-authority record and floor opening, as the engine's own live test
// does. Every decision's verb, class, stated amount, proposal_at, outcome,
// failing checks and every rule's result are the engine's.
func TestRealSellerRecordsAgreeWithTheEngine(t *testing.T) {
	want := decodeJSON[realSellerExpected](t, readFile(t, realSellerDir, "expected_decisions.json"))
	if want.Pack.PackID != "asg/seller/0.1.2" || want.Pack.DefinitionDigest != "e599c5aff8d4171d5e58cae33fdc7022e4ee3762d1a884bb6e6098aca532a353" {
		t.Fatalf("expectations under %s %s", want.Pack.PackID, want.Pack.DefinitionDigest)
	}
	e011, p011 := seller011Engine(t)
	e012, p012 := seller012Engine(t)
	for _, run := range []struct {
		name string
		e    *Engine
		p    pack.Pack
	}{{"0.1.1", e011, p011}, {"0.1.2", e012, p012}} {
		diffs := realSellerDifferences(t, realSellerRun(t, run.e, run.p, want), want.Decisions)
		if !slices.Equal(diffs, realSellerPre137Differences) {
			t.Errorf("%s: Go and the engine differ at\n%s\npinned\n%s", run.name, strings.Join(diffs, "\n"),
				strings.Join(realSellerPre137Differences, "\n"))
		}
	}
}

// realSellerPre137Differences are where Go, which mirrors capsule-engine #137
// (a145f95), differs from these expectations, written by the engine at
// f256abc, before it: a replay never has item_ref, so single_commitment now
// refuses every replayed commitment, as the engine at 08cd96d does
// (TestSeller013RealSellerAgreesWithTheEngine, with no difference).
var realSellerPre137Differences = []string{
	`#1 replay outcome Go "deny" engine "allow"`,
	`#2 replay outcome Go "deny" engine "allow"`,
	`#5 replay outcome Go "deny" engine "escalate"`,
}

// realSellerRun is the replay of want.Replayed, then the live decision on
// each check input, as TestRealSellerRecordsAgreeWithTheEngine describes,
// under e running p. A claim the agent made writes its statement record to
// the replay's ledger when it is reached (StatementOf; once), as the
// engine's replay writes it since #139: only required_disclosure/1.1.0
// reads it.
func realSellerRun(t *testing.T, e *Engine, p pack.Pack, want realSellerExpected) []realSellerDecision {
	t.Helper()
	var got []realSellerDecision
	var ledger []Record
	stated := map[string]bool{}
	next := 0
	for _, name := range want.Replayed {
		raw := readFile(t, realSellerDir, name)
		var bundle struct {
			Records     []json.RawMessage `json:"records"`
			Disclosures map[string]struct {
				AgentInput json.RawMessage `json:"agent_input"`
			} `json:"disclosures"`
		}
		if err := json.Unmarshal(raw, &bundle); err != nil {
			t.Fatal(err)
		}
		for _, capsule := range bundle.Records {
			var r struct {
				CapsuleID string `json:"capsule_id"`
			}
			if err := json.Unmarshal(capsule, &r); err != nil {
				t.Fatal(err)
			}
			shown, ok := bundle.Disclosures[r.CapsuleID]
			if !ok {
				continue
			}
			if s, ok := StatementOf(capsule, shown.AgentInput); ok {
				if !stated[s.CapsuleID] {
					stated[s.CapsuleID] = true
					ledger = append(ledger, s)
				}
				continue
			}
			var typed struct {
				Type string `json:"type"`
			}
			if err := json.Unmarshal(shown.AgentInput, &typed); err != nil {
				t.Fatal(err)
			}
			// A withheld record, and a deal report (which the engine
			// gives no decision and check --record refuses as no deal
			// record), are never decided.
			if typed.Type == "deal_report" {
				continue
			}
			a, err := DecodeRecord(raw, r.CapsuleID)
			if err != nil {
				t.Fatalf("%s %s: %v", name, r.CapsuleID, err)
			}
			if a.statesNoAct {
				continue
			}
			d, err := e.Check(a, ledger)
			if err != nil {
				t.Fatalf("%s %s: %v", name, r.CapsuleID, err)
			}
			got = append(got, realSellerGot("replay", r.CapsuleID, a, d, p))
			engineOutcome := d.Outcome
			if next < len(want.Decisions) {
				engineOutcome = want.Decisions[next].Outcome
			}
			next++
			rec := RecordFor(a, Decision{Outcome: engineOutcome})
			rec.CapsuleID = r.CapsuleID
			rec.DryRun = ptr(true)
			ledger = append(ledger, rec)
		}
	}
	for _, name := range []string{"a-offer-1900", "a-commit-1900", "b-offer-1500", "c-offer-1600", "c-commit-1600"} {
		var in struct {
			Record                  json.RawMessage `json:"record"`
			TaskAuthorityRecord     json.RawMessage `json:"task_authority_record"`
			CommercialBoundsOpening json.RawMessage `json:"commercial_bounds_opening"`
			ItemRef                 json.RawMessage `json:"item_ref"`
			PartyRole               json.RawMessage `json:"party_role"`
		}
		if err := json.Unmarshal(readFile(t, realSellerDir, "check-inputs", name+".json"), &in); err != nil {
			t.Fatal(err)
		}
		a, err := DecodeCheckInputRecord(in.Record)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		a = WithPartyRole(WithItemRef(a, in.ItemRef), in.PartyRole)
		d, err := e.checkWith(a, nil, checkInputs{taskAuthority: in.TaskAuthorityRecord, opening: in.CommercialBoundsOpening})
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		var id struct {
			CapsuleID string `json:"capsule_id"`
		}
		if err := json.Unmarshal(in.Record, &id); err != nil {
			t.Fatal(err)
		}
		got = append(got, realSellerGot("check-inputs/"+name+".json", id.CapsuleID, a, d, p))
	}
	return got
}
