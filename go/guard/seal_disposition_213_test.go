package guard

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// sealDisposition213 are the Go vectors capsule-cli #213 staged for this
// plugin (README.md there): one two-thread sale and one two-deal booking,
// sealed by capsulectl c7ff0880 (merged as c3d464f7, the same code), whose
// offer, commit, sign and cancel acts seal their disposition and the
// counterparty of the check they rest on. Each file is the staged bytes,
// checked against the staged SHA256SUMS.
var sealDisposition213 = filepath.Join(liveHistoryVectors, "seal-disposition-213")

// sealDisposition213Files is how many files the set vendors: SHA256SUMS,
// the 22 files it lists (its own .tmp line names no file) and README.md.
const sealDisposition213Files = 24

func TestSealDisposition213VectorsMatchTheirSHA256SUMS(t *testing.T) {
	sums := readFile(t, sealDisposition213, "SHA256SUMS")
	if got := fileSHA256Hex(sums); got != "3eb6be91edd09e1e759294b04c65c135334bd1404f2c7b9931e3086809fbcd9d" {
		t.Fatalf("SHA256SUMS sha256 %s", got)
	}
	n := 0
	sc := bufio.NewScanner(bytes.NewReader(sums))
	for sc.Scan() {
		sum, name, ok := strings.Cut(sc.Text(), "  ")
		if !ok {
			t.Fatalf("SHA256SUMS line %q", sc.Text())
		}
		name = strings.TrimPrefix(name, "./")
		if name == "SHA256SUMS.tmp" {
			continue // the staging script's own scratch file, empty and not staged
		}
		if got := fileSHA256Hex(readFile(t, sealDisposition213, name)); got != sum {
			t.Errorf("%s sha256 %s, SHA256SUMS says %s", name, got, sum)
		}
		n++
	}
	if n != sealDisposition213Files-2 {
		t.Fatalf("SHA256SUMS lists %d staged files", n)
	}
}

// claimsInHistory is the engine's claim_entries (tests/test_live_history.py):
// every claim either thread's bundle seals by before, as a history entry (the
// capsule with its disclosed agent_input and the sale's item_ref beside it).
func claimsInHistory(t *testing.T, before string, itemRef json.RawMessage) []map[string]json.RawMessage {
	t.Helper()
	var out []map[string]json.RawMessage
	for _, own := range []string{"buyer-a.bundle.json", "buyer-b.bundle.json"} {
		var b struct {
			Records     []map[string]json.RawMessage `json:"records"`
			Disclosures map[string]struct {
				AgentInput json.RawMessage `json:"agent_input"`
			} `json:"disclosures"`
		}
		if err := json.Unmarshal(readFile(t, sealDisposition213, "live-history", own), &b); err != nil {
			t.Fatal(err)
		}
		for _, rec := range b.Records {
			var id, at string
			if json.Unmarshal(rec["capsule_id"], &id) != nil || json.Unmarshal(rec["timestamp"], &at) != nil {
				t.Fatalf("%s: a record without capsule_id or timestamp", own)
			}
			shown := b.Disclosures[id].AgentInput
			var kind struct {
				XDeal struct {
					RecordType string `json:"record_type"`
				} `json:"x-deal-v0"`
			}
			if len(shown) == 0 || json.Unmarshal(shown, &kind) != nil || kind.XDeal.RecordType != "claim" || at > before {
				continue
			}
			entry := map[string]json.RawMessage{}
			for k, v := range rec {
				entry[k] = v
			}
			entry["agent_input"], entry[itemRefInput] = shown, itemRef
			out = append(out, entry)
		}
	}
	return out
}

// entryTime is a history entry's timestamp.
func entryTime(t *testing.T, e map[string]json.RawMessage) string {
	t.Helper()
	var at string
	if err := json.Unmarshal(e["timestamp"], &at); err != nil {
		t.Fatal(err)
	}
	return at
}

