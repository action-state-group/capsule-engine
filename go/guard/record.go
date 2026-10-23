package guard

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// recordHeaderMembers are the capsule header members a deal record supplies
// to the check, never re-derived and never defaulted (docs/check-output.md).
var recordHeaderMembers = []string{"operator", "developer", "timestamp"}

// DecodeRecord reads one capsule-cli deal record and normalizes it into the
// check's action. raw is either one capsule (its header members with the
// disclosed agent_input beside them) or an Evidence Bundle (records and
// disclosures, keyed by capsule id), from which capsuleID picks the record;
// with no capsuleID a bundle must hold exactly one deal record.
//
// operator, developer and timestamp come from the capsule header; verb from
// the deal body's action; the body's other fields as --action reads them.
// The target is the payee's sealed fingerprint (payeeTarget); the body's own
// counterparty member is not the target. rail and refundable are read from the
// body's recourse block as the engine's deal bridge reads them
// (report/replay.py _bridge_deal_check at 365eb92): the rail from recourse
// when it is a non-empty string, else the top-level rail (also only when
// non-empty); refundable only when it is a boolean; upfront_amount_minor
// only when it is an integer; recipient_role only when a non-empty string;
// disclosing, as disclosed_classes, only as a non-empty list of known class
// names (disclosedClassesOf); listing_classes only as a list of class
// tokens, sorted and each named once, empty kept, and dropped whole
// otherwise (listingClassesOf, capsule-engine #204); terms.handover, as handover, only as a
// Handovers member (capsule-engine #201), so terms is then listed as read;
// task_authority_ref as the digest of a typed reference {type, digest_alg,
// digest} with a non-empty type, digest_alg SHA-256 and a lowercase hex64
// digest, and not read otherwise. A body field --action
// does not know (item_ref_commitment, ...) is accepted and listed as ignored. A missing header
// member or body action leaves the action unevaluable, naming it (see
// Unevaluable), rather than refusing the record. The disclosed agent_input
// must be the one the header's agent_input_digest seals (sha256 over its
// JCS form), or the record is refused: the check never reads a body the
// header does not bind. The capsule's signature is not verified here; run
// capsulectl verify on the record for that.
func DecodeRecord(raw []byte, capsuleID string) (Action, error) {
	return decodeRecord(raw, capsuleID, nil)
}

// DecodeCheckInputRecord reads the record entry of an external-check-input/v0
// envelope as DecodeRecord reads one capsule, and beside it the envelope's
// optional counterparty_profile, the payee keyed per profile, which capsulectl
// computes and passes and the capsule does not seal (the engine's
// action_for_check_input, report/replay.py at capsule-engine 4ac9fb0). It
// never enters an integrity computation: the agent_input digest is over
// agent_input alone. In its agreed shape (profileTarget) it is the target, so
// one merchant is the same target across the profile's deals; in any other it
// is ignored, named in the counterparty_seen_before evidence, and the
// per-deal target stands. Absent or null, nothing changes.
func DecodeCheckInputRecord(raw []byte) (Action, error) {
	var entry map[string]json.RawMessage
	if err := json.Unmarshal(raw, &entry); err != nil {
		return Action{}, fmt.Errorf("record: %w", err)
	}
	return decodeRecord(raw, "", entry[profileInput])
}

