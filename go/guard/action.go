// Package guard is the Go port of the everyday pack's six deterministic checks
// (caps, dedupe, destination_rail, counterparty_identity_change,
// credential_pattern, recurring_charge) and the decision they compose into.
// The semantics are the vendored wicket and fold definitions'; where a
// definition leaves something open, capsule-engine 559288d0f61a's
// implementation (capsule_engine/guards/...) was read and is cited at the
// point it decides. The pack is the authority: an engine is built from a
// loaded pack and refuses one whose definitions are not the ones ported.
package guard

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"slices"
	"strings"
	"time"
)

// Action is one normalized candidate action (capsule_engine/guards/action.py
// Action). Absent optional fields are nil.
type Action struct {
	Verb        string  `json:"verb"`
	Operator    string  `json:"operator"`
	Developer   string  `json:"developer"`
	ActionClass *string `json:"action_class,omitempty"`
	ActionID    string  `json:"action_id,omitempty"`
	ActionType  string  `json:"action_type,omitempty"`
	Timestamp   string  `json:"timestamp,omitempty"`
	AmountMinor *int64  `json:"amount_minor,omitempty"`
	// SpendAuthorizedMinor is the most the payment may take when that was
	// declared (a card hold, a pre-authorisation with a buffer): what a
	// caps/5.0.0 per-action limit reads. Never summed by the window.
	SpendAuthorizedMinor *int64 `json:"spend_authorized_minor,omitempty"`
	// StatedAmountMinor is the amount a deal record states (action.py
	// stated_amount_minor, capsule-engine #129): on a sale the price, while
	// its spend is 0. Read only by price_floor; never summed by a window.
	StatedAmountMinor     *int64  `json:"stated_amount_minor,omitempty"`
	Currency              *string `json:"currency,omitempty"`
	Target                *string `json:"target,omitempty"`
	CitedMandateCapsuleID *string `json:"cited_mandate_capsule_id,omitempty"`
	EquivalenceKey        *string `json:"equivalence_key,omitempty"`
	Rail                  *string `json:"rail,omitempty"`
	// Handover is how the item reaches the buyer, a closed token
	// (Handovers: in_person or shipped; action.py handover_of): anything
	// else is no handover. Read by seller.destination_rail/1.1.0.
	Handover               *string `json:"handover,omitempty"`
	CounterpartyAccountRef *string `json:"counterparty_account_ref,omitempty"`
	OutgoingContent        *string `json:"outgoing_content,omitempty"`
	Recurrence             *string `json:"recurrence,omitempty"`
	// Refundable is whether the payment can be refunded, read by
	// refundability (action.py refundable). On a deal record it comes from
	// the body's recourse block (see DecodeRecord).
	Refundable *bool `json:"refundable,omitempty"`
	// UpfrontAmountMinor is the stated deposit, in the same minor units as
	// amount_minor, read by upfront_amount (action.py upfront_amount_minor).
	UpfrontAmountMinor *int64 `json:"upfront_amount_minor,omitempty"`
	// RecipientRole is the role of whoever receives a disclosure, one of a
	// closed set, never who they are; read by recipient_role (action.py
	// recipient_role).
	RecipientRole *string `json:"recipient_role,omitempty"`
	// DisclosedClasses are the classes of personal data a disclosure gives
	// (email, phone, address, ...), the names only, never the values: sorted,
	// each named once, all from the closed set DisclosedClassNames (action.py
	// disclosed_classes, capsule-engine #173). Read by recipient_role for a
	// role a pack allows only some classes (role_classes). On a deal record
	// it is the body's disclosing (see DecodeRecord).
	DisclosedClasses []string `json:"disclosed_classes,omitempty"`
	// RepeatDisclosures are the classes the producer reports as already
	// told to this recipient, and RecipientToldBefore whether it reports the
	// recipient as told anything before (capsule-engine #203): read by
	// action_class_gate/1.2.0's except keys; never sealed. Classes are
	// DisclosedClassNames members, sorted and each named once; empty allowed.
	RepeatDisclosures   []string `json:"repeat_disclosures,omitempty"`
	RecipientToldBefore *bool    `json:"recipient_told_before,omitempty"`
	// ListingClasses are, on an offer or a commit of a sale's thread, the
	// representation classes every live listing of the sale states when the
	// check was made, as capsulectl seals them (capsule-engine #204,
	// Action.listing_classes): class tokens sorted and each named once,
	// empty when the listings in force state none in common; nil with no
	// post. Read by required_disclosure/1.2.0; never sealed.
	ListingClasses []string `json:"listing_classes,omitempty"`
	// TaskAuthorityRef is the SHA-256 digest (hex) of the whole sealed
	// task-authority record the action cites, read by task_authority
	// (action.py task_authority_ref). On a deal record it is the digest of
	// the body's typed task_authority_ref (see DecodeRecord).
	TaskAuthorityRef *string `json:"task_authority_ref,omitempty"`
	// RepresentationClass is the one class of statement the action makes to
	// a counterparty (price, condition, ...), one member of a closed set,
	// never the statement itself (action.py representation_class at
	// ba76a97): read by promise_never. AuthorizedBy is the SHA-256 digest
	// (hex) of the whole sealed approval record the action cites
	// (authorized_by), which promise_never records and does not count. On a
	// deal record the class is a represent check's representing.class
	// (DecodeRecord, capsule-engine #205); authorized_by is never read from
	// a deal body, so DecodeRecord lists it as ignored there.
	RepresentationClass *string `json:"representation_class,omitempty"`
	AuthorizedBy        *string `json:"authorized_by,omitempty"`
	// StatementAttributedTo says whose claim the statement is: user when it
	// goes out as the user's own claim, marked unverified (capsulectl's
	// representing.attributed_to), unset for the agent's own. Read by
	// promise_never/1.1.0. A closed token: any other value is dropped when
	// the action is decoded (action.py Action.__post_init__,
	// capsule-engine #205).
	StatementAttributedTo *string `json:"statement_attributed_to,omitempty"`
	// representationTextCommitment is a represent check's sealed
	// representing.text_commitment (DecodeRecord): the digest when it is 64
	// lowercase hex, malformedTextCommitment when it is present and out of
	// shape, nil when absent. promise_never/1.1.0 opens it with the words
	// passed beside the check (statementWords). It is never sealed.
	representationTextCommitment *string
	// Fields of a capsule-cli v0.1.0-rc9 deal action body
	// (record-common-v0.schema.json action_body). spend_minor is what a spend
	// cap evaluates there. The vendored fold and caps read amount_minor, so
	// caps evaluates an action only where the two agree (see checkCaps).
	TaxonomyVersion      *string `json:"taxonomy_version,omitempty"`
	SpendMinor           *int64  `json:"spend_minor,omitempty"`
	FeeMinor             *int64  `json:"fee_minor,omitempty"`
	CancelledAmountMinor *int64  `json:"cancelled_amount_minor,omitempty"`
	Direction            *string `json:"direction,omitempty"`
	// The change scalars a deal check body carries (capsule-cli #142): each
	// a number, a closed-set member or a digest, read by the check of the
	// same name (capsule-engine 365eb92 guards/action.py). Channel is the
	// channel kind in use at this check and FirstContactChannel the one the
	// relationship started on, read by channel_change.
	Channel             *string `json:"channel,omitempty"`
	FirstContactChannel *string `json:"first_contact_channel,omitempty"`
	// MaterialFieldsChanged is how many fields on the material list differ
	// from what was agreed, and MaterialFieldsBasis the digest of the list
	// the producer counted over, read by material_fields_changed.
	MaterialFieldsChanged *int64  `json:"material_fields_changed,omitempty"`
	MaterialFieldsBasis   *string `json:"material_fields_basis,omitempty"`
	// OfferFieldsChanged is the same count over the offer list against what
	// the user stated, and OfferFieldsBasis that list's digest, read by
	// offer_fields_changed.
	OfferFieldsChanged *int64  `json:"offer_fields_changed,omitempty"`
	OfferFieldsBasis   *string `json:"offer_fields_basis,omitempty"`
	// ReturnedMinor is the amount an act moving money in (a refund, a
	// partial cancel) returns, in the same minor units as amount_minor, and
	// ReversesRef the SHA-256 digest (hex) of the record of the act it
	// reverses (capsule-engine #108, guards/action.py returned_minor and
	// reverses_ref). On a deal record both are set only when the money moved
	// in (see DecodeRecord). Read by dedupe: they key a refund in place of
	// the amount.
	ReturnedMinor *int64  `json:"returned_minor,omitempty"`
	ReversesRef   *string `json:"reverses_ref,omitempty"`
	// DealID is the deal the act is checked in (capsule-engine #111,
	// guards/action.py deal_id): on a deal record its sealed
	// x-deal-v0.deal_id, never the action_id prefix. Read by dedupe: a
	// repeat in the same deal is refused, one in another deal may ask an
	// approver. It never enters the act key.
	DealID *string `json:"deal_id,omitempty"`
	// ProposalAt is the RFC 3339 UTC timestamp of the proposal the action
	// acts on, read by offer_expiry (action.py proposal_at). capsule-engine's
	// deal bridge does not read one from a deal record (report/replay.py
	// _bridge_deal_check at ba76a97), and neither does DecodeRecord: a deal
	// check states none, and offer_expiry fails it as unverified.
	ProposalAt *string `json:"proposal_at,omitempty"`
	// NoProposal is set on an offer whose producer sealed that it answers no
	// proposal (no counterparty message was on record before it) and which
	// names no source ref (capsule-engine #202, Action.no_proposal; read by
	// offer_expiry/1.1.0). A flag only: true or absent.
	NoProposal *bool `json:"no_proposal,omitempty"`
	// ItemRef is the opaque reference to the item a sale is about, equal
	// across the sale's threads, read by single_commitment (action.py
	// item_ref). No deal record carries it: on --emit it is the checker
	// input's top-level item_ref (WithItemRef), as capsule-engine's
	// action_for_check_input takes it.
	ItemRef *string `json:"item_ref,omitempty"`
	// PartyRole is the side of the deal the action is checked for: buyer or
	// seller, as capsule-cli seals it on the deal's intent (DecodeRecord reads
	// it there); absent on a buyer's deal. No check reads it: the caller
	// picks the pack by it. A role that is not a known one
	// (pack.KnownPartyRole) leaves the action unevaluable, naming it.
	PartyRole *string `json:"party_role,omitempty"`
	// The rest of an rc9 deal action body. No check reads them; they are
	// accepted so a real deal body is not refused, and the machine output
	// lists them as ignored. action is not taken as verb, nor counterparty
	// as target: no ruling maps them.
	DealAction            json.RawMessage `json:"action,omitempty"`
	ReferenceCommitment   json.RawMessage `json:"reference_commitment,omitempty"`
	DescriptionCommitment json.RawMessage `json:"description_commitment,omitempty"`
	Disclosed             json.RawMessage `json:"disclosed,omitempty"`
	Counterparty          json.RawMessage `json:"counterparty,omitempty"`
	// present are the fields the action body carried, as DecodeAction read
	// them (for the machine output's inputs).
	present []string
	// Set by DecodeRecord: the record's own name for an action field (the
	// body's action is the verb), body fields the action does not carry,
	// why the record cannot be evaluated, and that a taxonomy_version is
	// required beside an action_class.
	sourceName  map[string]string
	extra       []string
	unevaluable []string
	fromRecord  bool
	// statesNoAct: a deal record that is not a check (its sealed
	// x-deal-v0.record_type is another string): it states no act, so dedupe
	// does not apply to it (capsule-engine #107, report/replay.py
	// _states_no_act).
	statesNoAct bool
	// ignoredInputs names each input the action was built without because
	// it was not in its agreed shape (DecodeCheckInputRecord: a
	// counterparty_profile block), as the engine's Action.ignored_inputs
	// (capsule-engine #115, 4ac9fb0); the counterparty_seen_before evidence
	// lists them. Names only, never the value.
	ignoredInputs []string
	// dealIDConflict marks a deal record whose x-deal-v0.deal_id and
	// chain_id name different deals (dealOf): it is in no deal.
	dealIDConflict bool
	// counterpartyIDs and counterpartyFPAlg are the keyed fingerprints by
	// kind, and their algorithm, of the counterparty block a record seals
	// (sealedCounterparty), as the engine's Action.counterparty_ids and
	// counterparty_fp_alg (report/replay.py _sealed_counterparty: every
	// member that is a non-empty string; none when no member is).
	// counterpartyMalformed marks a sealed block that is not a check's,
	// whole (wellFormedCounterparty; Action.counterparty_malformed,
	// capsule-engine 9066490): release_on_acceptance 1.2.0 reads such a
	// block as unusable. None of these is ever sealed in a decision.
	counterpartyIDs       map[string]string
	counterpartyFPAlg     string
	counterpartyMalformed bool
}

