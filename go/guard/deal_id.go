package guard

import (
	"bytes"
	"encoding/json"
)

// DealIDConflict is the marker of a record naming two different deals
// (AMENDMENT 4, capsule-engine #127): it is in no deal, never refused,
// never resolved by a pick, and neither value is repeated.
const DealIDConflict = "deal_id_conflict: the record states its deal in x-deal-v0.deal_id and chain_id with different values, so it is in no deal"

// DealIDConflictRow is the report row the marker is given, beside the
// rules' rows (as the engine reference check has one): verdict
// not_evaluable, reason DealIDConflict.
const DealIDConflictRow = "deal_id_conflict"

// chainIDMember names a typed record's deal, in inputs.
const chainIDMember = "chain_id"

// dealOf is the deal a disclosed agent_input names, as capsule-engine
// report/replay.py _deal_id reads it (#127, 763f5a9): x-deal-v0.deal_id when
// that key is present, else the top-level chain_id (a typed record names its
// deal so; capsulectl sets one to the other). Both keys present and unequal,
// in any type ("" against an id, an id against null), is a conflict: no deal.
// The deal is its value when a non-empty string; source is the member it
// came from.
func dealOf(agentInput json.RawMessage) (deal, source string, conflict bool) {
	var top map[string]json.RawMessage
	if json.Unmarshal(agentInput, &top) != nil {
		return "", "", false
	}
	var block map[string]json.RawMessage
	_ = json.Unmarshal(top["x-deal-v0"], &block)
	dealRaw, hasDeal := block["deal_id"]
	chainRaw, hasChain := top["chain_id"]
	if hasDeal && hasChain && !sameJSON(dealRaw, chainRaw) {
		return "", "", true
	}
	raw, src := chainRaw, chainIDMember
	if hasDeal {
		raw, src = dealRaw, dealIDMember
	}
	var s string
	if (!hasDeal && !hasChain) || json.Unmarshal(raw, &s) != nil || s == "" {
		return "", "", false
	}
	return s, src, false
}

// sameJSON is Python's == over two decoded JSON values: each decoded and
// encoded again, which sorts object keys and writes 1 and 1.0 alike.
func sameJSON(a, b json.RawMessage) bool {
	x, errX := reencoded(a)
	y, errY := reencoded(b)
	return errX == nil && errY == nil && bytes.Equal(x, y)
}

// reencoded is raw decoded and encoded again.
func reencoded(raw json.RawMessage) ([]byte, error) {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return nil, err
	}
	return json.Marshal(v)
}
