package guard

import (
	"encoding/json"
	"regexp"
	"strconv"
)

// The deal profile blocks the guard reads (capsule-engine #301). A disclosed
// agent_input carries its profile in one top-level member named for its
// version; evidencebook's dealprofile.Version (v0.18.1) is the rule, restated
// here because evidencebook requires this module (importing it back is a
// module cycle). The shared vectors pin the two.
const (
	profileV0 = "x-deal-v0"
	profileV1 = "x-deal-v1"
)

// profileBlockOf is the profile block of a disclosed agent_input and its
// version name, or nil and "" when it has none the guard reads.
//
// x-deal-v0 is read as it always was (no check of its own profile member), so
// no v0 record changes. An x-deal-v1 block counts only when its `profile`
// equals its name, and a record carrying both blocks is read as neither: it
// is no deal record of a known version, never one version's record read as the
// other's (v1 PROFILE.md section 1, "Versions"). A v1 record_type is returned
// as the v0 record type the guard's readers already know for it, so every
// reader below is version-blind (v1ToV0Type).
func profileBlockOf(top map[string]json.RawMessage) (map[string]json.RawMessage, string) {
	raw0, has0 := top[profileV0]
	raw1, has1 := top[profileV1]
	switch {
	case has0 && has1:
		return nil, ""
	case has0:
		var block map[string]json.RawMessage
		_ = json.Unmarshal(raw0, &block)
		return block, profileV0
	case has1:
		var block map[string]json.RawMessage
		if json.Unmarshal(raw1, &block) != nil || block == nil {
			return nil, ""
		}
		var profile string
		if json.Unmarshal(block["profile"], &profile) != nil || profile != profileV1 {
			return nil, ""
		}
		var recordType string
		if json.Unmarshal(block["record_type"], &recordType) == nil {
			if v0, ok := v1ToV0Type[recordType]; ok {
				block = copyBlock(block)
				block["record_type"] = encodeString(v0)
			}
		}
		return block, profileV1
	}
	return nil, ""
}

// profileBlockOfInput is profileBlockOf over an agent_input's bytes.
func profileBlockOfInput(agentInput json.RawMessage) (map[string]json.RawMessage, string) {
	var top map[string]json.RawMessage
	if json.Unmarshal(agentInput, &top) != nil {
		return nil, ""
	}
	return profileBlockOf(top)
}

// v1ToV0Type maps the v1 record types the guard reads to the v0 type it reads
// for the same step: an act carried out (perform, settle) is v0's action, and
// detail_change is v0's change. The journal and journey overlays that keep
// v0's names (check, verdict, approval, message, claim, evidence, agent_move)
// need no entry. Every other v1 type (propose, agree, statement, dispute,
// ruling, ask, answer, admit, rules_verdict) states no act and keeps its own
// name, which is not "check", as every non-check v0 record does.
var v1ToV0Type = map[string]string{"perform": actionStep, "settle": actionStep, "detail_change": "change"}

func copyBlock(b map[string]json.RawMessage) map[string]json.RawMessage {
	c := make(map[string]json.RawMessage, len(b))
	for k, v := range b {
		c[k] = v
	}
	return c
}

// profileMember names a block member in the version it was read from, as
// inputs name it.
func profileMember(version, member string) string {
	if version == "" {
		version = profileV0
	}
	return version + "." + member
}

// unreadableProfile is why a record's profile blocks cannot be read, or "" when
// they can (none, x-deal-v0, or a valid x-deal-v1). A record the guard cannot
// read is never taken as stating nothing: a check marks it not evaluable and
// a history act is refused, so not knowing never means allowing.
func unreadableProfile(agentInput json.RawMessage) string {
	var top map[string]json.RawMessage
	if json.Unmarshal(agentInput, &top) != nil {
		return ""
	}
	var names []string
	for k := range top {
		if profileName.MatchString(k) {
			names = append(names, k)
		}
	}
	switch {
	case len(names) > 1:
		return "the record names more than one deal profile block"
	case len(names) == 1 && names[0] != profileV0 && names[0] != profileV1:
		return "the record's deal profile version is not one the guard reads"
	case len(names) == 1 && names[0] == profileV1:
		if block, _ := profileBlockOf(top); block == nil {
			return "the record's x-deal-v1 block does not name itself as its profile"
		}
	}
	return ""
}

// minorDigits is an amount in minor units as the settlement member writes it:
// plain digits, no sign, no leading zero.
// maxSafeMinor is the largest amount the guard reads from a settlement member:
// the largest integer JCS and the evidence digest carry exactly (2^53-1), the
// same bound the Python replay holds.
const maxSafeMinor = 1<<53 - 1

var minorDigits = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)

var profileName = regexp.MustCompile(`^x-deal-v[0-9]+$`)

// v1ActRead is an x-deal-v1 perform or settle read as the act the guard's
// history knows: a payer leg is money out, a payee leg money in (v0's
// direction "in", never spend), the amount and currency come from the
// Capsule's top-level settlement member (v1 PROFILE.md sections 5 and 7), and
// body.reverses is the act reversed (v0's refs[rel=reverses]). A perform
// states no amount.
type v1ActRead struct {
	// isAct is a perform or a settle: any other v1 record type keeps its own
	// body (a check's amounts, a journal overlay's), as its v0 namesake does.
	isAct     bool
	settle    bool
	leg       string
	amount    *int64
	currency  *string
	reverses  *string
	malformed string
}

// readV1Act reads a v1 act's body and its capsule's settlement member.
func readV1Act(agentInput, capsule json.RawMessage) *v1ActRead {
	var in struct {
		Block struct {
			RecordType string `json:"record_type"`
		} `json:"x-deal-v1"`
		Body struct {
			Leg      string          `json:"leg"`
			Reverses json.RawMessage `json:"reverses"`
		} `json:"body"`
	}
	_ = json.Unmarshal(agentInput, &in)
	a := &v1ActRead{settle: in.Block.RecordType == "settle", leg: in.Body.Leg}
	a.isAct = a.settle || in.Block.RecordType == "perform"
	if digest, ok := typedRefDigest(in.Body.Reverses); ok {
		var d string
		_ = json.Unmarshal(digest, &d)
		a.reverses = &d
	}
	if !a.settle {
		return a
	}
	var head struct {
		Settlement *struct {
			Amount struct {
				Value     string `json:"value"`
				AssetCode string `json:"assetCode"`
			} `json:"amount"`
		} `json:"settlement"`
	}
	_ = json.Unmarshal(capsule, &head)
	if head.Settlement == nil {
		a.malformed = "an x-deal-v1 settle's capsule carries no settlement member"
		return a
	}
	n, err := strconv.ParseInt(head.Settlement.Amount.Value, 10, 64)
	if err != nil || n > maxSafeMinor || !minorDigits.MatchString(head.Settlement.Amount.Value) || head.Settlement.Amount.AssetCode == "" {
		a.malformed = "an x-deal-v1 settle's settlement member states no amount in minor units and currency"
		return a
	}
	a.amount, a.currency = &n, &head.Settlement.Amount.AssetCode
	return a
}

// act is the read as the act body the history decoder reads.
func (a *v1ActRead) act() act {
	var out act
	if !a.settle {
		return out
	}
	if a.amount != nil {
		raw := json.RawMessage(strconv.FormatInt(*a.amount, 10))
		out.AmountMinor, out.SpendMinor, out.Currency = raw, raw, a.currency
	}
	dir := "out"
	if a.leg == "payee" {
		dir = moneyIn
	}
	out.Direction = &dir
	return out
}