// DecodeAction reads one Action as strict JSON. action_type defaults to
// "decide" (action.py:85); the timestamp is required here, where Python
// falls back to the wall clock, so a verdict never depends on when it ran.
func DecodeAction(r io.Reader) (Action, error) {
	body, err := io.ReadAll(r)
	if err != nil {
		return Action{}, fmt.Errorf("action: %w", err)
	}
	return decodeAction(body, true)
}

// dropForeignAttribution is body without any statement_attributed_to but
// the exact member holding the JSON string user: action.py
// Action.__post_init__ reads any other value, of any type, as the agent's
// own statement and keeps none, so it never refuses the action. A member
// whose name differs only in case is dropped too, since the struct decode
// would read it as this field.
func dropForeignAttribution(body []byte) ([]byte, error) {
	const name = "statement_attributed_to"
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		return nil, err
	}
	dropped := false
	for k, raw := range fields {
		if who, _ := stringOf(raw); strings.EqualFold(k, name) && (k != name || who != attributedToUser) {
			delete(fields, k)
			dropped = true
		}
	}
	if !dropped {
		return body, nil
	}
	return json.Marshal(fields)
}

// decodeAction decodes a normalized action. With checkTimestamp false the
// timestamp is not parsed (DecodeRecord's placeholder for a record that
// will not be evaluated).
func decodeAction(given []byte, checkTimestamp bool) (Action, error) {
	body, err := dropForeignAttribution(given)
	if err != nil {
		return Action{}, fmt.Errorf("action: %w", err)
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	var a Action
	if err := dec.Decode(&a); err != nil {
		return Action{}, fmt.Errorf("action: %w", err)
	}
	// What the action carried, as given: an attribution it drops is still
	// listed, as a value out of its closed set is (handover).
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(given, &fields); err != nil {
		return Action{}, fmt.Errorf("action: %w", err)
	}
	for k, v := range fields {
		if string(v) != "null" {
			a.present = append(a.present, k)
		}
	}
	slices.Sort(a.present)
	if a.Verb == "" || a.Operator == "" || a.Developer == "" {
		return Action{}, fmt.Errorf("action: verb, operator and developer are required")
	}
	if a.ActionType == "" {
		a.ActionType = "decide"
	}
	if _, err := parseInstant(a.Timestamp); checkTimestamp && err != nil {
		return Action{}, fmt.Errorf("action: timestamp: %w", err)
	}
	if a.DisclosedClasses != nil && !isDisclosedClasses(a.DisclosedClasses) {
		// action.py Action.__post_init__ raises on any other value.
		return Action{}, fmt.Errorf("action: disclosed_classes must be a non-empty list of %s names, sorted and each named once",
			strings.Join(DisclosedClassNames, ", "))
	}
	if a.ListingClasses != nil && !isListingClasses(a.ListingClasses) {
		// action.py Action.__post_init__ raises on any other value.
		return Action{}, errors.New("action: listing_classes must be class names, sorted and each named once")
	}
	if a.RepeatDisclosures != nil && len(a.RepeatDisclosures) > 0 && !isDisclosedClasses(a.RepeatDisclosures) {
		// action.py Action.__post_init__ raises on any other value.
		return Action{}, fmt.Errorf("action: repeat_disclosures must be %s names, sorted and each named once",
			strings.Join(DisclosedClassNames, ", "))
	}
	if a.StatementAttributedTo != nil && *a.StatementAttributedTo != attributedToUser {
		// Never reached through dropForeignAttribution; kept so the closed
		// token holds however the field is set.
		a.StatementAttributedTo = nil
	}
	if a.Handover != nil && !slices.Contains(Handovers, *a.Handover) {
		// action.py handover_of: a non-member is no handover.
		a.Handover = nil
	}
	if a.PartyRole != nil {
		if why := unknownPartyRole(*a.PartyRole); why != "" {
			a.unevaluable = append(a.unevaluable, why)
		}
	}
	return a, nil
}

// Handovers are how a sold item reaches the buyer, as the producer seals it
// (x-deal-v0 terms.handover; action.py HANDOVERS, capsule-engine #201):
// closed, so a value read as one is a token and never a place.
var Handovers = []string{"in_person", "shipped"}

// DisclosedClassNames are the classes of personal data a disclosure may name,
// as the producer names them (capsule-engine guards/action.py
// DISCLOSED_CLASSES at 2a873e6, #195, which adds pickup_area, a listing's
// public area, never the exact spot; from capsule-cli deal_rules.go
// disclosureClasses). Closed, so a value read as one is a class name and
// never the data.
var DisclosedClassNames = []string{"name", "phone", "email", "home_address", "address", "pickup_location", "pickup_area",
	"other_contact", "credential", "verification_code", "payment_card", "id_document"}

// disclosedClassesOf is action.py disclosed_classes_of: raw as
// Action.DisclosedClasses, its classes sorted and each named once, or nil
// when it is not a non-empty list of DisclosedClassNames members. One
// unknown member drops the whole value, never kept: it may be the data.
func disclosedClassesOf(raw json.RawMessage) []string {
	var items []json.RawMessage
	if json.Unmarshal(raw, &items) != nil || len(items) == 0 {
		return nil
	}
	var out []string
	for _, it := range items {
		var c string
		if json.Unmarshal(it, &c) != nil || !slices.Contains(DisclosedClassNames, c) {
			return nil
		}
		if !slices.Contains(out, c) {
			out = append(out, c)
		}
	}
	slices.Sort(out)
	return out
}

// isDisclosedClasses is whether cs is a valid Action.disclosed_classes:
// non-empty, every member a DisclosedClassNames name, sorted, none twice.
func isDisclosedClasses(cs []string) bool {
	if len(cs) == 0 {
		return false
	}
	for i, c := range cs {
		if !slices.Contains(DisclosedClassNames, c) || (i > 0 && cs[i-1] >= c) {
			return false
		}
	}
	return true
}

func (a Action) class() string {
	if a.ActionClass == nil {
		return ""
	}
	return *a.ActionClass
}

// Record is one prior ledger record, as the checks read it: the fields of an
// Agent Action Capsule they consult.
type Record struct {
	CapsuleID              string
	ActionID               string
	ActionType             string
	Operator               string
	Developer              string
	Timestamp              string
	Decision               *string
	Target                 *string
	AmountMinor            *int64
	CounterpartyAccountRef *string
	// ActionClass is asg_payload.action_class; DryRun is
	// asg_payload.checkpoint.dry_run (GuardEngine marks a dry-run decision
	// so); ChainParent and ChainRelation are the record's chain block.
	ActionClass   *string
	DryRun        *bool
	ChainParent   *string
	ChainRelation *string
	// SpendMinor is asg_payload.spend_minor, which the fold does not read:
	// caps uses it only to tell whether the window total can be trusted.
	SpendMinor *int64
	// TaxonomyVersion is a history act's deal body taxonomy_version
	// (DecodeHistoryAct), or a ledger capsule's asg_payload.taxonomy_version.
	TaxonomyVersion *string
	// ReturnedMinor, ReversesRef and DealID are what dedupe reads beside the
	// key fields (Action's fields of the same names): a ledger capsule's
	// asg_payload members, sealed only when set, or what DecodeHistoryAct
	// reads from a history act. Currency is asg_payload.currency (a history
	// act's body currency), reported with a match in another deal.
	ReturnedMinor *int64
	ReversesRef   *string
	DealID        *string
	Currency      *string
	// TaskAuthorityRef and ItemRef are asg_payload.task_authority_ref and
	// asg_payload.item_ref (the action's, when a decision is recorded), read
	// by single_commitment to find the sale's acceptance.
	TaskAuthorityRef *string
	ItemRef          *string
	// RepresentationClass is asg_payload.representation_class, the class of
	// statement the recorded action made, sealed only when set: what
	// disclosure.class_seen counts.
	RepresentationClass *string
	// EquivalenceKeyDigest is asg_payload.equivalence_key_digest, the
	// digest of the caller's equivalence key a decision seals (local-only;
	// capsule-engine #134), or nil: dedupe keys the record on it before the
	// default formula (equivalenceKeyForRecord).
	EquivalenceKeyDigest *string
	// Unchecked marks a history act capsulectl sealed as taken without
	// authority (DecodeHistoryAct: an x-deal-v0 body.unchecked, or a typed
	// action-outcome/v0 body.attempted; capsule-cli rule no_sealed_approval
	// and the like). It moved money, so the caps window counts it as spend
	// (uncheckedInWindow); it is never accepted, never seen, and never an
	// earlier act for dedupe (ruling (a), 2026-10-09).
	Unchecked bool
	// Stated is asg_payload.stated, set only on a statement record: a claim
	// the user's agent made to the counterparty in a deal (StatementOf). It
	// is never an act: dedupe and the spend window leave it out, and it
	// carries no decision, target or class for any other fold to read.
	Stated *Statement
	// LiveHistory is asg_payload.live_history and SpendUnreadable
	// asg_payload.spend_unreadable, as a ledger capsule seals them: the marks
	// capsule-engine writes on its own copy of a history act and on its
	// record of an executed act whose spend it could not read (#138,
	// report/live_history.py and report/replay.py). spend.weekly/3.1.0 reads
	// the first; caps/5.1.0 the second (unreadableExecuted).
	LiveHistory     *string
	SpendUnreadable bool
	// live is a history act's state as the engine's history ledger writes it
	// (history_state.py; DecodeHistoryAct), "" on a record that is not from
	// a check input's history; sealed is the disposition.decision its capsule
	// seals, which single_commitment reads as the act's decision.
	live   liveState
	sealed *string
	// ignoredInputs names each input passed beside a history act's capsule
	// that its target was read without because it was not in its agreed
	// shape (DecodeHistoryAct): counterparty_profile, then counterparty, as
	// the engine's Action.ignored_inputs names them for an act. The values
	// are never kept.
	ignoredInputs []string
	// threadCounterparty is the counterparty block a history act's check
	// sealed, when it is whole (DecodeHistoryAct; history_state.py
	// thread_counterparty, capsule-engine 9066490): release_on_acceptance
	// 1.2.0 reads it to tell the buyer who accepted. It is this ledger's own
	// state and never sealed in a decision.
	threadCounterparty *threadCounterparty
	// Direction is a history act's deal body direction (DecodeHistoryAct):
	// "in" when the money moved in (a refund, a partial cancel), which the
	// engine's deal bridge never reads as spend (report/replay.py MONEY_IN).
	// The caps window reads it only to tell such an act carrying no
	// recorded decision from one that could be spend (windowCoverage).
	Direction *string
}

// liveState is history_state.py's live_history: what a history act's
// capsule says of the decision on it.
type liveState string

const (
	// liveDisposition: the act's capsule seals a disposition.
	liveDisposition liveState = "disposition"
	// liveNoDisposition: it seals none, so whether it was accepted is not
	// known (not the same as "not accepted").
	liveNoDisposition liveState = "no_disposition"
	// liveUnread: the act cannot be read as the engine's history ledger reads
	// one; here, an act taken without authority (Unchecked), which is never a
	// typed act record.
	liveUnread liveState = "unread"
)

type capsuleJSON struct {
	CapsuleID   string `json:"capsule_id"`
	ActionID    string `json:"action_id"`
	ActionType  string `json:"action_type"`
	Operator    string `json:"operator"`
	Developer   string `json:"developer"`
	Timestamp   string `json:"timestamp"`
	Disposition *struct {
		Decision *string `json:"decision"`
	} `json:"disposition"`
	Payload *struct {
		Target                 *string `json:"target"`
		AmountMinor            *int64  `json:"amount_minor"`
		CounterpartyAccountRef *string `json:"counterparty_account_ref"`
		ActionClass            *string `json:"action_class"`
		SpendMinor             *int64  `json:"spend_minor"`
		TaxonomyVersion        *string `json:"taxonomy_version"`
		ReturnedMinor          *int64  `json:"returned_minor"`
		ReversesRef            *string `json:"reverses_ref"`
		DealID                 *string `json:"deal_id"`
		Currency               *string `json:"currency"`
		TaskAuthorityRef       *string `json:"task_authority_ref"`
		ItemRef                *string `json:"item_ref"`
		RepresentationClass    *string `json:"representation_class"`
		EquivalenceKeyDigest   *string `json:"equivalence_key_digest"`
		Stated                 *struct {
			Class      string `json:"class"`
			SourceKind string `json:"source_kind"`
			DealID     string `json:"deal_id"`
			Claim      string `json:"claim"`
		} `json:"stated"`
		LiveHistory     *string `json:"live_history"`
		SpendUnreadable *bool   `json:"spend_unreadable"`
		Checkpoint      *struct {
			DryRun *bool `json:"dry_run"`
		} `json:"checkpoint"`
	} `json:"asg_payload"`
	Chain *struct {
		ParentCapsuleID *string `json:"parent_capsule_id"`
		Relation        *string `json:"relation"`
	} `json:"chain"`
}

// ReadLedger reads Agent Action Capsules, one JSON object per line, in ledger
// order.
func ReadLedger(r io.Reader) ([]Record, error) {
	var out []Record
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 1<<16), 1<<24)
	for n := 1; sc.Scan(); n++ {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		var c capsuleJSON
		if err := json.Unmarshal(line, &c); err != nil {
			return nil, fmt.Errorf("ledger line %d: %w", n, err)
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
		out = append(out, rec)
	}
	return out, sc.Err()
}

