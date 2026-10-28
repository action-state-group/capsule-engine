package guard

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
	"gopkg.in/yaml.v3"
)

// A PURCHASE THAT NAMES NO PAYEE ASKS ON r06 (capsule-engine #135, c878edf0;
// asg/everyday/0.3.5). counterparty_seen_before/4.0.0 is 3.0.0 with config
// missing_target: unseen. A money.purchase whose target is absent, empty or
// only whitespace fails as a first-time counterparty ("no payee named"),
// running no fold, and escalates; under 3.0.0 (asg/everyday/0.3.4, frozen)
// an absent target is n/a and a blank one is counted under "".
//
// Held to the engine side's vectors (testdata/r06-no-payee-vectors, gen.py at
// e78c33a, reproduced byte for byte at the merge c878edf0), under 0.3.5 (now
// frozen) and 0.3.4, and to the same gen.py rerun at a710636c
// (vectors-a710636c.json), whose catalog pack is 0.3.6 (now frozen): each
// live scenario under each pack, its setup acts decided for real and its
// probe checked over them, giving the engine's result, method, sealed
// evidence digest, failing checks, outcome, and r02/r06.

const r06NoPayeeDir = "r06-no-payee-vectors"

type r06NoPayeeView struct {
	Result               string            `json:"result"`
	Reason               string            `json:"reason"`
	Method               string            `json:"method"`
	Evidence             json.RawMessage   `json:"evidence"`
	SealedEvidenceDigest string            `json:"sealed_evidence_digest"`
	FailingChecks        []string          `json:"failing_checks"`
	Outcome              string            `json:"outcome"`
	Rules                map[string]string `json:"rules"`
	SetupOutcomes        []string          `json:"setup_outcomes"`
	ProbeTarget          *string           `json:"probe_target"`
	ProbeActionClass     string            `json:"probe_action_class"`
	Record               json.RawMessage   `json:"record"`
}

type r06NoPayeeVectors struct {
	Wickets map[string]struct {
		Digest string          `json:"digest"`
		Config json.RawMessage `json:"config"`
	} `json:"wickets"`
	Packs map[string]struct {
		PackID string `json:"pack_id"`
		Digest string `json:"digest"`
	} `json:"packs"`
	Live        map[string]map[string]r06NoPayeeView `json:"live"`
	ReplayTyped map[string]map[string]r06NoPayeeView `json:"replay_typed"`
}

// r06NoPayeeFiles are the vector files: the engine side's (c878edf0) and gen.py's
// rerun at a710636c.
var r06NoPayeeFiles = []string{"vectors.json", "vectors-a710636c.json"}

// r06NoPayeePacks loads each pack the vectors ran, by gen.py's label, as the
// vendored pack the vectors name by id, checking it is at the vectors'
// digest. gen.py labels the catalog pack "0.3.5" whatever it is.
func r06NoPayeePacks(t *testing.T, v r06NoPayeeVectors) map[string]pack.Pack {
	t.Helper()
	loads := map[string]func() (pack.Pack, error){"asg/everyday/0.3.6": pack.LoadEveryday036, "asg/everyday/0.3.5": pack.LoadEveryday035,
		"asg/everyday/0.3.4": pack.LoadEveryday034}
	out := map[string]pack.Pack{}
	for version, ran := range v.Packs {
		load, ok := loads[ran.PackID]
		if !ok {
			t.Fatalf("%s: vectors under %s, which is not vendored", version, ran.PackID)
		}
		p, err := load()
		if err != nil {
			t.Fatal(err)
		}
		if want := v.Packs[version]; p.ID != want.PackID || p.DefinitionDigest != want.Digest {
			t.Fatalf("%s: vendored %s %s, vectors %s %s", version, p.ID, p.DefinitionDigest, want.PackID, want.Digest)
		}
		out[version] = p
	}
	return out
}

func r06NoPayee(t *testing.T, file string) r06NoPayeeVectors {
	t.Helper()
	var prov struct {
		EngineCommit string `json:"engine_commit"`
		Files        map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", r06NoPayeeDir, "PROVENANCE.json"), &prov); err != nil {
		t.Fatal(err)
	}
	if prov.EngineCommit != "c878edf0d8f6bfe2d9b7aba84e0023412efa4fb7" || len(prov.Files) != 3 {
		t.Fatalf("provenance: %s, %d files", prov.EngineCommit, len(prov.Files))
	}
	for name, f := range prov.Files {
		if sum := sha256.Sum256(readFile(t, "testdata", r06NoPayeeDir, name)); hex.EncodeToString(sum[:]) != f.SHA256 {
			t.Fatalf("%s is not the vendored bytes", name)
		}
	}
	v := decodeJSON[r06NoPayeeVectors](t, readFile(t, "testdata", r06NoPayeeDir, file))
	if len(v.Live) != 2 || len(v.Live["0.3.5"]) != 7 || len(v.Live["0.3.4"]) != 7 {
		t.Fatalf("live vectors: %d packs", len(v.Live))
	}
	return v
}

