package guard

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"reflect"
	"slices"
	"testing"
	"time"

	emit "github.com/action-state-group/capsule-emit/go"
	"github.com/action-state-group/capsule-emit/go/artifact"
	artifactmemory "github.com/action-state-group/capsule-emit/go/artifact/memory"
	"github.com/action-state-group/checkpointed-local-log/go/cll"
	cllmemory "github.com/action-state-group/checkpointed-local-log/go/store/memory"
)

func TestMemoryHistoryFind(t *testing.T) {
	h := MemoryHistory{
		{CapsuleID: "a", Operator: "o1", ActionType: "decide", Target: ptr("t1"), Timestamp: "2026-10-01T00:00:00.0000009Z"},
		{CapsuleID: "b", Operator: "o2", ActionType: "decide", Target: ptr("t1"), Timestamp: "2026-10-02T00:00:00Z"},
		{CapsuleID: "c", Operator: "o1", ActionType: "fyi", Timestamp: "not a time"},
		{CapsuleID: "d", Operator: "o1", ActionType: "decide", Stated: &Statement{DealID: "deal-1"}, Timestamp: "2026-10-03T00:00:00Z"},
	}
	ids := func(q Query) []string {
		t.Helper()
		recs, err := h.Find(q)
		if err != nil {
			t.Fatal(err)
		}
		var out []string
		for _, r := range recs {
			out = append(out, r.CapsuleID)
		}
		return out
	}
	for _, tc := range []struct {
		name string
		q    Query
		want []string
	}{
		{"every record", Query{}, []string{"a", "b", "c", "d"}},
		{"one field", Query{Where: map[string]string{FieldOperator: "o1"}}, []string{"a", "c", "d"}},
		{"every field must match", Query{Where: map[string]string{FieldOperator: "o1", FieldTarget: "t1"}}, []string{"a"}},
		{"a missing field never matches", Query{Where: map[string]string{FieldTarget: ""}}, nil},
		{"statement deal", Query{Where: map[string]string{FieldStatedDealID: "deal-1"}}, []string{"d"}},
		// Read to the microsecond: a's nanoseconds past the bound do not
		// leave it out, and an unreadable timestamp never matches.
		{"since", Query{Since: time.Date(2026, 10, 1, 0, 0, 0, 999, time.UTC)}, []string{"a", "b", "d"}},
		{"since leaves out earlier", Query{Since: time.Date(2026, 10, 2, 0, 0, 0, 0, time.UTC)}, []string{"b", "d"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := ids(tc.q); !slices.Equal(got, tc.want) {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
		})
	}
	if _, err := h.Find(Query{Where: map[string]string{FieldEvent: "x"}}); err == nil {
		t.Fatal("a field Go records do not carry was found by")
	}
	if r, ok, err := h.Get("b"); err != nil || !ok || r.Operator != "o2" {
		t.Fatalf("Get(b) = %v, %v, %v", r, ok, err)
	}
	if _, ok, err := h.Get("z"); err != nil || ok {
		t.Fatalf("Get(z) = %v, %v", ok, err)
	}
	if n, err := h.Size(); err != nil || n != 4 {
		t.Fatalf("Size = %d, %v", n, err)
	}
}

// storeFixture is an artifact store and a CLL log holding capsules, every
// one put (signed) in reverse order and none yet appended.
type storeFixture struct {
	ctx       context.Context
	artifacts *artifactmemory.Store
	log       *cllmemory.Store
	history   *StoreHistory
}

func newStoreFixture(t *testing.T, capsules [][]byte) storeFixture {
	t.Helper()
	ctx := t.Context()
	_, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	identity, err := emit.NewEd25519SigningIdentity(priv)
	if err != nil {
		t.Fatal(err)
	}
	artifacts, err := artifactmemory.New("engine", []ed25519.PublicKey{priv.Public().(ed25519.PublicKey)})
	if err != nil {
		t.Fatal(err)
	}
	if err := artifacts.Init(ctx); err != nil {
		t.Fatal(err)
	}
	log := cllmemory.New()
	h, err := NewStoreHistory(ctx, artifacts, log)
	if err != nil {
		t.Fatal(err)
	}
	for i := len(capsules) - 1; i >= 0; i-- {
		id := capsuleIDOf(t, capsules[i])
		envelope, err := emit.Sign(emit.BuiltPayload{CapsuleID: id, JSON: capsules[i]}, identity)
		if err != nil {
			t.Fatalf("sign %s: %v", id, err)
		}
		if err := artifacts.Put(ctx, artifact.Record{CapsuleID: id, Capsule: capsules[i], ProducerEnvelope: envelope}); err != nil {
			t.Fatalf("put %s: %v", id, err)
		}
	}
	return storeFixture{ctx, artifacts, log, h}
}

func (f storeFixture) append(t *testing.T, capsule []byte) {
	t.Helper()
	value, err := hex.DecodeString(capsuleIDOf(t, capsule))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.log.Append(f.ctx, cll.AppendInput{Value: value}); err != nil {
		t.Fatal(err)
	}
}

func capsuleIDOf(t *testing.T, capsule []byte) string {
	t.Helper()
	var c struct {
		CapsuleID string `json:"capsule_id"`
	}
	if err := json.Unmarshal(capsule, &c); err != nil {
		t.Fatal(err)
	}
	return c.CapsuleID
}

func capsuleLines(t *testing.T, raw []byte) [][]byte {
	t.Helper()
	var out [][]byte
	for line := range bytes.SplitSeq(raw, []byte("\n")) {
		if line = bytes.TrimSpace(line); len(line) > 0 {
			out = append(out, line)
		}
	}
	return out
}

// TestStoreHistoryParity decides every fixture scenario over a StoreHistory
// and over a MemoryHistory of the same records, appended one at a time
// after all were put in reverse order (so the store's admission order is
// not the log's, and the records not yet appended are put but not
// committed), and requires the same decision from both.
func TestStoreHistoryParity(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) {
			e, _ := fx.engine(t)
			capsules := capsuleLines(t, readFile(t, fx.dir, "mini_ledger.jsonl"))
			ledger := miniLedger(t, fx)
			f := newStoreFixture(t, capsules)
			py := parity(t, fx).Scenarios
			f.append(t, capsules[0])
			for i, a := range scenarioActions(t, fx) {
				f.append(t, capsules[i+1])
				want, err := e.CheckWithTaskAuthority(a, MemoryHistory(ledger[:i+2]), py[i].TaskAuthorityRecord)
				if err != nil {
					t.Fatal(err)
				}
				got, err := e.CheckWithTaskAuthority(a, f.history, py[i].TaskAuthorityRecord)
				if err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(got, want) {
					t.Fatalf("%s: the store history decides %s, the memory history %s", a.ActionID, got.Outcome, want.Outcome)
				}
			}
			if n, err := f.history.Size(); err != nil || n != uint64(len(scenarioActions(t, fx))+1) {
				t.Fatalf("Size = %d, %v", n, err)
			}
		})
	}
}