func (r Record) accepted() bool { return r.Decision != nil && *r.Decision == "accept" }

// executedSpend is the spend of an executed act with no recorded decision
// (an act capsulectl seals fyi after its check: every act but pay through
// rc14), which the window counts (the G3 ruling): its sealed spend_minor,
// when it is one of 0 or more. false for an act with a decision, an
// unchecked act, an act that moved money in (never spend, #130), and an act
// whose spend cannot be read, which leaves the window unevaluated
// (windowCoverage).
func (r Record) executedSpend() (int64, bool) {
	if r.Decision != nil {
		return 0, false
	}
	return r.sealedSpend()
}

// sealedSpend is the spend an act's record seals (report/replay.py
// _executed_spend for an act neither accepted nor taken without a check):
// its spend_minor when that is one of 0 or more. false for an unchecked act
// and an act that moved money in, which the window reads otherwise, and
// for one whose spend cannot be read.
func (r Record) sealedSpend() (int64, bool) {
	if r.Unchecked || (r.Direction != nil && *r.Direction == moneyIn) || r.SpendMinor == nil || *r.SpendMinor < 0 {
		return 0, false
	}
	return *r.SpendMinor, true
}

// verb is the action_id prefix before the first "/" (dedupe.py:27-29).
func verbOf(actionID string) string {
	v, _, _ := strings.Cut(actionID, "/")
	return v
}

