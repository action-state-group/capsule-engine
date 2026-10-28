package guard

import (
	"encoding/json"
	"fmt"
	"maps"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// promise11Vectors are promise_requires_approval/1.1.0's and
// promise_never/1.1.0's differential vectors (PROVENANCE.json): capsulectl's
// represent checks on a seller's sale (capsule-cli 8eff953), the user's own
// attributed authenticity claim among them, with live-only edits, decided
// live and on the sale bundle's replay by capsule-engine 0c93a58 under
// asg/seller/0.1.5, regenerated at a489e49 (#209); and the engine's reading of the template and of a
// bundle's openings.
var promise11Vectors = filepath.Join("testdata", "promise-11-vectors")

// seller015Engine is the engine of the vendored asg/seller/0.1.5.
func seller015Engine(t *testing.T) (*Engine, pack.Pack) {
	t.Helper()
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	return e, p
}

// promiseReason is a promise check's reason as this plugin words it: the
// engine's, except for the agent's own statement of a never class, which
// names the class first as 1.0.0's reason does (as ruled for asg/seller/0.1.5).
func promiseReason(cell promiseCell) string {
	if m := ownClaimReason.FindStringSubmatch(cell.Reason); m != nil {
		return m[1] + " is a class of statement the agent never makes as its own; no approval clears it, only a change to the rule set"
	}
	return cell.Reason
}

// ownClaimReason is the engine's refusal of the agent's own claim, which
// names the class after its article (capsule-engine #209).
var ownClaimReason = regexp.MustCompile(`^the agent never makes an? (\S+) statement of its own; `)

// goReason is a promise check's own reason: a failure's note, an n/a's
// pending reason, and none on a pass.
func goReason(c Constraint) string {
	switch c.Result {
	case Fail:
		return c.failNote
	case NotApplicable:
		return c.Pending()
	}
	return ""
}

// CROSS-IMPLEMENTATION: every check input decided live, and every check of
// the sale bundle replayed, with NO known difference from the engine in
// s04's and s10's checks: the result, the evidence and the reason (the
// engine's wording, approved with capsule-engine #205, except the agent's
// own never claim, worded class first as 1.0.0's; a pass's reason is the
// engine's alone), and an attributed claim whose words are not in the
// bundle fails closed.
func TestPromise11AgreesWithTheEngine(t *testing.T) {
	e, _ := seller015Engine(t)
	cells := decodeJSON[struct {
		Live   map[string]map[string]promiseCell `json:"live"`
		Replay map[string]map[string]promiseCell `json:"replay"`
	}](t, readFile(t, promise11Vectors, "expected_promise.json"))
	var diffs []string
	compare := func(at string, d Decision, want map[string]promiseCell) {
		for _, id := range slices.Sorted(maps.Keys(want)) {
			c, ok := d.Constraint(id)
			if !ok {
				t.Fatalf("%s: no %s", at, id)
			}
			digest, _, err := c.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			cell := want[id]
			if c.failsClosed != (cell.Reason == wordsNotInBundle) {
				diffs = append(diffs, at+" "+id+" fails closed "+fmt.Sprint(c.failsClosed))
			}
			reason := promiseReason(cell)
			if cell.Result == string(Pass) {
				reason = ""
			}
			differ(&diffs, at, id, fmt.Sprintf("%s %s %q", c.Result, digest, goReason(c)),
				fmt.Sprintf("%s %s %q", cell.Result, jsonDigest(t, cell.Evidence), reason))
		}
	}
	live := decodeJSON[struct {
		Decisions []saleLiveVector `json:"decisions"`
	}](t, readFile(t, promise11Vectors, "expected_live.json"))
	for _, want := range live.Decisions {
		a, h, err := DecodeCheckInput(readFile(t, promise11Vectors, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		at := "live " + want.Input
		compare(at, d, cells.Live[strings.TrimSuffix(filepath.Base(want.Input), ".json")])
	}
	replayed := decodeJSON[saleReplayDocument](t, readFile(t, promise11Vectors, "expected_replay.json"))
	if replayed.Pack.PackID != "asg/seller/0.1.5" {
		t.Fatalf("vectors under %+v", replayed.Pack)
	}
	sale := readSaleIn(t, promise11Vectors)
	notInBundle := 0
	for _, want := range replayed.Decisions {
		id := capsuleIn(t, promise11Vectors, want.Input)
		a, h, err := sale.Check(id)
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		d, err := e.CheckSale(a, MemoryHistory(replayLedger(t, e, sale, id)), h)
		if err != nil {
			t.Fatal(err)
		}
		at := "replay " + want.Input
		compare(at, d, cells.Replay[strings.TrimSuffix(filepath.Base(want.Input), ".json")])
		// The report reads a claim whose words are not in the bundle as not
		// evaluated, with its reason, never as not applicable.
		if c, _ := d.Constraint("promise_never"); c.Pending() == wordsNotInBundle {
			res, _ := e.ruleResult("s10-statement-never-made", a, c)
			differ(&diffs, at, "s10 report", res.Verdict+" "+res.Reason, VerdictNotEvaluable+" "+wordsNotInBundle)
			notInBundle++
		}
	}
	if len(live.Decisions) != 31 || len(replayed.Decisions) != 7 || notInBundle != 1 {
		t.Errorf("%d live and %d replayed decisions", len(live.Decisions), len(replayed.Decisions))
	}
	if len(diffs) > 0 {
		slices.Sort(diffs)
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// CROSS-IMPLEMENTATION: the template promise_never/1.1.0 reads, decoded from
// the vendored definition as NewEngine decodes it, holds the engine's
// sentence marks, and fits every text exactly where the engine's
// fits_attributed_template does: each mark inside the claim, and a claim of
// each whitespace code point Python's str.isspace knows, and a few it does
// not.
func TestAttributedTemplateAgreesWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Definition       string   `json:"definition"`
		DefinitionDigest string   `json:"definition_digest"`
		SentenceMarks    []string `json:"sentence_marks"`
		Vectors          []struct {
			Text string `json:"text"`
			Fits bool   `json:"fits"`
		} `json:"vectors"`
	}](t, readFile(t, promise11Vectors, "template.json"))
	if v.Definition != "promise_never/1.1.0" || v.DefinitionDigest != portedDefinitions[v.Definition] {
		t.Fatalf("vectors under %s %s", v.Definition, v.DefinitionDigest)
	}
	e, _ := seller015Engine(t)
	tmpl := e.never.AttributedTemplate
	if tmpl == nil || !slices.Equal(tmpl.SentenceMarks, v.SentenceMarks) || len(tmpl.SentenceMarks) != 181 {
		t.Fatalf("the decoded template's marks differ from the engine's")
	}
	fits := 0
	for _, want := range v.Vectors {
		if got := tmpl.fits(want.Text); got != want.Fits {
			t.Errorf("%+q: Go %v, engine %v", want.Text, got, want.Fits)
		}
		if want.Fits {
			fits++
		}
	}
	if len(v.Vectors) != 439 || fits != 31 {
		t.Errorf("%d vectors, %d fit", len(v.Vectors), fits)
	}
}

// CROSS-IMPLEMENTATION: the openings a bundle's deal reports carry, read as
// the engine's representing_openings reads them: in each form capsulectl
// writes a report, the first opening of a commitment kept, and nothing from
// an entry or a member out of shape.
func TestRepresentingOpeningsAgreeWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Vectors []struct {
			Name     string                     `json:"name"`
			Bundle   json.RawMessage            `json:"bundle"`
			Openings map[string]json.RawMessage `json:"openings"`
		} `json:"vectors"`
	}](t, readFile(t, promise11Vectors, "openings.json"))
	for _, want := range v.Vectors {
		got := representingOpenings(objectOf(want.Bundle))
		if len(got) != len(want.Openings) {
			t.Errorf("%s: %d openings, engine %d", want.Name, len(got), len(want.Openings))
		}
		for commitment, opening := range want.Openings {
			if jsonDigest(t, got[commitment]) != jsonDigest(t, opening) {
				t.Errorf("%s: %s opens %s, engine %s", want.Name, commitment, got[commitment], opening)
			}
		}
	}
	if len(v.Vectors) != 20 {
		t.Errorf("%d vectors", len(v.Vectors))
	}
}

