package guard

import (
	"encoding/json"
	"regexp"
	"slices"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// typedNoAct are the typed records (capsule-cli action_records.go) that
// state no act, as capsule-engine report/replay.py _TYPED_NO_ACT reads them
// (#131, f256abc): the task authority, an evaluation, an approval (the
// user's, or a counterparty's acceptance), the action taken (which carries
// out the act its proposed action already stated), its outcome and a
// report. Only a proposed-action/v0 is a check; a type not named here still
// states an act.
var typedNoAct = []string{"task-authority/v0", "action-evaluation/v0", "action-approval/v0", "action-record/v0",
	"action-outcome/v0", "action-report/v0"}

// How a typed deal states a seller's offer, the buyer's acceptance of it and
// the seller's commit (capsule-cli action_records.go; replay.py at f256abc).
const (
	proposedActionType    = "proposed-action/v0"
	actionApprovalType    = "action-approval/v0"
	acceptanceAuthority   = "counterparty_acceptance"
	offerVerb, commitVerb = "offer", "commit"
)

// acceptedOfferMember names where a typed commit's proposal_at comes from,
// in inputs: the at the accepted offer's record seals (acceptedOfferAt; the
// name is kept from #131, when it was the capsule's timestamp).
const acceptedOfferMember = "accepted offer timestamp"

// answeredMessageMember names where a typed offer's proposal_at comes from,
// in inputs: the time of the counterparty's message it answers by its source
// ref (acceptedOfferAt, messageTime; capsule-engine #202).
const answeredMessageMember = "answered message time"

// typedStatesNoAct is whether a disclosed agent_input with no x-deal-v0 block
// is a typed record of a type that states no act (replay.py _states_no_act).
func typedStatesNoAct(agentInput json.RawMessage) bool {
	var in struct {
		Type string `json:"type"`
	}
	return json.Unmarshal(agentInput, &in) == nil && slices.Contains(typedNoAct, in.Type)
}

// changesDetails is replay.py _changes_details (capsule-cli changesDetails):
// an x-deal-v0 record that changes the deal's details, so an earlier
// acceptance no longer stands: a change, or a message or evidence that
// names the counterparty.
func changesDetails(block map[string]json.RawMessage) bool {
	var recordType string
	_ = json.Unmarshal(block["record_type"], &recordType)
	_, names := block["counterparty"]
	return recordType == "change" || ((recordType == "message" || recordType == "evidence") && names)
}

// acceptedOfferAt is replay.py _accepted_offer_at over a bundle's records in
// their order: for each bound typed commit, by the digest of its record, the
// at that the record of the offer it rests on seals (capsule-engine #132,
// 4ca8993: the value capsulectl sends live as record.proposal_at; the offer
// capsule's timestamp is the same instant, from one clock read, but is not
// read). That is the deal's latest
// offer, when a bound acceptance (an action-approval/v0 of authority
// counterparty_acceptance) names that offer's record by proposed_action_ref
// and nothing after it changed the details (changesDetails). A later offer
// supersedes an earlier one and its acceptance. Every record is read within
// the deal it names (dealOf; one naming two names none). A commit with no
// such offer is left out, so its proposal's age stays unestablished.
//
// A typed offer that names the counterparty's message it answers by its one
// source ref gets that message's time (messageTime; capsule-engine #202,
// d3066f5): the message must be a bound x-deal-v0 message from the
// counterparty, earlier in the bundle and in the same deal. A withheld,
// unbound or unresolved message, or more than one source ref, leaves the
// offer out.
func acceptedOfferAt(bundle []byte) map[string]string {
	var b struct {
		Records     []map[string]json.RawMessage `json:"records"`
		Disclosures map[string]struct {
			AgentInput json.RawMessage `json:"agent_input"`
		} `json:"disclosures"`
	}
	found := map[string]string{}
	if json.Unmarshal(bundle, &b) != nil {
		return found
	}
	type offer struct{ digest, at string }
	latest := map[string]offer{}
	accepted := map[string]string{}
	type message struct{ deal, at string }
	messages := map[string]message{}
	for _, header := range b.Records {
		var id string
		if json.Unmarshal(header["capsule_id"], &id) != nil {
			continue
		}
		shown := b.Disclosures[id].AgentInput
		if shown == nil || checkAgentInputDigest(header, shown) != nil {
			continue
		}
		deal, _, _ := dealOf(shown)
		if deal == "" {
			continue
		}
		var in struct {
			Type  string                     `json:"type"`
			At    json.RawMessage            `json:"at"`
			Body  map[string]json.RawMessage `json:"body"`
			XDeal map[string]json.RawMessage `json:"x-deal-v0"`
		}
		if json.Unmarshal(shown, &in) != nil {
			continue
		}
		var action, authority, recordType, from string
		_ = json.Unmarshal(in.Body["action"], &action)
		_ = json.Unmarshal(in.Body["authority"], &authority)
		_ = json.Unmarshal(in.XDeal["record_type"], &recordType)
		_ = json.Unmarshal(in.Body["from"], &from)
		if in.XDeal != nil && recordType == "message" && from == "counterparty" {
			at, ok := messageTime(in.XDeal, in.Body)
			if !ok {
				at = ""
			}
			messages[agentInputDigest(shown)] = message{deal: deal, at: at}
		}
		switch {
		case in.Type == proposedActionType && action == offerVerb:
			var at string
			_ = json.Unmarshal(in.At, &at)
			digest := agentInputDigest(shown)
			latest[deal] = offer{digest: digest, at: at}
			delete(accepted, deal)
			if source, ok := answeredMessage(shown); ok {
				if m, ok := messages[source]; ok && m.deal == deal && m.at != "" {
					found[digest] = m.at
				}
			}
		case in.Type == proposedActionType && action == commitVerb:
			if at := accepted[deal]; at != "" {
				found[agentInputDigest(shown)] = at
			}
		case in.Type == actionApprovalType && authority == acceptanceAuthority:
			if o, ok := latest[deal]; ok {
				var ref string
				if raw, ok := typedRefDigest(in.Body["proposed_action_ref"]); ok && json.Unmarshal(raw, &ref) == nil && ref == o.digest {
					accepted[deal] = o.at
				}
			}
		case in.XDeal != nil && changesDetails(in.XDeal):
			delete(accepted, deal)
		}
	}
	return found
}

// agentInputDigest is the SHA-256 over the JCS bytes of a disclosed
// agent_input (its record digest), or "" when it has none.
func agentInputDigest(agentInput json.RawMessage) string {
	v, err := canon.FromJSON(agentInput)
	if err != nil {
		return ""
	}
	d, err := canon.Digest(v)
	if err != nil {
		return ""
	}
	return d
}

// isDealCheck is replay.py _is_check: an x-deal-v0 check record, or, with
// no x-deal-v0 block, a typed proposed-action/v0.
func isDealCheck(agentInput json.RawMessage, xdeal map[string]json.RawMessage) bool {
	if xdeal != nil {
		t, _ := stringOf(xdeal["record_type"])
		return t == "check"
	}
	var in struct {
		Type string `json:"type"`
	}
	return json.Unmarshal(agentInput, &in) == nil && in.Type == proposedActionType
}

// sourceRel is the ref an offer names the message it answers by.
const sourceRel = "source"

// dealRefs is replay.py _refs: an x-deal-v0 record's block refs, else a
// typed record's own; none when they are not a list.
func dealRefs(agentInput json.RawMessage, xdeal map[string]json.RawMessage) []json.RawMessage {
	raw := objectOf(agentInput)["refs"]
	if xdeal != nil {
		raw = xdeal["refs"]
	}
	var refs []json.RawMessage
	if json.Unmarshal(raw, &refs) != nil {
		return nil
	}
	return refs
}

// namesSource is replay.py _names_source: whether a deal record names any
// source ref, in whatever shape.
func namesSource(agentInput json.RawMessage, xdeal map[string]json.RawMessage) bool {
	return slices.ContainsFunc(dealRefs(agentInput, xdeal), func(ref json.RawMessage) bool {
		rel, _ := stringOf(objectOf(ref)["rel"])
		return objectOf(ref) != nil && rel == sourceRel
	})
}

// sealedTimePattern is replay.py _SEALED_TIME: RFC 3339 UTC in whole
// seconds, as capsulectl writes a sealed time (ASCII digits: Go's \d).
var sealedTimePattern = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`)

func sealedTime(raw json.RawMessage) (time.Time, bool) {
	s, ok := stringOf(raw)
	if !ok || !sealedTimePattern.MatchString(s) {
		return time.Time{}, false
	}
	at, err := time.Parse(time.RFC3339, s)
	// Python's datetime holds years 1 to 9999: year 0 is no time there.
	return at, err == nil && at.Year() >= 1
}

// messageTime is replay.py _message_time: when a counterparty's message was
// sent: its sealed sent_at when the member is present, else its block's
// sealed at. A present sent_at that is not a real UTC time, or that is later
// than the message's own at, gives no time: it is never replaced by at.
func messageTime(xdeal, body map[string]json.RawMessage) (string, bool) {
	at, okAt := sealedTime(xdeal["at"])
	sentRaw, present := body["sent_at"]
	if !present {
		if !okAt {
			return "", false
		}
		return stringOf(xdeal["at"])
	}
	sent, okSent := sealedTime(sentRaw)
	if !okSent || !okAt || sent.After(at) {
		return "", false
	}
	return stringOf(sentRaw)
}

// answeredMessage is replay.py _answered_message: the digest a typed
// offer's one source ref names, when it has exactly one SHA-256 typed ref.
func answeredMessage(agentInput json.RawMessage) (string, bool) {
	var refs []json.RawMessage
	_ = json.Unmarshal(objectOf(agentInput)["refs"], &refs)
	var found []json.RawMessage
	for _, ref := range refs {
		if rel, _ := stringOf(objectOf(ref)["rel"]); objectOf(ref) != nil && rel == sourceRel {
			found = append(found, ref)
		}
	}
	if len(found) != 1 {
		return "", false
	}
	raw, ok := typedRefDigest(found[0])
	if !ok {
		return "", false
	}
	return stringOf(raw)
}
