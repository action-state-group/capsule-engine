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

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// liveHistoryVectors are capsule-engine's live history-ledger vectors
// (#137, with #136): the five check inputs of capsulectl 65f54e5's
// two-thread sale, each decided live under the seller pack in every way the
// engine's test gives its history, with the ledger its builder wrote. At
// 0ab20965 (the engine side's go-vectors) under asg/seller/0.1.2; at 08cd96d (after
// #138 and #139) under asg/seller/0.1.3, the claims-in-history way too.
var liveHistoryVectors = filepath.Join("testdata", "live-history-ledger-vectors")

func TestLiveHistoryVectorsAreVendoredAsRecorded(t *testing.T) {
	var prov struct {
		Files map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(readFile(t, liveHistoryVectors, "PROVENANCE.json"), &prov); err != nil {
		t.Fatal(err)
	}
	var onDisk, recorded []string
	err := fs.WalkDir(os.DirFS(liveHistoryVectors), ".", func(p string, d fs.DirEntry, err error) error {
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
		if got := fileSHA256Hex(readFile(t, liveHistoryVectors, name)); got != f.SHA256 {
			t.Errorf("%s sha256 %s, provenance records %s", name, got, f.SHA256)
		}
	}
	slices.Sort(onDisk)
	if slices.Sort(recorded); !slices.Equal(onDisk, recorded) || len(recorded) != 85+sealDisposition213Files {
		t.Fatalf("vendored %v, provenance records %v", onDisk, recorded)
	}
}

type liveLedgerRow struct {
	CapsuleID        string  `json:"capsule_id"`
	LiveHistory      string  `json:"live_history"`
	Decision         *string `json:"decision"`
	ActionClass      *string `json:"action_class"`
	DealID           *string `json:"deal_id"`
	ItemRef          *string `json:"item_ref"`
	TaskAuthorityRef *string `json:"task_authority_ref"`
	Stated           *string `json:"stated"`
}

type liveVector struct {
	Input            string            `json:"input"`
	History          string            `json:"history"`
	Ledger           []liveLedgerRow   `json:"ledger"`
	Complete         bool              `json:"complete"`
	Outcome          string            `json:"outcome"`
	Verdict          string            `json:"verdict"`
	Rules            map[string]string `json:"rules"`
	SingleCommitment struct {
		Result   string          `json:"result"`
		Reason   string          `json:"reason"`
		Evidence json.RawMessage `json:"evidence"`
	} `json:"single_commitment"`
}

type liveVectors struct {
	Pack struct {
		PackID           string `json:"pack_id"`
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	Decisions []liveVector `json:"decisions"`
}

// The ways the engine's test (python/tests/test_live_history.py _given) gives a
// check input's history.
const (
	historyAsSealed     = "as-sealed"
	historyAbsent       = "absent"
	historyCommitNoItem = "disposition-accept-commit-names-no-item"
	historyRecordNoItem = "disposition-accept-record-names-no-item"
)

// givenEnvelope is the check input name with its history given as history
// (_given): an accept disposition on every history act, unless as sealed or
// absent; no history and no scope when absent; no item_ref on the history's
// acceptances, or none at the top level; and the deal's claims added (the
// file the #139 vectors vendor, which is _given's bytes for that way).
func givenEnvelope(t *testing.T, name, history string) []byte {
	t.Helper()
	if history == historyWithClaims {
		return readFile(t, seller013Vectors, "go-vectors", "check-inputs", name+".claims-in-history.json")
	}
	var env map[string]json.RawMessage
	if err := json.Unmarshal(readFile(t, liveHistoryVectors, "engine-0ab20965", "check-inputs", name+".json"), &env); err != nil {
		t.Fatal(err)
	}
	var entries []map[string]json.RawMessage
	if err := json.Unmarshal(env["history"], &entries); err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if history != historyAsSealed {
			e["disposition"] = json.RawMessage(`{"decision":"accept"}`)
		}
		var shown struct {
			Body struct {
				ActionClass string `json:"action_class"`
			} `json:"body"`
		}
		if err := json.Unmarshal(e["agent_input"], &shown); err != nil {
			t.Fatal(err)
		}
		if history == historyCommitNoItem && shown.Body.ActionClass == "agreement.accept" {
			delete(e, itemRefInput)
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

// liveRows are the records the engine's history ledger writes for h, as the
// vectors list them: h's records oldest first, the statements of the claims
// beside the checked record after them, then the incomplete record
// under a's operator at its time when h may be missing acts.
func liveRows(t *testing.T, a Action, h History) []liveLedgerRow {
	t.Helper()
	rows := []liveLedgerRow{}
	for _, r := range append(liveOrder(h.Acts), h.claimed...) {
		row := liveLedgerRow{CapsuleID: r.CapsuleID, LiveHistory: string(r.live), Decision: r.sealed, ActionClass: r.ActionClass,
			DealID: r.DealID, ItemRef: r.ItemRef, TaskAuthorityRef: r.TaskAuthorityRef}
		if r.Stated != nil {
			row.LiveHistory, row.Stated = "statement", &r.Stated.Class
		}
		rows = append(rows, row)
	}
	if h.saleUnknown() {
		id, err := canon.Digest(canon.Obj(canon.M("operator", canon.Str(a.Operator)), canon.M("timestamp", canon.Str(a.Timestamp)),
			canon.M("asg_payload", canon.Obj(canon.M("live_history", canon.Str("incomplete"))))))
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, liveLedgerRow{CapsuleID: id, LiveHistory: "incomplete"})
	}
	return rows
}

// liveVectorDifferences decides every vector in dir under the pack the
// vectors name, as check --emit decides it (DecodeCheckInput,
// CheckWithHistory, Report), and returns where Go differs from the engine
// ("<input> <history> <what> Go <x> engine <y>").
func liveVectorDifferences(t *testing.T, dir string, load func() (pack.Pack, error), envelope func(name, history string) []byte) []string {
	t.Helper()
	return liveVectorFileDifferences(t, filepath.Join(dir, "expected_live.json"), load, envelope)
}

// liveVectorFileDifferences is liveVectorDifferences on the vectors in file
// (under liveHistoryVectors).
func liveVectorFileDifferences(t *testing.T, file string, load func() (pack.Pack, error), envelope func(name, history string) []byte) []string {
	t.Helper()
	dir := filepath.Dir(file)
	v := decodeJSON[liveVectors](t, readFile(t, liveHistoryVectors, file))
	p, err := load()
	if err != nil {
		t.Fatal(err)
	}
	if v.Pack.PackID != p.ID || v.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("%s: vectors under %+v, pack %s %s", dir, v.Pack, p.ID, p.DefinitionDigest)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	var diffs []string
	for _, want := range v.Decisions {
		name := strings.TrimSuffix(strings.TrimPrefix(want.Input, "check-inputs/"), ".json")
		at := name + " " + want.History
		a, h, err := DecodeCheckInput(envelope(name, want.History))
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
		differ(&diffs, at, "complete", fmt.Sprint(!h.saleUnknown()), fmt.Sprint(want.Complete))
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
		gotRows, err := json.Marshal(liveRows(t, a, h))
		if err != nil {
			t.Fatal(err)
		}
		wantRows, err := json.Marshal(want.Ledger)
		if err != nil {
			t.Fatal(err)
		}
		differ(&diffs, at, "ledger", string(gotRows), string(wantRows))
	}
	slices.Sort(diffs)
	return diffs
}

// CROSS-IMPLEMENTATION: every live vector, at 0ab20965 under
// asg/seller/0.1.2 (25) and at 08cd96d under asg/seller/0.1.3 (30, the
// deal's claims in the history too), decided as check --emit decides it,
// with NO known difference: the outcome, the verdict, whether the history
// is complete, every rule's result, single_commitment's result, reason and
// evidence, and the ledger the engine's history builder wrote (each act's
// state, sealed decision, class, deal, item_ref and task authority, oldest
// first, then the incomplete record).
func TestLiveHistoryVectorsAgreeWithTheEngine(t *testing.T) {
	given := func(name, history string) []byte { return givenEnvelope(t, name, history) }
	for _, c := range []struct {
		dir  string
		load func() (pack.Pack, error)
	}{{"engine-0ab20965", pack.LoadSeller012}, {"engine-08cd96d", pack.LoadSeller013}} {
		if diffs := liveVectorDifferences(t, c.dir, c.load, given); len(diffs) > 0 {
			t.Errorf("%s: Go and the engine differ at\n%s", c.dir, strings.Join(diffs, "\n"))
		}
	}
}