// parseInstant parses an RFC 3339 timestamp; a trailing Z is UTC.
func parseInstant(ts string) (time.Time, error) {
	return time.Parse(time.RFC3339Nano, ts)
}

// engineAmount is the amount the engine's action carries, and the fields it
// is read from. On a deal record it is what the engine's deal bridge takes
// (report/replay.py _bridge_deal_check at a5e9e30): spend_minor, 0 when the
// money moved in (direction "in"), and none when spend_minor is absent;
// never amount_minor, which on a cancel is a refund. On a normalized
// --action it is amount_minor.
func (a Action) engineAmount() (*int64, []string) {
	if !a.fromRecord {
		return a.AmountMinor, []string{"amount_minor"}
	}
	if a.Direction != nil && *a.Direction == "in" {
		zero := int64(0)
		return &zero, []string{"direction"}
	}
	return a.SpendMinor, []string{"spend_minor"}
}

// classToken is action.py _CLASS_TOKEN: a representation class name.
var classToken = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)

// isListingClasses is whether cs is a valid Action.listing_classes: class
// tokens, sorted and each named once (empty allowed).
func isListingClasses(cs []string) bool {
	for i, c := range cs {
		if !classToken.MatchString(c) || (i > 0 && cs[i-1] >= c) {
			return false
		}
	}
	return true
}

