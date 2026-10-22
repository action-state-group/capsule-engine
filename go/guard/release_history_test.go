package guard

import (
	"encoding/json"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// releaseHistoryVectors are seller.release_on_acceptance/1.2.0's
// differential vectors (PROVENANCE.json): the release-on-acceptance
// fixture's check inputs (capsulectl 6210b93), as written and each with one
// edit, decided live by capsule-engine 0c93a58's check_release_on_acceptance
// under the 1.2.0 definition, and the engine's replay of the fixture's sale
// bundle under asg/seller/0.1.5.
var releaseHistoryVectors = filepath.Join("testdata", "release-history-vectors")

// releaseWicket is seller.release_on_acceptance/1.2.0 as asg/seller/0.1.5
// vendors it.
const releaseWicket = "seller.release_on_acceptance/1.2.0"

// release12Engine is the seller 0.1.4 engine with s13 decided under
// seller.release_on_acceptance/1.2.0's config, read from the vendored
// asg/seller/0.1.5 definition. 0.1.5 itself does not load into the guard
// until every definition it cites is ported.
func release12Engine(t *testing.T) *Engine {
	t.Helper()
	e, _ := seller014Engine(t)
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.ID == releaseWicket })
	if i < 0 {
		t.Fatalf("%s cites no %s", p.ID, releaseWicket)
	}
	if e.release, err = decodeConfig[releaseConfig](p.Wickets[i]); err != nil {
		t.Fatal(err)
	}
	if e.release.Binding != sameDealSameBuyer {
		t.Fatalf("%s binding %q", releaseWicket, e.release.Binding)
	}
	return e
}

type releaseVector struct {
	Input     string          `json:"input"`
	CapsuleID string          `json:"capsule_id"`
	Check     string          `json:"check"`
	Result    string          `json:"result"`
	Reason    string          `json:"reason"`
	Evidence  json.RawMessage `json:"evidence"`
}

// releaseCell is a decision's result, its reason where Go keeps one (a
// fail's note, a platform's out-of-scope note) and its evidence digest.
func releaseCell(t *testing.T, c Constraint) releaseOutcome {
	t.Helper()
	digest, _, err := c.EvidenceDigest()
	if err != nil {
		t.Fatal(err)
	}
	out := releaseOutcome{Result: string(c.Result), Digest: digest}
	switch {
	case c.Result == Fail:
		out.Reason = c.failNote
	case c.Result == NotApplicable:
		out.Reason = c.scopeNote
	}
	return out
}

// engineCell is the engine's outcome as releaseCell reads Go's: a pass
// keeps no reason in Go, and an out-of-scope n/a only the platform's.
func engineCell(t *testing.T, v releaseVector) releaseOutcome {
	t.Helper()
	o := releaseOutcomeOf(t, v.Result, v.Reason, v.Evidence)
	if v.Result == "pass" || v.Reason == "the rule is not configured for this action class" {
		o.Reason = ""
	}
	return o
}

