package guard

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// saleReplayVectors are capsule-engine #147's vectors at 7b2d0339, with
// #148's findings at a6405ea2 (tests/fixtures/sale-replay/, capsulectl
// 9691acadf137): the user's own
// copy of a two-thread sale, five check inputs, the documented fixture
// steps, each check decided live and by the replay of the copy as written
// and with B withheld, and twelve tampered copies with the engine's
// findings (PROVENANCE.json).
var saleReplayVectors = filepath.Join("testdata", "sale-replay-vectors")

// The fixture's sale bundles and threads, and the ways its tests give them.
const (
	saleAsWritten = "sale.bundle.json"
	saleBWithheld = "sale-b-withheld"
	saleThreadA   = "deal-ae1582c2fe995822"
	saleThreadB   = "deal-73bb7fe1c1462a86"
	saleDisposed  = "disposition-accept"
)

// saleVerified is what capsulectl 9691acadf137 verify --bundle reports of
// the sale as written (verify/sale.verify.json, which the plugin reads):
// the sale's own bundle, each of its records and every carried thread VALID.
var saleVerified = SaleVerification{Sale: true, Records: saleRecordsVerified(), Threads: map[string]bool{saleThreadA: true, saleThreadB: true}}

// saleRecordsVerified are the sale's own records verify/sale.verify.json
// says verify: record_identity passed, and record_signatures not invalid.
func saleRecordsVerified() map[string]bool {
	raw, err := os.ReadFile(filepath.Join(saleReplayVectors, "verify", "sale.verify.json"))
	if err != nil {
		panic(err) // the vendored vectors are part of the package's tests
	}
	var report struct {
		Identity   map[string]string `json:"record_identity"`
		Signatures map[string]string `json:"record_signatures"`
	}
	if err := json.Unmarshal(raw, &report); err != nil {
		panic(err)
	}
	out := map[string]bool{}
	for id, status := range report.Identity {
		signature, ok := report.Signatures[id]
		out[id] = status == "passed" && ok && signature != "invalid"
	}
	return out
}

type saleLedgerRow struct {
	CapsuleID   string  `json:"capsule_id"`
	LiveHistory string  `json:"live_history"`
	Decision    *string `json:"decision"`
	ActionClass *string `json:"action_class"`
	DealID      *string `json:"deal_id"`
	ItemRef     *string `json:"item_ref"`
	Target      *string `json:"target"`
}

type saleReplayVector struct {
	Sale             string            `json:"sale"`
	Input            string            `json:"input"`
	History          string            `json:"history"`
	Ledger           []saleLedgerRow   `json:"ledger"`
	Outcome          string            `json:"outcome"`
	Verdict          string            `json:"verdict"`
	Rules            map[string]string `json:"rules"`
	SingleCommitment struct {
		Result   string          `json:"result"`
		Reason   string          `json:"reason"`
		Evidence json.RawMessage `json:"evidence"`
	} `json:"single_commitment"`
}

