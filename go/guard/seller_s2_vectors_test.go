package guard

import (
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// s2Corpus is one of testdata/seller-s2-vectors' corpora: steps run in
// order on one ledger, each checked with the records it names from Records
// (generate_s2_decisions.py, run against capsule-engine ba76a97, the seller
// pack's merge).
type s2Corpus struct {
	Records map[string]json.RawMessage `json:"records"`
	Steps   []struct {
		Name                string          `json:"name"`
		DryRun              bool            `json:"dry_run"`
		Action              json.RawMessage `json:"action"`
		TaskAuthorityRecord string          `json:"task_authority_record"`
		AuthorizationRecord string          `json:"authorization_record"`
	} `json:"steps"`
}

type s2Decisions struct {
	CapsuleEngineCommit string `json:"capsule_engine_commit"`
	Pack                struct {
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Decisions map[string]struct {
		Outcome         string             `json:"outcome"`
		Results         map[string]string  `json:"results"`
		EvidenceDigests map[string]*string `json:"evidence_digests"`
	} `json:"decisions"`
}

// sellerPackCommit is capsule-engine #122's merge, where asg/seller/0.1.0
// and the checks it cites were read for this port.
const sellerPackCommit = "ba76a97a0fb5b41333b1b76a489060221e51ea45"

// runS2Vectors runs corpus name through the seller engine and holds it to
// the engine's decisions: every check this port runs gives the engine's
// result on every step, each of checks also its evidence digest, in full;
// and where no rule is left not yet run, the outcome is the engine's. It
// returns each step's report, for the no-echo tests.
func runS2Vectors(t *testing.T, name string, checks ...string) map[string]CheckReport {
	t.Helper()
	e, p := sellerEngine(t)
	return runS2VectorsOn(t, e, p, filepath.Join("testdata", "seller-s2-vectors"), sellerPackCommit, name, checks...)
}

// runS2VectorsOn is runS2Vectors for e running pack p, held to the engine's
// decisions in decisionsDir, written at commit, on the vendored corpus.
func runS2VectorsOn(t *testing.T, e *Engine, p pack.Pack, decisionsDir, commit, name string, checks ...string) map[string]CheckReport {
	t.Helper()
	corpus := decodeJSON[s2Corpus](t, readFile(t, "testdata", "seller-s2-vectors", name+"-corpus.json"))
	py := decodeJSON[s2Decisions](t, readFile(t, decisionsDir, name+"-decisions.json"))
	if py.CapsuleEngineCommit != commit || py.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors from %s for %s; the port reads %s at %s", py.CapsuleEngineCommit, py.Pack.DefinitionDigest, commit, p.DefinitionDigest)
	}
	reports := map[string]CheckReport{}
	var ledger []Record
	for _, step := range corpus.Steps {
		want, ok := py.Decisions[step.Name]
		if !ok {
			t.Fatalf("%s: no engine decision", step.Name)
		}
		a, err := decodeAction(step.Action, true)
		if err != nil {
			t.Fatalf("%s: %v", step.Name, err)
		}
		d, err := e.CheckWithRecords(a, MemoryHistory(ledger), corpus.Records[step.TaskAuthorityRecord], corpus.Records[step.AuthorizationRecord])
		if err != nil {
			t.Fatalf("%s: %v", step.Name, err)
		}
		rec := RecordFor(a, Decision{Outcome: want.Outcome})
		if step.DryRun {
			rec.DryRun = ptr(true)
		}
		ledger = append(ledger, rec)
		reports[step.Name] = e.Report(a, d)
		pending := false
		for _, c := range d.Constraints {
			if c.Pending() == NotYetRun {
				pending = true
				continue
			}
			if engine := want.Results[c.ID]; string(c.Result) != engine {
				t.Errorf("%s: %s Go %s, engine %s", step.Name, c.ID, c.Result, engine)
			}
			if !slices.Contains(checks, c.ID) {
				continue
			}
			got, digestable, err := c.EvidenceDigest()
			if err != nil || !digestable {
				t.Errorf("%s: %s evidence not rebuilt: %v", step.Name, c.ID, err)
				continue
			}
			if engine := want.EvidenceDigests[c.ID]; engine == nil || got != *engine {
				t.Errorf("%s: %s evidence digest Go %s, engine %v", step.Name, c.ID, got, deref(engine))
			}
		}
		if !pending && d.Outcome != want.Outcome {
			t.Errorf("%s: outcome Go %s, engine %s", step.Name, d.Outcome, want.Outcome)
		}
		for _, check := range checks {
			if c, ok := d.Constraint(check); !ok || c.Pending() == NotYetRun {
				t.Errorf("%s: %s did not run", step.Name, check)
			}
		}
	}
	if len(reports) != len(py.Decisions) {
		t.Errorf("ran %d steps, the engine decided %d", len(reports), len(py.Decisions))
	}
	return reports
}

func deref(s *string) string {
	if s == nil {
		return "<none>"
	}
	return *s
}

// assertNoEcho fails when a report, as the plugin prints it, carries any of
// secrets: an identifier or a value no output may repeat.
func assertNoEcho(t *testing.T, step string, rep CheckReport, secrets ...string) {
	t.Helper()
	out, err := json.Marshal(rep)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range secrets {
		if s != "" && strings.Contains(string(out), s) {
			t.Errorf("%s: the report echoes %q", step, s)
		}
	}
}

// CROSS-IMPLEMENTATION: seller.destination_rail/1.0.0, the allow-list, on
// the engine's decisions: card and p2p pass, cheque and an empty rail fail
// as not allowed, no rail is n/a in scope, an offer is out of scope.
func TestSellerDestinationRailAgreesWithTheEngine(t *testing.T) {
	reports := runS2Vectors(t, "rail", "destination_rail")
	for step, rep := range reports {
		assertNoEcho(t, step, rep, strings.Repeat("1", 64))
	}
	for _, r := range reports["sale-on-cheque"].Rules {
		if r.Rule == "s07-payment-rail-the-user-allows" && (r.Verdict != VerdictFail || r.Reason != "rail cheque is not one of the allowed rails card, p2p") {
			t.Errorf("s07 on cheque: %s %q", r.Verdict, r.Reason)
		}
	}
}

// stepSecrets are the identifiers a corpus step carries that no report may
// repeat: its target, and the digests it cites.
func stepSecrets(t *testing.T, raw json.RawMessage) []string {
	t.Helper()
	var a struct {
		Target           string `json:"target"`
		AuthorizedBy     string `json:"authorized_by"`
		TaskAuthorityRef string `json:"task_authority_ref"`
	}
	if err := json.Unmarshal(raw, &a); err != nil {
		t.Fatal(err)
	}
	return []string{a.Target, a.AuthorizedBy, a.TaskAuthorityRef}
}

// noEchoInCorpus holds every step's report to stepSecrets, and to more.
func noEchoInCorpus(t *testing.T, name string, reports map[string]CheckReport, more ...string) {
	t.Helper()
	corpus := decodeJSON[s2Corpus](t, readFile(t, "testdata", "seller-s2-vectors", name+"-corpus.json"))
	for _, step := range corpus.Steps {
		assertNoEcho(t, step.Name, reports[step.Name], append(stepSecrets(t, step.Action), more...)...)
	}
}

// CROSS-IMPLEMENTATION: promise_never/1.0.0 on the engine's decisions. An
// authenticity statement fails whatever it cites (a bound approval, a task
// authority listing the class) and the decision is refused, never asked; a
// class outside the closed set fails; no class is n/a in scope; another
// action class is out of scope. A class outside the closed set is never
// repeated in the output, nor is any target or cited digest.
func TestPromiseNeverAgreesWithTheEngine(t *testing.T) {
	reports := runS2Vectors(t, "promise-never", "promise_never")
	noEchoInCorpus(t, "promise-never", reports, "Authenticity")
	for step, want := range map[string]string{
		"authenticity":                   "authenticity is a class of statement the agent never makes; no approval clears it, only a change to the rule set",
		"a-class-outside-the-closed-set": "the statement's representation_class is not one of the classes the rule knows",
	} {
		for _, r := range reports[step].Rules {
			if r.Rule == "s10-statement-never-made" && (r.Verdict != VerdictFail || r.Reason != want) {
				t.Errorf("%s: s10 %s %q, want fail %q", step, r.Verdict, r.Reason, want)
			}
		}
		if reports[step].Decision != Deny {
			t.Errorf("%s: %s, want deny", step, reports[step].Decision)
		}
	}
}

// A deal record's body carries no representation_class or authorized_by the
// engine reads: its deal bridge (report/replay.py _bridge_deal_check at
// ba76a97) takes neither, so here they are listed as ignored and the
// statement checks see no class.
func TestDealBodyStatementFieldsAreNotRead(t *testing.T) {
	raw := sealedRecord(t, func(body jsonMembers) {
		body["representation_class"] = j(`"authenticity"`)
		body["authorized_by"] = j(`"` + strings.Repeat("f", 64) + `"`)
	})
	a, err := DecodeRecord(raw, "")
	if err != nil {
		t.Fatal(err)
	}
	if a.RepresentationClass != nil || a.AuthorizedBy != nil {
		t.Fatalf("bridged: %v %v", a.RepresentationClass, a.AuthorizedBy)
	}
	for _, f := range []string{"authorized_by", "representation_class"} {
		if !slices.Contains(a.extra, f) {
			t.Errorf("%s not listed as ignored: %v", f, a.extra)
		}
	}
}

// CROSS-IMPLEMENTATION: promise_requires_approval/1.0.0 on the engine's
// decisions. A class needing approval passes inside the bound task
// authority, or with a bound approval for that class; it fails citing
// nothing, citing an approval not supplied, with a record the reference
// does not bind, an approval for another class, an approval naming no one
// class, an approval with no JSON digest (a float, an integer past 2^53),
// a task authority not bound, not supplied, or not listing the class as a
// list. A class needing none passes; a class outside the set fails; none is
// n/a in scope. A failure asks, and its reason repeats no digest and no
// class the approval names.
func TestPromiseRequiresApprovalAgreesWithTheEngine(t *testing.T) {
	reports := runS2Vectors(t, "promise-requires-approval", "promise_requires_approval")
	noEchoInCorpus(t, "promise-requires-approval", reports, "guarantee")
	for step, want := range map[string]string{
		"delivery-date-citing-nothing":                     "a delivery_date statement is outside the task authority and needs a bound approval: the action cites no approval (authorized_by)",
		"delivery-date-with-an-approval-for-another-class": "a delivery_date statement is outside the task authority and needs a bound approval: the approval is for another class",
		"delivery-date-approval-with-a-float":              "a delivery_date statement is outside the task authority and needs a bound approval: the supplied approval record has no digest",
	} {
		for _, r := range reports[step].Rules {
			if r.Rule == "s04-statement-that-needs-the-users-word" && (r.Verdict != VerdictFail || r.Reason != want) {
				t.Errorf("%s: s04 %s %q, want fail %q", step, r.Verdict, r.Reason, want)
			}
		}
	}
}

// CROSS-IMPLEMENTATION: required_disclosure/1.0.0 over its fold
// disclosure.class_seen/1.0.0, on one ledger, on the engine's decisions. An
// offer passes only once this operator's accepted, real statement of the
// required class to that counterparty is on the ledger: not before it, not
// after a dry run, a refused statement, another operator's statement or a
// statement of another class. No target is n/a in scope; a statement is out
// of scope. No report repeats a target.
func TestRequiredDisclosureAgreesWithTheEngine(t *testing.T) {
	reports := runS2Vectors(t, "required-disclosure", "required_disclosure")
	noEchoInCorpus(t, "required-disclosure", reports)
	const want = "no accepted prior statement of condition to this counterparty: make the disclosure first or change the task authority; an approval does not waive it"
	for _, r := range reports["offer-to-t2-after-a-dry-run-statement"].Rules {
		if r.Rule == "s05-required-statement-made-first" && (r.Verdict != VerdictFail || r.Reason != want) {
			t.Errorf("s05: %s %q, want fail %q", r.Verdict, r.Reason, want)
		}
	}
}

// A ledger capsule's asg_payload.representation_class is what the fold
// counts: DecodeCapsule reads it, and the record a decision leaves carries the
// action's.
func TestLedgerCarriesTheStatementClass(t *testing.T) {
	recs, err := readCapsules(strings.NewReader(`{"capsule_id":"c1","operator":"op","asg_payload":{"target":"cp/a","representation_class":"condition"}}` + "\n"))
	if err != nil {
		t.Fatal(err)
	}
	if got := recs[0].RepresentationClass; got == nil || *got != "condition" {
		t.Fatalf("read %v", got)
	}
	cls := "price"
	if got := RecordFor(Action{RepresentationClass: &cls}, Decision{Outcome: Allow}).RepresentationClass; got == nil || *got != cls {
		t.Fatalf("RecordFor: %v", got)
	}
}