// CROSS-IMPLEMENTATION: every live vector, decided as CheckWithHistory
// decides a check input (its history the ledger), with NO known difference
// from the engine: the result, the reason and the evidence.
func TestReleaseOnAcceptance12AgreesWithTheEngineLive(t *testing.T) {
	v := decodeJSON[struct {
		Definition       string          `json:"definition"`
		DefinitionDigest string          `json:"definition_digest"`
		Vectors          []releaseVector `json:"vectors"`
	}](t, readFile(t, releaseHistoryVectors, "expected.json"))
	if v.Definition != releaseWicket || v.DefinitionDigest != portedDefinitions[releaseWicket] {
		t.Fatalf("vectors under %s %s, ported %s", v.Definition, v.DefinitionDigest, portedDefinitions[releaseWicket])
	}
	e := release12Engine(t)
	results := map[string]int{}
	for _, want := range v.Vectors {
		a, h, err := DecodeCheckInput(readFile(t, releaseHistoryVectors, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		c, err := e.constraintFor("release_on_acceptance", a, h.ledger(), checkInputs{history: true, historyIncomplete: h.saleUnknown()})
		if err != nil {
			t.Fatal(err)
		}
		if got, engine := releaseCell(t, c), engineCell(t, want); got != engine {
			t.Errorf("%s: Go %+v, engine %+v", want.Input, got, engine)
		}
		results[want.Result]++
	}
	if len(v.Vectors) != 36 || results["pass"] == 0 || results["fail"] == 0 || results["n/a"] == 0 {
		t.Errorf("%d vectors, %v", len(v.Vectors), results)
	}
}

// standaloneShare is the capsule id of a share of the release-on-acceptance
// fixture (one of its check inputs).
func standaloneShare(t *testing.T, check string) string {
	t.Helper()
	return standaloneCapsule(t, filepath.Join("check-inputs", check+".json"))
}

// CROSS-IMPLEMENTATION: the replay of the fixture's sale bundle, each check
// as CheckSale decides it (the sale's history cut to the live window), with
// NO known difference from the engine's replay under asg/seller/0.1.5: A's
// phone share passes, B's refuses, the pickup area passes to both.
func TestReleaseOnAcceptance12AgreesWithTheEngineOnReplay(t *testing.T) {
	v := decodeJSON[struct {
		PackID           string          `json:"pack_id"`
		DefinitionDigest string          `json:"definition_digest"`
		Vectors          []releaseVector `json:"vectors"`
	}](t, readFile(t, releaseHistoryVectors, "expected_replay.json"))
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	if v.PackID != p.ID || v.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("replayed under %s %s, vendored %s %s", v.PackID, v.DefinitionDigest, p.ID, p.DefinitionDigest)
	}
	e := release12Engine(t)
	sale := readStandaloneSale(t, readFile(t, standaloneVectors, "sale.bundle.json"))
	for _, want := range v.Vectors {
		if id := standaloneShare(t, want.Check); id != want.CapsuleID {
			t.Fatalf("%s: capsule %s, vectors %s", want.Check, id, want.CapsuleID)
		}
		a, h, err := sale.Check(want.CapsuleID)
		if err != nil {
			t.Fatal(err)
		}
		c, err := e.constraintFor("release_on_acceptance", a, nil, checkInputs{sale: &h})
		if err != nil {
			t.Fatal(err)
		}
		if got, engine := releaseCell(t, c), engineCell(t, want); got != engine {
			t.Errorf("%s: Go %+v, engine %+v", want.Check, got, engine)
		}
	}
}

// The replay gives 1.2.0 the acts of the last 31 days before the share, as
// a live check is given (capsule-engine #206's window test: A's commit 1 s
// inside, exactly at, and 1 s outside 31 days before A's phone share; s13
// passes, passes, then fails). Every record of both threads sealed before
// the share is moved back by the same amount, so their order holds.
func TestTheReplayGivesTheShareTheLiveWindow(t *testing.T) {
	e := release12Engine(t)
	share := standaloneShare(t, "a-share-phone")
	commit := commitAct(t)
	for _, c := range []struct {
		name   string
		offset time.Duration
		want   Result
	}{
		{"1 s inside", -time.Second, Pass},
		{"exactly at", 0, Pass},
		{"1 s outside", time.Second, Fail},
	} {
		sale := readStandaloneSale(t, readFile(t, standaloneVectors, "sale.bundle.json"))
		at, sealed := recordTime(t, sale, share), recordTime(t, sale, commit)
		shift := sealed.Sub(at.AddDate(0, 0, -rulesHistoryDays)) + c.offset
		for _, thread := range sale.Threads {
			for _, r := range thread.records {
				if when := secondOf(r["timestamp"]); when != nil && when.Before(at) {
					r["timestamp"] = encodeJSON(t, when.Add(-shift).Format(time.RFC3339))
				}
			}
		}
		a, h, err := sale.Check(share)
		if err != nil {
			t.Fatal(err)
		}
		if h.releaseWindow == nil || !h.Complete || !h.releaseWindow.Complete {
			t.Fatalf("%s: complete %v, window %+v", c.name, h.Complete, h.releaseWindow)
		}
		got, err := e.constraintFor("release_on_acceptance", a, nil, checkInputs{sale: &h})
		if err != nil {
			t.Fatal(err)
		}
		if got.Result != c.want {
			t.Errorf("%s: %s %q", c.name, got.Result, got.failNote)
		}
		// single_commitment reads the whole history: the commit is in it either way.
		if !slices.ContainsFunc(h.Acts, func(r Record) bool { return r.CapsuleID == commit }) {
			t.Errorf("%s: the whole history lost the commit", c.name)
		}
	}
}

// commitAct is the capsule id of A's commit act, as the history of A's
// phone share carries it.
func commitAct(t *testing.T) string {
	t.Helper()
	in := decodeJSON[struct {
		History []struct {
			CapsuleID  string `json:"capsule_id"`
			AgentInput struct {
				Body struct {
					Action string `json:"action"`
				} `json:"body"`
			} `json:"agent_input"`
		} `json:"history"`
	}](t, readFile(t, standaloneVectors, "check-inputs", "a-share-phone.json"))
	for _, e := range in.History {
		if e.AgentInput.Body.Action == "commit" {
			return e.CapsuleID
		}
	}
	t.Fatal("no commit act in A's phone share's history")
	return ""
}

// recordTime is the whole second the record capsuleID of a carried thread
// names.
func recordTime(t *testing.T, sale SaleBundle, capsuleID string) time.Time {
	t.Helper()
	for _, thread := range sale.Threads {
		for _, r := range thread.records {
			if id, _ := textOf(r["capsule_id"]); id == capsuleID {
				if at := secondOf(r["timestamp"]); at != nil {
					return *at
				}
			}
		}
	}
	t.Fatalf("no record %s", capsuleID)
	return time.Time{}
}

// A replay with no window given (a History built without SaleBundle.Check)
// reads the replay's own ledger, as the engine's replay outside a sale
// does: a decision record carries no thread counterparty, so a share that
// needs the acceptance refuses even with the sale's accepted commit on that
// ledger. 1.0.0 and 1.1.0 never read the window.
func TestWithNoWindowTheShareReadsTheReplayLedger(t *testing.T) {
	e := release12Engine(t)
	sale := readStandaloneSale(t, readFile(t, standaloneVectors, "sale.bundle.json"))
	a, h, err := sale.Check(standaloneShare(t, "a-share-phone"))
	if err != nil {
		t.Fatal(err)
	}
	h.releaseWindow = nil
	accepted := slices.IndexFunc(h.Acts, func(r Record) bool { return r.CapsuleID == commitAct(t) })
	if accepted < 0 {
		t.Fatal("the sale's history holds no commit act")
	}
	decision := h.Acts[accepted]
	decision.live, decision.Decision, decision.threadCounterparty = "", new("accept"), nil
	if decision.ActionClass == nil || *decision.ActionClass != "agreement.accept" || decision.ItemRef == nil || *decision.ItemRef != *a.ItemRef {
		t.Fatalf("the commit is not this sale's acceptance: %+v", decision)
	}
	if acceptance, unknown := saleAcceptance(a, []Record{decision}, checkInputs{}, e.release.AcceptanceClasses); acceptance == nil || unknown != "" {
		t.Fatalf("the ledger's commit is not read as the acceptance (%q)", unknown)
	}
	c, err := e.constraintFor("release_on_acceptance", a, []Record{decision}, checkInputs{sale: &h})
	if err != nil {
		t.Fatal(err)
	}
	if c.Result != Fail || c.failNote != releaseNotAccepted {
		t.Errorf("no window: %s %q", c.Result, c.failNote)
	}
}

// A binding the port does not know is refused when the engine is built, as
// the engine refuses it when the check runs.
func TestAnUnknownBindingIsRefused(t *testing.T) {
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.ID == releaseWicket })
	c, err := decodeConfig[releaseConfig](p.Wickets[i])
	if err != nil || c.Binding != sameDealSameBuyer {
		t.Fatalf("%+v %v", c, err)
	}
	if err := checkReleaseBinding(releaseConfig{Binding: "cited"}, releaseWicket); err == nil {
		t.Error("an unknown binding loads")
	}
	if err := checkReleaseBinding(c, releaseWicket); err != nil {
		t.Error(err)
	}
}