type saleReplayDocument struct {
	Pack struct {
		PackID           string `json:"pack_id"`
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Sales map[string]struct {
		Complete bool     `json:"complete"`
		Findings []string `json:"findings"`
	} `json:"sales"`
	Decisions []saleReplayVector `json:"decisions"`
}

// withheldThread is fixture_steps.withhold_thread: the copy an adjudicator
// receives with the present thread withheld, its sale_threads entry keeping
// only its registration and member missing, and x-deal-sale/v0 no longer
// carrying it.
func withheldThread(t *testing.T, raw []byte, thread string) []byte {
	t.Helper()
	b := decodeJSON[map[string]json.RawMessage](t, raw)
	ext := decodeJSON[map[string]json.RawMessage](t, b["extensions"])
	section := decodeJSON[map[string]json.RawMessage](t, ext["x-deal-v0"])
	entries := decodeJSON[[]map[string]json.RawMessage](t, section["sale_threads"])
	found := 0
	for i, entry := range entries {
		if string(entry["thread_id"]) == `"`+thread+`"` && string(entry["member"]) == `"present"` {
			entries[i] = map[string]json.RawMessage{"registration": entry["registration"], "member": json.RawMessage(`"missing"`)}
			found++
		}
	}
	if found != 1 {
		t.Fatalf("%d present entries of %s", found, thread)
	}
	carrier := decodeJSON[map[string]json.RawMessage](t, ext[SaleExtension])
	threads := decodeJSON[map[string]json.RawMessage](t, carrier["threads"])
	delete(threads, thread)
	carrier["threads"] = encodeJSON(t, threads)
	section["sale_threads"] = encodeJSON(t, entries)
	ext["x-deal-v0"], ext[SaleExtension] = encodeJSON(t, section), encodeJSON(t, carrier)
	b["extensions"] = encodeJSON(t, ext)
	return encodeJSON(t, b)
}

func encodeJSON[T any](t *testing.T, v T) json.RawMessage {
	t.Helper()
	out, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// withActDispositions is fixture_steps.with_act_dispositions: the sale with
// {"decision": "accept"} as the disposition of every carried act record,
// added after the sale was read, as the engine's step adds it after
// read_sale_bundle verified it. It refuses a sale whose acts already seal
// one.
func withActDispositions(t *testing.T, s SaleBundle) SaleBundle {
	t.Helper()
	out := s
	out.Threads = nil
	for _, thread := range s.Threads {
		records := make([]jsonObject, 0, len(thread.records))
		for _, r := range thread.records {
			id, _ := textOf(r["capsule_id"])
			if typeOf(objectOf(thread.disclosed[id])) == typedActRecord {
				if _, ok := r["disposition"]; ok {
					t.Fatal("an act already seals a disposition: rebuild the fixture and delete this step")
				}
				r = maps.Clone(r)
				r["disposition"] = json.RawMessage(`{"decision":"accept"}`)
			}
			records = append(records, r)
		}
		thread.records = records
		out.Threads = append(out.Threads, thread)
	}
	return out
}

func readSale(t *testing.T, name string) SaleBundle {
	t.Helper()
	raw := readFile(t, saleReplayVectors, saleAsWritten)
	if name == saleBWithheld {
		raw = readFile(t, saleReplayVectors, saleBWithheld+".bundle.json")
	}
	s, err := ReadSaleBundle(raw, saleVerified)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func givenSale(t *testing.T, name, history string) SaleBundle {
	t.Helper()
	s := readSale(t, name)
	if history == saleDisposed {
		return withActDispositions(t, s)
	}
	return s
}

// checkedCapsule is the capsule id of a check input's record.
func checkedCapsule(t *testing.T, input string) string {
	t.Helper()
	var in struct {
		Record struct {
			CapsuleID string `json:"capsule_id"`
		} `json:"record"`
	}
	if err := json.Unmarshal(readFile(t, saleReplayVectors, input), &in); err != nil {
		t.Fatal(err)
	}
	return in.Record.CapsuleID
}

// saleRows is the ledger single_commitment read, as the engine's test
// writes it: each act's state, sealed decision, class, deal, item_ref and
// target, oldest first, then the incomplete record when the sale's
// acceptance is unknown.
func saleRows(t *testing.T, a Action, h History) []saleLedgerRow {
	t.Helper()
	rows := []saleLedgerRow{}
	for _, r := range liveOrder(h.Acts) {
		rows = append(rows, saleLedgerRow{CapsuleID: r.CapsuleID, LiveHistory: string(r.live), Decision: r.sealed,
			ActionClass: r.ActionClass, DealID: r.DealID, ItemRef: r.ItemRef, Target: r.Target})
	}
	if h.saleUnknown() {
		id, err := canon.Digest(canon.Obj(canon.M("operator", canon.Str(a.Operator)), canon.M("timestamp", canon.Str(a.Timestamp)),
			canon.M("asg_payload", canon.Obj(canon.M("live_history", canon.Str("incomplete"))))))
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, saleLedgerRow{CapsuleID: id, LiveHistory: "incomplete"})
	}
	return rows
}

// sealOrder is every carried thread's records in seal order, as
// SaleBundle.records orders them: by the second each names, and within one
// second by registration order, then each thread's own order (every record
// of the fixture names one).
func sealOrder(t *testing.T, s SaleBundle) []struct {
	thread SaleThread
	record jsonObject
} {
	t.Helper()
	type at struct {
		sec           int64
		thread, index int
		th            SaleThread
		r             jsonObject
	}
	var all []at
	for ti, th := range s.Threads {
		for i, r := range th.records {
			sec := secondOf(r["timestamp"])
			if sec == nil {
				t.Fatalf("%s: a record names no time", th.ID)
			}
			all = append(all, at{sec.Unix(), ti, i, th, r})
		}
	}
	slices.SortStableFunc(all, func(x, y at) int {
		if x.sec != y.sec {
			return int(x.sec - y.sec)
		}
		if x.thread != y.thread {
			return x.thread - y.thread
		}
		return x.index - y.index
	})
	out := make([]struct {
		thread SaleThread
		record jsonObject
	}, len(all))
	for i, x := range all {
		out[i].thread, out[i].record = x.th, x.r
	}
	return out
}

// replayLedger is the ledger the engine's replay of s (replay_sale) has
// written when it reaches the record capsuleID: every carried thread's
// records in seal order, a claim the agent made writing its statement once
// (StatementOf), a record it does not disclose or that states no act
// getting no decision, and every other record decided as check --record
// decides a sale bundle's record (SaleBundle.Check, CheckSale) on the
// ledger so far and written to it as a dry run.
func replayLedger(t *testing.T, e *Engine, s SaleBundle, capsuleID string) []Record {
	t.Helper()
	return replayLedgerSealed(t, e, s, capsuleID, nil)
}

// replayLedgerSealed is replayLedger with each decided record given the
// capsule id sealed names for it, where it names one: the caller seals, so
// a replay that sealed its own decisions (the engine's) names them.
func replayLedgerSealed(t *testing.T, e *Engine, s SaleBundle, capsuleID string, sealed map[string]string) []Record {
	t.Helper()
	var ledger []Record
	claims := map[string]bool{}
	for _, x := range sealOrder(t, s) {
		id, _ := textOf(x.record["capsule_id"])
		if id == capsuleID {
			return ledger
		}
		shown, ok := x.thread.disclosed[id]
		if !ok {
			continue
		}
		capsule, err := json.Marshal(x.record)
		if err != nil {
			t.Fatal(err)
		}
		if stated, ok := StatementOf(capsule, shown); ok {
			if !claims[stated.Stated.Claim] {
				claims[stated.Stated.Claim] = true
				ledger = append(ledger, stated)
			}
			continue
		}
		a, h, err := s.Check(id)
		if err != nil || a.statesNoAct || len(a.Unevaluable()) > 0 {
			continue
		}
		d, err := e.CheckSale(a, ledger, h)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		rec := RecordFor(a, d)
		rec.CapsuleID, rec.DryRun = id, ptr(true)
		if as, ok := sealed[id]; ok {
			rec.CapsuleID = as
		}
		ledger = append(ledger, rec)
	}
	t.Fatalf("no carried record %s", capsuleID)
	return nil
}

// saleReplayDifferences decides every replay vector as check --record
// decides a sale bundle's check (SaleBundle.Check, CheckSale) and returns
// where Go differs from the engine.
func saleReplayDifferences(t *testing.T) []string {
	t.Helper()
	v := decodeJSON[saleReplayDocument](t, readFile(t, saleReplayVectors, "expected_replay.json"))
	e, p := seller014Engine(t)
	if v.Pack.PackID != p.ID || v.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors under %+v, pack %s %s", v.Pack, p.ID, p.DefinitionDigest)
	}
	var diffs []string
	for _, name := range slices.Sorted(maps.Keys(v.Sales)) {
		want := v.Sales[name]
		s := readSale(t, name)
		differ(&diffs, name, "complete", fmt.Sprint(s.Complete()), fmt.Sprint(want.Complete))
		differ(&diffs, name, "findings", fmt.Sprint(s.Findings), fmt.Sprint(want.Findings))
	}
	for _, want := range v.Decisions {
		at := strings.Join([]string{want.Sale, want.Input, want.History}, " ")
		sale := givenSale(t, want.Sale, want.History)
		a, h, err := sale.Check(checkedCapsule(t, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", at, err)
		}
		d, err := e.CheckSale(a, replayLedger(t, e, sale, checkedCapsule(t, want.Input)), h)
		if err != nil {
			t.Fatalf("%s: %v", at, err)
		}
		rep := e.Report(a, d)
		differ(&diffs, at, "outcome", d.Outcome, want.Outcome)
		differ(&diffs, at, "verdict", rep.Decision, floorUnreadVerdict(want.Verdict, d))
		got := rulesOf(d, p)
		for _, rule := range slices.Sorted(maps.Keys(want.Rules)) {
			differ(&diffs, at, rule, got[rule], want.Rules[rule])
		}
		c, _ := d.Constraint("single_commitment")
		sc := want.SingleCommitment
		differ(&diffs, at, "s11 result", string(c.Result), sc.Result)
		digest, _, err := c.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		differ(&diffs, at, "s11 evidence", digest, jsonDigest(t, sc.Evidence))
		switch c.Result {
		case NotApplicable:
			differ(&diffs, at, "s11 reason", c.Pending(), sc.Reason)
		case Fail:
			differ(&diffs, at, "s11 reason", c.failNote, sc.Reason)
		}
		gotRows, _ := json.Marshal(saleRows(t, a, h))
		wantRows, _ := json.Marshal(want.Ledger)
		differ(&diffs, at, "ledger", string(gotRows), string(wantRows))
	}
	if len(v.Sales) != 2 || len(v.Decisions) != 16 {
		t.Errorf("%d sales and %d decisions, the engine's 2 and 16", len(v.Sales), len(v.Decisions))
	}
	slices.Sort(diffs)
	return diffs
}

// CROSS-IMPLEMENTATION: capsule-engine #147's replay vectors (7b2d0339,
// capsulectl 9691acadf137), every check of each sale bundle (as written,
// and with B withheld) with its acts as sealed and with an accept
// disposition added, decided as check --record decides a sale
// bundle's check, with NO known difference: whether each sale is complete
// and its findings, the outcome, the verdict, every rule's result,
// single_commitment's result, reason and evidence, and the ledger it read.
func TestSaleReplayVectorsAgreeWithTheEngine(t *testing.T) {
	if diffs := saleReplayDifferences(t); len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// saleLiveVector is one expected_live.json decision of #147's fixture.
type saleLiveVector struct {
	Input            string            `json:"input"`
	History          string            `json:"history"`
	Ledger           []saleLedgerRow   `json:"ledger"`
	Outcome          string            `json:"outcome"`
	Verdict          string            `json:"verdict"`
	Rules            map[string]string `json:"rules"`
	SingleCommitment struct {
		Result   string          `json:"result"`
		Reason   string          `json:"reason"`
		Evidence json.RawMessage `json:"evidence"`
	} `json:"single_commitment"`
}

// saleLiveInput is a check input of the fixture as its test gives it: as
// capsulectl wrote it, or with an accept disposition on every history entry.
func saleLiveInput(t *testing.T, input, history string) []byte {
	t.Helper()
	raw := readFile(t, saleReplayVectors, input)
	if history != saleDisposed {
		return raw
	}
	in := decodeJSON[map[string]json.RawMessage](t, raw)
	entries := decodeJSON[[]map[string]json.RawMessage](t, in["history"])
	for _, e := range entries {
		e["disposition"] = json.RawMessage(`{"decision":"accept"}`)
	}
	in["history"] = encodeJSON(t, entries)
	return encodeJSON(t, in)
}

// s11Cell is single_commitment's result, reason and evidence digest. A pass
// carries no reason in Go (the engine's pass reason is its own text), so a
// pass compares on its result and evidence, as the other vectors do.
func s11Cell(t *testing.T, d Decision) string {
	t.Helper()
	c, _ := d.Constraint("single_commitment")
	digest, _, err := c.EvidenceDigest()
	if err != nil {
		t.Fatal(err)
	}
	reason := ""
	switch c.Result {
	case NotApplicable:
		reason = c.Pending()
	case Fail:
		reason = c.failNote
	}
	return strings.Join([]string{string(c.Result), reason, digest}, " | ")
}

// CROSS-IMPLEMENTATION: #147's live vectors, each check input decided as
// check --emit decides it, as capsulectl wrote it and with an accept
// disposition on every history act, with NO known difference: the outcome,
// the verdict, every rule, single_commitment's result, reason and evidence,
// and the ledger (each act and statement, then the incomplete record).
func TestSaleLiveVectorsAgreeWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Decisions []saleLiveVector `json:"decisions"`
	}](t, readFile(t, saleReplayVectors, "expected_live.json"))
	e, p := seller014Engine(t)
	var diffs []string
	for _, want := range v.Decisions {
		at := want.Input + " " + want.History
		a, h, err := DecodeCheckInput(saleLiveInput(t, want.Input, want.History))
		if err != nil {
			t.Fatalf("%s: %v", at, err)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		differ(&diffs, at, "outcome", d.Outcome, want.Outcome)
		differ(&diffs, at, "verdict", e.Report(a, d).Decision, want.Verdict)
		got := rulesOf(d, p)
		for _, rule := range slices.Sorted(maps.Keys(want.Rules)) {
			differ(&diffs, at, rule, got[rule], want.Rules[rule])
		}
		sc := want.SingleCommitment
		reason := sc.Reason
		if sc.Result == string(Pass) {
			reason = ""
		}
		differ(&diffs, at, "s11", s11Cell(t, d), strings.Join([]string{sc.Result, reason, jsonDigest(t, sc.Evidence)}, " | "))
		rows := []saleLedgerRow{}
		for _, r := range append(liveOrder(h.Acts), h.claimed...) {
			row := saleLedgerRow{CapsuleID: r.CapsuleID, LiveHistory: string(r.live), Decision: r.sealed, ActionClass: r.ActionClass,
				DealID: r.DealID, ItemRef: r.ItemRef, Target: r.Target}
			if r.Stated != nil {
				row.LiveHistory = "statement"
			}
			rows = append(rows, row)
		}
		rows = append(rows, saleRows(t, a, History{Complete: !h.saleUnknown()})...)
		gotRows, _ := json.Marshal(rows)
		wantRows, _ := json.Marshal(want.Ledger)
		differ(&diffs, at, "ledger", string(gotRows), string(wantRows))
	}
	if len(v.Decisions) != 10 {
		t.Errorf("%d live decisions, the engine's 10", len(v.Decisions))
	}
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// The replay of the copy as capsulectl writes it (its sale's log shows each
// thread opened and cut whole, after every check) decides s11 as the live
// check does, cell for cell (result, reason and evidence), as sealed and
// with dispositions: A's follow-through passes and B after A is refused.
// With B withheld, every s11 cell is not evaluable for the history, and
// refused.
func TestSaleReplayDecidesS11AsLive(t *testing.T) {
	e, _ := seller014Engine(t)
	inputs := []string{"a-offer-1900", "a-commit-1900", "b-offer-1850", "b-commit-1850", "a-commit-again-1900"}
	results := map[string]int{}
	for _, history := range []string{"as-sealed", saleDisposed} {
		for _, name := range inputs {
			input := "check-inputs/" + name + ".json"
			a, h, err := DecodeCheckInput(saleLiveInput(t, input, history))
			if err != nil {
				t.Fatal(err)
			}
			live, err := e.CheckWithHistory(a, h)
			if err != nil {
				t.Fatal(err)
			}
			for _, name := range []string{saleAsWritten, saleBWithheld} {
				sale := givenSale(t, name, history)
				ra, rh, err := sale.Check(checkedCapsule(t, input))
				if err != nil {
					if name == saleBWithheld && strings.HasPrefix(input, "check-inputs/b-") {
						continue // B's checks are not in the copy
					}
					t.Fatal(err)
				}
				replayed, err := e.CheckSale(ra, replayLedger(t, e, sale, checkedCapsule(t, input)), rh)
				if err != nil {
					t.Fatal(err)
				}
				cell := s11Cell(t, replayed)
				switch {
				case name == saleAsWritten:
					if want := s11Cell(t, live); cell != want {
						t.Errorf("%s %s: replay s11 %s, live %s", input, history, cell, want)
					}
					results[cell[:strings.Index(cell, " ")]]++
				case !strings.HasPrefix(cell, "n/a | "+saleUnknownReasons["history"]) || replayed.Outcome != Deny:
					t.Errorf("%s %s %s: s11 %s, outcome %s; want n/a for the history, refused", name, input, history, cell, replayed.Outcome)
				}
			}
		}
	}
	// As sealed: A's offer and commit pass, the rest wait on a disposition;
	// disposed: A's three pass, B's two fail.
	if results["pass"] != 5 || results["fail"] != 2 || results["n/a"] != 3 {
		t.Errorf("s11 on the copy as written: %v", results)
	}
}

// A thread's own bundle replayed alone is never the sale: read as a sale
// bundle it is not one (incomplete), and checked as any bundle's record
// (check --record) its commit names no item, so s11 is not evaluable and
// refuses it. It never passes.
func TestAThreadBundleAloneStaysIncomplete(t *testing.T) {
	e, _ := seller014Engine(t)
	sale := readSale(t, saleAsWritten)
	var thread SaleThread
	for _, th := range sale.Threads {
		if th.ID == saleThreadA {
			thread = th
		}
	}
	if IsSaleBundle(thread.bundle) {
		t.Fatal("a thread bundle reads as a sale bundle")
	}
	alone, err := ReadSaleBundle(thread.bundle, saleVerified)
	if err != nil {
		t.Fatal(err)
	}
	if alone.Complete() || !slices.Contains(alone.Findings, "not_a_sale_bundle") {
		t.Errorf("a thread alone: complete %v, findings %v", alone.Complete(), alone.Findings)
	}
	for _, name := range []string{"a-commit-1900", "a-commit-again-1900"} {
		a, err := DecodeRecord(thread.bundle, checkedCapsule(t, "check-inputs/"+name+".json"))
		if err != nil {
			t.Fatal(err)
		}
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatal(err)
		}
		if cell := s11Cell(t, d); !strings.HasPrefix(cell, "n/a | the action names no item") || d.Outcome != Deny {
			t.Errorf("%s alone: s11 %s, outcome %s", name, cell, d.Outcome)
		}
	}
}

// The copy with B withheld is the engine's documented step
// (fixture_steps.withhold_thread, run on sale.bundle.json), and
// withheldThread is that step: the two agree member for member.
func TestTheWithheldCopyIsTheFixtureStep(t *testing.T) {
	got, err := canon.FromJSON(withheldThread(t, readFile(t, saleReplayVectors, saleAsWritten), saleThreadB))
	if err != nil {
		t.Fatal(err)
	}
	want, err := canon.FromJSON(readFile(t, saleReplayVectors, saleBWithheld+".bundle.json"))
	if err != nil {
		t.Fatal(err)
	}
	g, _ := canon.JCS(got)
	w, _ := canon.JCS(want)
	if g != w {
		t.Error("withheldThread is not the fixture step's copy")
	}
}

// Every vendored file is byte for byte what PROVENANCE.json records, and it
// records every file, at the merge of #149.
func TestSaleReplayVectorsAreVendoredAsRecorded(t *testing.T) {
	prov := decodeJSON[struct {
		EngineCommit string `json:"engine_commit"`
		Files        map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}](t, readFile(t, saleReplayVectors, "PROVENANCE.json"))
	if prov.EngineCommit != "fd43d4329ea31d69e9bdd0a79f23f013c3615219" {
		t.Fatalf("vectors at %s", prov.EngineCommit)
	}
	var onDisk, recorded []string
	err := fs.WalkDir(os.DirFS(saleReplayVectors), ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || p == "PROVENANCE.json" {
			return err
		}
		onDisk = append(onDisk, p)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for name, f := range prov.Files {
		recorded = append(recorded, name)
		if got := fileSHA256Hex(readFile(t, saleReplayVectors, name)); got != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, got, f.SHA256)
		}
	}
	slices.Sort(onDisk)
	if slices.Sort(recorded); !slices.Equal(onDisk, recorded) || len(recorded) != 39 {
		t.Fatalf("vendored %v, provenance records %v", onDisk, recorded)
	}
}