func TestStoreHistoryFindAndGet(t *testing.T) {
	capsules := capsuleLines(t, readFile(t, fixtures037.dir, "mini_ledger.jsonl"))
	f := newStoreFixture(t, capsules)
	// Appended in the reverse of the puts' order, the first three only.
	for _, c := range capsules[:3] {
		f.append(t, c)
	}
	recs, err := f.history.Find(Query{})
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, r := range recs {
		got = append(got, r.CapsuleID)
	}
	want := []string{capsuleIDOf(t, capsules[0]), capsuleIDOf(t, capsules[1]), capsuleIDOf(t, capsules[2])}
	if !slices.Equal(got, want) {
		t.Fatalf("Find in log order = %v, want %v", got, want)
	}
	if _, ok, err := f.history.Get(capsuleIDOf(t, capsules[3])); err != nil || ok {
		t.Fatalf("a record put but never appended was got: %v, %v", ok, err)
	}
	if r, ok, err := f.history.Get(want[1]); err != nil || !ok || r.CapsuleID != want[1] {
		t.Fatalf("Get = %v, %v, %v", r.CapsuleID, ok, err)
	}
	if _, _, err := f.history.Get("zz"); err == nil {
		t.Fatal("a capsule id that is not hex was looked up")
	}
}

// TestStoreHistoryPages reads more matching records than one page of the
// artifact store's Find holds.
func TestStoreHistoryPages(t *testing.T) {
	capsules := capsuleLines(t, readFile(t, fixtures037.dir, "mini_ledger.jsonl"))
	f := newStoreFixture(t, capsules)
	for _, c := range capsules {
		f.append(t, c)
	}
	pages := &pagedStore{IndexStore: f.artifacts, limit: 2}
	h := &StoreHistory{ctx: f.ctx, artifacts: pages, log: f.log}
	recs, err := h.Find(Query{})
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != len(capsules) || pages.calls < len(capsules)/2 {
		t.Fatalf("read %d records in %d pages, want %d records", len(recs), pages.calls, len(capsules))
	}
	for i, r := range recs {
		if r.CapsuleID != capsuleIDOf(t, capsules[i]) {
			t.Fatalf("record %d is %s, out of log order", i, r.CapsuleID)
		}
	}
}

// pagedStore serves Find in pages of limit.
type pagedStore struct {
	artifact.IndexStore
	limit, calls int
}

func (p *pagedStore) Find(ctx context.Context, q artifact.Query) (artifact.Page, error) {
	p.calls++
	q.Limit = p.limit
	return p.IndexStore.Find(ctx, q)
}
