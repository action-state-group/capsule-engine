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

// atB455063a is capsule-engine's live vectors at b455063a (#141, #144):
// each set's expected_live.json and README.md as the engine's fixture holds
// them, the check inputs capsulectl wrote, and under given/ each way the
// engine's test gives an input (its _given), as the bytes that function
// returns: what the engine decided, so Go decides the same envelope.
var atB455063a = filepath.Join(liveHistoryVectors, "engine-b455063a")

// historyActRow is one history entry as a vector states it: its target and
// the inputs beside it the act was read without (history-act-counterparty),
// or its state, record type and target (live-history-untyped-acts).
type historyActRow struct {
	CapsuleID     string   `json:"capsule_id"`
	Target        *string  `json:"target"`
	IgnoredInputs []string `json:"ignored_inputs"`
	LiveHistory   string   `json:"live_history"`
	RecordType    string   `json:"record_type"`
}

type buyerLiveVector struct {
	Set     string            `json:"set"`
	Input   string            `json:"input"`
	Variant string            `json:"variant"`
	History []historyActRow   `json:"history"`
	Outcome string            `json:"outcome"`
	Verdict string            `json:"verdict"`
	Rules   map[string]string `json:"rules"`
	Dedupe  struct {
		Result string `json:"result"`
		Reason string `json:"reason"`
	} `json:"dedupe"`
}