// jsonEdit is raw with the member at path (object keys, or array indexes
// as decimal text) set to value, or removed when value is nil.
func jsonEdit(t *testing.T, raw json.RawMessage, path []string, value json.RawMessage) json.RawMessage {
	t.Helper()
	if len(path) == 0 {
		return value
	}
	if strings.HasPrefix(string(raw), "[") {
		items := decodeJSON[[]json.RawMessage](t, raw)
		var i int
		if _, err := fmt.Sscan(path[0], &i); err != nil || i >= len(items) {
			t.Fatalf("no item %s", path[0])
		}
		if value == nil && len(path) == 1 {
			return encodeJSON(t, slices.Delete(items, i, i+1))
		}
		items[i] = jsonEdit(t, items[i], path[1:], value)
		return encodeJSON(t, items)
	}
	o := decodeJSON[map[string]json.RawMessage](t, raw)
	if value == nil && len(path) == 1 {
		delete(o, path[0])
	} else {
		o[path[0]] = jsonEdit(t, o[path[0]], path[1:], value)
	}
	return encodeJSON(t, o)
}

// Each link of the sale bundle, broken on the real copy as written, is named
// by its finding and leaves the sale incomplete (read_sale_bundle's links).
func TestEachBrokenSaleLinkIsNamed(t *testing.T) {
	raw := json.RawMessage(readFile(t, saleReplayVectors, saleAsWritten))
	b := decodeJSON[struct {
		Records []struct {
			CapsuleID string `json:"capsule_id"`
		} `json:"records"`
		Disclosures map[string]struct {
			AgentInput struct {
				XDeal struct {
					RecordType string `json:"record_type"`
				} `json:"x-deal-v0"`
			} `json:"agent_input"`
		} `json:"disclosures"`
	}](t, raw)
	of := func(recordType string) string {
		for _, r := range b.Records {
			if b.Disclosures[r.CapsuleID].AgentInput.XDeal.RecordType == recordType {
				return r.CapsuleID
			}
		}
		t.Fatalf("no %s record", recordType)
		return ""
	}
	threads := []string{"extensions", SaleExtension, "threads"}
	entry := []string{"extensions", "x-deal-v0", "sale_threads", "0"}
	q := func(s string) json.RawMessage { return encodeJSON(t, s) }
	for _, c := range []struct {
		name    string
		path    []string
		value   json.RawMessage
		v       SaleVerification
		finding string
	}{
		{"a registration not disclosed", []string{"disclosures", of("thread")}, nil, saleVerified, "sale_record_not_disclosed"},
		{"no sale root", []string{"disclosures", of("sale")}, nil, saleVerified, "not_a_sale_bundle"},
		{"the sale not VALID", nil, nil, SaleVerification{Sale: false, Records: saleVerified.Records, Threads: saleVerified.Threads}, "sale_bundle_not_valid"},
		{"a thread not VALID", nil, nil, SaleVerification{Sale: true, Records: saleVerified.Records, Threads: map[string]bool{saleThreadA: true}}, "thread_not_valid"},
		{"threads predate registration", []string{"extensions", "x-deal-v0", "threads_predate_registration"}, json.RawMessage("true"), saleVerified, "threads_predate_registration"},
		{"no sale_threads", []string{"extensions", "x-deal-v0", "sale_threads"}, nil, saleVerified, "no_sale_threads"},
		{"an entry dropped", []string{"extensions", "x-deal-v0", "sale_threads", "1"}, nil, saleVerified, "sale_threads_do_not_match_the_registrations"},
		{"an entry naming another registration", append(entry, "registration", "digest"), q(strings.Repeat("0", 64)), saleVerified, "sale_threads_do_not_match_the_registrations"},
		{"an unknown member", append(entry, "member"), q("shown"), saleVerified, "unknown_thread_state"},
		{"a nonce that does not open", append(entry, "nonce"), q(strings.Repeat("1", 64)), saleVerified, "registration_opening_does_not_match"},
		{"a thread opened twice", append(slices.Clone(entry[:3]), "1", "thread_id"), q(saleThreadA), saleVerified, "registration_opening_does_not_match"},
		{"a thread carried but not present", append(entry, "member"), q("never_opened"), saleVerified, "carried_threads_are_not_the_present_ones"},
		{"a thread's checkpoint key", append(slices.Clone(threads), saleThreadA, "checkpoint", "key_id"), q(strings.Repeat("2", 64)), saleVerified, "thread_not_signed_as_the_sale"},
		{"a thread's certificate", append(slices.Clone(threads), saleThreadA, "completeness_certificate", "first_seq"), json.RawMessage("2"), saleVerified, "thread_does_not_cover_its_log"},
		{"the sale's certificate", []string{"completeness_certificate", "last_seq"}, json.RawMessage("4"), saleVerified, "sale_bundle_does_not_cover_its_log"},
		{"a record dropped from the sale's bundle", []string{"records", "4"}, nil, saleVerified, "sale_bundle_does_not_cover_its_log"},
		{"the sale's checkpoint log", []string{"checkpoint", "log_id"}, q("deal/sale-0000000000000000"), saleVerified, "sale_bundle_does_not_cover_its_log"},
		{"no checkpoint time", []string{"checkpoint", "timestamp"}, nil, saleVerified, "checkpoint_time_not_read"},
		{"a thread under another sale", append(slices.Clone(threads), saleThreadA, "extensions", "x-deal-v0", "sealed_report"), q("0"), saleVerified, "thread_is_not_under_this_sale"},
	} {
		edited := raw
		if c.path != nil {
			edited = jsonEdit(t, raw, c.path, c.value)
		}
		s, err := ReadSaleBundle(edited, c.v)
		if err != nil {
			t.Fatal(err)
		}
		if s.Complete() || !slices.Contains(s.Findings, c.finding) {
			t.Errorf("%s: findings %v, want %s", c.name, s.Findings, c.finding)
		}
	}
}