// listingClassesOf is action.py listing_classes_of: raw as
// Action.ListingClasses, its class tokens sorted and each named once (empty
// when the list is empty), or nil when it is not a list of class tokens. A
// value out of shape is dropped whole.
func listingClassesOf(raw json.RawMessage) []string {
	var items []json.RawMessage
	if json.Unmarshal(raw, &items) != nil || items == nil {
		return nil
	}
	out := []string{}
	for _, it := range items {
		c, ok := stringOf(it)
		if !ok || !classToken.MatchString(c) {
			return nil
		}
		if !slices.Contains(out, c) {
			out = append(out, c)
		}
	}
	slices.Sort(out)
	return out
}

// repeatDisclosuresOf is action.py repeat_disclosures_of: raw as
// Action.RepeatDisclosures, its classes sorted and each named once (empty
// when the list is), or nil when it is not a list of DisclosedClassNames
// members. One unknown member drops the whole value.
func repeatDisclosuresOf(raw json.RawMessage) []string {
	var items []json.RawMessage
	if json.Unmarshal(raw, &items) != nil || items == nil {
		return nil
	}
	out := []string{}
	for _, it := range items {
		c, ok := stringOf(it)
		if !ok || !slices.Contains(DisclosedClassNames, c) {
			return nil
		}
		if !slices.Contains(out, c) {
			out = append(out, c)
		}
	}
	slices.Sort(out)
	return out
}
