package guard

import (
	"encoding/json"
	"fmt"
	"slices"
)

// DecodeCheckInput reads one external-check-input/v0 envelope (already
// validated against its schema) into the action check --emit decides and
// the history it decides it on:
//
//   - the record entry (DecodeCheckInputRecord), with what capsulectl passes
//     beside it (report/replay.py action_for_check_input, capsule-engine
//     #136): the envelope's top-level item_ref, or, when there is none, the
//     record entry's own (AMENDMENT 6a); the entry's proposal_at, the time
//     of the accepted offer a seller's commit rests on (AMENDMENT 7); and
//     the envelope's party_role (WithPartyRole);
//   - the history: its scope, the task-authority record and floor opening
//     beside the check, the opening of a represent check's words in the
//     record entry (representing_opening, capsule-cli rules_checker.go;
//     capsule-engine #205), and each entry (History.AddEntry); then the deal's
//     earlier agent claims beside the record entry (record.deal_claims,
//     AMENDMENT 9; History.AddDealClaims).
func DecodeCheckInput(raw []byte) (Action, History, error) {
	var in struct {
		Record       json.RawMessage   `json:"record"`
		History      []json.RawMessage `json:"history"`
		HistoryScope struct {
			Days     int  `json:"days"`
			Complete bool `json:"complete"`
		} `json:"history_scope"`
		// The sealed task-authority record beside the deal check (capsule-cli
		// #142): task_authority reads it only when its digest equals the
		// check body's task_authority_ref.
		TaskAuthorityRecord json.RawMessage `json:"task_authority_record"`
		// The opening of the user's floor in force (capsule-cli #156),
		// read by price_floor only once it opens the commitment the bound
		// task-authority record seals.
		CommercialBoundsOpening json.RawMessage `json:"commercial_bounds_opening"`
		// The sale's item reference on a check of a sale's thread
		// (capsule-cli #159), read by single_commitment.
		ItemRef json.RawMessage `json:"item_ref"`
		// The side of the deal the user is on (capsule-cli #171), which
		// picks the pack.
		PartyRole json.RawMessage `json:"party_role"`
	}
	if err := json.Unmarshal(raw, &in); err != nil {
		return Action{}, History{}, fmt.Errorf("input: %w", err)
	}
	a, err := DecodeCheckInputRecord(in.Record)
	if err != nil {
		return Action{}, History{}, err
	}
	var entry struct {
		ItemRef    json.RawMessage `json:"item_ref"`
		ProposalAt json.RawMessage `json:"proposal_at"`
	}
	if err := json.Unmarshal(in.Record, &entry); err != nil {
		return Action{}, History{}, fmt.Errorf("record: %w", err)
	}
	itemRef := in.ItemRef
	if isJSONNull(itemRef) {
		itemRef = entry.ItemRef
	}
	a = withProposalAt(WithItemRef(a, itemRef), entry.ProposalAt)
	a = WithPartyRole(a, in.PartyRole)
	h := History{Days: in.HistoryScope.Days, Complete: in.HistoryScope.Complete, TaskAuthorityRecord: in.TaskAuthorityRecord,
		CommercialBoundsOpening: in.CommercialBoundsOpening, RepresentingOpening: objectOf(in.Record)[representingOpeningInput]}
	for _, e := range in.History {
		if err := h.AddEntry(e, a); err != nil {
			return Action{}, History{}, err
		}
	}
	a = h.AddDealClaims(in.Record, a)
	return a, h, nil
}

// representingOpeningInput names the record entry's opening of a represent
// check's words, {nonce, text}, read only by promise_never/1.1.0 once it
// opens the commitment the check seals.
const representingOpeningInput = "representing_opening"

// proposalAtInput names the record entry's proposal_at.
const proposalAtInput = "proposal_at"

// withProposalAt is a with the record entry's proposal_at (AMENDMENT 7,
// action_for_check_input): a string is the proposal's time, which
// offer_expiry reads and judges; any other value is named in the ignored
// inputs, never read. Absent (nil or null), a is unchanged.
func withProposalAt(a Action, raw json.RawMessage) Action {
	if isJSONNull(raw) {
		return a
	}
	var at string
	if json.Unmarshal(raw, &at) != nil {
		a.ignoredInputs = append(slices.Clone(a.ignoredInputs), proposalAtInput)
		return a
	}
	a.ProposalAt = &at
	a.present = append(slices.Clone(a.present), proposalAtInput)
	slices.Sort(a.present)
	return a
}