type buyerLiveVectors struct {
	Pack struct {
		PackID           string `json:"pack_id"`
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Decisions []buyerLiveVector `json:"decisions"`
}

// dedupeMatched is the capsule a failing dedupe names, as far as the
// engine's reason shows it: its first 16 hex digits.
var dedupeMatched = regexp.MustCompile(`^equivalent action already recorded \(([0-9a-f]{16})…\)$`)

// buyerVectorDifferences decides every vector of set under everyday as
// check --emit decides it (DecodeCheckInput, CheckWithHistory, Report),
// each from the envelope envelope names, and returns where Go differs from
// the engine ("<input> <variant> <what> Go <x> engine <y>"): the outcome,
// the verdict, every rule, dedupe's result and the act it matched, and each
// history act's target, ignored inputs, state and record type, as far as the
// vectors state them.
func buyerVectorDifferences(t *testing.T, set string, envelope func(buyerLiveVector) []byte) []string {
	t.Helper()
	v := decodeJSON[buyerLiveVectors](t, readFile(t, atB455063a, set, "expected_live.json"))
	p, err := pack.LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	if v.Pack.PackID != p.ID || v.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("%s: vectors under %+v, pack %s %s", set, v.Pack, p.ID, p.DefinitionDigest)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	var diffs []string
	for _, want := range v.Decisions {
		at := strings.TrimSpace(want.Input + " " + want.Variant)
		a, h, err := DecodeCheckInput(envelope(want))
		if err != nil {
			t.Fatalf("%s: %v", at, err)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatalf("%s: %v", at, err)
		}
		rep := e.Report(a, d)
		differ(&diffs, at, "outcome", d.Outcome, want.Outcome)
		differ(&diffs, at, "verdict", rep.Decision, want.Verdict)
		got := reportRules(rep)
		for _, rule := range slices.Sorted(maps.Keys(want.Rules)) {
			differ(&diffs, at, rule, got[rule], want.Rules[rule])
		}
		c, _ := d.Constraint("dedupe")
		differ(&diffs, at, "dedupe", string(c.Result), want.Dedupe.Result)
		matched, _ := c.MatchedCapsuleID()
		if m := dedupeMatched.FindStringSubmatch(want.Dedupe.Reason); m != nil {
			differ(&diffs, at, "dedupe matched", matched[:min(16, len(matched))], m[1])
		}
		if len(h.Acts) != len(want.History) {
			t.Fatalf("%s: %d history acts, the vector states %d", at, len(h.Acts), len(want.History))
		}
		for i, row := range want.History {
			r := h.Acts[i]
			differ(&diffs, at, "history "+row.CapsuleID+" capsule", r.CapsuleID, row.CapsuleID)
			target := r.Target
			if r.live == liveUnread {
				target = nil // the engine reads no act, so no target
			}
			differ(&diffs, at, "history "+row.CapsuleID+" target", fmt.Sprint(deref(target)), fmt.Sprint(deref(row.Target)))
			if row.IgnoredInputs != nil {
				differ(&diffs, at, "history "+row.CapsuleID+" ignored", fmt.Sprint(r.ignoredInputs), fmt.Sprint(row.IgnoredInputs))
			}
			if row.LiveHistory != "" {
				differ(&diffs, at, "history "+row.CapsuleID+" state", string(r.live), row.LiveHistory)
			}
		}
	}
	slices.Sort(diffs)
	return diffs
}

// reportRules is each rule's verdict in rep, in the engine's terms, as
// check --emit states it: a rule in parts (caps' limits) takes the first of
// fail, not_evaluable, pass, n/a its parts have.
func reportRules(rep CheckReport) map[string]string {
	out := map[string]string{}
	for _, r := range rep.Rules {
		verdicts := []string{r.Verdict}
		if len(r.Parts) > 0 {
			verdicts = nil
			for _, part := range r.Parts {
				verdicts = append(verdicts, part.Verdict)
			}
		}
		for _, v := range []string{VerdictFail, VerdictNotEvaluable, VerdictPass, VerdictNotApplicable} {
			if slices.Contains(verdicts, v) {
				out[r.Rule] = strings.ReplaceAll(v, VerdictNotApplicable, "n/a")
				break
			}
		}
	}
	return out
}

// CROSS-IMPLEMENTATION: capsule-engine #141's vectors at b455063a, the two
// record sets (x-deal-v0 and typed, capsulectl 424e794) in every way the
// engine gives a history act its counterparty: as sealed, accepted, without
// the profile key (the block beside the act is its target), without the
// block (no target), and with a block in another shape (ignored, named).
// Go agrees on all 20 rows but the pinned r26 fixture difference.
func TestHistoryActCounterpartyVectorsAgreeWithTheEngine(t *testing.T) {
	diffs := buyerVectorDifferences(t, "history-act-counterparty", func(v buyerLiveVector) []byte {
		name := strings.TrimSuffix(filepath.Base(v.Input), ".input.json")
		return readFile(t, atB455063a, "history-act-counterparty", "given", v.Set, name+"."+v.Variant+".json")
	})
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// CROSS-IMPLEMENTATION: capsule-engine #144's vectors, eight buyer check
// inputs whose history holds capsulectl's untyped x-deal-v0 acts (rc14 and
// main 2214b0f), each byte as capsulectl wrote it, with NO known difference,
// each history act's state and target included.
func TestLiveHistoryUntypedActVectorsAgreeWithTheEngine(t *testing.T) {
	diffs := buyerVectorDifferences(t, "live-history-untyped-acts", func(v buyerLiveVector) []byte {
		return readFile(t, atB455063a, "live-history-untyped-acts", v.Input)
	})
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// The given envelopes are what the engine's _given returns for each way:
// as sealed, each is the vendored input itself.
func TestTheGivenAsSealedEnvelopesAreTheInputs(t *testing.T) {
	for _, set := range []string{"typed", "x-deal-v0"} {
		for _, name := range []string{"other-merchant", "repeat-in-one-deal"} {
			var input, given any
			if err := json.Unmarshal(readFile(t, atB455063a, "history-act-counterparty", "inputs", set, name+".input.json"), &input); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(readFile(t, atB455063a, "history-act-counterparty", "given", set, name+".as-sealed.json"), &given); err != nil {
				t.Fatal(err)
			}
			a, _ := json.Marshal(input)
			b, _ := json.Marshal(given)
			if string(a) != string(b) {
				t.Errorf("%s/%s: the as-sealed envelope is not the input", set, name)
			}
		}
	}
}

// CROSS-IMPLEMENTATION: the seller's live vectors at b455063a (asg/seller/0.1.3,
// capsulectl 424e794): the five check inputs of the two-thread sale in all
// seven ways, the deal's claims passed as record.deal_claims (#140, AMENDMENT
// 9) and in the history included, decided as check --emit decides them,
// with NO known difference, the ledger the engine's history builder wrote
// included.
func TestSellerLiveHistoryVectorsAtB455063aAgreeWithTheEngine(t *testing.T) {
	given := func(name, history string) []byte {
		return readFile(t, atB455063a, "live-history", "given", name+"."+history+".json")
	}
	if diffs := liveVectorDifferences(t, filepath.Join("engine-b455063a", "live-history"), pack.LoadSeller014, given); len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}
