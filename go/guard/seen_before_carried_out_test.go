package guard

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// COUNTERPARTY.SEEN_BEFORE/3.0.0 (capsule-engine #121, 97a2aff): the fold
// counts a record whose disposition.decision is carried_out beside an
// accepted one, leaving dry runs out; /2.0.0 counts accepted ones only, and
// no spend fold counts a carried_out record. Held against the engine's own
// fold evaluation of the engine side's cases (tests/test_counterparty_seen_before_
// carried_out.py), every vendored fold that could read them
// (testdata/parity/generate_seen_before_carried_out.py, "fold"). The file
// was regenerated at capsule-engine d1293252 (#126's merge), whose real
// two-deal fixture capsulectl rebuilt at capsule-cli 831afeeb: the chain
// half carries that fixture's deal 1. Normalised, every decision and count
// equals the #124 (79b63ea) copy; the pack it was generated under is the
// vendored one, digest for digest (seen-before-carried-out-parity.provenance.json).

type carriedOutVectors struct {
	CapsuleEngineCommit string `json:"capsule_engine_commit"`
	Pack                struct {
		PackID           string `json:"pack_id"`
		DefinitionDigest string `json:"definition_digest"`
	} `json:"pack"`
	FoldDigests map[string]string `json:"fold_digests"`
	Fold        map[string]struct {
		Records []foldVectorRecord `json:"records"`
		Counts  map[string]int64   `json:"counts"`
	} `json:"fold"`
}

// foldVectorRecord is one record of a fold case, as the engine reads it.
type foldVectorRecord struct {
	Operator    string `json:"operator"`
	Timestamp   string `json:"timestamp"`
	Disposition struct {
		Decision string `json:"decision"`
	} `json:"disposition"`
	Payload struct {
		Target     string `json:"target"`
		Checkpoint struct {
			DryRun *bool `json:"dry_run"`
		} `json:"checkpoint"`
		AmountMinor int64 `json:"amount_minor"`
	} `json:"asg_payload"`
}

func (v foldVectorRecord) record() Record {
	return Record{Operator: v.Operator, Timestamp: v.Timestamp, Decision: &v.Disposition.Decision,
		Target: &v.Payload.Target, DryRun: v.Payload.Checkpoint.DryRun, AmountMinor: &v.Payload.AmountMinor}
}

func TestSeenBeforeV3CountsACarriedOutActAsTheEngineDoes(t *testing.T) {
	var v carriedOutVectors
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "seen-before-carried-out-parity.json"), &v); err != nil {
		t.Fatal(err)
	}
	if v.CapsuleEngineCommit != "a710636c29814ce67e838c673026fd2063d913f8" || len(v.Fold) < 8 {
		t.Fatalf("%d fold cases at %s", len(v.Fold), v.CapsuleEngineCommit)
	}
	// Generated under 0.3.6, whose counterparty_seen_before and fold 0.3.7
	// cites unchanged (TestEveryday037Is036WithThePlatform).
	p, err := pack.LoadEveryday036()
	if err != nil {
		t.Fatal(err)
	}
	if v.Pack.PackID != p.ID || v.Pack.DefinitionDigest != p.DefinitionDigest {
		t.Fatalf("generated under %s %s, vendored %s %s", v.Pack.PackID, v.Pack.DefinitionDigest, p.ID, p.DefinitionDigest)
	}
	// What each fold reads the cases by: the target for seen_before, the
	// operator (as of one second after the records) for spend.
	keyed := map[string]bool{"counterparty.seen_before/2.0.0": true, "counterparty.seen_before/3.0.0": true,
		"spend.weekly/1.0.0": false, "spend.weekly/3.0.0": false}
	folds := map[string]fold{}
	for id, digest := range v.FoldDigests {
		f, ok := p.CatalogFold(id)
		if !ok || f.Digest != digest {
			t.Fatalf("fold %s: catalog %s, engine %s", id, f.Digest, digest)
		}
		if folds[id], err = newFold(f); err != nil {
			t.Fatal(err)
		}
	}
	if len(folds) != len(keyed) {
		t.Fatalf("%d folds, want %d", len(folds), len(keyed))
	}
	asOf := time.Date(2026, 10, 9, 12, 0, 1, 0, time.UTC)
	for name, c := range v.Fold {
		var records []Record
		for _, r := range c.Records {
			records = append(records, r.record())
		}
		for id, f := range folds {
			key, anchor := "household-a", asOf
			if keyed[id] {
				key, anchor = "merchant/ref-1", time.Time{}
			}
			got, _ := f.evaluate(records, key, anchor)
			if want, ok := c.Counts[id]; !ok || got != want {
				t.Errorf("%s: %s counts %d, the engine %d", name, id, got, want)
			}
		}
	}
	if got := v.Fold["carried-out"].Counts; got["counterparty.seen_before/3.0.0"] != 1 || got["counterparty.seen_before/2.0.0"] != 0 ||
		got["spend.weekly/3.0.0"] != 0 {
		t.Fatalf("the vectors no longer hold the change itself: %v", got)
	}
}

// THE SHIPPED r02/r06 READ /3.0.0: asg/everyday/0.3.8 cites
// counterparty_seen_before/4.0.0, whose fold is counterparty.seen_before/3.0.0,
// while recipient_seen_before/1.0.0 still reads /2.0.0 from the catalog.
func TestTheShippedSeenBeforeChecksReadTheirFolds(t *testing.T) {
	e, _ := everydayEngine(t)
	if e.seenBefore.fold.id != "counterparty.seen_before/3.0.0" || e.recipientSeenBefore.fold.id != "counterparty.seen_before/2.0.0" {
		t.Fatalf("counterparty_seen_before reads %s, recipient_seen_before %s", e.seenBefore.fold.id, e.recipientSeenBefore.fold.id)
	}
}
