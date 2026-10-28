// SPDX-License-Identifier: Apache-2.0

package guard

import (
	"fmt"
	"slices"
	"time"
)

// History is what the checks read of earlier records: each check asks for
// the records that can bear on it and runs its own rule over them, so no
// check reads the whole log. The store never decides "accepted", "first",
// "last" or "count"; the check does. NewStoreHistory reads a CLL entry log
// beside capsule-emit's artifact store; MemoryHistory holds records in
// memory, as a check input's history and tests give them.
type History interface {
	// Find returns every record matching q, in log order.
	Find(q Query) ([]Record, error)
	// Get returns the record with the capsule id, and false when there is
	// none.
	Get(capsuleID string) (Record, bool, error)
	// Size is the number of entries in the log.
	Size() (uint64, error)
}

// Query selects records by equality on indexed fields (IndexPaths), all of
// which must match, and by a lower bound on their time.
type Query struct {
	// Where maps an indexed field to the value it must hold.
	Where map[string]string
	// Since, when not zero, is the earliest time a record may carry, read
	// to the microsecond. A record whose timestamp cannot be read as an
	// RFC 3339 time does not match.
	Since time.Time
}

// The indexed fields: the capsule paths a store indexes, which Query.Where
// names. Fields this package never queries are indexed too, so a Go and a
// Python engine open one store namespace with the same declaration.
const (
	FieldOperator      = "operator"
	FieldDeveloper     = "developer"
	FieldActionType    = "action_type"
	FieldTarget        = "asg_payload.target"
	FieldDealID        = "asg_payload.deal_id"
	FieldStatedDealID  = "asg_payload.stated.deal_id"
	FieldItemRef       = "asg_payload.item_ref"
	FieldChainParent   = "chain.parent_capsule_id"
	FieldEvent         = "asg_payload.event"
	FieldSessionID     = "asg_payload.detail.session_id"
	FieldReferencedIDs = "asg_payload.detail.referenced_capsule_ids"
)

// IndexPaths are the paths a store declares for the engine, in the order
// capsule-engine's Python package declares them.
var IndexPaths = []string{
	FieldOperator, FieldDeveloper, FieldActionType, FieldTarget, FieldDealID, FieldStatedDealID,
	FieldItemRef, FieldChainParent, FieldEvent, FieldSessionID, FieldReferencedIDs,
}

// recordField is r's value at an indexed field, and whether r has one. The
// Python-only fields have no Record member, so a query on them is refused.
func recordField(r Record, field string) (string, bool, error) {
	ptr := func(p *string) (string, bool, error) {
		if p == nil {
			return "", false, nil
		}
		return *p, true, nil
	}
	switch field {
	case FieldOperator:
		return r.Operator, true, nil
	case FieldDeveloper:
		return r.Developer, true, nil
	case FieldActionType:
		return r.ActionType, true, nil
	case FieldTarget:
		return ptr(r.Target)
	case FieldDealID:
		return ptr(r.DealID)
	case FieldStatedDealID:
		if r.Stated == nil {
			return "", false, nil
		}
		return r.Stated.DealID, true, nil
	case FieldItemRef:
		return ptr(r.ItemRef)
	case FieldChainParent:
		return ptr(r.ChainParent)
	}
	return "", false, fmt.Errorf("history: field %q is not one a Go record can be found by", field)
}

// matches is whether r matches q, as Query documents it.
func (q Query) matches(r Record) (bool, error) {
	for field, want := range q.Where {
		got, ok, err := recordField(r, field)
		if err != nil {
			return false, err
		}
		if !ok || got != want {
			return false, nil
		}
	}
	if !q.Since.IsZero() {
		t, err := parseInstant(r.Timestamp)
		if err != nil || t.Truncate(time.Microsecond).Before(q.Since.Truncate(time.Microsecond)) {
			return false, nil
		}
	}
	return true, nil
}

// MemoryHistory is a History over records held in memory, in log order:
// a check input's history, a replay's records, a test's.
type MemoryHistory []Record

// Find returns the records matching q, in order.
func (m MemoryHistory) Find(q Query) ([]Record, error) {
	var out []Record
	for _, r := range m {
		ok, err := q.matches(r)
		if err != nil {
			return nil, err
		}
		if ok {
			out = append(out, r)
		}
	}
	return out, nil
}

// Get returns the first record with the capsule id.
func (m MemoryHistory) Get(capsuleID string) (Record, bool, error) {
	i := slices.IndexFunc(m, func(r Record) bool { return r.CapsuleID == capsuleID })
	if i < 0 {
		return Record{}, false, nil
	}
	return m[i], true, nil
}

// Size is the number of records.
func (m MemoryHistory) Size() (uint64, error) { return uint64(len(m)), nil }

// orEmpty is h, or an empty history for a nil one.
func orEmpty(h History) History {
	if h == nil {
		return MemoryHistory(nil)
	}
	return h
}

// historyReader is the History a check reads through. A check's rule has
// no error path of its own, so the first error a read meets is kept here, the
// read returns no records, and constraintFor returns the error in place of
// the check's result.
type historyReader struct {
	h   History
	err error
}

// find is h.Find, keeping the first error. A nil reader, or one over no
// history, holds no records.
func (r *historyReader) find(q Query) []Record {
	if r == nil || r.h == nil || r.err != nil {
		return nil
	}
	recs, err := r.h.Find(q)
	if err != nil {
		r.err = err
		return nil
	}
	return recs
}