// statement_attributed_to is a closed token on --action too: any value but
// user is the agent's own statement and is dropped (action.py
// Action.__post_init__).
func TestStatementAttributedToKeepsOnlyUser(t *testing.T) {
	for value, want := range map[string]string{`"user"`: "user", `"User"`: "None", `"agent"`: "None", `""`: "None",
		`false`: "None", `17`: "None", `[]`: "None", `{}`: "None", `null`: "None"} {
		a, err := DecodeAction(strings.NewReader(`{"verb":"statement.send","operator":"o","developer":"d",` +
			`"timestamp":"2026-10-22T09:00:00Z","statement_attributed_to":` + value + `}`))
		if err != nil {
			t.Fatal(err)
		}
		if got := strOrNone(a.StatementAttributedTo); got != want {
			t.Errorf("%s: %s, want %s", value, got, want)
		}
		// A dropped value is still one the action carried.
		if value != `null` && !slices.Contains(a.present, "statement_attributed_to") {
			t.Errorf("%s: not listed as carried", value)
		}
	}
	// A member differing only in case is dropped, whatever it holds and
	// whether or not the exact member is beside it.
	for members, want := range map[string]string{
		`"STATEMENT_ATTRIBUTED_TO":"agent"`:                             "None",
		`"Statement_Attributed_To":"user"`:                              "None",
		`"STATEMENT_ATTRIBUTED_TO":17`:                                  "None",
		`"statement_attributed_to":"user","STATEMENT_ATTRIBUTED_TO":17`: "user",
	} {
		a, err := DecodeAction(strings.NewReader(`{"verb":"statement.send","operator":"o","developer":"d",` +
			`"timestamp":"2026-10-22T09:00:00Z",` + members + `}`))
		if err != nil {
			t.Fatalf("%s: %v", members, err)
		}
		if got := strOrNone(a.StatementAttributedTo); got != want {
			t.Errorf("%s: %s, want %s", members, got, want)
		}
	}
}