func decodeRecord(raw []byte, capsuleID string, profile json.RawMessage) (Action, error) {
	var top map[string]json.RawMessage
	if err := json.Unmarshal(raw, &top); err != nil {
		return Action{}, fmt.Errorf("record: %w", err)
	}
	header, agentInput, err := selectRecord(raw, top, capsuleID)
	if err != nil {
		return Action{}, err
	}
	if err := checkAgentInputDigest(header, agentInput); err != nil {
		return Action{}, err
	}
	var input struct {
		Body  map[string]json.RawMessage `json:"body"`
		XDeal map[string]json.RawMessage `json:"x-deal-v0"`
	}
	if err := json.Unmarshal(agentInput, &input); err != nil {
		return Action{}, fmt.Errorf("record: agent_input: %w", err)
	}
	if input.Body == nil {
		return Action{}, errors.New("record: the disclosed agent_input has no body: not a deal record")
	}
	fields := map[string]json.RawMessage{}
	var extra, unevaluable []string
	for _, m := range recordHeaderMembers {
		v, ok := header[m]
		if !ok || string(v) == "null" {
			unevaluable = append(unevaluable, "capsule header has no "+m)
			continue
		}
		fields[m] = v
	}
	// The record's own action_type and action_id, as the engine's deal
	// bridge takes them (report/replay.py _bridge_deal_check:
	// record.get("action_type", "decide"), record.get("action_id") or
	// None): a deal check is fyi, deal-<id>/<seq>. Absent, action_type is
	// decide (decodeAction's default) and there is no action_id.
	for _, m := range []string{"action_type", "action_id"} {
		if v, ok := nonEmptyString(header[m]); ok {
			fields[m] = v
		}
	}
	if v, ok := input.Body["action"]; ok && string(v) != "null" {
		fields["verb"] = v
	} else {
		unevaluable = append(unevaluable, "deal body has no action")
	}
	sourceName := map[string]string{"verb": "action"}
	if rail, ok := nonEmptyString(input.Body["rail"]); ok {
		fields["rail"] = rail
	}
	var recourse map[string]json.RawMessage
	if json.Unmarshal(input.Body["recourse"], &recourse) == nil {
		if rail, ok := nonEmptyString(recourse["rail"]); ok {
			fields["rail"], sourceName["rail"] = rail, "recourse"
		}
		var refundable *bool
		if json.Unmarshal(recourse["refundable"], &refundable) == nil && refundable != nil {
			fields["refundable"], sourceName["refundable"] = recourse["refundable"], "recourse"
		}
	}
	// How the item reaches the buyer, as the check's terms.handover states it
	// (replay.py _handover, capsule-engine #201): kept only as a Handovers
	// member.
	if h, ok := stringOf(objectOf(input.Body["terms"])["handover"]); ok && slices.Contains(Handovers, h) {
		fields["handover"], sourceName["handover"] = encodeString(h), "terms"
	}
	if raw, ok := input.Body["upfront_amount_minor"]; ok && isInteger(raw) {
		fields["upfront_amount_minor"] = raw
	}
	// The amount the body states, carried apart from the spend for
	// price_floor (capsule-engine #129, report/replay.py _bridge_deal_check
	// stated_amount_minor): on a sale it is the price, while the spend is 0.
	// Taken only as an integer; never summed.
	if raw, ok := input.Body["amount_minor"]; ok && isInteger(raw) {
		fields["stated_amount_minor"], sourceName["stated_amount_minor"] = raw, "amount_minor"
	}
	if role, ok := nonEmptyString(input.Body["recipient_role"]); ok {
		fields["recipient_role"] = role
	}
	// A share's classes of data, carried only as a non-empty list of known
	// class names and dropped whole otherwise (report/replay.py
	// _bridge_deal_check disclosed_classes, capsule-engine #173).
	if classes := disclosedClassesOf(input.Body["disclosing"]); classes != nil {
		raw, err := json.Marshal(classes)
		if err != nil {
			return Action{}, err
		}
		fields["disclosed_classes"], sourceName["disclosed_classes"] = raw, "disclosing"
	}
	// What the producer's telling memory says of the recipient
	// (replay.py repeat_disclosures_of and _flag, capsule-engine #203): the
	// classes already told, only as a list of known disclosure classes
	// (empty kept, dropped whole otherwise), and whether it was told
	// anything before, only as a boolean.
	if classes := repeatDisclosuresOf(input.Body["repeat_disclosures"]); classes != nil {
		fields["repeat_disclosures"] = encodeJSONStrings(classes)
	}
	if told := input.Body["recipient_told_before"]; string(told) == "true" || string(told) == "false" {
		fields["recipient_told_before"] = told
	}
	// The classes the sale's listings in force state, as the check sealed
	// them (replay.py listing_classes_of, capsule-engine #204): kept only as
	// a list of class tokens, empty included; dropped whole otherwise.
	if classes := listingClassesOf(input.Body["listing_classes"]); classes != nil {
		fields["listing_classes"] = encodeJSONStrings(classes)
	}
	// The class of statement a represent check makes, as its
	// representing.class seals it (replay.py _bridge_deal_check,
	// capsule-engine #205): a non-empty string, or, on a represent check,
	// malformedRepresentationClass.
	if cls, ok := representationClassOf(input.Body); ok {
		fields["representation_class"], sourceName["representation_class"] = cls, representingMember
	}
	// Whose claim the statement is, only as the closed token user
	// (replay.py _attributed_to).
	representing := objectOf(input.Body[representingMember])
	if who, _ := stringOf(representing["attributed_to"]); who == attributedToUser {
		fields["statement_attributed_to"], sourceName["statement_attributed_to"] = representing["attributed_to"], representingMember
	}
	if digest, ok := typedRefDigest(input.Body["task_authority_ref"]); ok {
		fields["task_authority_ref"] = digest
	}
	// When the money moved in, the amount returned and the act reversed,
	// as the bridge carries them for dedupe (#108): returnedMinor and the
	// digest of the body's typed reverses_ref. A bare-string reverses_ref
	// is not that shape and is absent.
	var direction string
	_ = json.Unmarshal(input.Body["direction"], &direction)
	if direction == "in" {
		if field, raw, ok := returnedMinor(input.Body); ok {
			fields["returned_minor"], sourceName["returned_minor"] = raw, field
		}
		if digest, ok := typedRefDigest(input.Body["reverses_ref"]); ok {
			fields["reverses_ref"] = digest
		}
	}
	deal, dealSource, conflict := dealOf(agentInput)
	if deal != "" {
		raw, err := json.Marshal(deal)
		if err != nil {
			return Action{}, err
		}
		fields["deal_id"], sourceName["deal_id"] = raw, dealSource
	}
	// An offer that answers no proposal (replay.py _no_proposal,
	// capsule-engine #202): a check whose body seals no_proposal true and
	// which names no source ref. A check naming a source answers a message,
	// so it is decided on that message's age, never as answering none.
	var noProposal bool
	if json.Unmarshal(input.Body["no_proposal"], &noProposal) == nil && noProposal && isDealCheck(agentInput, input.XDeal) &&
		!namesSource(agentInput, input.XDeal) {
		fields["no_proposal"] = json.RawMessage("true")
	}

	// The side of the deal the user is on (intent.party_role): from this
	// record's intent and, in a bundle, from every record of the same deal,
	// whose opening is where capsule-cli seals it. A single capsule that is
	// not the opening states none, and is read as a buyer's.
	var roles []string
	if role, ok := intentPartyRole(input.Body); ok {
		roles = append(roles, role)
	}
	if _, isBundle := top["records"]; isBundle && recordDeal(agentInput) != "" {
		more, err := bundleDealRoles(raw, recordDeal(agentInput))
		if err != nil {
			return Action{}, err
		}
		roles = append(roles, more...)
	}
	// A seller's typed commit checked from its bundle is dated by the offer
	// its buyer accepted, and a typed offer by the counterparty's message it
	// answers, read from the bundle's records as capsule-engine #131 and
	// #202 read a deal's (acceptedOfferAt). No deal record seals a
	// proposal_at, so a record read alone, or with no such offer or message,
	// has none.
	var proposalAt string
	proposalSource := acceptedOfferMember
	if verb, _ := stringOf(input.Body["action"]); verb == offerVerb {
		proposalSource = answeredMessageMember
	}
	if _, isBundle := top["records"]; isBundle {
		proposalAt = acceptedOfferAt(raw)[agentInputDigest(agentInput)]
	}
	if proposalAt != "" {
		v, err := json.Marshal(proposalAt)
		if err != nil {
			return Action{}, err
		}
		fields["proposal_at"], sourceName["proposal_at"] = v, proposalSource
	}
	role, hasRole, why := partyRoleFrom(roles)
	if hasRole {
		v, err := json.Marshal(role)
		if err != nil {
			return Action{}, err
		}
		fields["party_role"], sourceName["party_role"] = v, partyRoleMember
	}
	if why != "" {
		unevaluable = append(unevaluable, why)
	}
	sealed, targetSource := sealedCounterparty(agentInput, input.Body, input.XDeal)
	target, hasTarget := payeeTarget(sealed)
	hasPerDeal := hasTarget && targetSource == counterpartyBlock
	// The payee keyed per profile, read only where the engine's deal bridge
	// reads it: on a deal check it bridges (bridgesAsCheck).
	var ignoredInputs []string
	hasProfile := len(profile) > 0 && string(profile) != "null"
	if hasProfile && bridgesAsCheck(agentInput, input.Body, input.XDeal) {
		if t, ok := profileTarget(profile); ok {
			target, hasTarget, targetSource = t, true, profileInput
		} else {
			ignoredInputs = []string{profileInput}
		}
	}
	if hasTarget {
		raw, err := json.Marshal(target)
		if err != nil {
			return Action{}, err
		}
		fields["target"], sourceName["target"] = raw, targetSource
	}
	for k, v := range input.Body {
		switch {
		case k == "action", k == "rail", k == "recourse", k == "upfront_amount_minor", k == "recipient_role", k == "task_authority_ref",
			k == "reverses_ref", k == "returned_minor", k == "disclosing", k == "listing_classes", k == "repeat_disclosures",
			k == "recipient_told_before", k == "no_proposal", k == representingMember:
		case slices.Contains(bodyFields, k):
			if bridged(k, v) {
				fields[k] = v
			}
		default:
			if string(v) != "null" {
				extra = append(extra, k)
			}
		}
	}
	// Placeholders let the decoder run on an unevaluable record; the
	// engine never sees them (Unevaluable is checked first).
	for _, f := range []string{"verb", "operator", "developer"} {
		if _, ok := fields[f]; !ok {
			fields[f] = json.RawMessage(`"-"`)
		}
	}
	if _, ok := fields["timestamp"]; !ok {
		fields["timestamp"] = json.RawMessage(`"1970-01-01T00:00:00Z"`)
	}
	body, err := json.Marshal(fields)
	if err != nil {
		return Action{}, err
	}
	a, err := decodeAction(body, len(unevaluable) == 0)
	if err != nil {
		return Action{}, fmt.Errorf("record: %w", err)
	}
	// What the record carried, under its own names: the header members
	// present, the body's action (the verb) and the body's fields.
	a.present = nil
	for _, m := range recordHeaderMembers {
		if v, ok := header[m]; ok && string(v) != "null" {
			a.present = append(a.present, m)
		}
	}
	for k, v := range input.Body {
		if string(v) != "null" {
			a.present = append(a.present, k)
		}
	}
	if hasPerDeal {
		a.present = append(a.present, counterpartyBlock)
	}
	a.counterpartyIDs, a.counterpartyFPAlg = sealedFingerprints(sealed)
	_, whole := wellFormedCounterparty(sealed)
	a.counterpartyMalformed = !isJSONNull(sealed) && !whole
	if hasProfile {
		a.present = append(a.present, profileInput)
	}
	a.ignoredInputs = ignoredInputs
	if _, ok := fields["deal_id"]; ok {
		a.present = append(a.present, dealSource)
	}
	if hasRole {
		a.present = append(a.present, partyRoleMember)
	}
	if proposalAt != "" {
		a.present = append(a.present, proposalSource)
	}
	slices.Sort(a.present)
	a.sourceName = sourceName
	a.representationTextCommitment = textCommitmentOf(representing)
	a.extra = extra
	a.unevaluable = unevaluable
	// A record naming two deals is in no deal; every check still runs, and
	// the report marks it (dealIDConflictRow).
	a.dealIDConflict = conflict
	a.fromRecord = true
	var recordType string
	if json.Unmarshal(input.XDeal["record_type"], &recordType) == nil && recordType != "" && recordType != "check" {
		a.statesNoAct = true
	}
	// A typed record, which has no x-deal-v0 block, states no act when its
	// sealed type is one that does not (capsule-engine #131, typedNoAct).
	if input.XDeal == nil && typedStatesNoAct(agentInput) {
		a.statesNoAct = true
	}
	return a, nil
}

