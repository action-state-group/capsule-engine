package guard

import (
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// partyRoleMember names where a deal record's party_role comes from, in
// inputs: the intent block capsule-cli seals it in.
const partyRoleMember = "intent.party_role"

// intentPartyRole is a deal body's intent.party_role, as capsule-cli seals it
// (x-deal-v0 intent.party_role: on the deal's opening, the baseline or a
// sale, and optionally, equal to it, on a later intent). ok is false when
// the body states none. A party_role that is not a string is returned as its
// JSON text, so it is named, not read as absent.
func intentPartyRole(body map[string]json.RawMessage) (role string, ok bool) {
	var intent map[string]json.RawMessage
	if json.Unmarshal(body["intent"], &intent) != nil {
		return "", false
	}
	raw, ok := intent["party_role"]
	if !ok || string(raw) == "null" {
		return "", false
	}
	if json.Unmarshal(raw, &role) != nil {
		return string(raw), true
	}
	return role, true
}

// bundleDealRoles are the party_roles the bundle's records of dealID state
// on their intent, read only from an agent_input its header seals: a record
// whose disclosed body is not the sealed one is refused, as the checked
// record is. One bundle is one deal's log, so its opening record, where
// capsule-cli seals the role, is in it.
func bundleDealRoles(raw []byte, dealID string) ([]string, error) {
	var bundle struct {
		Records     []map[string]json.RawMessage `json:"records"`
		Disclosures map[string]struct {
			AgentInput json.RawMessage `json:"agent_input"`
		} `json:"disclosures"`
	}
	if err := json.Unmarshal(raw, &bundle); err != nil {
		return nil, fmt.Errorf("record: bundle: %w", err)
	}
	var roles []string
	for _, h := range bundle.Records {
		var id string
		if json.Unmarshal(h["capsule_id"], &id) != nil {
			continue
		}
		d, ok := bundle.Disclosures[id]
		if !ok || d.AgentInput == nil {
			continue
		}
		var input struct {
			Body map[string]json.RawMessage `json:"body"`
		}
		if json.Unmarshal(d.AgentInput, &input) != nil || input.Body == nil || recordDeal(d.AgentInput) != dealID {
			continue
		}
		role, ok := intentPartyRole(input.Body)
		if !ok {
			continue
		}
		if err := checkAgentInputDigest(h, d.AgentInput); err != nil {
			at := " (record " + id + ")"
			if se, ok := asSealError(err); ok {
				return nil, errors.New(se.refusal("the body of a record of the deal", at))
			}
			return nil, fmt.Errorf("the agent_input of a record of the deal cannot be read%s: %w", at, err)
		}
		roles = append(roles, role)
	}
	return roles, nil
}

// recordDeal is the deal a disclosed agent_input belongs to (dealOf), or
// "" when it names none or names two.
func recordDeal(agentInput json.RawMessage) string {
	deal, _, conflict := dealOf(agentInput)
	if conflict {
		return ""
	}
	return deal
}

// partyRoleFrom is the deal's party_role from the roles its records state:
// none is absent (a buyer's deal), one is that role, and two different ones,
// which capsule-cli refuses to seal, leave the action unevaluable. why is set
// when the action cannot be evaluated: two roles, or a role the plugin has no
// pack for (pack.KnownPartyRole), named, never defaulted.
func partyRoleFrom(roles []string) (role string, present bool, why string) {
	slices.Sort(roles)
	roles = slices.Compact(roles)
	switch {
	case len(roles) == 0:
		return "", false, ""
	case len(roles) > 1:
		return "", false, "the deal states more than one party_role: " + strings.Join(roles, ", ")
	}
	return roles[0], true, unknownPartyRole(roles[0])
}

// unknownPartyRole is why a stated party_role leaves the action unevaluable,
// or "": an explicit empty role is not the absent one.
func unknownPartyRole(role string) string {
	if role == "" || !pack.KnownPartyRole(role) {
		return (&pack.UnknownPartyRoleError{Role: role}).Error()
	}
	return ""
}

// WithPartyRole is a with the checker input's top-level party_role
// (external-check-input/v0, capsule-cli #171, AMENDMENT 4): the side of the
// deal capsulectl sends on every deal check, taken from the deal's sealed
// opening. It picks the pack. Absent (nil or null, an older capsulectl), a
// keeps what its record states (DecodeCheckInputRecord). A role the plugin
// has no pack for, or one other than the checked record's own intent
// states, leaves a unevaluable, naming it.
func WithPartyRole(a Action, raw json.RawMessage) Action {
	if isJSONNull(raw) {
		return a
	}
	var role string
	if json.Unmarshal(raw, &role) != nil {
		role = string(raw)
	}
	if a.PartyRole != nil && *a.PartyRole != role {
		a.unevaluable = append(slices.Clone(a.unevaluable), "the checker input's party_role is not the one the checked record states")
		return a
	}
	a.PartyRole = &role
	if why := unknownPartyRole(role); why != "" {
		a.unevaluable = append(slices.Clone(a.unevaluable), why)
	}
	return a
}