func ptrTo(n int) *int { return &n }

// A record of the sale's log is read only when it verifies (#149): one the
// verifier does not verify is a record the copy does not read, whatever its
// disclosure binds, and only one after the latest cut leaves no cut.
func TestASaleRecordThatDoesNotVerifyIsNotRead(t *testing.T) {
	raw := json.RawMessage(readFile(t, saleReplayVectors, saleAsWritten))
	records := recordsOf(objectOf(raw)["records"])
	idOf := func(recordType string) string {
		for _, r := range records {
			id, _ := textOf(r["capsule_id"])
			if recordTypeOf(objectOf(disclosedOf(objectOf(raw))[id])) == recordType {
				return id
			}
		}
		t.Fatalf("no %s", recordType)
		return ""
	}
	for name, c := range map[string]struct {
		id       string
		findings []string
	}{
		"a registration":  {idOf("thread"), []string{"sale_record_not_disclosed", "sale_threads_do_not_match_the_registrations", "carried_threads_are_not_the_present_ones"}},
		"a thread_opened": {idOf("thread_opened"), []string{"sale_record_not_disclosed", "opened_not_evidenced"}},
		"the cut":         {idOf("sale_cut"), []string{"sale_record_not_disclosed", "no_sale_cut"}},
	} {
		v := saleVerified
		v.Records = maps.Clone(saleVerified.Records)
		v.Records[c.id] = false
		s, err := ReadSaleBundle(raw, v)
		if err != nil {
			t.Fatal(err)
		}
		if !slices.Equal(s.Findings, c.findings) {
			t.Errorf("%s: findings %v, want %v", name, s.Findings, c.findings)
		}
	}
	if s, err := ReadSaleBundle(raw, SaleVerification{Sale: true, Threads: saleVerified.Threads}); err != nil || !slices.Contains(s.Findings, "sale_record_not_disclosed") || len(s.Threads) != 0 {
		t.Errorf("no record verified: %v, %d threads, %v", s.Findings, len(s.Threads), err)
	}
	// Only the latest cut counts: each thread_opened moved after the cut on
	// the log (its membership seq; seq 7 is the cut), verified or not.
	openedA, openedB := idOf("thread_opened"), ""
	for _, r := range records {
		if id, _ := textOf(r["capsule_id"]); id != openedA && recordTypeOf(objectOf(disclosedOf(objectOf(raw))[id])) == "thread_opened" {
			openedB = id
		}
	}
	at := func(id string) []string {
		return []string{"completeness_certificate", "memberships", id, "log_coordinates", "seq"}
	}
	moved := jsonEdit(t, raw, at(openedB), json.RawMessage("8"))
	for name, c := range map[string]struct {
		unread   []string
		findings []string
	}{
		"a verified record after the cut":             {nil, nil},
		"an unverified record after the cut":          {[]string{openedB}, []string{"sale_record_not_disclosed", "no_sale_cut", "opened_not_evidenced"}},
		"unverified records before and after the cut": {[]string{openedA, openedB}, []string{"sale_record_not_disclosed", "opened_not_evidenced", "no_sale_cut"}},
		"an unverified record before the cut only":    {[]string{openedA}, []string{"sale_record_not_disclosed", "opened_not_evidenced"}},
	} {
		v := saleVerified
		v.Records = maps.Clone(saleVerified.Records)
		for _, id := range c.unread {
			v.Records[id] = false
		}
		s, err := ReadSaleBundle(moved, v)
		if err != nil {
			t.Fatal(err)
		}
		if !slices.Equal(s.Findings, c.findings) {
			t.Errorf("%s: findings %v, want %v", name, s.Findings, c.findings)
		}
	}
}

