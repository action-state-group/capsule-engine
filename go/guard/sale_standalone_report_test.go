package guard

import (
	"encoding/json"
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
)

// standaloneVectors are a sale bundle current capsulectl writes (capsule-cli
// 6210b93; capsule-engine tests/fixtures/release-on-acceptance/), whose
// thread reports are standalone (x-deal-report/v0), with the engine's live
// and replay decisions of it under asg/seller/0.1.4 at capsule-engine
// 0b9a293, the merge of #206, which taught its reader the standalone
// report; the replay decisions regenerated at 0c93a58 (#205), whose replay
// decides a check citing a task authority with the record the bundle
// discloses under that digest (PROVENANCE.json).
var standaloneVectors = filepath.Join("testdata", "sale-standalone-report-vectors")

// rc20PickupAreaVectors are the same fixture steps run by capsulectl
// v0.1.0-rc20 (capsule-cli 401f39b, the release that ships the pickup_area
// class, the Go port's #238), with three platform variants of A's
// pickup-area share, and the engine's live and replay decisions under
// asg/seller/0.1.4 (PROVENANCE.json).
var rc20PickupAreaVectors = filepath.Join("testdata", "rc20-pickup-area-vectors")

// standaloneSets are the sale-bundle vector sets decided as the engine
// decides them: each directory, how many live and replayed decisions it
// holds, and how many shares' sale ledgers hold A's accepted commit.
var standaloneSets = []struct {
	dir                      string
	live, replayed, accepted int
}{
	{standaloneVectors, 5, 5, 4},
	{rc20PickupAreaVectors, 8, 5, 4},
	{representCheckVectors, 10, 5, 0},
}

// standaloneVerified is what capsulectl verify --bundle reports of the
// standaloneVectors fixture; verifiedIn reads it for any set.
func standaloneVerified(t *testing.T, raw []byte) SaleVerification {
	t.Helper()
	return verifiedIn(t, standaloneVectors, raw)
}

// verifiedIn is what capsulectl verify --bundle reported of a set's sale
// (verify/sale.verify.json): the sale VALID, its records as record_identity
// and record_signatures say, and each carried thread VALID unless
// x-deal-sale/v0 names it invalid or incomplete.
func verifiedIn(t *testing.T, dir string, raw []byte) SaleVerification {
	t.Helper()
	return verifiedBy(t, readFile(t, dir, "verify", "sale.verify.json"), raw)
}

// verifiedBy is what one capsulectl verify --bundle report says of the sale
// bundle raw, read as verifiedIn reads it.
func verifiedBy(t *testing.T, verify, raw []byte) SaleVerification {
	t.Helper()
	var report struct {
		Verdict    string            `json:"verdict"`
		Identity   map[string]string `json:"record_identity"`
		Signatures map[string]string `json:"record_signatures"`
		Extensions []struct {
			Kind     string   `json:"kind"`
			Status   string   `json:"status"`
			Findings []string `json:"findings"`
		} `json:"extensions"`
	}
	if err := json.Unmarshal(verify, &report); err != nil {
		t.Fatal(err)
	}
	v := SaleVerification{Sale: report.Verdict == "VALID", Records: map[string]bool{}, Threads: map[string]bool{}}
	for id, status := range report.Identity {
		signature, ok := report.Signatures[id]
		v.Records[id] = status == "passed" && ok && signature != "invalid"
	}
	var findings []string
	assessed := false
	for _, x := range report.Extensions {
		if x.Kind == SaleExtension {
			assessed = x.Status == "pass"
			findings = append(findings, x.Findings...)
		}
	}
	for id := range objectOf(objectOf(objectOf(objectOf(raw)["extensions"])[SaleExtension])["threads"]) {
		v.Threads[id] = assessed && !slices.Contains(findings, "thread_invalid:"+id) && !slices.Contains(findings, "thread_incomplete:"+id)
	}
	return v
}

// narrowedAudiences are fewer copy audiences than the engine reads: the
// two a caller offering no internal-only copy passes.
var narrowedAudiences = []string{"counterparty", "keep"}

func readStandaloneSale(t *testing.T, raw []byte) SaleBundle {
	t.Helper()
	return readStandaloneSaleFor(t, raw, narrowedAudiences)
}