// bodyFields are the deal body fields DecodeRecord passes to the action
// under their own names.
var bodyFields = []string{"action_class", "taxonomy_version", "amount_minor", "spend_minor", "spend_authorized_minor", "fee_minor", "cancelled_amount_minor",
	"currency", "direction", "reference_commitment", "description_commitment", "disclosed", "channel", "counterparty",
	"first_contact_channel", "material_fields_changed", "material_fields_basis",
	"offer_fields_changed", "offer_fields_basis"}

// bridgedText are the body fields the engine's deal reader takes only as a
// non-empty string (report/replay.py _bridge_deal_check _text at 365eb92):
// any other value is dropped, as there, never refused.
var bridgedText = []string{"channel", "first_contact_channel", "material_fields_basis", "offer_fields_basis"}

// bridgedInt are the body fields it takes only as an integer (_minor): a
// JSON number with no fraction or exponent, which Python reads as an int.
var bridgedInt = []string{"material_fields_changed", "offer_fields_changed", "amount_minor"}

var jsonInteger = regexp.MustCompile(`^-?(0|[1-9][0-9]*)$`)

// bridged is whether DecodeRecord passes body field k's value v to the
// action: a field the engine's deal reader takes in one shape only is
// dropped in any other.
func bridged(k string, v json.RawMessage) bool {
	if slices.Contains(bridgedText, k) {
		var s string
		return json.Unmarshal(v, &s) == nil && s != ""
	}
	if slices.Contains(bridgedInt, k) {
		var n int64
		return jsonInteger.Match(v) && json.Unmarshal(v, &n) == nil
	}
	return true
}

// representingMember is the deal body member a represent check seals its
// statement under (capsule-cli deal_representations.go).
const representingMember = "representing"

// malformedRepresentationClass is the class the bridge gives a represent
// check whose representing.class is not a non-empty string (replay.py
// MALFORMED_REPRESENTATION_CLASS): a closed token no rule recognises, so
// promise_requires_approval and promise_never fail it rather than read a
// missing class as not applicable. It is never a stated value.
const malformedRepresentationClass = "malformed_representing"

// representationClassOf is the class a deal body's representing.class
// states, as JSON: the string when it is non-empty, malformedRepresentationClass
// when it is not and the body's action is represent, and nothing otherwise.
func representationClassOf(body map[string]json.RawMessage) (json.RawMessage, bool) {
	if cls, ok := nonEmptyString(objectOf(body[representingMember])["class"]); ok {
		return cls, true
	}
	if action, _ := stringOf(body["action"]); action == "represent" {
		return encodeString(malformedRepresentationClass), true
	}
	return nil, false
}