// A cut that is read but has no head for a present thread names it not in
// the cut, never no_sale_cut (the engine's test of #149).
func TestAReadableCutWithoutAThreadNamesItNotInTheCut(t *testing.T) {
	registration := strings.Repeat("ab", 32)
	read := saleLog{hasCut: true, cut: map[string]string{strings.Repeat("ef", 32): strings.Repeat("01", 32)}}
	if why := openedAndWhole(jsonObject{}, registration, "x", nil, read); !slices.Contains(why, "thread_not_in_the_cut") || slices.Contains(why, "no_sale_cut") {
		t.Errorf("a readable cut: %v", why)
	}
	if why := openedAndWhole(jsonObject{}, registration, "x", nil, saleLog{}); !slices.Contains(why, "no_sale_cut") {
		t.Errorf("no cut: %v", why)
	}
}

// editThread is s with edit applied to a copy of the carried thread id.
func editThread(t *testing.T, s SaleBundle, id string, edit func(*SaleThread)) SaleBundle {
	t.Helper()
	out := s
	out.Threads = slices.Clone(s.Threads)
	for i, th := range out.Threads {
		if th.ID != id {
			continue
		}
		th.records = slices.Clone(th.records)
		for j, r := range th.records {
			th.records[j] = maps.Clone(r)
		}
		th.disclosed = maps.Clone(th.disclosed)
		edit(&th)
		out.Threads[i] = th
		return out
	}
	t.Fatalf("no thread %s", id)
	return SaleBundle{}
}

// capsuleIndex is the index of the record whose capsule id starts with prefix.
func capsuleIndex(t *testing.T, th *SaleThread, prefix string) (int, string) {
	t.Helper()
	for i, r := range th.records {
		if id, _ := textOf(r["capsule_id"]); strings.HasPrefix(id, prefix) {
			return i, id
		}
	}
	t.Fatalf("%s: no record %s…", th.ID, prefix)
	return 0, ""
}

// rebind discloses agentInput for the record at i, its header sealing it.
func rebind(t *testing.T, th *SaleThread, i int, agentInput json.RawMessage) {
	t.Helper()
	id, _ := textOf(th.records[i]["capsule_id"])
	d, ok := rawDigest(agentInput)
	if !ok {
		t.Fatal("no digest")
	}
	th.disclosed[id] = agentInput
	th.records[i]["model_attestation"] = encodeJSON(t, map[string]map[string]string{"compute_attestation": {"agent_input_digest": d}})
}

// The sale's history for one check (a-commit-again-1900, thread A at
// 10:56:27; before it A's offer and commit acts and B's offer act), as each
// ordering and binding rule of sale_history leaves it.
func TestTheSaleHistoryKeepsSalesOrderAndBindings(t *testing.T) {
	sale := readSale(t, saleAsWritten)
	check := checkedCapsule(t, "check-inputs/a-commit-again-1900.json")
	historyOf := func(s SaleBundle) History {
		t.Helper()
		_, h, err := s.Check(check)
		if err != nil {
			t.Fatal(err)
		}
		return h
	}
	if h := historyOf(sale); !h.Complete || len(h.Acts) != 3 {
		t.Fatalf("as read: complete %v, %d acts", h.Complete, len(h.Acts))
	}
	at := mustInstant(t, "2026-10-10T10:56:27Z")
	for name, c := range map[string]struct {
		certified time.Time
		complete  bool
	}{"checkpoint in the check's second": {at, false}, "checkpoint the second after": {at.Add(time.Second), true}} {
		s := sale
		s.CertifiedUntil = &c.certified
		if got := historyOf(s).Complete; got != c.complete {
			t.Errorf("%s: complete %v", name, got)
		}
	}
	retime := func(id, prefix, ts string) SaleBundle {
		return editThread(t, sale, id, func(th *SaleThread) {
			i, _ := capsuleIndex(t, th, prefix)
			th.records[i]["timestamp"] = encodeJSON(t, ts)
		})
	}
	if historyOf(retime(saleThreadB, "7c02ee76", "2026-10-10T10:56:27Z")).Complete {
		t.Error("another thread's act in the check's second: complete")
	}
	if historyOf(retime(saleThreadB, "7c02ee76", "2026-10-10T10:56:20Z")).Complete {
		t.Error("two threads' acts in one second before the check: complete")
	}
	if !historyOf(retime(saleThreadB, "7c02ee76", "2026-10-10T10:56:25Z")).Complete {
		t.Error("B's act alone in its second: incomplete")
	}
	// An act the copy does not disclose is written unread, with the key.
	hidden := editThread(t, sale, saleThreadA, func(th *SaleThread) {
		_, id := capsuleIndex(t, th, "e1e3d4f9")
		delete(th.disclosed, id)
	})
	unread := 0
	for _, r := range historyOf(hidden).Acts {
		if r.live == liveUnread && r.ItemRef != nil && *r.ItemRef == *sale.Key {
			unread++
		}
	}
	if unread != 1 {
		t.Errorf("an undisclosed act: %d unread records", unread)
	}
	// An act authorized by the buyer's approval rests on the check it names.
	targetOf := func(s SaleBundle, prefix string) *string {
		t.Helper()
		for _, r := range historyOf(s).Acts {
			if strings.HasPrefix(r.CapsuleID, prefix) {
				return r.Target
			}
		}
		t.Fatalf("no act %s", prefix)
		return nil
	}
	sealed := targetOf(sale, "fa4e8566")
	approved := editThread(t, sale, saleThreadA, func(th *SaleThread) {
		i, id := capsuleIndex(t, th, "fa4e8566")
		_, approval := capsuleIndex(t, th, "98cd76a9")
		d, _ := rawDigest(th.disclosed[approval])
		act := decodeJSON[map[string]json.RawMessage](t, th.disclosed[id])
		act["refs"] = encodeJSON(t, []map[string]string{{"rel": "authorized_by", "type": "deal-record", "digest_alg": "SHA-256", "digest": d}})
		rebind(t, th, i, encodeJSON(t, act))
	})
	if got := targetOf(approved, "fa4e8566"); sealed == nil || got == nil || *got != *sealed {
		t.Errorf("through the approval: target %v, through the evaluation %v", got, sealed)
	}
	// A check's counterparty_profile companion keys the check and each act
	// that rests on it (replay.py _companion_profiles).
	payee := strings.Repeat("ab", 32)
	commitCheck := ""
	companion := editThread(t, sale, saleThreadA, func(th *SaleThread) {
		_, commitCheck = capsuleIndex(t, th, "5908babb")
		d, _ := rawDigest(th.disclosed[commitCheck])
		ai := encodeJSON(t, map[string]map[string]any{"x-deal-v0": {
			"record_type":          "counterparty_profile",
			"refs":                 []map[string]string{{"rel": "about", "type": "deal-record", "digest_alg": "SHA-256", "digest": d}},
			"counterparty_profile": map[string]any{"fp_alg": "hmac-sha256-profile-key", "ids": map[string]string{"payee": payee}},
		}})
		th.records = append(th.records, jsonObject{"capsule_id": encodeJSON(t, "companion"), "timestamp": encodeJSON(t, "2026-10-10T10:56:19Z")})
		rebind(t, th, len(th.records)-1, ai)
	})
	want := ProfileTargetPrefix + payee
	if got := targetOf(companion, "e1e3d4f9"); got == nil || *got != want {
		t.Errorf("the act on a check with a companion: target %v", got)
	}
	a, _, err := companion.Check(commitCheck)
	if err != nil {
		t.Fatal(err)
	}
	if a.Target == nil || *a.Target != want {
		t.Errorf("the check with a companion: target %v", a.Target)
	}
}