// promise_never/1.1.0's config is refused as the engine refuses it: an
// attributed_never class outside never, or attributed_never with no
// template.
func TestAttributedNeverConfigIsChecked(t *testing.T) {
	template := &attributedTemplate{Prefix: "p ", Suffix: " s"}
	for name, c := range map[string]neverConfig{
		"outside never": {Never: []string{"authenticity"}, AttributedNever: &[]string{"price"}, AttributedTemplate: template},
		"no template":   {Never: []string{"authenticity"}, AttributedNever: &[]string{"authenticity"}},
	} {
		if checkAttributed(c, "promise_never/1.1.0") == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	ok := neverConfig{Never: []string{"authenticity"}, AttributedNever: &[]string{"authenticity"}, AttributedTemplate: template}
	if err := checkAttributed(ok, "promise_never/1.1.0"); err != nil {
		t.Errorf("a valid config: %v", err)
	}
	if err := checkAttributed(neverConfig{Never: []string{"authenticity"}}, "promise_never/1.0.0"); err != nil {
		t.Errorf("1.0.0: %v", err)
	}
}

// check --record on a thread's own bundle (BundleReplay, CheckReplay)
// decides s10 as the sale's replay does: A's attributed claim on the
// opening A's deal report carries, B's not evaluable and refused, as its
// report carries none; and a single capsule, which carries no report, is
// not evaluable too.
func TestBundleReplayGivesTheThreadsWords(t *testing.T) {
	e, _ := seller015Engine(t)
	cells := decodeJSON[struct {
		Replay map[string]map[string]promiseCell `json:"replay"`
	}](t, readFile(t, promise11Vectors, "expected_promise.json"))
	sale := decodeJSON[struct {
		Extensions struct {
			Sale struct {
				Threads map[string]json.RawMessage `json:"threads"`
			} `json:"x-deal-sale/v0"`
		} `json:"extensions"`
	}](t, readFile(t, promise11Vectors, "sale.bundle.json"))
	threadOf := func(input string) json.RawMessage {
		in := decodeJSON[struct {
			Record struct {
				DealID string `json:"deal_id"`
			} `json:"record"`
		}](t, readFile(t, promise11Vectors, input))
		return sale.Extensions.Sale.Threads[in.Record.DealID]
	}
	for _, name := range []string{"a-attributed", "b-attributed"} {
		input := "check-inputs/" + name + ".json"
		bundle := threadOf(input)
		a, err := DecodeRecord(bundle, capsuleIn(t, promise11Vectors, input))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		d, err := e.CheckReplay(a, nil, BundleReplay(bundle, a))
		if err != nil {
			t.Fatal(err)
		}
		c, _ := d.Constraint("promise_never")
		if want := cells.Replay[name]["promise_never"]; string(c.Result) != want.Result || c.failsClosed != (want.Reason == wordsNotInBundle) {
			t.Errorf("%s: s10 %s (fails closed %v), engine %s %q", name, c.Result, c.failsClosed, want.Result, want.Reason)
		}
	}
	single := decodeJSON[struct {
		Record json.RawMessage `json:"record"`
	}](t, readFile(t, promise11Vectors, "check-inputs/a-attributed.json")).Record
	a, err := DecodeRecord(single, "")
	if err != nil {
		t.Fatal(err)
	}
	d, err := e.CheckReplay(a, nil, BundleReplay(single, a))
	if err != nil {
		t.Fatal(err)
	}
	if c, _ := d.Constraint("promise_never"); c.Pending() != wordsNotInBundle || !c.failsClosed {
		t.Errorf("a single capsule: s10 %s %q", c.Result, c.Pending())
	}
}

// CROSS-IMPLEMENTATION: the same vectors under the whole asg/seller/0.1.5,
// live and replayed, with NO known difference from the engine in the
// outcome, the verdict and every rule.
func TestSeller015DecidesRepresentChecksAsTheEngine(t *testing.T) {
	e, p := seller015Engine(t)
	var diffs []string
	decided := func(at string, a Action, d Decision, outcome, verdict string, rules map[string]string) {
		differ(&diffs, at, "outcome", d.Outcome, outcome)
		differ(&diffs, at, "verdict", e.Report(a, d).Decision, floorUnreadVerdict(verdict, d))
		got := rulesOf(d, p)
		for _, rule := range slices.Sorted(maps.Keys(rules)) {
			differ(&diffs, at, rule, got[rule], rules[rule])
		}
	}
	live := decodeJSON[struct {
		Decisions []saleLiveVector `json:"decisions"`
	}](t, readFile(t, promise11Vectors, "expected_live.json"))
	for _, want := range live.Decisions {
		a, h, err := DecodeCheckInput(readFile(t, promise11Vectors, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		decided("live "+want.Input, a, d, want.Outcome, want.Verdict, want.Rules)
	}
	replayed := decodeJSON[saleReplayDocument](t, readFile(t, promise11Vectors, "expected_replay.json"))
	if replayed.Pack.PackID != p.ID || replayed.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors under %+v, pack %s %s", replayed.Pack, p.ID, p.DefinitionDigest)
	}
	sale := readSaleIn(t, promise11Vectors)
	for _, want := range replayed.Decisions {
		id := capsuleIn(t, promise11Vectors, want.Input)
		a, h, err := sale.Check(id)
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		d, err := e.CheckSale(a, MemoryHistory(replayLedger(t, e, sale, id)), h)
		if err != nil {
			t.Fatal(err)
		}
		decided("replay "+want.Input, a, d, want.Outcome, want.Verdict, want.Rules)
	}
	if len(diffs) > 0 {
		slices.Sort(diffs)
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// A reason names a class after its article, as the engine's with_article
// does: "an" before a vowel, "a" otherwise.
func TestWithArticle(t *testing.T) {
	for cls, want := range map[string]string{"authenticity": "an authenticity", "availability": "an availability",
		"address": "an address", "other": "an other", "price": "a price", "delivery_date": "a delivery_date"} {
		if got := withArticle(cls); got != want {
			t.Errorf("%s: %q, want %q", cls, got, want)
		}
	}
}