// attributedToUser is the one value statement_attributed_to takes: the
// statement is the user's own claim (action.py ATTRIBUTED_TO_USER).
const attributedToUser = "user"

// malformedTextCommitment is the commitment the bridge gives a represent
// check whose sealed text_commitment is present but not 64 lowercase hex
// (replay.py MALFORMED_TEXT_COMMITMENT), so promise_never/1.1.0 fails it,
// never reading it as absent.
const malformedTextCommitment = "malformed_text_commitment"

// textCommitmentOf is a represent check's sealed text_commitment
// (replay.py _text_commitment): the digest when it is 64 lowercase hex,
// nil when absent, and malformedTextCommitment otherwise, JSON null
// included.
func textCommitmentOf(representing jsonObject) *string {
	raw, ok := representing["text_commitment"]
	if !ok {
		return nil
	}
	if s, ok := stringOf(raw); ok && hex64.MatchString(s) {
		return &s
	}
	malformed := malformedTextCommitment
	return &malformed
}

// nonEmptyString is raw when it is a non-empty JSON string (the bridge's
// _text).
func nonEmptyString(raw json.RawMessage) (json.RawMessage, bool) {
	var s string
	if json.Unmarshal(raw, &s) != nil || s == "" {
		return nil, false
	}
	return raw, true
}

// isInteger is whether raw is a JSON integer (the bridge's _minor: not a
// boolean, a string or a fraction).
func isInteger(raw json.RawMessage) bool {
	_, err := strconv.ParseInt(string(raw), 10, 64)
	return err == nil
}

// counterpartyBlock names where a record's target comes from, in inputs.
const counterpartyBlock = "x-deal-v0.counterparty"

// typedCounterparty names where a typed check's target comes from: its
// body's counterparty field, which inputs already lists by that name.
const typedCounterparty = "counterparty"

// sealedCounterparty is the counterparty block a record seals and where it
// was read, as the engine's deal bridge takes it (report/replay.py
// _checked_body, capsule-engine #133 at b1c7fb4f): an x-deal-v0 check's
// block, else a typed proposed-action/v0's body.counterparty, where
// capsulectl moves it (fp_alg hmac-sha256-chain-key). An x-deal-v0 check
// never reads a body.counterparty. Any other record keeps its x-deal-v0
// block, which the engine never bridges.
func sealedCounterparty(agentInput json.RawMessage, body, xdeal map[string]json.RawMessage) (json.RawMessage, string) {
	var recordType, typed string
	_ = json.Unmarshal(xdeal["record_type"], &recordType)
	var top map[string]json.RawMessage
	if json.Unmarshal(agentInput, &top) == nil {
		_ = json.Unmarshal(top["type"], &typed)
	}
	if recordType != "check" && typed == proposedActionType {
		return body["counterparty"], typedCounterparty
	}
	return xdeal["counterparty"], counterpartyBlock
}

// dealIDMember names where a record's deal comes from, in inputs: the
// sealed x-deal-v0.deal_id (the bridge's _text), never the action_id prefix.
const dealIDMember = "x-deal-v0.deal_id"

// returnedAmountFields are the body fields that state the amount a money-in
// record returns, first integer wins (report/replay.py
// _RETURNED_AMOUNT_FIELDS at 35874b8): the producer's explicit
// returned_minor, then a refund's amount_minor, then a partial cancel's
// cancelled_amount_minor (the producer's agreed field names).
var returnedAmountFields = []string{"returned_minor", "amount_minor", "cancelled_amount_minor"}

// returnedMinor is the amount a money-in body returns (the bridge's
// _returned_minor), and the field it was read from.
func returnedMinor(body map[string]json.RawMessage) (string, json.RawMessage, bool) {
	for _, f := range returnedAmountFields {
		if raw, ok := body[f]; ok && isInteger(raw) {
			return f, raw, true
		}
	}
	return "", nil, false
}

// payeeTarget is the payee's sealed fingerprint as an opaque target,
// payee-fp:<fp_alg>:<hex>, from the counterparty block a record seals
// (sealedCounterparty) {fp_alg, ids}: the engine's deal bridge (report/replay.py _payee_target and
// _sealed_counterparty at 365eb92), never recomputed. Prefixed so it never
// equals a clear reference or a fingerprint made by another algorithm. None
// when the block seals no usable payee fingerprint (a non-empty string
// under ids.payee, with a non-empty fp_alg). capsule-cli keys these
// fingerprints by deal, so one merchant's target differs from deal to deal.
// Members are read by their exact names (sealedFingerprints), as the engine
// reads a dict.
func payeeTarget(block json.RawMessage) (string, bool) {
	ids, alg := sealedFingerprints(block)
	payee, ok := ids["payee"]
	if !ok {
		return "", false
	}
	return "payee-fp:" + alg + ":" + payee, true
}

// profileInput names the payee keyed per profile, in the checker input
// (record.counterparty_profile, history[i].counterparty_profile) and in
// inputs; profileFPAlg is the fp_alg it must carry (the bridge's
// _PROFILE_INPUT and _PROFILE_FP_ALG at capsule-engine 4ac9fb0).
const (
	profileInput = "counterparty_profile"
	profileFPAlg = "hmac-sha256-profile-key"
)

// ProfileTargetPrefix starts every target keyed per profile. One merchant
// has one such target across all of a profile's deals, so it links them:
// it is never written into a finding or a reason (the engine's
// LOCAL_ONLY_TARGET_PREFIX).
const ProfileTargetPrefix = "payee-fp:" + profileFPAlg + ":"

// payeeHex is the profile payee's shape: exactly 64 lowercase hex, with no
// final newline (the bridge's _HEX64.fullmatch).
var payeeHex = regexp.MustCompile(`^[0-9a-f]{64}$`)

// profileTarget is the payee keyed per profile as a target,
// payee-fp:hmac-sha256-profile-key:<hex>, from a counterparty_profile block
// in its agreed shape: an object whose fp_alg is exactly
// hmac-sha256-profile-key and whose ids is an object with a payee of 64
// lowercase hex (the bridge's _profile_target). Members are matched by their
// exact names. Anything else is not a target.
func profileTarget(block json.RawMessage) (string, bool) {
	var cp map[string]json.RawMessage
	if json.Unmarshal(block, &cp) != nil || cp == nil {
		return "", false
	}
	var alg string
	if json.Unmarshal(cp["fp_alg"], &alg) != nil || alg != profileFPAlg {
		return "", false
	}
	var ids map[string]json.RawMessage
	if json.Unmarshal(cp["ids"], &ids) != nil || ids == nil {
		return "", false
	}
	var payee string
	if json.Unmarshal(ids["payee"], &payee) != nil || !payeeHex.MatchString(payee) {
		return "", false
	}
	return ProfileTargetPrefix + payee, true
}