func mustInstant(t *testing.T, s string) time.Time {
	t.Helper()
	at, err := parseInstant(s)
	if err != nil {
		t.Fatal(err)
	}
	return at
}

// rebindRecord is bundle with record id's disclosed agent_input replaced by
// edit's result, and its header sealing it, so it stays bound.
func rebindRecord(t *testing.T, bundle json.RawMessage, id string, edit func(map[string]json.RawMessage)) json.RawMessage {
	t.Helper()
	b := decodeJSON[struct {
		Records     []map[string]json.RawMessage          `json:"records"`
		Disclosures map[string]map[string]json.RawMessage `json:"disclosures"`
	}](t, bundle)
	ai := decodeJSON[map[string]json.RawMessage](t, b.Disclosures[id]["agent_input"])
	edit(ai)
	raw := encodeJSON(t, ai)
	d, _ := rawDigest(raw)
	bundle = jsonEdit(t, bundle, []string{"disclosures", id, "agent_input"}, raw)
	for i, r := range b.Records {
		if string(r["capsule_id"]) == `"`+id+`"` {
			return jsonEdit(t, bundle, []string{"records", fmt.Sprint(i), "model_attestation", "compute_attestation", "agent_input_digest"}, encodeJSON(t, d))
		}
	}
	t.Fatalf("no record %s", id)
	return nil
}

// Each binding of a carried thread to the sale, broken alone on the real
// copy as written with every record it touches still bound, is named.
func TestEachBrokenThreadBindingIsNamed(t *testing.T) {
	raw := json.RawMessage(readFile(t, saleReplayVectors, saleAsWritten))
	sale := readSale(t, saleAsWritten)
	var threadA SaleThread
	for _, th := range sale.Threads {
		if th.ID == saleThreadA {
			threadA = th
		}
	}
	find := func(th SaleThread, match func(jsonObject) bool) string {
		for id, ai := range th.disclosed {
			if match(objectOf(ai)) {
				return id
			}
		}
		t.Fatalf("%s: no such record", th.ID)
		return ""
	}
	authority := find(threadA, func(o jsonObject) bool { return typeOf(o) == taskAuthorityType })
	pointer, _ := textOf(objectOf(objectOf(objectOf(threadA.bundle)["extensions"])[dealProfile])[sealedReport])
	inThreadA := func(edit func(json.RawMessage) json.RawMessage) json.RawMessage {
		path := []string{"extensions", SaleExtension, "threads", saleThreadA}
		return jsonEdit(t, raw, path, edit(threadA.bundle))
	}
	opening := decodeJSON[map[string]string](t, objectOf(objectOf(threadA.disclosed[pointer])["report"])["sale_authority_opening"])
	setOpening := func(b json.RawMessage, o map[string]string) json.RawMessage {
		return rebindRecord(t, b, pointer, func(ai map[string]json.RawMessage) {
			report := decodeJSON[map[string]json.RawMessage](t, ai["report"])
			report["sale_authority_opening"] = encodeJSON(t, o)
			ai["report"] = encodeJSON(t, report)
		})
	}
	withBody := func(ai map[string]json.RawMessage, member string, value string) {
		body := decodeJSON[map[string]json.RawMessage](t, ai["body"])
		body[member] = encodeJSON(t, value)
		ai["body"] = encodeJSON(t, body)
	}
	other := strings.Repeat("c", 64)
	cases := map[string]struct {
		raw     json.RawMessage
		finding string
	}{
		"a thread record signed by another key": {inThreadA(func(b json.RawMessage) json.RawMessage {
			return jsonEdit(t, b, []string{"records", "0", "key_id"}, encodeJSON(t, other))
		}), "thread_not_signed_as_the_sale"},
		"the task authority names another registration": {inThreadA(func(b json.RawMessage) json.RawMessage {
			var newDigest string
			b = rebindRecord(t, b, authority, func(ai map[string]json.RawMessage) {
				refs := decodeJSON[[]map[string]string](t, ai["refs"])
				for _, r := range refs {
					if r["rel"] == "registration" {
						r["digest"] = other
					}
				}
				ai["refs"] = encodeJSON(t, refs)
				newDigest, _ = rawDigest(encodeJSON(t, ai))
			})
			o := maps.Clone(opening)
			o["record_digest"] = newDigest
			return setOpening(b, o)
		}), "thread_does_not_name_its_registration"},
		"an opening that names another task authority": {inThreadA(func(b json.RawMessage) json.RawMessage {
			o := maps.Clone(opening)
			o["record_digest"] = other
			return setOpening(b, o)
		}), "thread_is_not_under_this_sale"},
		"an opening of another sale's key": {inThreadA(func(b json.RawMessage) json.RawMessage {
			var newDigest string
			b = rebindRecord(t, b, authority, func(ai map[string]json.RawMessage) {
				withBody(ai, "sale_authority_commitment", openingDigest(opening["nonce"], other))
				newDigest, _ = rawDigest(encodeJSON(t, ai))
			})
			o := maps.Clone(opening)
			o["text"], o["record_digest"] = other, newDigest
			return setOpening(b, o)
		}), "thread_is_not_under_this_sale"},
	}
	// Thread B's registration re-sealed to commit to thread A under A's
	// nonce, and its entry opening it so: one thread opened twice.
	entries := decodeJSON[[]map[string]json.RawMessage](t, objectOf(objectOf(objectOf(raw)["extensions"])[dealProfile])["sale_threads"])
	regB := decodeJSON[map[string]string](t, entries[1]["registration"])["digest"]
	var nonceA string
	_ = json.Unmarshal(entries[0]["nonce"], &nonceA)
	registrationB := ""
	for id, ai := range disclosedOf(objectOf(raw)) {
		if d, _ := rawDigest(ai); d == regB {
			registrationB = id
		}
	}
	twice := rebindRecord(t, raw, registrationB, func(ai map[string]json.RawMessage) {
		withBody(ai, "thread_ref_commitment", openingDigest(nonceA, saleThreadA))
		regB, _ = rawDigest(encodeJSON(t, ai))
	})
	entry := []string{"extensions", dealProfile, "sale_threads", "1"}
	twice = jsonEdit(t, twice, append(slices.Clone(entry), "registration", "digest"), encodeJSON(t, regB))
	twice = jsonEdit(t, twice, append(slices.Clone(entry), "nonce"), encodeJSON(t, nonceA))
	twice = jsonEdit(t, twice, append(slices.Clone(entry), "thread_id"), encodeJSON(t, saleThreadA))
	cases["one thread opened twice"] = struct {
		raw     json.RawMessage
		finding string
	}{twice, "registration_opening_does_not_match"}
	for name, c := range cases {
		s, err := ReadSaleBundle(c.raw, saleVerified)
		if err != nil {
			t.Fatal(err)
		}
		t.Logf("%s: %v", name, s.Findings)
		if s.Complete() || !slices.Contains(s.Findings, c.finding) {
			t.Errorf("%s: findings %v, want %s", name, s.Findings, c.finding)
		}
	}
}

