// SPDX-License-Identifier: Apache-2.0

package guard

import (
	"cmp"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"time"

	"github.com/action-state-group/capsule-emit/go/artifact"
	"github.com/action-state-group/checkpointed-local-log/go/cll"
)

// StoreLog is the CLL entry log a StoreHistory reads: the order records were
// committed in and how many there are.
type StoreLog interface {
	cll.EntryStore
	cll.EntryCounter
}

// StoreHistory is a History over a CLL entry log and capsule-emit's artifact
// store, the pair a record is written to (artifact Put, then CLL Append; the
// append is the commit). A record put but never appended is not in it.
type StoreHistory struct {
	ctx       context.Context
	artifacts artifact.IndexStore
	log       StoreLog
}

// NewStoreHistory declares IndexPaths on the artifact store's namespace
// (indexing the records already stored) and returns the history of the two
// stores. ctx bounds every read the history makes.
func NewStoreHistory(ctx context.Context, artifacts artifact.IndexStore, log StoreLog) (*StoreHistory, error) {
	if err := artifacts.InitIndex(ctx, IndexPaths); err != nil {
		return nil, fmt.Errorf("history: declare index paths: %w", err)
	}
	return &StoreHistory{ctx: ctx, artifacts: artifacts, log: log}, nil
}

// Find reads every page of the artifact store's Find, drops the records the
// log does not hold, and returns the rest in log order. The time bound goes
// to the store truncated to the microsecond, the store's own precision, so
// it never leaves out a record Query.Since admits; a record whose timestamp
// the store does not read as a time (more than nine fraction digits, a comma
// before them) is not found under a time bound.
func (s *StoreHistory) Find(q Query) ([]Record, error) {
	aq := artifact.Query{Where: q.Where, Limit: artifact.MaxFindLimit}
	if !q.Since.IsZero() {
		aq.Since = q.Since.UTC().Truncate(time.Microsecond).Format(time.RFC3339Nano)
	}
	type found struct {
		seq uint64
		rec Record
	}
	var out []found
	for {
		page, err := s.artifacts.Find(s.ctx, aq)
		if err != nil {
			return nil, fmt.Errorf("history: find: %w", err)
		}
		for _, id := range page.IDs {
			seq, ok, err := s.seq(id)
			if err != nil {
				return nil, err
			}
			if !ok {
				continue
			}
			rec, err := s.record(id)
			if err != nil {
				return nil, err
			}
			out = append(out, found{seq, rec})
		}
		if page.Next == "" {
			break
		}
		aq.After = page.Next
	}
	slices.SortFunc(out, func(a, b found) int { return cmp.Compare(a.seq, b.seq) })
	recs := make([]Record, len(out))
	for i, f := range out {
		recs[i] = f.rec
	}
	return recs, nil
}

// Get returns the record with the capsule id, when the log holds it.
func (s *StoreHistory) Get(capsuleID string) (Record, bool, error) {
	_, ok, err := s.seq(capsuleID)
	if err != nil || !ok {
		return Record{}, false, err
	}
	rec, err := s.record(capsuleID)
	if err != nil {
		return Record{}, false, err
	}
	return rec, true, nil
}

// Size is the log's entry count.
func (s *StoreHistory) Size() (uint64, error) {
	n, err := s.log.EntryCount(s.ctx)
	if err != nil {
		return 0, fmt.Errorf("history: entry count: %w", err)
	}
	return n, nil
}

// seq is the log position of the capsule id, and false when the log does not
// hold it.
func (s *StoreHistory) seq(capsuleID string) (uint64, bool, error) {
	value, err := hex.DecodeString(capsuleID)
	if err != nil || len(value) != cll.EntryBytes {
		return 0, false, fmt.Errorf("history: capsule id %q is not %d bytes of hex", capsuleID, cll.EntryBytes)
	}
	e, err := s.log.GetEntry(s.ctx, value)
	if errors.Is(err, cll.ErrNotFound) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, fmt.Errorf("history: log entry %s: %w", capsuleID, err)
	}
	return e.Seq, true, nil
}

// record reads and decodes one stored capsule.
func (s *StoreHistory) record(capsuleID string) (Record, error) {
	stored, err := s.artifacts.Get(s.ctx, capsuleID)
	if err != nil {
		return Record{}, fmt.Errorf("history: get %s: %w", capsuleID, err)
	}
	rec, err := DecodeCapsule(stored.Capsule)
	if err != nil {
		return Record{}, fmt.Errorf("history: %s: %w", capsuleID, err)
	}
	return rec, nil
}

// DecodeCapsule reads one stored Agent Action Capsule as the checks read it.
func DecodeCapsule(capsule []byte) (Record, error) {
	var c capsuleJSON
	if err := json.Unmarshal(capsule, &c); err != nil {
		return Record{}, err
	}
	rec := Record{CapsuleID: c.CapsuleID, ActionID: c.ActionID, ActionType: c.ActionType,
		Operator: c.Operator, Developer: c.Developer, Timestamp: c.Timestamp}
	if c.Disposition != nil {
		rec.Decision = c.Disposition.Decision
	}
	if c.Payload != nil {
		rec.Target, rec.AmountMinor, rec.CounterpartyAccountRef = c.Payload.Target, c.Payload.AmountMinor, c.Payload.CounterpartyAccountRef
		rec.ActionClass, rec.SpendMinor, rec.TaxonomyVersion = c.Payload.ActionClass, c.Payload.SpendMinor, c.Payload.TaxonomyVersion
		rec.ReturnedMinor, rec.ReversesRef, rec.DealID, rec.Currency = c.Payload.ReturnedMinor, c.Payload.ReversesRef, c.Payload.DealID, c.Payload.Currency
		rec.TaskAuthorityRef, rec.ItemRef = c.Payload.TaskAuthorityRef, c.Payload.ItemRef
		rec.RepresentationClass, rec.EquivalenceKeyDigest = c.Payload.RepresentationClass, c.Payload.EquivalenceKeyDigest
		if s := c.Payload.Stated; s != nil {
			rec.Stated = &Statement{Class: s.Class, SourceKind: s.SourceKind, DealID: s.DealID, Claim: s.Claim}
		}
		rec.LiveHistory = c.Payload.LiveHistory
		rec.SpendUnreadable = c.Payload.SpendUnreadable != nil && *c.Payload.SpendUnreadable
		if c.Payload.Checkpoint != nil {
			rec.DryRun = c.Payload.Checkpoint.DryRun
		}
	}
	if c.Chain != nil {
		rec.ChainParent, rec.ChainRelation = c.Chain.ParentCapsuleID, c.Chain.Relation
	}
	return rec, nil
}