// r06Act is gen.py's act(n, target, cls, verb, **f), as an action body.
func r06Act(t *testing.T, n int, target *string, cls, verb string, extra string) Action {
	t.Helper()
	body := fmt.Sprintf(`{"verb":%q,"operator":"r06-no-payee","developer":"assistant@v1","action_class":%q,"amount_minor":900,`+
		`"currency":"EUR","rail":"card","action_id":"%s/r06-no-payee-%d","timestamp":"2026-10-09T12:%02d:00Z"%s`, verb, cls, verb, n, n, extra)
	if target != nil {
		b, err := json.Marshal(*target)
		if err != nil {
			t.Fatal(err)
		}
		body += `,"target":` + string(b)
	}
	a, err := DecodeAction(strings.NewReader(body + "}"))
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// r06Known is gen.py's known(n, target): a real transfer to target.
func r06Known(t *testing.T, n int, target string) Action {
	return r06Act(t, n, &target, "money.transfer", "make_payment",
		fmt.Sprintf(`,"recurrence":"one_time","counterparty_account_ref":"acct-ref-%d"`, n))
}

// r06Scenarios are gen.py's LIVE: the setup acts, then the probe.
func r06Scenarios(t *testing.T) map[string][]Action {
	t.Helper()
	s := func(v string) *string { return &v }
	purchase := func(target *string) Action { return r06Act(t, 10, target, "money.purchase", "make_purchase", "") }
	return map[string][]Action{
		"no_target":                    {purchase(nil)},
		"empty_target":                 {purchase(s(""))},
		"whitespace_target":            {purchase(s("   "))},
		"new_payee":                    {purchase(s("shop/new"))},
		"seen_payee":                   {r06Known(t, 1, "shop/known"), purchase(s("shop/known"))},
		"blank_after_blank_accepted":   {r06Known(t, 1, ""), purchase(s(""))},
		"out_of_scope_class_no_target": {r06Act(t, 10, nil, "comms.external", "send_message", "")},
	}
}

// r06View is what the vectors record of a decision on a.
func r06View(t *testing.T, e *Engine, a Action, d Decision) r06NoPayeeView {
	t.Helper()
	c, ok := d.Constraint("counterparty_seen_before")
	if !ok {
		t.Fatal("no counterparty_seen_before")
	}
	digest, _, err := c.EvidenceDigest()
	if err != nil {
		t.Fatal(err)
	}
	failing := []string{}
	for _, k := range d.Constraints {
		if k.Result == Fail {
			failing = append(failing, k.ID)
		}
	}
	rules := map[string]string{}
	for _, r := range e.Report(a, d).Rules {
		if r.Rule == "r02-ordinary-purchase" || r.Rule == "r06-new-merchant" {
			rules[r.Rule] = map[string]string{VerdictPass: "pass", VerdictFail: "fail", VerdictNotApplicable: "n/a"}[r.Verdict]
		}
	}
	return r06NoPayeeView{Result: string(c.Result), Method: c.Method, SealedEvidenceDigest: digest, FailingChecks: failing,
		Outcome: d.Outcome, Rules: rules}
}

// r06Compared is the part of a view both sides decide.
func r06Compared(t *testing.T, v r06NoPayeeView) string {
	t.Helper()
	slices.Sort(v.FailingChecks)
	b, err := json.Marshal(struct {
		Result, Method, Digest, Outcome string
		Failing                         []string
		Rules                           map[string]string
	}{v.Result, v.Method, v.SealedEvidenceDigest, v.Outcome, v.FailingChecks, v.Rules})
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestNoPayeeAsksOnR06AsTheEngineDecides(t *testing.T) {
	for _, file := range r06NoPayeeFiles {
		t.Run(file, func(t *testing.T) { testNoPayeeAsksOnR06(t, r06NoPayee(t, file)) })
	}
}

func testNoPayeeAsksOnR06(t *testing.T, v r06NoPayeeVectors) {
	if w := v.Wickets["counterparty_seen_before/4.0.0"]; w.Digest != "c7c20d9e57cb0a75184b9c6c4d037c9bea813a7b1105c581defd4481acf964b0" {
		t.Fatalf("4.0.0 at %s", w.Digest)
	}
	packs := r06NoPayeePacks(t, v)
	for version, scenarios := range v.Live {
		e, err := NewEngine(packs[version])
		if err != nil {
			t.Fatal(err)
		}
		for name, acts := range r06Scenarios(t) {
			want, ok := scenarios[name]
			if !ok {
				t.Fatalf("%s %s: no vector", version, name)
			}
			setup, probe := acts[:len(acts)-1], acts[len(acts)-1]
			if !eqPtr(probe.Target, want.ProbeTarget) || *probe.ActionClass != want.ProbeActionClass {
				t.Fatalf("%s %s: probe target %s class %s, vector %s %s", version, name, shown(probe.Target), *probe.ActionClass,
					shown(want.ProbeTarget), want.ProbeActionClass)
			}
			var ledger []Record
			setupOutcomes := []string{}
			for _, a := range setup {
				d, err := e.Check(a, MemoryHistory(ledger))
				if err != nil {
					t.Fatal(err)
				}
				setupOutcomes = append(setupOutcomes, d.Outcome)
				ledger = append(ledger, RecordFor(a, d))
			}
			if !slices.Equal(setupOutcomes, want.SetupOutcomes) {
				t.Errorf("%s %s: setup %v, engine %v", version, name, setupOutcomes, want.SetupOutcomes)
			}
			d, err := e.Check(probe, MemoryHistory(ledger))
			if err != nil {
				t.Fatal(err)
			}
			got := r06View(t, e, probe, d)
			if jcsDigest(t, want.Evidence) != want.SealedEvidenceDigest {
				t.Fatalf("%s %s: the vector's evidence does not digest to its sealed digest", version, name)
			}
			if g, w := r06Compared(t, got), r06Compared(t, want); g != w {
				t.Errorf("%s %s:\nGo     %s\nengine %s\nengine evidence %s", version, name, g, w, want.Evidence)
			}
			// A no-payee failure says so on r02 and r06, never that the
			// merchant was looked for and not found.
			if strings.HasPrefix(want.Reason, "no payee named") {
				for _, r := range e.Report(probe, d).Rules {
					if (r.Rule == "r02-ordinary-purchase" || r.Rule == "r06-new-merchant") && r.Reason != want.Reason {
						t.Errorf("%s %s: %s reason %q, want the engine's %q", version, name, r.Rule, r.Reason, want.Reason)
					}
				}
			}
		}
	}
}

// The mode is read from the wicket config, as the engine reads it; any
// value but n/a or unseen is refused (MISSING_TARGET_MODES), never run as
// either.
func TestAMissingTargetModeOutsideTheTwoIsRefused(t *testing.T) {
	p, err := pack.LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	w, ok := p.Wicket("counterparty_seen_before")
	if !ok || w.ID != "counterparty_seen_before/4.0.0" {
		t.Fatalf("the vendored pack cites %s", w.ID)
	}
	var config yaml.Node
	if err := yaml.Unmarshal([]byte("fold_id: counterparty.seen_before/3.0.0\n"+
		"fold_digest: 73dbf8cd8a36fadca0ac823d2b17592fdc457c24081bc44667f032601e914033\n"+
		"action_classes: [money.purchase]\nmissing_target: skip\n"), &config); err != nil {
		t.Fatal(err)
	}
	w.Config = *config.Content[0]
	if _, err := newSeenBefore(w, p); err == nil {
		t.Fatal("missing_target skip was accepted")
	}
	w.Config.Content = w.Config.Content[:len(w.Config.Content)-2]
	sb, err := newSeenBefore(w, p)
	if err != nil || sb.missingTarget != "n/a" {
		t.Fatalf("no missing_target: %q %v; the engine's default is n/a", sb.missingTarget, err)
	}
}

// The vectors' replay pair: a typed proposed-action/v0 check sealing no
// counterparty, and one sealing a new payee in body.counterparty (#133),
// each bound by gen.py's capsule (operator r06-no-payee, fyi), under both
// packs. With no payee, 0.3.5 asks and 0.3.4 lets it through as n/a; with
// one, both read it as a first-time merchant. gen.py replays with the pack's
// window limits and no per-action limit (replay() given caps_minor only), so
// the engine here runs without one too: the 4500 purchase is over the
// pack's per-purchase limit, which the vectors do not exercise.
func TestNoPayeeOnATypedCheckAsTheEngineReplays(t *testing.T) {
	for _, file := range r06NoPayeeFiles {
		t.Run(file, func(t *testing.T) { testNoPayeeOnATypedCheck(t, r06NoPayee(t, file)) })
	}
}

func testNoPayeeOnATypedCheck(t *testing.T, v r06NoPayeeVectors) {
	packs := r06NoPayeePacks(t, v)
	if len(v.ReplayTyped) != 2 {
		t.Fatalf("%d replay packs", len(v.ReplayTyped))
	}
	for version, cases := range v.ReplayTyped {
		e, err := NewEngine(packs[version])
		if err != nil {
			t.Fatal(err)
		}
		e.perActionMinor, e.perActionReads, e.limitSources = map[string]int64{}, "", false
		if len(cases) != 2 {
			t.Fatalf("%s: %d typed cases", version, len(cases))
		}
		for name, want := range cases {
			capsule := `{"capsule_id":"` + fmt.Sprintf("%064x", 1) + `","action_id":"deal-cccccccccccccccc/1","action_type":"fyi",` +
				`"operator":"r06-no-payee","developer":"capsulectl-deal","timestamp":"2026-10-09T12:00:00Z",` +
				`"model_attestation":{"compute_attestation":{"agent_input_digest":"` + jcsDigest(t, want.Record) + `"}}}`
			a, err := DecodeCheckInputRecord(withMember(t, json.RawMessage(capsule), "agent_input", want.Record))
			if err != nil {
				t.Fatalf("%s %s: %v", version, name, err)
			}
			d, err := e.Check(a, nil)
			if err != nil {
				t.Fatal(err)
			}
			if g, w := r06Compared(t, r06View(t, e, a, d)), r06Compared(t, want); g != w {
				t.Errorf("%s %s:\nGo     %s\nengine %s\nengine evidence %s", version, name, g, w, want.Evidence)
			}
		}
	}
}