// saleBound is the sale's bound records of the copy as written, in order,
// and its sale_threads entries.
func saleBound(t *testing.T, raw json.RawMessage) ([]jsonObject, []jsonObject) {
	t.Helper()
	b := objectOf(raw)
	bound := boundRecords(recordsOf(b["records"]), disclosedOf(b))
	var entries []jsonObject
	for _, e := range decodeJSON[[]json.RawMessage](t, objectOf(objectOf(b["extensions"])[dealProfile])["sale_threads"]) {
		entries = append(entries, objectOf(e))
	}
	return bound, entries
}

// registrationOf is the registration digest a sale_threads entry names.
func registrationOf(t *testing.T, entry jsonObject) string {
	t.Helper()
	d, ok := refDigest(entry["registration"])
	if !ok {
		t.Fatal("an entry names no registration")
	}
	return d
}

// The copy as written, as AMENDMENT 11 names its records: one thread_opened
// per thread, A's then B's, and one sale_cut with a head for each; each
// present entry opens both to its thread, nothing found.
func TestTheSaleLogOpensEachThreadAndCutsItWhole(t *testing.T) {
	raw := json.RawMessage(readFile(t, saleReplayVectors, saleAsWritten))
	bound, entries := saleBound(t, raw)
	var findings []string
	log := saleLogOpenings(bound, nil, &findings)
	a, b := registrationOf(t, entries[0]), registrationOf(t, entries[1])
	if len(findings) != 0 || len(log.opened) != 2 || len(log.opened[a]) != 1 || len(log.opened[b]) != 1 ||
		!log.hasCut || len(log.cut) != 2 || log.cut[a] == "" || log.cut[b] == "" {
		t.Fatalf("findings %v, opened %v, cut %v %v", findings, log.opened, log.hasCut, log.cut)
	}
	var opened []string
	for _, s := range bound {
		if recordTypeOf(s) == "thread_opened" {
			d, _ := refDigest(decodeJSON[[]json.RawMessage](t, objectOf(s[dealProfile])["refs"])[0])
			opened = append(opened, d)
		}
	}
	if !slices.Equal(opened, []string{a, b}) {
		t.Errorf("thread_opened in seal order %v, registrations %v", opened, []string{a, b})
	}
	// Certified through the earliest signed checkpoint: A's, before B's and
	// the sale's.
	if s := readSale(t, saleAsWritten); !s.Complete() || s.CertifiedUntil == nil ||
		!s.CertifiedUntil.Equal(mustInstant(t, "2026-10-10T10:56:29.044Z")) {
		t.Errorf("as written: %v, certified until %v", s.Findings, s.CertifiedUntil)
	}
}

// A carried thread cut short by its last record is not whole at the cut,
// the sale's own openings read from its log: the entry's head no longer
// opens to its last record.
func TestATruncatedThreadIsNotWholeAtTheCut(t *testing.T) {
	raw := json.RawMessage(readFile(t, saleReplayVectors, saleAsWritten))
	bound, entries := saleBound(t, raw)
	var findings []string
	log := saleLogOpenings(bound, nil, &findings)
	thread := objectOf(objectOf(objectOf(objectOf(raw)["extensions"])[SaleExtension])["threads"])[saleThreadA]
	threadBundle := objectOf(thread)
	var authority string
	for _, ai := range disclosedOf(threadBundle) {
		if typeOf(objectOf(ai)) == taskAuthorityType {
			authority, _ = rawDigest(ai)
		}
	}
	registration := registrationOf(t, entries[0])
	if why := openedAndWhole(entries[0], registration, authority, threadBundle, log); len(why) != 0 {
		t.Fatalf("whole: %v", why)
	}
	certificate := decodeJSON[map[string]json.RawMessage](t, threadBundle["completeness_certificate"])
	records := decodeJSON[[]map[string]json.RawMessage](t, threadBundle["records"])
	last := records[len(records)-1]
	memberships := decodeJSON[map[string]json.RawMessage](t, certificate["memberships"])
	delete(memberships, strings.Trim(string(last["capsule_id"]), `"`))
	var lastSeq int
	_ = json.Unmarshal(certificate["last_seq"], &lastSeq)
	certificate["memberships"], certificate["last_seq"] = encodeJSON(t, memberships), encodeJSON(t, lastSeq-1)
	cut := maps.Clone(threadBundle)
	cut["records"], cut["completeness_certificate"] = encodeJSON(t, records[:len(records)-1]), encodeJSON(t, certificate)
	if why := openedAndWhole(entries[0], registration, authority, cut, log); !slices.Equal(why, []string{"thread_not_whole_at_the_cut"}) || len(findings) != 0 {
		t.Errorf("cut short: %v (%v)", why, findings)
	}
	// Two records at the last seq: no one record is the thread's last.
	certificate = decodeJSON[map[string]json.RawMessage](t, threadBundle["completeness_certificate"])
	memberships = decodeJSON[map[string]json.RawMessage](t, certificate["memberships"])
	for id, m := range memberships {
		if id != strings.Trim(string(last["capsule_id"]), `"`) {
			memberships[id] = jsonEdit(t, m, []string{"log_coordinates", "seq"}, certificate["last_seq"])
			break
		}
	}
	certificate["memberships"] = encodeJSON(t, memberships)
	twice := maps.Clone(threadBundle)
	twice["completeness_certificate"] = encodeJSON(t, certificate)
	if why := openedAndWhole(entries[0], registration, authority, twice, log); !slices.Equal(why, []string{"thread_not_whole_at_the_cut"}) {
		t.Errorf("two last records: %v", why)
	}
	// The last record carried twice: no one record is there either.
	again := maps.Clone(threadBundle)
	again["records"] = encodeJSON(t, append(records, last))
	if why := openedAndWhole(entries[0], registration, authority, again, log); !slices.Equal(why, []string{"thread_not_whole_at_the_cut"}) {
		t.Errorf("the last record twice: %v", why)
	}
}

// A nonce opens a commitment only as a JSON string (_opens): null opens
// nothing, even a commitment to the empty nonce.
func TestOnlyAStringNonceOpens(t *testing.T) {
	empty := openingDigest("", "text")
	if !opens(json.RawMessage(`""`), "text", empty) || opens(json.RawMessage("null"), "text", empty) || opens(nil, "text", empty) {
		t.Error("a nonce that is not a JSON string opens")
	}
}

// saleLogOf is a sale's bound records as an older producer, or this one,
// seals them: two registrations, a thread_opened for the first, and a cut
// naming it (the engine's test's _sale_log).
func saleLogOf(t *testing.T, threadOpened, cut bool) []jsonObject {
	t.Helper()
	registration := func(thread, nonce string, seq int) jsonObject {
		return objectOf(encodeJSON(t, map[string]any{
			"x-deal-v0": map[string]any{"record_type": "thread", "seq": seq},
			"body":      map[string]string{"thread_ref_commitment": openingDigest(nonce, thread)},
		}))
	}
	log := []jsonObject{registration(saleThreadA, strings.Repeat("a", 64), 3), registration(saleThreadB, strings.Repeat("b", 64), 5)}
	d, _ := digestOf(log[0])
	first := map[string]string{"type": "deal-record", "digest_alg": "SHA-256", "digest": d}
	if threadOpened {
		log = append(log, objectOf(encodeJSON(t, map[string]any{
			"x-deal-v0": map[string]any{"record_type": "thread_opened", "seq": 4,
				"refs": []map[string]string{{"rel": "registration", "type": first["type"], "digest_alg": first["digest_alg"], "digest": d}}},
			"body": map[string]string{"task_authority_commitment": strings.Repeat("c", 64)},
		})))
	}
	if cut {
		log = append(log, objectOf(encodeJSON(t, map[string]any{
			"x-deal-v0": map[string]any{"record_type": "sale_cut", "seq": 6},
			"body":      map[string]any{"thread_heads": []map[string]any{{"registration": first, "head_commitment": strings.Repeat("d", 64)}}},
		})))
	}
	return log
}

