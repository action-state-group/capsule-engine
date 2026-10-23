package guard

import (
	"encoding/json"
	"slices"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// A statement the user's agent made to the counterparty in a deal
// (capsule-engine #139, 08cd96d: guards/statements.py, report/replay.py
// statement_record). A seller's agent tells a buyer something about the
// deal (the item's condition, a delivery date) in a capsulectl claim record
// that seals body.source_kind "agent" and a body.class. It states no act,
// so no decision carries it; the replay and a live check each write one
// ledger record for it, so required_disclosure/1.1.0 finds the statement
// made first in the deal. The record carries no disposition, no target and
// no action class, so no fold that reads decisions, payees or spend counts
// it.

// Statement is a ledger record's asg_payload.stated: the class stated
// (CLASS_ALIASES applied), the source_kind ("agent"), the one deal the claim
// names, and the claim's capsule id.
type Statement struct {
	Class, SourceKind, DealID, Claim string
}

// statementClassAliases is statements.py CLASS_ALIASES: capsulectl sealed
// delivery_promise from rc13 until it adopted the seller pack's
// representation classes (AMENDMENT 5a-bis), and writes delivery_date for
// new claims since. The only alias.
var statementClassAliases = map[string]string{"delivery_promise": "delivery_date"}

// claimRecordType is the x-deal-v0 record type of a statement in a deal
// (capsule-cli internal/cli/deal_claims.go), and agentSource the
// source_kind of one the user's agent made to the counterparty.
const (
	claimRecordType = "claim"
	agentSource     = "agent"
)

// StatementOf is replay.py statement_record: the ledger record of the
// statement capsule (a capsule header) makes, read from its disclosed
// agentInput, or false when it makes none. It must be an x-deal-v0 claim
// the capsule binds (its agent_input_digest), whose body seals source_kind
// "agent" and a non-empty class, in the one deal it names (dealOf; a record
// naming two names none), with a non-empty capsule_id. A disclosure
// record's body.fields[].class is never read: that is the user's personal
// data given out, not a statement about the deal. The same reader serves
// the replay and a live check's history (HistoryStatement).
//
// The record is the engine's make_statement_record (statementRecord):
// operator (the capsule's, "" when absent), timestamp the claim's own sealed
// x-deal-v0.at, never the capsule's timestamp (capsule-engine #142, note 2
// on AMENDMENT 9: one clock read in capsulectl, and only at is the
// string a live check also receives in deal_claims; null unless a non-empty
// string), and asg_payload.stated. A capsule whose operator is
// present and not a string makes no statement here, where the engine
// writes its Python str(); such a capsule is no capsulectl record.
func StatementOf(capsule, agentInput json.RawMessage) (Record, bool) {
	var header map[string]json.RawMessage
	if len(agentInput) == 0 || json.Unmarshal(capsule, &header) != nil || checkAgentInputDigest(header, agentInput) != nil {
		return Record{}, false
	}
	var in struct {
		XDeal *struct {
			RecordType json.RawMessage `json:"record_type"`
			At         json.RawMessage `json:"at"`
		} `json:"x-deal-v0"`
		Body *struct {
			SourceKind json.RawMessage `json:"source_kind"`
			Class      json.RawMessage `json:"class"`
		} `json:"body"`
	}
	if json.Unmarshal(agentInput, &in) != nil || in.XDeal == nil || in.Body == nil ||
		!isString(in.XDeal.RecordType, claimRecordType) || !isString(in.Body.SourceKind, agentSource) {
		return Record{}, false
	}
	class, ok := nonEmptyText(in.Body.Class)
	deal, _, _ := dealOf(agentInput)
	claim, hasID := nonEmptyText(header["capsule_id"])
	if !ok || deal == "" || !hasID {
		return Record{}, false
	}
	operator := ""
	if raw, present := header["operator"]; present && json.Unmarshal(raw, &operator) != nil {
		return Record{}, false
	}
	var at *string
	if ts, ok := nonEmptyText(in.XDeal.At); ok {
		at = &ts
	}
	return statementRecord(operator, at, class, deal, claim)
}

// statementRecord is statements.py make_statement_record: the ledger
// record of one statement, its class with CLASS_ALIASES applied, dated by
// at (null when nil), its capsule_id the JSON digest of the rest, so one
// claim is one record live and in a replay.
func statementRecord(operator string, at *string, class, deal, claim string) (Record, bool) {
	if alias, ok := statementClassAliases[class]; ok {
		class = alias
	}
	r := Record{Operator: operator, Stated: &Statement{Class: class, SourceKind: agentSource, DealID: deal, Claim: claim}}
	ts := canon.Null()
	if at != nil {
		r.Timestamp, ts = *at, canon.Str(*at)
	}
	id, err := canon.Digest(canon.Obj(canon.M("operator", canon.Str(operator)), canon.M("timestamp", ts),
		canon.M("asg_payload", canon.Obj(canon.M("stated", canon.Obj(canon.M("class", canon.Str(class)),
			canon.M("source_kind", canon.Str(agentSource)), canon.M("deal_id", canon.Str(deal)),
			canon.M("claim", canon.Str(claim))))))))
	if err != nil {
		return Record{}, false
	}
	r.CapsuleID = id
	return r, true
}

// isString is whether raw is the JSON string want.
func isString(raw json.RawMessage, want string) bool {
	var s string
	return json.Unmarshal(raw, &s) == nil && s == want
}

// nonEmptyText is replay.py _text: raw when it is a non-empty JSON string.
func nonEmptyText(raw json.RawMessage) (string, bool) {
	var s string
	if len(raw) == 0 || json.Unmarshal(raw, &s) != nil || s == "" {
		return "", false
	}
	return s, true
}

// HistoryStatement is replay.py statement_for_history_entry: the statement
// an external-check-input/v0 history entry records (StatementOf), read from
// its capsule and its disclosed agent_input; false when it records none.
// The members capsulectl passes beside the capsule (agent_input,
// counterparty_profile, proposal_at, item_ref; _entry_parts) are left in:
// StatementOf reads none of them from the header.
func HistoryStatement(entry json.RawMessage) (Record, bool) {
	var e struct {
		AgentInput json.RawMessage `json:"agent_input"`
	}
	if json.Unmarshal(entry, &e) != nil {
		return Record{}, false
	}
	return StatementOf(entry, e.AgentInput)
}

// AddEntry adds one history entry to h, as live_history.py history_ledger
// writes it, for the check a: a claim the user's agent made to the
// counterparty (HistoryStatement) is written as its statement record, never
// as an act, so it is never counted as unread, deduped or added to a spend
// window. It is written only when sealed at or before a's timestamp, and
// not at all when either time cannot be read (an entry whose own time
// cannot be read also leaves the history incomplete, as the engine reads
// it); a claim given twice is written once. Any other entry is an act
// (DecodeHistoryAct), refused when it cannot be read.
func (h *History) AddEntry(entry json.RawMessage, a Action) error {
	// live_history.py: an entry passing an item_ref not in its agreed shape
	// (a null included) leaves which sale its act is in unknown, so the
	// history may be missing an act of a sale (saleUnknown).
	var side struct {
		ItemRef json.RawMessage `json:"item_ref"`
	}
	if json.Unmarshal(entry, &side) == nil && side.ItemRef != nil {
		var item string
		if json.Unmarshal(side.ItemRef, &item) != nil || !hex64.MatchString(item) {
			h.itemRefUnread = true
		}
	}
	stated, ok := HistoryStatement(entry)
	if !ok {
		r, err := DecodeHistoryAct(entry)
		if err != nil {
			return err
		}
		h.Acts = append(h.Acts, r)
		return nil
	}
	var header struct {
		Timestamp string `json:"timestamp"`
	}
	_ = json.Unmarshal(entry, &header)
	at, err := parseInstant(header.Timestamp)
	if err != nil {
		h.Complete = false
		return nil
	}
	checked, err := parseInstant(a.Timestamp)
	if err != nil || at.After(checked) || h.statements[stated.Stated.Claim] {
		return nil
	}
	h.addStatement(stated)
	h.Acts = append(h.Acts, stated)
	return nil
}

// addStatement marks stated's claim as written, so the same claim given
// again, in the history or in deal_claims, is written once (live_history.py
// claim_of, capsule-engine #142).
func (h *History) addStatement(stated Record) {
	if h.statements == nil {
		h.statements = map[string]bool{}
	}
	h.statements[stated.Stated.Claim] = true
}

// dealClaimsInput names the claims capsulectl passes beside the checked
// record (record.deal_claims, AMENDMENT 9; the engine's _CLAIMS_INPUT).
const dealClaimsInput = "deal_claims"

// dealClaim is one record.deal_claims entry as AMENDMENT 9 states it: the
// sealed claim record's own values, the words only as a commitment.
type dealClaim struct {
	CapsuleID      json.RawMessage `json:"capsule_id"`
	RecordDigest   json.RawMessage `json:"record_digest"`
	Class          json.RawMessage `json:"class"`
	SourceKind     json.RawMessage `json:"source_kind"`
	At             json.RawMessage `json:"at"`
	TextCommitment json.RawMessage `json:"text_commitment"`
}

// isHex64 is whether raw is a JSON string of 64 lowercase hex.
func isHex64(raw json.RawMessage) bool {
	var s string
	return json.Unmarshal(raw, &s) == nil && hex64.MatchString(s)
}

// AddDealClaims writes the statements the claims beside the checked record
// make (replay.py deal_claim_statements and live_history.py, capsule-engine
// #140 and #142), after the history's records, as the engine writes them:
// each as statementRecord writes the same claim in a bundle, with the claim's
// capsule_id, its class, source_kind "agent", the checked record's deal and
// operator (a's), and its sealed at as its time. A claim the history already
// gave is written once; the same claim given twice in deal_claims, once.
//
// An entry counts only in its agreed shape: capsule_id, record_digest and
// text_commitment 64 lowercase hex, a non-empty class, source_kind "agent",
// and at an RFC 3339 time with a zone at or before a's timestamp (whole
// seconds: capsulectl seals a claim and the next step in one second). Any
// other entry, a deal_claims that is not a list, or a checked record whose
// deal cannot be read (naming two, or none) or whose time cannot be read is
// ignored: deal_claims is named in a's ignored inputs, and nothing it holds
// is kept, so none of it is ever echoed. A class outside the pack's set is
// not ignored: it satisfies nothing. Absent or null, a and h are unchanged;
// an empty list is no claims. The entries are capsulectl's word, passed
// beside the sealed record like counterparty_profile: record_digest is
// checked for shape only, since a live check has no claim record to
// recompute it from (AMENDMENT 9 note 1).
func (h *History) AddDealClaims(record json.RawMessage, a Action) Action {
	var entry struct {
		DealClaims json.RawMessage `json:"deal_claims"`
		AgentInput json.RawMessage `json:"agent_input"`
	}
	if json.Unmarshal(record, &entry) != nil || isJSONNull(entry.DealClaims) {
		return a
	}
	ignore := func() Action {
		a.ignoredInputs = append(slices.Clone(a.ignoredInputs), dealClaimsInput)
		return a
	}
	var claims []json.RawMessage
	deal, _, _ := dealOf(entry.AgentInput)
	checked, err := parseInstant(a.Timestamp)
	if json.Unmarshal(entry.DealClaims, &claims) != nil || claims == nil || deal == "" || err != nil {
		return ignore()
	}
	ignored := false
	for _, raw := range claims {
		var c dealClaim
		var at, class, kind string
		if json.Unmarshal(raw, &c) != nil || json.Unmarshal(c.At, &at) != nil || json.Unmarshal(c.Class, &class) != nil ||
			json.Unmarshal(c.SourceKind, &kind) != nil {
			ignored = true
			continue
		}
		when, err := parseInstant(at)
		if err != nil || when.After(checked) || class == "" || kind != agentSource ||
			!isHex64(c.CapsuleID) || !isHex64(c.RecordDigest) || !isHex64(c.TextCommitment) {
			ignored = true
			continue
		}
		var claim string
		_ = json.Unmarshal(c.CapsuleID, &claim)
		stated, ok := statementRecord(a.Operator, &at, class, deal, claim)
		if !ok {
			ignored = true
			continue
		}
		if h.statements[claim] {
			continue
		}
		h.addStatement(stated)
		h.claimed = append(h.claimed, stated)
	}
	if ignored {
		return ignore()
	}
	return a
}

// statementsIn is the operator's statement records in deal
// (required_disclosure.py _in_deal).
func statementsIn(ledger []Record, operator, deal string) []Record {
	var out []Record
	for _, r := range ledger {
		if r.Operator == operator && r.Stated != nil && r.Stated.DealID == deal {
			out = append(out, r)
		}
	}
	return out
}
