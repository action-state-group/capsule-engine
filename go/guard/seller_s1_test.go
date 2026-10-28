package guard

import (
	"encoding/json"
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// s1Checks are the seller checks S1 ports, in the engine's order: caps
// (caps/1.0.0 over spend.weekly/1.0.0), then the pack's offer_expiry,
// price_floor and single_commitment.
var s1Checks = []string{"caps", "offer_expiry", "price_floor", "single_commitment"}

// s1Ported are the S1 checks this port runs: each is held to the engine's
// result and evidence digest on every step. Each port adds its check.
var s1Ported = map[string]bool{"caps": true, "offer_expiry": true, "price_floor": true, "single_commitment": true}

// s1NotYetFields are the corpus action fields this port's Action does not
// carry: only its empty extra, which no check reads (representation_class
// and authorized_by are read since the S2 ports).
var s1NotYetFields = []string{"extra"}

type s1Corpus struct {
	DefinitionDigest string                     `json:"definition_digest"`
	AcceptanceStep   string                     `json:"acceptance_step"`
	Tasks            map[string]json.RawMessage `json:"tasks"`
	Openings         map[string]json.RawMessage `json:"openings"`
	Steps            []struct {
		Name            string                     `json:"name"`
		DryRun          bool                       `json:"dry_run"`
		CitesAcceptance bool                       `json:"cites_acceptance"`
		Task            string                     `json:"task"`
		Opening         string                     `json:"opening"`
		Action          map[string]json.RawMessage `json:"action"`
	} `json:"steps"`
}

type s1Decisions struct {
	CapsuleEngineCommit string `json:"capsule_engine_commit"`
	Decisions           map[string]struct {
		CapsuleID   string `json:"capsule_id"`
		Outcome     string `json:"outcome"`
		Constraints map[string]struct {
			Result         string `json:"result"`
			EvidenceDigest string `json:"evidence_digest"`
		} `json:"constraints"`
	} `json:"decisions"`
}

// CROSS-IMPLEMENTATION, constraint by constraint: capsule-engine's own
// GuardEngine ran the seller pack over testdata/seller-s1-vectors (generate.py,
// at the pack's commit) in order on one ledger. Replaying the same steps on
// the ledger the engine sealed (each step's decision and capsule id), every
// S1 check this port runs gives the engine's result and evidence digest on
// every step, a step citing a mandate included (the check is called
// directly; the engine's refusal of such a step is ErrCitedMandate's, not
// the check's). An S1 check not ported yet is n/a, and "not yet run" on
// every step where the engine's check applied.
func TestSellerS1ChecksAgreeWithTheEngine(t *testing.T) {
	e, p := sellerEngine(t)
	diffs := runS1Vectors(t, e, p, filepath.Join("testdata", "seller-s1-vectors"), "ba76a97a0fb5b41333b1b76a489060221e51ea45", s1Covered)
	if !slices.Equal(diffs, s1Pre137Differences) {
		t.Errorf("Go and the engine differ at\n%s\npinned\n%s", strings.Join(diffs, "\n"), strings.Join(s1Pre137Differences, "\n"))
	}
}

// s1Pre137Differences are where Go, which mirrors capsule-engine #137
// (a145f95), differs from S1 vectors written by an engine before it
// (ba76a97 under asg/seller/0.1.0, e0e664d under 0.1.2): single_commitment
// keys the sale on item_ref alone, so sc-other-task (another task authority
// on the same item) now meets the sale's acceptance and fails; and a
// commitment naming no item fails closed, so sc-no-item is refused. The
// engine at 08cd96d decides both the same as Go (testdata/seller-013-vectors
// s1, held with no difference by TestSeller013S1ChecksAgreeWithTheEngine).
var s1Pre137Differences = []string{
	"sc-no-item outcome Go deny engine allow",
	`sc-other-task constraint single_commitment Go fail engine "pass"`,
	"sc-other-task outcome Go deny engine allow",
	"sc-other-task single_commitment Go fail bec525fb32cc2ec1af31cda123fbf41a79069639046912b9cd81c0415d630719 engine pass 83e820d32bee27e17d5125b25eb4c2263947bff5812e1f5e87a7c06dbff30b9e",
}

// s1Covered are the results each ported S1 check was held to the engine on
// under asg/seller/0.1.0: a pass, a fail and an n/a for every one.
var s1Covered = map[string][]Result{"caps": {Pass, Fail, NotApplicable}, "offer_expiry": {Pass, Fail, NotApplicable},
	"price_floor": {Pass, Fail, NotApplicable}, "single_commitment": {Pass, Fail, NotApplicable}}

// runS1Vectors holds e (running pack p) to the engine's S1 decisions in dir,
// written at commit, and requires each ported check to have been compared on
// every result covered names for it. It returns where a ported check's
// result or evidence digest, a constraint's result or the outcome differ
// from the engine's, sorted ("<step> <what> Go <x> engine <y>"), for the
// caller to require none, or exactly the ones it pins. Each check in also
// is held to the engine's result and evidence digest on every step too.
func runS1Vectors(t *testing.T, e *Engine, p pack.Pack, dir, commit string, covered map[string][]Result, also ...string) []string {
	t.Helper()
	corpus := decodeJSON[s1Corpus](t, readFile(t, dir, "corpus.json"))
	py := decodeJSON[s1Decisions](t, readFile(t, dir, "decisions.json"))
	if corpus.DefinitionDigest != p.DefinitionDigest || py.CapsuleEngineCommit != commit {
		t.Fatalf("vectors for %s at %s; the pack digests to %s", corpus.DefinitionDigest, py.CapsuleEngineCommit, p.DefinitionDigest)
	}
	var ledger []Record
	var diffs []string
	compared := map[string]map[Result]int{}
	decided := 0
	for _, step := range corpus.Steps {
		want, ok := py.Decisions[step.Name]
		if !ok {
			t.Fatalf("%s: no engine decision", step.Name)
		}
		fields := maps.Clone(step.Action)
		for _, f := range s1NotYetFields {
			delete(fields, f)
		}
		if step.CitesAcceptance {
			fields["cited_mandate_capsule_id"] = json.RawMessage(`"` + py.Decisions[corpus.AcceptanceStep].CapsuleID + `"`)
		}
		body, err := json.Marshal(fields)
		if err != nil {
			t.Fatal(err)
		}
		a, err := decodeAction(body, true)
		if err != nil {
			t.Fatalf("%s: %v", step.Name, err)
		}
		in := s1Inputs(t, corpus.Tasks[step.Task], corpus.Openings[step.Opening])
		for _, check := range slices.Concat(s1Checks, also) {
			c, err := e.constraintFor(check, a, MemoryHistory(ledger), in)
			if err != nil {
				t.Fatalf("%s %s: %v", step.Name, check, err)
			}
			engine, ok := want.Constraints[check]
			if !ok {
				t.Fatalf("%s: the engine ran no %s", step.Name, check)
			}
			if !s1Ported[check] && !slices.Contains(also, check) {
				if c.Result != NotApplicable || (engine.Result != string(NotApplicable) && c.Pending() != NotYetRun) {
					t.Errorf("%s: unported %s is %s %q", step.Name, check, c.Result, c.Pending())
				}
				continue
			}
			digest, ok, err := c.EvidenceDigest()
			if err != nil || !ok {
				t.Errorf("%s: %s evidence not digestable: %v", step.Name, check, err)
			}
			if string(c.Result) != engine.Result || digest != engine.EvidenceDigest {
				diffs = append(diffs, fmt.Sprintf("%s %s Go %s %s engine %s %s", step.Name, check, c.Result, digest, engine.Result, engine.EvidenceDigest))
			}
			if compared[check] == nil {
				compared[check] = map[Result]int{}
			}
			compared[check][c.Result]++
			// NEVER ECHOED: what the report says of the rule names no
			// reference the step carries.
			rr, _ := e.ruleResult(check, a, c)
			said, err := json.Marshal(rr)
			if err != nil {
				t.Fatal(err)
			}
			for _, id := range s1Identifiers(t, step.Action, fields, corpus.Openings[step.Opening]) {
				if strings.Contains(string(said)+c.Pending(), id) {
					t.Errorf("%s: %s says %q: %s %s", step.Name, check, id, said, c.Pending())
				}
			}
		}
		// The whole step, where it cites no mandate: every constraint's
		// result and the outcome are the engine's.
		if a.CitedMandateCapsuleID == nil {
			d, err := e.checkWith(a, MemoryHistory(ledger), in)
			if err != nil {
				t.Fatalf("%s: %v", step.Name, err)
			}
			if d.Outcome != want.Outcome {
				diffs = append(diffs, fmt.Sprintf("%s outcome Go %s engine %s", step.Name, d.Outcome, want.Outcome))
			}
			if len(d.Constraints) != len(want.Constraints) {
				t.Errorf("%s: %d constraints, the engine recorded %d", step.Name, len(d.Constraints), len(want.Constraints))
			}
			for _, c := range d.Constraints {
				if engine, ok := want.Constraints[c.ID]; !ok || string(c.Result) != engine.Result {
					diffs = append(diffs, fmt.Sprintf("%s constraint %s Go %s engine %q", step.Name, c.ID, c.Result, engine.Result))
				}
			}
			decided++
		}
		rec := RecordFor(a, Decision{Outcome: want.Outcome})
		rec.CapsuleID = want.CapsuleID
		if step.DryRun {
			rec.DryRun = ptr(true)
		}
		ledger = append(ledger, rec)
	}
	if decided != 74 {
		t.Errorf("%d steps decided whole, the corpus has 74 citing no mandate", decided)
	}
	// Every ported check was held to the engine on each result covered names.
	for _, check := range slices.Sorted(maps.Keys(s1Ported)) {
		for _, r := range covered[check] {
			if compared[check][r] == 0 {
				t.Errorf("%s: no step where the engine's result is %s", check, r)
			}
		}
	}
	slices.Sort(diffs)
	return diffs
}

// s1Identifiers are the reference values a step carries: its item_ref,
// task_authority_ref, target and cited capsule id, and its opening's nonce
// and commitment.
func s1Identifiers(t *testing.T, action, fields map[string]json.RawMessage, opening json.RawMessage) []string {
	t.Helper()
	var out []string
	for _, k := range []string{"item_ref", "task_authority_ref", "target"} {
		var s string
		if json.Unmarshal(action[k], &s) == nil && s != "" {
			out = append(out, s)
		}
	}
	var cited string
	if json.Unmarshal(fields["cited_mandate_capsule_id"], &cited) == nil && cited != "" {
		out = append(out, cited)
	}
	var o struct {
		Nonce            string `json:"nonce"`
		BoundsCommitment string `json:"bounds_commitment"`
	}
	if json.Unmarshal(opening, &o) == nil {
		for _, s := range []string{o.Nonce, o.BoundsCommitment} {
			if s != "" {
				out = append(out, s)
			}
		}
	}
	return out
}

// s1Inputs are a step's inputs beside the action: the task-authority record
// it is checked with, and the commercial-bounds opening ("null" when none).
func s1Inputs(t *testing.T, task, opening json.RawMessage) checkInputs {
	t.Helper()
	return checkInputs{taskAuthority: task, opening: opening}
}

// The checker input's item_ref is taken only as 64 lowercase hex, as the
// engine's action_for_check_input takes it; any other value is named as an
// ignored input, never kept, and single_commitment says so without it.
func TestWithItemRef(t *testing.T) {
	e, _ := sellerEngine(t)
	ref := strings.Repeat("a", 64)
	base := Action{Verb: "offer", Operator: "op", Developer: "dev", ActionClass: strp("marketplace.offer"), ActionID: "offer/1",
		ActionType: "decide", Timestamp: "2026-10-09T10:00:00Z", TaskAuthorityRef: &ref}
	good := strings.Repeat("7c", 32)
	if a := WithItemRef(base, json.RawMessage(`"`+good+`"`)); a.ItemRef == nil || *a.ItemRef != good || len(a.ignoredInputs) != 0 {
		t.Fatalf("hex item_ref: %+v %v", a.ItemRef, a.ignoredInputs)
	}
	for _, raw := range []json.RawMessage{json.RawMessage(`null`), nil} {
		if a := WithItemRef(base, raw); a.ItemRef != nil || len(a.ignoredInputs) != 0 {
			t.Errorf("%q: %+v %v", raw, a.ItemRef, a.ignoredInputs)
		}
	}
	for _, raw := range []string{`"item-ref:` + good + `"`, `"` + strings.ToUpper(good) + `"`, `"` + good + `\n"`, `7`} {
		a := WithItemRef(base, json.RawMessage(raw))
		if a.ItemRef != nil || !slices.Equal(a.ignoredInputs, []string{"item_ref"}) {
			t.Errorf("%s: %+v %v", raw, a.ItemRef, a.ignoredInputs)
		}
		c, err := e.constraintFor("single_commitment", a, nil, checkInputs{})
		if err != nil || c.MissingField() != "item_ref" || c.Pending() != "the item_ref input was not in its agreed shape; the sale could not be identified" {
			t.Errorf("%s: %s %q %v", raw, c.MissingField(), c.Pending(), err)
		}
	}
}