// bridgesAsCheck is whether the engine's deal bridge reads this record as a
// check (report/replay.py _bridge_deal_check and _checked_body): an x-deal-v0
// record_type of "check", or a typed proposed-action/v0, whose body states a
// truthy action_class and taxonomy_version. Only there does it read
// counterparty_profile; on any other record the block changes nothing and is
// not marked. Truthy is a non-empty string here: decodeAction refuses any
// other non-null value of either field.
func bridgesAsCheck(agentInput json.RawMessage, body, xdeal map[string]json.RawMessage) bool {
	var recordType, typed string
	_ = json.Unmarshal(xdeal["record_type"], &recordType)
	var top map[string]json.RawMessage
	if json.Unmarshal(agentInput, &top) == nil {
		_ = json.Unmarshal(top["type"], &typed)
	}
	if recordType != "check" && typed != "proposed-action/v0" {
		return false
	}
	_, class := nonEmptyString(body["action_class"])
	_, version := nonEmptyString(body["taxonomy_version"])
	return class && version
}

// actionStep is an x-deal-v0 record_type of an act carried out (the
// bridge's _ACTION_STEP), which seals its counterparty in its block as a
// check does.
const actionStep = "action"

// counterpartyInput names the counterparty block a history act's check
// sealed, passed beside the act's capsule (history[i].counterparty; the
// engine's _COUNTERPARTY_INPUT, capsule-engine #141).
const counterpartyInput = "counterparty"

// checkFPAlgs are the fp_algs a check seals its counterparty under (the
// bridge's _CHECK_FP_ALGS): keyed per deal, or per chain on a typed record.
var checkFPAlgs = []string{"hmac-sha256-deal-key", "hmac-sha256-chain-key"}

// checkCounterparty is whether block is a check's counterparty block in its
// agreed shape, {fp_alg: <a check's fp_alg>, ids: {<kind>: <64 lowercase
// hex>}}, so it may be read as the history act's own (the bridge's
// _check_counterparty). An ids with no members is in that shape.
func checkCounterparty(block json.RawMessage) bool {
	var cp map[string]json.RawMessage
	if json.Unmarshal(block, &cp) != nil || cp == nil {
		return false
	}
	var alg string
	if json.Unmarshal(cp["fp_alg"], &alg) != nil || !slices.Contains(checkFPAlgs, alg) {
		return false
	}
	var ids map[string]json.RawMessage
	if json.Unmarshal(cp["ids"], &ids) != nil || ids == nil {
		return false
	}
	for _, raw := range ids {
		var id string
		if json.Unmarshal(raw, &id) != nil || !hex64.MatchString(id) {
			return false
		}
	}
	return true
}

// counterpartyKinds are the kinds a check's counterparty block fingerprints
// (capsule-cli deal_rules.go whoFields; history_state.py COUNTERPARTY_KINDS).
var counterpartyKinds = []string{"payee", "name", "domain", "phone", "email", "relay_address", "profile_id"}

// threadCounterparty is a check's counterparty block, whole: its keyed
// fingerprints by kind and the algorithm that made them.
type threadCounterparty struct {
	FPAlg string
	IDs   map[string]string
}

// wellFormedCounterparty is block as a check's counterparty block, whole
// (history_state.py well_formed_counterparty, capsule-engine 9066490):
// fp_alg one a check seals, and ids a non-empty object whose every member
// is a known kind with a 64-hex fingerprint. Stricter than
// checkCounterparty, which only decides whether an act gets its check's
// target. Members are read by their exact names (jsonObject), as the engine
// reads a dict: encoding/json would match a struct field to FP_ALG too.
func wellFormedCounterparty(block json.RawMessage) (threadCounterparty, bool) {
	cp := objectOf(block)
	members := objectOf(cp["ids"])
	if len(members) == 0 {
		return threadCounterparty{}, false
	}
	alg, ok := stringOf(cp["fp_alg"])
	if !ok || !slices.Contains(checkFPAlgs, alg) {
		return threadCounterparty{}, false
	}
	ids := map[string]string{}
	for k, raw := range members {
		v, ok := stringOf(raw)
		if !ok || !slices.Contains(counterpartyKinds, k) || !hex64.MatchString(v) {
			return threadCounterparty{}, false
		}
		ids[k] = v
	}
	return threadCounterparty{FPAlg: alg, IDs: ids}, true
}

// sealedFingerprints are the fingerprints in a sealed counterparty block
// and their fp_alg (report/replay.py _sealed_counterparty): every member
// that is a non-empty string, under a non-empty fp_alg; none when no member
// is.
func sealedFingerprints(block json.RawMessage) (map[string]string, string) {
	cp := objectOf(block)
	alg, ok := stringOf(cp["fp_alg"])
	if !ok || alg == "" {
		return nil, ""
	}
	ids := map[string]string{}
	for k, raw := range objectOf(cp["ids"]) {
		if v, ok := stringOf(raw); ok && v != "" {
			ids[k] = v
		}
	}
	if len(ids) == 0 {
		return nil, ""
	}
	return ids, alg
}

// hex64 is a lowercase SHA-256 hex digest, matched in full (the bridge's
// _HEX64.fullmatch since capsule-engine #118, 12ee139): a final newline is
// not part of a digest. Go's $ matches only at the end of the text.
var hex64 = regexp.MustCompile(`^[0-9a-f]{64}$`)

// typedRefDigest is the digest of a typed record reference when it is a
// SHA-256 one (the bridge's _typed_ref_digest), as a JSON string.
func typedRefDigest(raw json.RawMessage) (json.RawMessage, bool) {
	var ref map[string]json.RawMessage
	if json.Unmarshal(raw, &ref) != nil || ref == nil {
		return nil, false
	}
	var alg, digest string
	if _, ok := nonEmptyString(ref["type"]); !ok || json.Unmarshal(ref["digest_alg"], &alg) != nil || alg != "SHA-256" ||
		json.Unmarshal(ref["digest"], &digest) != nil || !hex64.MatchString(digest) {
		return nil, false
	}
	return ref["digest"], true
}