// given213 is the check input name of the staged live-history set given as
// history says: the engine's _given (tests/test_live_history.py) for the six
// ways expected_live.without-deal-claims-step.json decides.
func given213(t *testing.T, name, history string) []byte {
	t.Helper()
	var env map[string]json.RawMessage
	if err := json.Unmarshal(readFile(t, sealDisposition213, "live-history", "check-inputs", name+".json"), &env); err != nil {
		t.Fatal(err)
	}
	if history == historyAsSealed {
		return readFile(t, sealDisposition213, "live-history", "check-inputs", name+".json")
	}
	var entries []map[string]json.RawMessage
	if err := json.Unmarshal(env["history"], &entries); err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		e["disposition"] = json.RawMessage(`{"decision":"accept"}`)
	}
	switch history {
	case historyWithClaims:
		var rec struct {
			Timestamp string `json:"timestamp"`
		}
		if err := json.Unmarshal(env["record"], &rec); err != nil {
			t.Fatal(err)
		}
		entries = append(entries, claimsInHistory(t, rec.Timestamp, env[itemRefInput])...)
		slices.SortStableFunc(entries, func(a, b map[string]json.RawMessage) int {
			return strings.Compare(entryTime(t, b), entryTime(t, a))
		})
	case historyCommitNoItem:
		for _, e := range entries {
			var shown struct {
				Body struct {
					ActionClass string `json:"action_class"`
				} `json:"body"`
			}
			if err := json.Unmarshal(e["agent_input"], &shown); err != nil {
				t.Fatal(err)
			}
			if shown.Body.ActionClass == "agreement.accept" {
				delete(e, itemRefInput)
			}
		}
	}
	b, err := json.Marshal(entries)
	if err != nil {
		t.Fatal(err)
	}
	env["history"] = b
	switch history {
	case historyRecordNoItem:
		delete(env, itemRefInput)
	case historyAbsent:
		delete(env, "history")
		delete(env, "history_scope")
	}
	out, err := json.Marshal(env)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// CROSS-IMPLEMENTATION: the two-thread sale as capsulectl #213 seals it,
