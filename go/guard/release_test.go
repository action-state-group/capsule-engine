package guard

import (
	"encoding/json"
	"maps"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

type releaseCorpus struct {
	Pack                    string          `json:"pack"`
	TaskAuthorityRecord     json.RawMessage `json:"task_authority_record"`
	CommercialBoundsOpening json.RawMessage `json:"commercial_bounds_opening"`
	Steps                   []struct {
		Name                string                     `json:"name"`
		DryRun              bool                       `json:"dry_run"`
		CitesStep           *string                    `json:"cites_step"`
		Action              map[string]json.RawMessage `json:"action"`
		AuthorizationRecord json.RawMessage            `json:"authorization_record"`
	} `json:"steps"`
}

type releaseDecisions struct {
	CapsuleEngineCommit string `json:"capsule_engine_commit"`
	Pack                struct {
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Decisions map[string]struct {
		Outcome string            `json:"outcome"`
		Results map[string]string `json:"results"`
		Release struct {
			Result   string          `json:"result"`
			Reason   string          `json:"reason"`
			Evidence json.RawMessage `json:"evidence"`
		} `json:"release_on_acceptance"`
	} `json:"decisions"`
}

func seller011Engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadSeller011()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// releasePassesOnVerify are the steps the engine passes only once the cited
// acceptance re-verifies in its ledger: the port, which has no capsule
// verifier, reads each as not evaluated (NoCapsuleVerifier), and no other.
var releasePassesOnVerify = []string{"address-to-a-after-acceptance", "pickup-detail-to-a-after-acceptance", "other-detail-to-a-after-acceptance"}

// CROSS-IMPLEMENTATION: capsule-engine's own GuardEngine ran asg/seller/0.1.1
// over its acceptance scenario (testdata/seller-011-vectors, at #125's head,
// whose tree is the merge's). Replayed in order on the ledger the engine
// sealed (each step's decision; a step's capsule id is a stand-in, since
// release_on_acceptance and single_commitment only compare a citation with
// it), release_on_acceptance gives the engine's result, reason and evidence
// digest on every step, except where the engine passes after re-verifying
// the cited acceptance: there it is n/a, not evaluated, and the report says
// not_evaluable, never pass. Every other check the port runs gives the
// engine's result (verify_before_dispatch only where no mandate is cited:
// with one it needs the same verifier, and checkWith refuses the step); on a
// step citing no mandate, so does the outcome. What
// the report says of the rule names no reference the step carries.
func TestSeller011ReleaseAgreesWithTheEngine(t *testing.T) {
	e, p := seller011Engine(t)
	runReleaseVectors(t, e, p, filepath.Join("testdata", "seller-011-vectors", "python-decisions.json"), "ee091e1f729bdc08b5b26cfe3a6dfbf8e5310a10")
}

// runReleaseVectors holds e (running pack p) to the engine's decisions in
// decisionsFile, written at commit, over the vendored 0.1.1 acceptance
// scenario.
func runReleaseVectors(t *testing.T, e *Engine, p pack.Pack, decisionsFile, commit string) {
	t.Helper()
	corpus := decodeJSON[releaseCorpus](t, readFile(t, "testdata", "seller-011-vectors", "corpus.json"))
	py := decodeJSON[releaseDecisions](t, readFile(t, decisionsFile))
	if py.Pack.DefinitionDigest != p.DefinitionDigest || py.CapsuleEngineCommit != commit {
		t.Fatalf("vectors for %s at %s; the pack digests to %s", py.Pack.DefinitionDigest, py.CapsuleEngineCommit, p.DefinitionDigest)
	}
	var ledger []Record
	var held []string
	results := map[Result]int{}
	for _, step := range corpus.Steps {
		want, ok := py.Decisions[step.Name]
		if !ok {
			t.Fatalf("%s: no engine decision", step.Name)
		}
		fields := maps.Clone(step.Action)
		delete(fields, "extra")
		if step.CitesStep != nil {
			fields["cited_mandate_capsule_id"] = json.RawMessage(`"step:` + *step.CitesStep + `"`)
		}
		body, err := json.Marshal(fields)
		if err != nil {
			t.Fatal(err)
		}
		a, err := decodeAction(body, true)
		if err != nil {
			t.Fatalf("%s: %v", step.Name, err)
		}
		in := checkInputs{taskAuthority: corpus.TaskAuthorityRecord, opening: corpus.CommercialBoundsOpening, authorization: step.AuthorizationRecord}
		c, err := e.constraintFor("release_on_acceptance", a, MemoryHistory(ledger), in)
		if err != nil {
			t.Fatal(err)
		}
		rr, _ := e.ruleResult("s13-personal-data-only-to-the-buyer-who-accepted", a, c)
		engine := releaseOutcomeOf(t, want.Release.Result, want.Release.Reason, want.Release.Evidence)
		if c.Pending() == NoCapsuleVerifier {
			held = append(held, step.Name)
			// The engine passed it; the port says not evaluated, never pass.
			got := heldOutcome{Engine: engine.Result, Port: string(c.Result), Verdict: rr.Verdict}
			if want := (heldOutcome{Engine: "pass", Port: "n/a", Verdict: VerdictNotEvaluable}); got != want {
				t.Errorf("%s: held %+v, want %+v", step.Name, got, want)
			}
		} else {
			digest, _, err := c.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			got := releaseOutcome{Result: string(c.Result), Digest: digest}
			if c.Result == Fail {
				got.Reason = c.failNote
			}
			if engine.Result != "fail" {
				engine.Reason = ""
			}
			if got != engine {
				t.Errorf("%s: Go %+v, engine %+v", step.Name, got, engine)
			}
			results[c.Result]++
		}
		said, err := json.Marshal(rr)
		if err != nil {
			t.Fatal(err)
		}
		for _, k := range []string{"item_ref", "task_authority_ref", "target", "cited_mandate_capsule_id"} {
			var s string
			if json.Unmarshal(fields[k], &s) == nil && s != "" && strings.Contains(string(said)+c.Pending(), s) {
				t.Errorf("%s: s13 says the %s: %s", step.Name, k, said)
			}
		}
		// The other checks, and the outcome where the port decides the step.
		for check, engine := range want.Results {
			// verify_before_dispatch with a cited mandate needs the capsule
			// verifier too; checkWith refuses such a step (ErrCitedMandate).
			if check == "release_on_acceptance" || (check == "verify_before_dispatch" && a.CitedMandateCapsuleID != nil) {
				continue
			}
			oc, err := e.constraintFor(check, a, MemoryHistory(ledger), in)
			if err != nil {
				t.Fatal(err)
			}
			if string(oc.Result) != engine {
				t.Errorf("%s: %s Go %s, engine %s", step.Name, check, oc.Result, engine)
			}
		}
		if a.CitedMandateCapsuleID == nil {
			d, err := e.checkWith(a, MemoryHistory(ledger), in)
			if err != nil {
				t.Fatal(err)
			}
			if d.Outcome != want.Outcome {
				t.Errorf("%s: outcome %s, engine %s", step.Name, d.Outcome, want.Outcome)
			}
		}
		rec := RecordFor(a, Decision{Outcome: want.Outcome})
		rec.CapsuleID = "step:" + step.Name
		if step.DryRun {
			rec.DryRun = ptr(true)
		}
		ledger = append(ledger, rec)
	}
	if !slices.Equal(held, releasePassesOnVerify) {
		t.Errorf("held for the verifier: %v, want %v", held, releasePassesOnVerify)
	}
	if results[Fail] == 0 || results[NotApplicable] == 0 {
		t.Errorf("compared %v", results)
	}
}

// The paths the scenario does not take, each with the result, reason and
// evidence digest capsule-engine's check_release_on_acceptance gives at
// 21975ce (computed by that code on these actions, an empty ledger).
func TestReleaseOnAcceptanceEdges(t *testing.T) {
	e, _ := seller011Engine(t)
	str := func(s string) *string { return &s }
	base := Action{Verb: "share_address", Operator: "op", Developer: "dev", ActionClass: str("disclosure.personal"), ActionID: "share/1",
		ActionType: "decide", Timestamp: "2026-10-09T10:00:00Z", Target: str("cp/a"), TaskAuthorityRef: str(strings.Repeat("a", 64)),
		ItemRef: str(strings.Repeat("7c", 32)), RepresentationClass: str("address"), CitedMandateCapsuleID: str(strings.Repeat("b", 64))}
	const outOfScope = "6fb788d0c526ae16081bf5e85ee3b1dc4d96c64fbbf99a2c48fda13ba7f0d7bb"
	const noRow = "d540c23c6297617bc6cbcecc0f9f5b66a69a2c5214a62e01850c3955f8007245"
	for name, c := range map[string]struct {
		edit           func(*Action)
		result, reason string
		digest         string
	}{
		"no task_authority_ref": {func(a *Action) { a.TaskAuthorityRef = nil }, "fail",
			"the disclosure has no task_authority_ref; it cannot be bound to an acceptance", "fb693db07871dde3cb41a18f654f81e1b7ec3fb9b7f9805a9ba4469a8624f769"},
		"no item_ref": {func(a *Action) { a.ItemRef = nil }, "fail",
			"the disclosure has no item_ref; it cannot be bound to an acceptance", "32163af1f54b516f65203a8f174b8fc5ab9c40caf6a294d202f33ac3e8749e1a"},
		"an item_ref out of shape": {func(a *Action) { a.ItemRef, a.ignoredInputs = nil, []string{"item_ref"} }, "fail",
			"the disclosure has the item_ref input was not in its agreed shape; it cannot be bound to an acceptance",
			"32163af1f54b516f65203a8f174b8fc5ab9c40caf6a294d202f33ac3e8749e1a"},
		"no target": {func(a *Action) { a.Target = nil }, "fail",
			"the disclosure has no target; it cannot be bound to an acceptance", "bf2c5d95715adbc0811312548466dbd41b02faf69d2f058aa6d5f61c6193838e"},
		"a class outside release_classes": {func(a *Action) { a.RepresentationClass = str("home_address") }, "fail",
			"the disclosure declares no class this rule knows; it is not released", "8a5d6def4db3524cd94407fcfe1a9c4fe19e864c1d03409cc4f167b9a57b4e75"},
		"no representation_class": {func(a *Action) { a.RepresentationClass = nil }, "fail",
			"the disclosure declares no class this rule knows; it is not released", "8a5d6def4db3524cd94407fcfe1a9c4fe19e864c1d03409cc4f167b9a57b4e75"},
		"no citation": {func(a *Action) { a.CitedMandateCapsuleID = nil }, "fail",
			"the disclosure cites no acceptance", "44e1d9f37093f0d5729d42e3b67880d5b350ab53cd8f92b26edebd99dd7c2028"},
		"no acceptance sealed": {func(*Action) {}, "fail", releaseNotBound, "44e1d9f37093f0d5729d42e3b67880d5b350ab53cd8f92b26edebd99dd7c2028"},
		"a class with no taxonomy row": {func(a *Action) { a.ActionClass = str("nonsense") }, "fail",
			"the action class has no taxonomy row; it cannot be shown to be outside the rule", noRow},
		"no action_class":                 {func(a *Action) { a.ActionClass = nil }, "fail", "the action class has no taxonomy row; it cannot be shown to be outside the rule", noRow},
		"another known class":             {func(a *Action) { a.ActionClass = str("disclosure.secret") }, "n/a", "", outOfScope},
		"a legacy alias of another class": {func(a *Action) { a.ActionClass = str("comms.external") }, "n/a", "", outOfScope},
	} {
		a := base
		c.edit(&a)
		got, err := e.constraintFor("release_on_acceptance", a, nil, checkInputs{})
		if err != nil {
			t.Fatal(err)
		}
		digest, _, err := got.EvidenceDigest()
		if err != nil || string(got.Result) != c.result || got.failNote != c.reason || digest != c.digest {
			t.Errorf("%s: %s %q %s, engine %s %q %s", name, got.Result, got.failNote, digest, c.result, c.reason, c.digest)
		}
	}
}

// release_on_acceptance is an integrity check (engine.py _INTEGRITY_CHECKS at
// 21975ce): a declared ASK never makes it ask, so even a pack declaring its
// rule ASK refuses a failing disclosure, and no approval clears it.
func TestReleaseOnAcceptanceIsAnIntegrityCheck(t *testing.T) {
	_, p := seller011Engine(t)
	p.Rules = slices.Clone(p.Rules)
	i := slices.IndexFunc(p.Rules, func(r pack.Rule) bool { return r.Check == "release_on_acceptance" })
	p.Rules[i].DefaultDisposition = "ASK"
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	if slices.Contains(e.AskWickets(), "release_on_acceptance") {
		t.Fatal("a declared ASK made release_on_acceptance ask")
	}
	str := func(s string) *string { return &s }
	a := Action{Verb: "share_address", Operator: "op", Developer: "dev", ActionClass: str("disclosure.personal"), ActionID: "share/1",
		ActionType: "decide", Timestamp: "2026-10-09T10:00:00Z", Target: str("cp/a"), TaskAuthorityRef: str(strings.Repeat("a", 64)),
		ItemRef: str(strings.Repeat("7c", 32)), RepresentationClass: str("address"), RecipientRole: str("buyer")}
	d, err := e.Check(a, nil)
	if err != nil {
		t.Fatal(err)
	}
	if c, _ := d.Constraint("release_on_acceptance"); c.Result != Fail || d.Outcome != Deny {
		t.Fatalf("release %s, outcome %s; want fail, deny", c.Result, d.Outcome)
	}
}

// releaseOutcome is release_on_acceptance's result, reason (on a fail) and
// evidence digest.
type releaseOutcome struct {
	Result, Reason, Digest string
}

// heldOutcome is a step the port holds for the verifier: the engine's
// result, the port's, and the report's verdict.
type heldOutcome struct {
	Engine, Port, Verdict string
}

// releaseOutcomeOf is the engine's recorded outcome, its evidence object
// digested as the capsule digests it.
func releaseOutcomeOf(t *testing.T, result, reason string, evidence json.RawMessage) releaseOutcome {
	t.Helper()
	v, err := canon.FromJSON(evidence)
	if err != nil {
		t.Fatal(err)
	}
	d, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	return releaseOutcome{Result: result, Reason: reason, Digest: d}
}