// neverOpened is the sale_threads entry listing registration i never_opened.
func neverOpened(t *testing.T, log []jsonObject, i int, thread, nonce string) json.RawMessage {
	t.Helper()
	d, _ := digestOf(log[i])
	return encodeJSON(t, map[string]any{"registration": map[string]string{"type": "deal-record", "digest_alg": "SHA-256", "digest": d},
		"nonce": nonce, "thread_id": thread, "member": threadNeverOpened})
}

// A registration listed never_opened holds only when no thread_opened and
// no head of the latest cut names it (the reader's steps on records alone:
// no sealed log here leaves a thread unopened).
func TestNeverOpenedRequiresNoOpeningOnTheSaleLog(t *testing.T) {
	for name, c := range map[string]struct {
		opened, cut bool
		findings    []string
	}{
		"no thread_opened and no head: never opened": {false, false, nil},
		"a thread_opened names it":                   {true, false, []string{"never_opened_but_opened"}},
		"the cut names it":                           {false, true, []string{"never_opened_but_in_the_cut"}},
	} {
		log := saleLogOf(t, c.opened, c.cut)
		var findings []string
		read := saleLogOpenings(log, nil, &findings)
		listed := []json.RawMessage{neverOpened(t, log, 0, saleThreadA, strings.Repeat("a", 64)), neverOpened(t, log, 1, saleThreadB, strings.Repeat("b", 64))}
		if present := presentThreads(listed, log[:2], read, &findings); len(present) != 0 || !slices.Equal(findings, c.findings) {
			t.Errorf("%s: present %v, findings %v", name, present, findings)
		}
	}
}

// A copy written before the sale's log sealed thread_opened and sale_cut: a
// present thread is shown neither opened nor whole.
func TestAnOldCopyWithNoOpeningAndNoCutIsIncomplete(t *testing.T) {
	var findings []string
	log := saleLogOpenings(saleLogOf(t, false, false), nil, &findings)
	if len(log.opened) != 0 || log.hasCut || len(findings) != 0 {
		t.Fatalf("opened %v, cut %v, findings %v", log.opened, log.hasCut, findings)
	}
	raw := json.RawMessage(readFile(t, saleReplayVectors, saleAsWritten))
	_, entries := saleBound(t, raw)
	threadBundle := objectOf(objectOf(objectOf(objectOf(raw)["extensions"])[SaleExtension])["threads"])[saleThreadA]
	why := openedAndWhole(entries[0], registrationOf(t, entries[0]), "", objectOf(threadBundle), log)
	if !slices.Equal(why, []string{"opened_not_evidenced", "no_sale_cut"}) {
		t.Errorf("an old copy: %v", why)
	}
}

// The sale's log as read: the latest cut's heads alone; two cuts at one
// seq, or a cut whose heads cannot be read or name one thread twice, is no
// cut (#148), and so is one a record the copy does not read comes after
// (#149); a thread_opened naming no one registration is a finding; a
// registration two thread_opened name is not evidenced as opened.
func TestTheSaleLogIsReadAsTheEngineReadsIt(t *testing.T) {
	log := saleLogOf(t, true, true)
	cut := log[len(log)-1]
	withCut := func(seq int, heads json.RawMessage) jsonObject {
		c := maps.Clone(cut)
		c["x-deal-v0"] = encodeJSON(t, map[string]any{"record_type": "sale_cut", "seq": seq})
		c["body"] = encodeJSON(t, map[string]json.RawMessage{"thread_heads": heads})
		return c
	}
	first := decodeJSON[[]map[string]json.RawMessage](t, objectOf(cut["body"])["thread_heads"])[0]
	later := []map[string]json.RawMessage{{"registration": first["registration"], "head_commitment": encodeJSON(t, strings.Repeat("e", 64))}}
	registration, _ := digestOf(log[0])
	refAs := func(rel string) map[string]string {
		return map[string]string{"rel": rel, "type": "deal-record", "digest_alg": "SHA-256", "digest": registration}
	}
	withRefs := func(opened jsonObject, refs ...map[string]string) jsonObject {
		o := maps.Clone(opened)
		o["x-deal-v0"] = encodeJSON(t, map[string]any{"record_type": "thread_opened", "seq": 4, "refs": refs})
		return o
	}
	for name, c := range map[string]struct {
		log      []jsonObject
		findings []string
		head     string
	}{
		"the later cut is read":                             {append([]jsonObject{withCut(8, encodeJSON(t, later))}, log...), nil, strings.Repeat("e", 64)},
		"a thread_opened with no refs":                      {append(slices.Clone(log[:2]), jsonObject{"x-deal-v0": encodeJSON(t, map[string]any{"record_type": "thread_opened"}), "body": log[2]["body"]}, cut), []string{"thread_opened_not_read"}, strings.Repeat("d", 64)},
		"a thread_opened whose ref is not its registration": {append(slices.Clone(log[:2]), withRefs(log[2], refAs("about")), cut), []string{"thread_opened_not_read"}, strings.Repeat("d", 64)},
		"a thread_opened with two refs":                     {append(slices.Clone(log[:2]), withRefs(log[2], refAs("registration"), refAs("registration")), cut), []string{"thread_opened_not_read"}, strings.Repeat("d", 64)},
		"a thread_opened with no commitment":                {append(slices.Clone(log[:2]), jsonObject{"x-deal-v0": log[2]["x-deal-v0"]}, cut), []string{"thread_opened_not_read"}, strings.Repeat("d", 64)},
	} {
		var findings []string
		read := saleLogOpenings(c.log, nil, &findings)
		if !slices.Equal(findings, c.findings) || read.cut[registration] != c.head || !read.hasCut {
			t.Errorf("%s: findings %v, head %q, cut %v", name, findings, read.cut[registration], read.hasCut)
		}
	}
	for name, unread := range map[string][]jsonObject{
		"two cuts at one seq":           append(slices.Clone(log), withCut(6, encodeJSON(t, later))),
		"heads that are not a list":     append(slices.Clone(log[:3]), withCut(6, encodeJSON(t, "all"))),
		"one thread headed twice":       append(slices.Clone(log[:3]), withCut(6, encodeJSON(t, append(later, later...)))),
		"a head naming no registration": append(slices.Clone(log[:3]), withCut(6, encodeJSON(t, []map[string]string{{"head_commitment": "f"}}))),
		"a head after one that is read": append(slices.Clone(log[:3]), withCut(6, encodeJSON(t, append(later, map[string]json.RawMessage{})))),
	} {
		var findings []string
		read := saleLogOpenings(unread, nil, &findings)
		if len(findings) != 0 || read.hasCut || len(read.cut) != 0 {
			t.Errorf("%s: findings %v, cut %v %v", name, findings, read.hasCut, read.cut)
		}
		if why := openedAndWhole(jsonObject{}, registration, "x", nil, read); !slices.Contains(why, "no_sale_cut") || slices.Contains(why, "thread_not_in_the_cut") {
			t.Errorf("%s: %v", name, why)
		}
	}
	for name, c := range map[string]struct {
		lastUnread *int
		hasCut     bool
	}{
		"no record unread":                  {nil, true},
		"an unread record before the cut":   {new(int), true},
		"an unread record at the cut's seq": {ptrTo(seqOf(cut)), true},
		"an unread record after the cut":    {ptrTo(seqOf(cut) + 1), false},
	} {
		var findings []string
		read := saleLogOpenings(log, c.lastUnread, &findings)
		if len(findings) != 0 || read.hasCut != c.hasCut || (len(read.cut) != 0) != c.hasCut {
			t.Errorf("%s: findings %v, cut %v %v", name, findings, read.hasCut, read.cut)
		}
	}
	var findings []string
	twice := saleLogOpenings(append(slices.Clone(log), log[2]), nil, &findings)
	entry := objectOf(encodeJSON(t, map[string]any{"opened": map[string]string{"nonce": "n"}}))
	if why := openedAndWhole(entry, registration, "x", nil, twice); len(why) == 0 || why[0] != "opened_not_evidenced" {
		t.Errorf("two thread_opened: %v", why)
	}
}