// under asg/seller/0.1.3: the five check inputs in the six ways the engine
// (capsule-engine fd43d43) decided them, the history as sealed first, with
// NO known difference: outcome, verdict, completeness, every rule,
// single_commitment's result, reason and evidence, and the ledger (each
// act's state and the decision its capsule seals).
func TestSealDisposition213SaleAgreesWithTheEngine(t *testing.T) {
	given := func(name, history string) []byte { return given213(t, name, history) }
	file := filepath.Join("seal-disposition-213", "live-history", "expected_live.without-deal-claims-step.json")
	if diffs := liveVectorFileDifferences(t, file, pack.LoadSeller013, given); len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// The sale as sealed, read the way the engine reads it: every act in a
// check input's history seals an accept disposition, which single_commitment
// reads as the act's decision. B's commit after A's is refused on s11 (the
// sale has an acceptance); A's own commit is allowed.
func TestSealDisposition213SaleAsSealed(t *testing.T) {
	p, err := pack.LoadSeller013()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]struct {
		outcome, s11 string
		accepted     bool
	}{
		"a-commit-1900": {"allow", "pass", false},
		"b-commit-1850": {"deny", "fail", true},
		"b-offer-1850":  {"deny", "fail", true},
	} {
		a, h, err := DecodeCheckInput(given213(t, name, historyAsSealed))
		if err != nil {
			t.Fatal(err)
		}
		if len(h.Acts) == 0 {
			t.Fatalf("%s: no history", name)
		}
		for _, r := range h.Acts {
			if r.Stated != nil {
				continue
			}
			if r.live != liveDisposition || r.sealed == nil || *r.sealed != "accept" {
				t.Errorf("%s: history act %s reads %s, sealed %v", name, r.CapsuleID, r.live, deref(r.sealed))
			}
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		c, _ := d.Constraint("single_commitment")
		got, _, err := c.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		ev := fmt.Sprintf(`{"constraint_id":"single_commitment","sale_has_acceptance":%t}`, want.accepted)
		if d.Outcome != want.outcome || string(c.Result) != want.s11 || got != jsonDigest(t, json.RawMessage(ev)) {
			t.Errorf("%s: %s, s11 %s, evidence is not %s", name, d.Outcome, c.Result, ev)
		}
	}
}

// CROSS-IMPLEMENTATION, buyer side: the two-deal booking as capsulectl #213
// seals it (every act now carries its check's counterparty), under
// asg/everyday/0.3.6, in the five ways the engine (fd43d43) decided it.
// The plugin's routing change does not touch a buyer's deal; this holds the
// buyer's result to the engine's on the new bytes.
func TestSealDisposition213BookingAgreesWithTheEngine(t *testing.T) {
	v := decodeJSON[buyerLiveVectors](t, readFile(t, sealDisposition213, "history-act-counterparty", "expected_live.json"))
	p, err := pack.LoadEveryday036()
	if err != nil {
		t.Fatal(err)
	}
	if v.Pack.PackID != p.ID || v.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors under %+v, pack %s %s", v.Pack, p.ID, p.DefinitionDigest)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	var diffs []string
	for _, want := range v.Decisions {
		at := want.Input + " " + want.Variant
		a, h, err := DecodeCheckInput(givenBooking213(t, want.Input, want.Variant))
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
		for rule, w := range want.Rules {
			differ(&diffs, at, rule, got[rule], w)
		}
		c, _ := d.Constraint("dedupe")
		differ(&diffs, at, "dedupe", string(c.Result), want.Dedupe.Result)
		matched, _ := c.MatchedCapsuleID()
		if m := dedupeMatched.FindStringSubmatch(want.Dedupe.Reason); m != nil {
			differ(&diffs, at, "dedupe matched", matched[:min(16, len(matched))], m[1])
		}
	}
	slices.Sort(diffs)
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// givenBooking213 is the engine's _given (tests/test_history_act_counterparty.py)
// on a staged booking input: as sealed; an accept disposition on every
// history act; and, with it, no counterparty_profile, then also no
// counterparty block, or a block with uppercase ids.
func givenBooking213(t *testing.T, input, variant string) []byte {
	t.Helper()
	raw := readFile(t, sealDisposition213, "history-act-counterparty", input)
	if variant == "as-sealed" {
		return raw
	}
	var env map[string]json.RawMessage
	if err := json.Unmarshal(raw, &env); err != nil {
		t.Fatal(err)
	}
	var entries []map[string]json.RawMessage
	if err := json.Unmarshal(env["history"], &entries); err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		e["disposition"] = json.RawMessage(`{"decision":"accept"}`)
	}
	if variant != "disposition-accept" {
		var rec map[string]json.RawMessage
		if err := json.Unmarshal(env["record"], &rec); err != nil {
			t.Fatal(err)
		}
		delete(rec, "counterparty_profile")
		b, err := json.Marshal(rec)
		if err != nil {
			t.Fatal(err)
		}
		env["record"] = b
		for _, e := range entries {
			delete(e, "counterparty_profile")
			switch variant {
			case "disposition-accept-no-profile-no-counterparty":
				delete(e, "counterparty")
			case "disposition-accept-no-profile-malformed":
				var good struct {
					FpAlg string `json:"fp_alg"`
					IDs   struct {
						Payee string `json:"payee"`
					} `json:"ids"`
				}
				if err := json.Unmarshal(e["counterparty"], &good); err != nil {
					t.Fatal(err)
				}
				bad, err := json.Marshal(map[string]any{"fp_alg": good.FpAlg, "ids": map[string]string{"payee": strings.ToUpper(good.IDs.Payee)}})
				if err != nil {
					t.Fatal(err)
				}
				e["counterparty"] = bad
			}
		}
	}
	b, err := json.Marshal(entries)
	if err != nil {
		t.Fatal(err)
	}
	env["history"] = b
	out, err := json.Marshal(env)
	if err != nil {
		t.Fatal(err)
	}
	return out
}