// selectRecord returns the record's header members and its disclosed
// agent_input: from one capsule, or from a bundle by capsule id.
func selectRecord(raw []byte, top map[string]json.RawMessage, capsuleID string) (map[string]json.RawMessage, json.RawMessage, error) {
	if _, ok := top["records"]; !ok {
		if capsuleID != "" {
			return nil, nil, errors.New("record: --capsule-id picks a record from a bundle; this file is one capsule")
		}
		ai, ok := top["agent_input"]
		if !ok {
			return nil, nil, errors.New("record: the capsule carries no disclosed agent_input")
		}
		return top, ai, nil
	}
	var bundle struct {
		Records     []map[string]json.RawMessage `json:"records"`
		Disclosures map[string]struct {
			AgentInput json.RawMessage `json:"agent_input"`
		} `json:"disclosures"`
	}
	if err := json.Unmarshal(raw, &bundle); err != nil {
		return nil, nil, fmt.Errorf("record: bundle: %w", err)
	}
	var deal []string
	headers := map[string]map[string]json.RawMessage{}
	for _, r := range bundle.Records {
		var id string
		if err := json.Unmarshal(r["capsule_id"], &id); err != nil {
			continue
		}
		headers[id] = r
		d, ok := bundle.Disclosures[id]
		if !ok || d.AgentInput == nil {
			continue
		}
		var probe struct {
			Body map[string]json.RawMessage `json:"body"`
		}
		if json.Unmarshal(d.AgentInput, &probe) == nil && probe.Body != nil {
			deal = append(deal, id)
		}
	}
	if capsuleID == "" {
		if len(deal) != 1 {
			slices.Sort(deal)
			return nil, nil, fmt.Errorf("record: the bundle holds %d deal records; name one with --capsule-id: %v", len(deal), deal)
		}
		capsuleID = deal[0]
	}
	h, ok := headers[capsuleID]
	if !ok {
		return nil, nil, fmt.Errorf("record: the bundle has no record %s", capsuleID)
	}
	d, ok := bundle.Disclosures[capsuleID]
	if !ok || d.AgentInput == nil {
		return nil, nil, fmt.Errorf("record: the bundle discloses no agent_input for %s", capsuleID)
	}
	return h, d.AgentInput, nil
}

// checkAgentInputDigest refuses an agent_input the header does not seal.
func checkAgentInputDigest(header map[string]json.RawMessage, agentInput json.RawMessage) error {
	var att struct {
		ComputeAttestation struct {
			AgentInputDigest string `json:"agent_input_digest"`
		} `json:"compute_attestation"`
	}
	if raw, ok := header["model_attestation"]; !ok || json.Unmarshal(raw, &att) != nil || att.ComputeAttestation.AgentInputDigest == "" {
		return errors.New("record: the capsule header has no model_attestation.compute_attestation.agent_input_digest, so the disclosed body is not bound to it")
	}
	v, err := canon.FromJSON(agentInput)
	if err != nil {
		return fmt.Errorf("record: agent_input: %w", err)
	}
	got, err := canon.Digest(v)
	if err != nil {
		return fmt.Errorf("record: agent_input: %w", err)
	}
	if got != att.ComputeAttestation.AgentInputDigest {
		return fmt.Errorf("record: the disclosed agent_input digests to %s, not the header's agent_input_digest %s: the body is not the one the capsule seals", got, att.ComputeAttestation.AgentInputDigest)
	}
	return nil
}

// Unevaluable is why a record cannot be evaluated at all (a missing header
// member or body action), or nil.
func (a Action) Unevaluable() []string { return a.unevaluable }