// readStandaloneSaleFor is readStandaloneSale reading the given copy
// audiences (nil: the engine's).
func readStandaloneSaleFor(t *testing.T, raw []byte, audiences []string) SaleBundle {
	t.Helper()
	v := standaloneVerified(t, readFile(t, standaloneVectors, "sale.bundle.json"))
	v.CopyAudiences = audiences
	s, err := ReadSaleBundle(raw, v)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// readSaleIn is a set's sale bundle, read as its verify result says.
func readSaleIn(t *testing.T, dir string) SaleBundle {
	t.Helper()
	raw := readFile(t, dir, "sale.bundle.json")
	s, err := ReadSaleBundle(raw, verifiedIn(t, dir, raw))
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func standaloneCapsule(t *testing.T, input string) string {
	t.Helper()
	return capsuleIn(t, standaloneVectors, input)
}

func capsuleIn(t *testing.T, dir, input string) string {
	t.Helper()
	in := decodeJSON[struct {
		Record struct {
			CapsuleID string `json:"capsule_id"`
		} `json:"record"`
	}](t, readFile(t, dir, input))
	return in.Record.CapsuleID
}

// CROSS-IMPLEMENTATION: the replay of a sale bundle capsulectl wrote (6210b93,
// and rc20 with its pickup_area shares), every check decided as check
// --record decides a sale bundle's check under asg/seller/0.1.4, with NO
// known difference from the engine:
// the sale is complete (every thread's standalone report read and bound),
// and the outcome, the verdict, every rule, single_commitment's result,
// reason and evidence, and the ledger it read. Each share's ledger holds
// A's accepted commit: s11 sees the acceptance.
func TestStandaloneReportVectorsAgreeWithTheEngine(t *testing.T) {
	for _, set := range standaloneSets {
		t.Run(filepath.Base(set.dir), func(t *testing.T) { standaloneReplayAgrees(t, set.dir, set.replayed, set.accepted) })
	}
}

func standaloneReplayAgrees(t *testing.T, dir string, decisions, wantAccepted int) {
	v := decodeJSON[saleReplayDocument](t, readFile(t, dir, "expected_replay.json"))
	e, p := seller014Engine(t)
	if v.Pack.PackID != p.ID || v.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors under %+v, pack %s %s", v.Pack, p.ID, p.DefinitionDigest)
	}
	sale := readSaleIn(t, dir)
	var diffs []string
	want := v.Sales["sale.bundle.json"]
	differ(&diffs, "sale", "complete", fmt.Sprint(sale.Complete()), fmt.Sprint(want.Complete))
	differ(&diffs, "sale", "findings", fmt.Sprint(sale.Findings), fmt.Sprint(want.Findings))
	accepted := 0
	for _, want := range v.Decisions {
		at := want.Input
		id := capsuleIn(t, dir, want.Input)
		a, h, err := sale.Check(id)
		if err != nil {
			t.Fatalf("%s: %v", at, err)
		}
		d, err := e.CheckSale(a, MemoryHistory(replayLedger(t, e, sale, id)), h)
		if err != nil {
			t.Fatalf("%s: %v", at, err)
		}
		differ(&diffs, at, "outcome", d.Outcome, want.Outcome)
		differ(&diffs, at, "verdict", e.Report(a, d).Decision, floorUnreadVerdict(want.Verdict, d))
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
		switch {
		case c.Result == NotApplicable && s11OutOfScope(t, sc.Evidence):
			// Out of scope: the evidence (in_scope false) is compared above,
			// and Go keeps no reason text for it.
		case c.Result == NotApplicable:
			differ(&diffs, at, "s11 reason", c.Pending(), sc.Reason)
		case c.Result == Fail:
			differ(&diffs, at, "s11 reason", c.failNote, sc.Reason)
		}
		rows := saleRows(t, a, h)
		gotRows, _ := json.Marshal(rows)
		wantRows, _ := json.Marshal(want.Ledger)
		differ(&diffs, at, "ledger", string(gotRows), string(wantRows))
		if strings.Contains(want.Input, "share") && slices.ContainsFunc(rows, func(r saleLedgerRow) bool {
			return r.ActionClass != nil && *r.ActionClass == "agreement.accept" && r.Decision != nil && *r.Decision == "accept"
		}) {
			accepted++
		}
	}
	if len(v.Sales) != 1 || len(v.Decisions) != decisions {
		t.Errorf("%d sales and %d decisions, the engine's 1 and %d", len(v.Sales), len(v.Decisions), decisions)
	}
	if accepted != wantAccepted {
		t.Errorf("%d shares' ledgers hold A's accepted commit, want %d", accepted, wantAccepted)
	}
	if len(diffs) > 0 {
		slices.Sort(diffs)
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// CROSS-IMPLEMENTATION: each set's check inputs decided live under
// asg/seller/0.1.4, with NO known difference from the engine: the outcome,
// the verdict, every rule and single_commitment's result, reason and
// evidence. The replay above decides each as live.
func TestStandaloneReportLiveVectorsAgreeWithTheEngine(t *testing.T) {
	for _, set := range standaloneSets {
		t.Run(filepath.Base(set.dir), func(t *testing.T) { standaloneLiveAgrees(t, set.dir, set.live) })
	}
}

func standaloneLiveAgrees(t *testing.T, dir string, decisions int) {
	v := decodeJSON[struct {
		Decisions []saleLiveVector `json:"decisions"`
	}](t, readFile(t, dir, "expected_live.json"))
	e, p := seller014Engine(t)
	var diffs []string
	for _, want := range v.Decisions {
		a, h, err := DecodeCheckInput(readFile(t, dir, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		differ(&diffs, want.Input, "outcome", d.Outcome, want.Outcome)
		differ(&diffs, want.Input, "verdict", e.Report(a, d).Decision, want.Verdict)
		got := rulesOf(d, p)
		for _, rule := range slices.Sorted(maps.Keys(want.Rules)) {
			differ(&diffs, want.Input, rule, got[rule], want.Rules[rule])
		}
		sc := want.SingleCommitment
		reason := sc.Reason
		if sc.Result == string(Pass) || s11OutOfScope(t, sc.Evidence) {
			reason = ""
		}
		differ(&diffs, want.Input, "s11", s11Cell(t, d), strings.Join([]string{sc.Result, reason, jsonDigest(t, sc.Evidence)}, " | "))
	}
	if len(v.Decisions) != decisions {
		t.Errorf("%d live decisions, the engine's %d", len(v.Decisions), decisions)
	}
	if len(diffs) > 0 {
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// s11OutOfScope is whether a not-applicable result's evidence says the rule
// is not configured for the action (in_scope false).
func s11OutOfScope(t *testing.T, evidence json.RawMessage) bool {
	t.Helper()
	e := decodeJSON[struct {
		InScope *bool `json:"in_scope"`
	}](t, evidence)
	return e.InScope != nil && !*e.InScope
}

// threadEdit sets the JSON value at path in a thread's copy, or deletes the
// member there when value is empty. A path step into an array is an index,
// -1 for the last element.
type threadEdit struct {
	path  []string
	value string
}

// withEdit is raw with value set at path (deleted when value is empty).
func withThreadEdit(t *testing.T, raw json.RawMessage, path []string, value string) json.RawMessage {
	t.Helper()
	if len(path) == 0 {
		return json.RawMessage(value)
	}
	if o := objectOf(raw); o != nil {
		if len(path) == 1 && value == "" {
			delete(o, path[0])
		} else {
			o[path[0]] = withThreadEdit(t, o[path[0]], path[1:], value)
		}
		return encodeJSON(t, o)
	}
	items := decodeJSON[[]json.RawMessage](t, raw)
	i, err := strconv.Atoi(path[0])
	if err != nil {
		t.Fatal(err)
	}
	if i < 0 {
		i += len(items)
	}
	items[i] = withThreadEdit(t, items[i], path[1:], value)
	return encodeJSON(t, items)
}

// Paths in a thread's copy.
var (
	reportPointer = []string{"extensions", dealProfile, sealedReport}
	reportRecord  = []string{"extensions", standaloneReport, "record"}
	reportPayload = []string{"extensions", standaloneReport, "agent_input"}
)

func pathUnder(base []string, rest ...string) []string { return append(slices.Clone(base), rest...) }

// editedThread is the fixture's sale with edits made to its first carried
// thread's copy, read as verified (the edits come after the host's
// verifier, so only this reader's own checks can catch them). With rebind,
// the report's record is re-bound to its edited agent_input, so an edit
// reaches the checks after the binding.
func editedThread(t *testing.T, rebind bool, edits ...threadEdit) SaleBundle {
	t.Helper()
	sale := objectOf(readFile(t, standaloneVectors, "sale.bundle.json"))
	threads := objectOf(objectOf(objectOf(sale["extensions"])[SaleExtension])["threads"])
	id := slices.Sorted(maps.Keys(threads))[0]
	thread := threads[id]
	for _, e := range edits {
		thread = withThreadEdit(t, thread, e.path, e.value)
	}
	if rebind {
		payload := objectOf(objectOf(objectOf(thread)["extensions"])[standaloneReport])["agent_input"]
		digest, ok := rawDigest(payload)
		if !ok {
			t.Fatal("the edited agent_input does not digest")
		}
		thread = withThreadEdit(t, thread, pathUnder(reportRecord, "model_attestation", "compute_attestation", "agent_input_digest"), strconv.Quote(digest))
	}
	threads[id] = thread
	raw := withThreadEdit(t, encodeJSON(t, sale), []string{"extensions", SaleExtension, "threads"}, string(encodeJSON(t, threads)))
	return readStandaloneSale(t, raw)
}

// The fixture as written reads complete through its standalone reports;
// without x-deal-report/v0 (the copy before capsule-cli #299 read it), its
// report is nowhere and the thread is not under the sale, but no standalone
// report is named unverified.
func TestTheStandaloneReportBindsEachThreadToTheSale(t *testing.T) {
	s := readStandaloneSale(t, readFile(t, standaloneVectors, "sale.bundle.json"))
	if !s.Complete() || len(s.Threads) != 2 {
		t.Fatalf("complete %v, %d threads, findings %v", s.Complete(), len(s.Threads), s.Findings)
	}
	gone := editedThread(t, false, threadEdit{[]string{"extensions", standaloneReport}, ""})
	if !slices.Equal(gone.Findings, []string{"thread_is_not_under_this_sale"}) {
		t.Errorf("without the standalone report: %v", gone.Findings)
	}
}

// Every standalone report that does not hold as capsulectl's verifier holds
// it reads exactly as the engine reads it: sealed_report_unverified, and
// its thread not under the sale. The same instant written otherwise holds.
func TestAStandaloneReportThatDoesNotHoldOpensNothing(t *testing.T) {
	unverified := []string{sealedReportUnverified, "thread_is_not_under_this_sale"}
	checkpoint := func(member, value string) []threadEdit {
		return []threadEdit{{pathUnder(reportPayload, "deal_checkpoint", member), value}}
	}
	asOf := func(value string) []threadEdit {
		return []threadEdit{{pathUnder(reportPayload, "report", "lifecycle", "as_of"), value}}
	}
	cases := []struct {
		name   string
		rebind bool
		edits  []threadEdit
		// more: the edit also breaks a link outside the report.
		more bool
	}{
		{"an extra member in the deal section", false, []threadEdit{{[]string{"extensions", dealProfile, "sale_threads"}, `[]`}}, false},
		{"a pointer that is not a string", false, []threadEdit{{reportPointer, `7`}}, false},
		{"a pointer to another capsule", false, []threadEdit{{reportPointer, strconv.Quote(strings.Repeat("0", 64))}}, false},
		{"an empty pointer and a record with no capsule_id", false, []threadEdit{{reportPointer, `""`}, {pathUnder(reportRecord, "capsule_id"), ""}}, false},
		{"an extension that is not an object", false, []threadEdit{{[]string{"extensions", standaloneReport}, `"report"`}}, false},
		{"an extra member beside record and agent_input", false, []threadEdit{{[]string{"extensions", standaloneReport, "more"}, `true`}}, false},
		{"another action_id", false, []threadEdit{{pathUnder(reportRecord, "action_id"), `"capsulectl-deal-step"`}}, false},
		{"a key other than the last step's", false, []threadEdit{{pathUnder(reportRecord, "key_id"), strconv.Quote(strings.Repeat("a", 64))}}, false},
		{"an empty key", false, []threadEdit{{pathUnder(reportRecord, "key_id"), `""`}}, false},
		{"a last step signed with another key", false, []threadEdit{{[]string{"records", "-1", "key_id"}, strconv.Quote(strings.Repeat("b", 64))}}, true},
		{"an agent_input the record does not bind", false, []threadEdit{{pathUnder(reportPayload, "nonce"), `"edited"`}}, false},
		{"another type", true, []threadEdit{{pathUnder(reportPayload, "type"), `"deal_step"`}}, false},
		{"another profile", true, []threadEdit{{pathUnder(reportPayload, "profile"), `"x-deal-v1"`}}, false},
		{"another deal", true, []threadEdit{{pathUnder(reportPayload, "deal_id"), `"deal-0000000000000000"`}}, false},
		{"an audience that is no copy's", true, []threadEdit{{pathUnder(reportPayload, "audience"), `"public"`}}, false},
		{"an audience that is no string", true, []threadEdit{{pathUnder(reportPayload, "audience"), `["keep"]`}}, false},
		// Not one of the audiences the caller reads (narrowedAudiences).
		{"the internal-only audience", true, []threadEdit{{pathUnder(reportPayload, "audience"), `"adjudicator"`}}, false},
		{"a checkpoint of another root", true, checkpoint("root", strconv.Quote(strings.Repeat("0", 64))), false},
		{"a checkpoint of another size", true, checkpoint("mmr_size", `999999`), false},
		{"a checkpoint of another log", true, checkpoint("log_id", `"deal/deal-0000000000000000"`), false},
		{"a checkpoint with a member more", true, checkpoint("timestamp", `"2026-10-22T06:52:39Z"`), false},
		{"an as_of a second later", true, asOf(`"2026-10-22T06:52:40Z"`), false},
		{"an as_of a nanosecond later", true, asOf(`"2026-10-22T06:52:39.000000001Z"`), false},
		{"an as_of that is no time", true, asOf(`"yesterday"`), false},
		{"an as_of that is no string", true, asOf(`0`), false},
		{"an in-log report beside it", false, []threadEdit{{[]string{"records", "0", "action_id"}, strconv.Quote(reportActionID)}}, true},
	}
	for _, c := range cases {
		got := editedThread(t, c.rebind, c.edits...)
		if !slices.Contains(got.Findings, sealedReportUnverified) || !slices.Contains(got.Findings, "thread_is_not_under_this_sale") {
			t.Errorf("%s: %v, want %v", c.name, got.Findings, unverified)
		} else if !c.more && !slices.Equal(got.Findings, unverified) {
			t.Errorf("%s: %v, want exactly %v", c.name, got.Findings, unverified)
		}
	}
	if same := editedThread(t, true, asOf(`"2026-10-22T01:52:39.000-05:00"`)...); !same.Complete() {
		t.Errorf("the checkpoint's instant written otherwise: %v", same.Findings)
	}
}

// lastStepKey picks the step at the highest membership seq, whatever the
// records' order, and passes over a later record that is no step, a step
// with no seq of at least 1, and a last step with an empty key.
func TestTheLastStepIsTheStepAtTheHighestSeq(t *testing.T) {
	type record struct {
		CapsuleID string `json:"capsule_id"`
		KeyID     string `json:"key_id"`
		Header    struct {
			RecordType string `json:"record_type"`
		} `json:"evidence_record_header"`
	}
	type membership struct {
		LogCoordinates struct {
			Seq json.RawMessage `json:"seq"`
		} `json:"log_coordinates"`
	}
	step := func(id, kind, key string) record {
		r := record{CapsuleID: id, KeyID: key}
		r.Header.RecordType = kind
		return r
	}
	bundle := func(seqs map[string]string, records ...record) jsonObject {
		var b struct {
			Records     []record `json:"records"`
			Certificate struct {
				Memberships map[string]membership `json:"memberships"`
			} `json:"completeness_certificate"`
		}
		b.Records, b.Certificate.Memberships = records, map[string]membership{}
		for id, seq := range seqs {
			var m membership
			m.LogCoordinates.Seq = json.RawMessage(seq)
			b.Certificate.Memberships[id] = m
		}
		return objectOf(encodeJSON(t, b))
	}
	for _, c := range []struct {
		name string
		b    jsonObject
		key  string
		read bool
	}{
		{"the last step first in the array", bundle(map[string]string{"a": "2", "b": "1"},
			step("a", "step_completed", "k2"), step("b", "deal_step", "k1")), "k2", true},
		{"a later record that is no step", bundle(map[string]string{"a": "1", "b": "2"},
			step("a", "deal_step", "k1"), step("b", "deal_report", "k2")), "k1", true},
		{"a failed step", bundle(map[string]string{"a": "1", "b": "2"},
			step("a", "deal_step", "k1"), step("b", "step_failed", "k2")), "k2", true},
		{"a step with no membership", bundle(map[string]string{"a": "1"},
			step("a", "deal_step", "k1"), step("b", "deal_step", "k2")), "k1", true},
		{"a step at seq 0", bundle(map[string]string{"a": "0"}, step("a", "deal_step", "k1")), "", false},
		{"a step at a seq that is no integer", bundle(map[string]string{"a": `"2"`}, step("a", "deal_step", "k1")), "", false},
		{"a last step with an empty key", bundle(map[string]string{"a": "1", "b": "2"},
			step("a", "deal_step", "k1"), step("b", "deal_step", "")), "", false},
		{"no step", bundle(map[string]string{"a": "1"}, step("a", "deal_report", "k1")), "", false},
	} {
		if key, read := lastStepKey(c.b); key != c.key || read != c.read {
			t.Errorf("%s: %q %v, want %q %v", c.name, key, read, c.key, c.read)
		}
	}
}

// The Go port's #238: a share capsulectl rc20 writes naming pickup_area
// keeps its classes (DisclosedClassNames knows the class, capsule-engine
// #195), so s01 reads them: to a buyer it passes, and to a platform it
// names pickup_area as the class a platform may not be given, as the engine
// does, where an unknown class would drop the whole list.
func TestAnRC20PickupAreaShareKeepsItsClasses(t *testing.T) {
	e, _ := seller014Engine(t)
	for _, c := range []struct {
		input   string
		classes []string
		result  Result
		note    string
	}{
		{"a-share-pickup-area", []string{"pickup_area"}, Pass, ""},
		{"a-share-platform-pickup-area", []string{"pickup_area"}, Fail,
			"the recipient is a platform, which may be given only email; this disclosure also gives pickup_area"},
		{"a-share-platform-pickup-area-email", []string{"email", "pickup_area"}, Fail,
			"the recipient is a platform, which may be given only email; this disclosure also gives pickup_area"},
		{"a-share-platform-email", []string{"email"}, Pass, ""},
	} {
		a, h, err := DecodeCheckInput(readFile(t, rc20PickupAreaVectors, "check-inputs", c.input+".json"))
		if err != nil {
			t.Fatalf("%s: %v", c.input, err)
		}
		if !slices.Equal(a.DisclosedClasses, c.classes) {
			t.Errorf("%s: classes %v, want %v", c.input, a.DisclosedClasses, c.classes)
		}
		got, err := e.constraintFor("recipient_role", a, MemoryHistory(h.records()), checkInputs{history: true, historyIncomplete: h.saleUnknown()})
		if err != nil {
			t.Fatal(err)
		}
		if got.Result != c.result || got.failNote != c.note {
			t.Errorf("%s: s01 %s %q, want %s %q", c.input, got.Result, got.failNote, c.result, c.note)
		}
	}
}

// With no audiences passed, a copy for the internal-only audience holds, as
// it does in the engine (report/sale_bundle.py _COPY_AUDIENCES).
func TestTheEnginesCopyAudiencesAreTheDefault(t *testing.T) {
	sale := objectOf(readFile(t, standaloneVectors, "sale.bundle.json"))
	threads := objectOf(objectOf(objectOf(sale["extensions"])[SaleExtension])["threads"])
	id := slices.Sorted(maps.Keys(threads))[0]
	thread := withThreadEdit(t, threads[id], pathUnder(reportPayload, "audience"), `"adjudicator"`)
	payload := objectOf(objectOf(objectOf(thread)["extensions"])[standaloneReport])["agent_input"]
	digest, ok := rawDigest(payload)
	if !ok {
		t.Fatal("the edited agent_input does not digest")
	}
	thread = withThreadEdit(t, thread, pathUnder(reportRecord, "model_attestation", "compute_attestation", "agent_input_digest"), strconv.Quote(digest))
	threads[id] = thread
	raw := withThreadEdit(t, encodeJSON(t, sale), []string{"extensions", SaleExtension, "threads"}, string(encodeJSON(t, threads)))
	if got := readStandaloneSaleFor(t, raw, nil); slices.Contains(got.Findings, sealedReportUnverified) {
		t.Errorf("engine audiences: %v, want the adjudicator copy's report to hold", got.Findings)
	}
	if got := readStandaloneSaleFor(t, raw, narrowedAudiences); !slices.Contains(got.Findings, sealedReportUnverified) {
		t.Errorf("narrowed audiences: %v, want %s", got.Findings, sealedReportUnverified)
	}
}