// DecodeHistoryAct reads one earlier sealed act (an external-check-input/v0
// history entry: a capsule with its disclosed agent_input) into the ledger
// row the spend fold reads, from the same sources DecodeRecord uses:
// operator, developer and timestamp from the capsule header, the decision
// from its disposition, the chain from its chain block, and amount_minor,
// spend_minor, action_class and taxonomy_version from the deal body (from
// body.unchecked, or a typed action-outcome/v0's body.attempted, for an act
// taken without authority: Unchecked), and the target from its
// x-deal-v0 counterparty block as DecodeRecord takes the check's
// (payeeTarget), or from the entry's counterparty_profile in its agreed shape
// (profileTarget), so counterparty_seen_before counts an earlier purchase from
// the same payee. An act that moved money in (direction "in") is bridged
// as the engine bridges its check: amount and spend 0, its direction kept,
// so the caps window can leave it out when it carries no recorded decision
// (windowCoverage). A checked act with no recorded decision whose amounts
// cannot be read (none, or one in the wrong shape) is kept with the amount
// it lacks absent, so the window counts it at its sealed spend or is left
// unevaluated by it (the G3 ruling). Any other act the fold would
// silently leave out (no amount, a timestamp it cannot read) or whose body
// the header does not seal is refused, not skipped.
func DecodeHistoryAct(raw []byte) (Record, error) {
	var header map[string]json.RawMessage
	if err := json.Unmarshal(raw, &header); err != nil {
		return Record{}, fmt.Errorf("history act: %w", err)
	}
	var id string
	if err := json.Unmarshal(header["capsule_id"], &id); err != nil || id == "" {
		return Record{}, errors.New("history act: no capsule_id")
	}
	refuse := func(why string) (Record, error) { return Record{}, fmt.Errorf("history act %s: %s", id, why) }
	agentInput, ok := header["agent_input"]
	if !ok {
		return refuse("the capsule carries no disclosed agent_input")
	}
	if err := checkAgentInputDigest(header, agentInput); err != nil {
		return refuse(err.Error())
	}
	var h struct {
		ActionID    string  `json:"action_id"`
		ActionType  string  `json:"action_type"`
		Operator    *string `json:"operator"`
		Developer   *string `json:"developer"`
		Timestamp   *string `json:"timestamp"`
		Disposition *struct {
			Decision *string `json:"decision"`
		} `json:"disposition"`
		Chain *struct {
			ParentCapsuleID *string `json:"parent_capsule_id"`
			Relation        *string `json:"relation"`
		} `json:"chain"`
	}
	if err := json.Unmarshal(raw, &h); err != nil {
		return refuse(err.Error())
	}
	for name, v := range map[string]*string{"operator": h.Operator, "developer": h.Developer, "timestamp": h.Timestamp} {
		if v == nil {
			return refuse("capsule header has no " + name)
		}
	}
	if _, err := parseInstant(*h.Timestamp); err != nil {
		return refuse("capsule header timestamp: " + err.Error())
	}
	// The amounts are read as sealed and parsed below (minorOf): the shape
	// an undecided act seals is the window's to judge, not a refusal.
	type act struct {
		AmountMinor     json.RawMessage `json:"amount_minor"`
		SpendMinor      json.RawMessage `json:"spend_minor"`
		ActionClass     *string         `json:"action_class"`
		TaxonomyVersion *string         `json:"taxonomy_version"`
		Currency        *string         `json:"currency"`
		Direction       *string         `json:"direction"`
	}
	var input struct {
		// Type is a typed record's sealed type: an action-outcome/v0 states
		// an act taken without authority in body.attempted.
		Type string `json:"type"`
		Body *struct {
			act
			Counterparty json.RawMessage `json:"counterparty"`
			Unchecked    *act            `json:"unchecked"`
			Attempted    *act            `json:"attempted"`
		} `json:"body"`
		XDeal struct {
			Counterparty json.RawMessage   `json:"counterparty"`
			DealID       json.RawMessage   `json:"deal_id"`
			RecordType   string            `json:"record_type"`
			Refs         []json.RawMessage `json:"refs"`
		} `json:"x-deal-v0"`
	}
	if err := json.Unmarshal(agentInput, &input); err != nil {
		return refuse("agent_input: " + err.Error())
	}
	if input.Body == nil {
		return refuse("the disclosed agent_input has no body")
	}
	got := input.Body.act
	// An act taken without authority (capsule-cli deal_profile.go: sealed
	// as an outcome, never as an action): the x-deal-v0 body.unchecked, or
	// a typed action-outcome/v0's body.attempted (action_records.go).
	uncheckedMember := "unchecked"
	if input.Type == typedOutcome && input.Body.Attempted != nil {
		input.Body.Unchecked, uncheckedMember = input.Body.Attempted, "attempted"
	}
	if u := input.Body.Unchecked; u != nil {
		got = *u
		if got.ActionClass == nil {
			got.ActionClass = input.Body.ActionClass
		}
		if got.TaxonomyVersion == nil {
			got.TaxonomyVersion = input.Body.TaxonomyVersion
		}
	}
	// An act that moved money in is bridged as the engine's deal bridge
	// bridges its check (report/replay.py MONEY_IN, capsule-engine #130):
	// its amount and its spend are 0, whatever the body states, so the fold
	// reads it as the engine reads its own decision on that check. It is
	// never spend, so a body stating no amount_minor (a partial cancel's
	// cancelled_amount_minor) is not refused; what it returned is read
	// below, for dedupe.
	amount, amountRead := minorOf(got.AmountMinor)
	spend, spendRead := minorOf(got.SpendMinor)
	if got.Direction != nil && *got.Direction == moneyIn {
		amount, spend, amountRead, spendRead = new(int64(0)), new(int64(0)), true, true
	}
	unchecked := input.Body.Unchecked != nil
	deal, _, conflict := dealOf(agentInput)
	// A checked act with no recorded decision (an act capsulectl seals fyi:
	// every act but pay through rc14) is counted by the window at its
	// sealed spend (the G3 ruling): an amount it lacks, or seals in the
	// wrong shape, leaves the window unevaluated (windowCoverage) rather
	// than refusing the history. Any other act is refused as before.
	switch {
	case !unchecked && !conflict && (h.Disposition == nil || h.Disposition.Decision == nil):
		if !amountRead {
			amount = nil
		}
		if !spendRead {
			spend = nil
		}
	case !amountRead || amount == nil:
		return refuse("the deal body has no amount_minor in integer minor units")
	case !spendRead:
		return refuse("the deal body's spend_minor is not in integer minor units")
	}
	r := Record{CapsuleID: id, ActionID: h.ActionID, ActionType: h.ActionType, Operator: *h.Operator, Developer: *h.Developer,
		Timestamp: *h.Timestamp, AmountMinor: amount, SpendMinor: spend, ActionClass: got.ActionClass,
		TaxonomyVersion: got.TaxonomyVersion, Currency: got.Currency, Direction: got.Direction}
	// The act's per-deal target, from the counterparty block it seals as
	// the engine's deal bridge reads a history act (report/replay.py
	// _checked_body, history=True, capsule-engine #144): an x-deal-v0 check
	// or action step seals it in its block, a typed proposed-action/v0 or
	// action-record/v0 in its body. When the act seals none, the block its
	// check sealed, passed beside the capsule (history[i].counterparty,
	// #141), is read in its place, only in a check's agreed shape
	// (checkCounterparty); in any other it is named in the act's ignored
	// inputs and never read.
	block := input.XDeal.Counterparty
	if input.XDeal.RecordType != "check" && input.XDeal.RecordType != actionStep &&
		(input.Type == proposedActionType || input.Type == typedActRecord) {
		block = input.Body.Counterparty
	}
	var ignoredCounterparty bool
	if isJSONNull(block) && !isJSONNull(header[counterpartyInput]) {
		if checkCounterparty(header[counterpartyInput]) {
			block = header[counterpartyInput]
		} else {
			ignoredCounterparty = true
		}
	}
	if target, ok := payeeTarget(block); ok {
		r.Target = &target
	}
	if tc, ok := wellFormedCounterparty(block); ok {
		r.threadCounterparty = &tc
	}
	// The act's payee keyed per profile, which capsulectl resolves act ->
	// its check -> that check's companion and passes beside the capsule
	// (history[i].counterparty_profile), as the engine's replay keys an
	// earlier check on its companion's block. In its agreed shape it is the
	// target, so a purchase in another deal at the same merchant is the same
	// payee; in any other it is named first in the act's ignored inputs, and
	// the per-deal target stands, as it does when it is absent.
	if !isJSONNull(header[profileInput]) {
		if target, ok := profileTarget(header[profileInput]); ok {
			r.Target = &target
		} else {
			r.ignoredInputs = append(r.ignoredInputs, profileInput)
		}
	}
	if ignoredCounterparty {
		r.ignoredInputs = append(r.ignoredInputs, counterpartyInput)
	}
	// The act's deal (#111), by the deal id's one source (dealOf): its
	// x-deal-v0.deal_id, else a typed act's chain_id. An act naming two is
	// in no deal and never accepted, as the engine decides such a record
	// (#127: a would-allow is refused, so it is never seen nor spend); it
	// does not hold the check.
	if deal != "" {
		r.DealID = &deal
	}
	// An act that moved money in keys dedupe on what it returned and the act
	// it reverses (#108), as its check did: the returned amount by the same
	// rule (returnedMinor, over the act's own body), and the act reversed by
	// the digest of its refs[rel=reverses]. That ref's type is
	// "deal-record" where the check's reverses_ref is "record": the two name
	// the same act by the same digest, so only the digest is compared.
	if got.Direction != nil && *got.Direction == "in" {
		var raw struct {
			Body map[string]json.RawMessage `json:"body"`
		}
		_ = json.Unmarshal(agentInput, &raw)
		actBody := raw.Body
		if input.Body.Unchecked != nil {
			actBody = nil
			_ = json.Unmarshal(raw.Body[uncheckedMember], &actBody)
		}
		if _, raw, ok := returnedMinor(actBody); ok {
			var n int64
			_ = json.Unmarshal(raw, &n)
			r.ReturnedMinor = &n
		}
		r.ReversesRef = reversesRefOf(input.XDeal.Refs)
	}
	// The class of statement the act states, read as a check's is
	// (representationClassOf; replay.py _bridge_deal_check reads a history
	// act through the same bridge, capsule-engine #205): s05 counts it on an
	// accepted act. capsulectl seals no representing on an act.
	if cls, ok := representationClassOf(objectOf(objectOf(agentInput)["body"])); ok {
		var s string
		if json.Unmarshal(cls, &s) == nil {
			r.RepresentationClass = &s
		}
	}
	// An unchecked act is never a decision: whatever disposition its
	// header carries (capsulectl seals none), it is never accepted, so it
	// is never seen (ruling (a)); the window counts it as spend
	// (uncheckedInWindow).
	r.Unchecked = unchecked
	if h.Disposition != nil && !r.Unchecked {
		r.Decision = h.Disposition.Decision
	}
	if conflict && !r.Unchecked {
		reject := "reject"
		r.Decision = &reject
	}
	if h.Chain != nil {
		r.ChainParent, r.ChainRelation = h.Chain.ParentCapsuleID, h.Chain.Relation
	}
	// What the engine's history ledger writes for single_commitment
	// (live_history.py, capsule-engine #137): the act's sale, the entry's
	// own item_ref beside the capsule (AMENDMENT 6a) when in its agreed shape
	// (AddEntry reads one in any other shape as unknown); the task
	// authority a typed act record's authority_basis names; the decision its
	// capsule seals and the state that leaves it in. An act taken without
	// authority is unread there.
	var item string
	if json.Unmarshal(header[itemRefInput], &item) == nil && hex64.MatchString(item) {
		r.ItemRef = &item
	}
	if input.Type == typedActRecord {
		r.TaskAuthorityRef = basisTaskAuthority(agentInput)
	}
	r.live = liveNoDisposition
	if h.Disposition != nil && h.Disposition.Decision != nil && *h.Disposition.Decision != "" {
		r.sealed, r.live = h.Disposition.Decision, liveDisposition
	}
	if r.Unchecked {
		r.live = liveUnread
	}
	return r, nil
}

// typedActRecord is the typed record a check input's history carries for
// each act carried out (capsule-cli rules_checker.go rulesHistory).
const typedActRecord = "action-record/v0"

// basisTaskAuthority is replay.py _basis_task_authority: the digest of the
// task authority a typed act record's authority_basis names, the ref of its
// one entry of type task_authority; nil when it has none, more than one, or
// a ref that is not a SHA-256 typed reference.
func basisTaskAuthority(agentInput json.RawMessage) *string {
	var in struct {
		Body struct {
			AuthorityBasis []json.RawMessage `json:"authority_basis"`
		} `json:"body"`
	}
	if json.Unmarshal(agentInput, &in) != nil {
		return nil
	}
	var refs []json.RawMessage
	for _, raw := range in.Body.AuthorityBasis {
		var e struct {
			Type string          `json:"type"`
			Ref  json.RawMessage `json:"ref"`
		}
		if json.Unmarshal(raw, &e) == nil && e.Type == "task_authority" {
			refs = append(refs, e.Ref)
		}
	}
	if len(refs) != 1 {
		return nil
	}
	digest, ok := typedRefDigest(refs[0])
	if !ok {
		return nil
	}
	var d string
	if json.Unmarshal(digest, &d) != nil {
		return nil
	}
	return &d
}

// minorOf is a deal body amount as sealed: nil when absent or null, and
// false when it is not an integer (a string, a fraction, an object).
func minorOf(raw json.RawMessage) (*int64, bool) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, true
	}
	var n int64
	if err := json.Unmarshal(raw, &n); err != nil {
		return nil, false
	}
	return &n, true
}

// typedOutcome is the typed record that states an act taken without
// authority in body.attempted (capsule-cli action_records.go).
const typedOutcome = "action-outcome/v0"

// reversesRefOf is the digest of the refs[rel=reverses] an act step seals
// (capsule-cli deal_profile.go relRef: a typed deal-record reference with a
// rel), when it is a SHA-256 one, or nil.
func reversesRefOf(refs []json.RawMessage) *string {
	for _, raw := range refs {
		var ref struct {
			Rel string `json:"rel"`
		}
		if json.Unmarshal(raw, &ref) != nil || ref.Rel != "reverses" {
			continue
		}
		if digest, ok := typedRefDigest(raw); ok {
			var d string
			_ = json.Unmarshal(digest, &d)
			return &d
		}
	}
	return nil
}

// encodeString is s as a JSON string.
func encodeString(s string) json.RawMessage {
	raw, _ := json.Marshal(s)
	return raw
}

// encodeJSONStrings is ss as a JSON array of strings ([] when empty).
func encodeJSONStrings(ss []string) json.RawMessage {
	raw, _ := json.Marshal(ss)
	return raw
}
