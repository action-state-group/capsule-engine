package guard

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// historyEntry is one check input history entry, its members as given.
type historyEntry map[string]json.RawMessage

// givenHistoryEntry is the one history act of a history-act-counterparty
// vector, as the engine gives it (given/typed/repeat-in-one-deal.<way>.json).
func givenHistoryEntry(t *testing.T, way string) historyEntry {
	t.Helper()
	env := decodeJSON[struct {
		InputHistory []historyEntry `json:"history"`
	}](t, readFile(t, atB455063a, "history-act-counterparty", "given", "typed", "repeat-in-one-deal."+way+".json"))
	if len(env.InputHistory) != 1 {
		t.Fatalf("%s: %d history entries", way, len(env.InputHistory))
	}
	return env.InputHistory[0]
}

// typedRepeatEntry is the typed repeat's one history act as the engine gives
// it without the profile key: an action-record/v0 sealing no counterparty,
// with its check's block beside it.
func typedRepeatEntry(t *testing.T) historyEntry {
	t.Helper()
	return givenHistoryEntry(t, "disposition-accept-no-profile")
}

// rawString is s as a JSON string.
func rawString(t *testing.T, s string) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// rawBlock is b as JSON.
func rawBlock(t *testing.T, b checkBlock) json.RawMessage {
	t.Helper()
	out, err := json.Marshal(b)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// set sets member at path (each but the last an object) inside the JSON
// object doc, and returns the result.
func set(t *testing.T, doc json.RawMessage, value json.RawMessage, path ...string) json.RawMessage {
	t.Helper()
	var obj map[string]json.RawMessage
	if len(doc) > 0 {
		if err := json.Unmarshal(doc, &obj); err != nil {
			t.Fatal(err)
		}
	}
	if obj == nil {
		obj = map[string]json.RawMessage{}
	}
	if len(path) == 1 {
		obj[path[0]] = value
	} else {
		obj[path[0]] = set(t, obj[path[0]], value, path[1:]...)
	}
	out, err := json.Marshal(obj)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func decodeEntry(t *testing.T, entry historyEntry) Record {
	t.Helper()
	b, err := json.Marshal(entry)
	if err != nil {
		t.Fatal(err)
	}
	r, err := DecodeHistoryAct(b)
	if err != nil {
		t.Fatal(err)
	}
	return r
}

// sealAgentInput sets entry's agent_input member at path to value and seals
// the result again (its agent_input_digest).
func sealAgentInput(t *testing.T, entry historyEntry, value json.RawMessage, path ...string) {
	t.Helper()
	entry["agent_input"] = set(t, entry["agent_input"], value, path...)
	v, err := canon.FromJSON(entry["agent_input"])
	if err != nil {
		t.Fatal(err)
	}
	digest, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	entry["model_attestation"] = set(t, entry["model_attestation"], rawString(t, digest), "compute_attestation", "agent_input_digest")
}

const typedRepeatPayee = "658f451705c7b667b5ea51cfaca5078fcd7890378fc10597198943a88b162732"

// checkBlock is a counterparty block {fp_alg, ids: {payee}}.
type checkBlock struct {
	FPAlg string            `json:"fp_alg"`
	IDs   map[string]string `json:"ids"`
}

// #141: an act record that seals no counterparty takes the block its check
// sealed, passed beside it, as its target.
func TestAHistoryActTakesTheCounterpartyBesideIt(t *testing.T) {
	r := decodeEntry(t, typedRepeatEntry(t))
	if deref(r.Target) != "payee-fp:hmac-sha256-chain-key:"+typedRepeatPayee || len(r.ignoredInputs) != 0 {
		t.Fatalf("target %s, ignored %v", deref(r.Target), r.ignoredInputs)
	}
}

// Only a check's agreed shape is read; any other is named, never read
// (the engine's BAD_BLOCKS).
func TestACounterpartyInAnotherShapeIsIgnoredAndNamed(t *testing.T) {
	payee := typedRepeatPayee
	for name, block := range map[string]string{
		"uppercase hex":      `{"fp_alg":"hmac-sha256-chain-key","ids":{"payee":"` + strings.ToUpper(payee) + `"}}`,
		"a profile key":      `{"fp_alg":"hmac-sha256-profile-key","ids":{"payee":"` + payee + `"}}`,
		"no fp_alg":          `{"ids":{"payee":"` + payee + `"}}`,
		"a short id":         `{"fp_alg":"hmac-sha256-chain-key","ids":{"payee":"` + payee[:63] + `"}}`,
		"an id not a string": `{"fp_alg":"hmac-sha256-chain-key","ids":{"payee":7}}`,
		"ids not an object":  `{"fp_alg":"hmac-sha256-chain-key","ids":["` + payee + `"]}`,
		"not a block":        `"not a block"`,
	} {
		entry := typedRepeatEntry(t)
		entry["counterparty"] = json.RawMessage(block)
		r := decodeEntry(t, entry)
		if r.Target != nil || !slices.Equal(r.ignoredInputs, []string{"counterparty"}) {
			t.Errorf("%s: target %s, ignored %v", name, deref(r.Target), r.ignoredInputs)
		}
	}
}

// A block under a check's other fp_alg, or one with no ids, is in the
// agreed shape (_check_counterparty): read, never named; null is absent.
func TestACounterpartyInTheAgreedShapeIsRead(t *testing.T) {
	entry := typedRepeatEntry(t)
	entry["counterparty"] = rawBlock(t, checkBlock{FPAlg: "hmac-sha256-deal-key", IDs: map[string]string{"payee": typedRepeatPayee}})
	if r := decodeEntry(t, entry); deref(r.Target) != "payee-fp:hmac-sha256-deal-key:"+typedRepeatPayee || len(r.ignoredInputs) != 0 {
		t.Errorf("deal key: target %s, ignored %v", deref(r.Target), r.ignoredInputs)
	}
	entry["counterparty"] = rawBlock(t, checkBlock{FPAlg: "hmac-sha256-chain-key", IDs: map[string]string{}})
	if r := decodeEntry(t, entry); r.Target != nil || len(r.ignoredInputs) != 0 {
		t.Errorf("no ids: target %s, ignored %v", deref(r.Target), r.ignoredInputs)
	}
	entry["counterparty"] = json.RawMessage("null")
	if r := decodeEntry(t, entry); r.Target != nil || len(r.ignoredInputs) != 0 {
		t.Errorf("null: target %s, ignored %v", deref(r.Target), r.ignoredInputs)
	}
}

// Both inputs beside the act in another shape are named, the profile first;
// a malformed one beside a profile key keeps the profile's target.
func TestIgnoredHistoryInputsAreNamedProfileFirst(t *testing.T) {
	entry := typedRepeatEntry(t)
	entry["counterparty"], entry["counterparty_profile"] = rawString(t, "not a block"), rawString(t, "not a block")
	if r := decodeEntry(t, entry); !slices.Equal(r.ignoredInputs, []string{"counterparty_profile", "counterparty"}) {
		t.Errorf("ignored %v", r.ignoredInputs)
	}
	entry = givenHistoryEntry(t, "disposition-accept")
	entry["counterparty"] = rawString(t, "not a block")
	if r := decodeEntry(t, entry); !strings.HasPrefix(deref(r.Target), ProfileTargetPrefix) || !slices.Equal(r.ignoredInputs, []string{"counterparty"}) {
		t.Errorf("target %s, ignored %v", deref(r.Target), r.ignoredInputs)
	}
}

// A counterparty the act record seals stands over the one beside it, which
// is then not read, nor named (capsulectl rc14 seals one on a typed act).
func TestACounterpartyTheActSealsStandsOverTheOneBesideIt(t *testing.T) {
	entry := typedRepeatEntry(t)
	sealed := strings.Repeat("ab", 32)
	sealAgentInput(t, entry, rawBlock(t, checkBlock{FPAlg: "hmac-sha256-chain-key", IDs: map[string]string{"payee": sealed}}), "body", "counterparty")
	if r := decodeEntry(t, entry); deref(r.Target) != "payee-fp:hmac-sha256-chain-key:"+sealed || len(r.ignoredInputs) != 0 {
		t.Errorf("target %s, ignored %v", deref(r.Target), r.ignoredInputs)
	}
	entry["counterparty"] = rawString(t, "not a block")
	if r := decodeEntry(t, entry); deref(r.Target) != "payee-fp:hmac-sha256-chain-key:"+sealed || len(r.ignoredInputs) != 0 {
		t.Errorf("beside a sealed one, a malformed block is not read: target %s, ignored %v", deref(r.Target), r.ignoredInputs)
	}
}

// A typed act record's x-deal-v0 counterparty block is never its target:
// the bridge reads a typed act's body only.
func TestATypedActRecordIsNotReadFromAnXDealBlock(t *testing.T) {
	entry := typedRepeatEntry(t)
	delete(entry, "counterparty")
	sealAgentInput(t, entry, rawBlock(t, checkBlock{FPAlg: "hmac-sha256-deal-key", IDs: map[string]string{"payee": typedRepeatPayee}}), "x-deal-v0", "counterparty")
	if r := decodeEntry(t, entry); r.Target != nil {
		t.Errorf("target %s", deref(r.Target))
	}
}

// dedupe keys a history act on its spend, as it keys the check: a sale's
// acceptance (spend 0, price 190000) repeats the earlier acceptance.
func TestDedupeKeysAHistoryActOnItsSpend(t *testing.T) {
	a, h, err := DecodeCheckInput(readFile(t, atB455063a, "live-history", "given", "a-commit-again-1900.disposition-accept.json"))
	if err != nil {
		t.Fatal(err)
	}
	r := h.Acts[0]
	if deref(r.ActionClass) != "agreement.accept" || r.AmountMinor == nil || *r.AmountMinor != 190000 || r.keyAmount() == nil || *r.keyAmount() != 0 {
		t.Fatalf("act %s amount %v key amount %v", deref(r.ActionClass), r.AmountMinor, r.keyAmount())
	}
	ak, err := equivalenceKeyForAction(a)
	if err != nil {
		t.Fatal(err)
	}
	rk, err := equivalenceKeyForRecord(r)
	if err != nil {
		t.Fatal(err)
	}
	if ak != rk {
		t.Errorf("the check keys %s, its earlier acceptance %s", ak, rk)
	}
}

// An x-deal-v0 action step seals its counterparty in its block, whatever
// typed name its agent_input also carries (_checked_body reads the step
// first), never in its body.
func TestAnActionStepIsReadFromItsBlockWhateverItsType(t *testing.T) {
	env := decodeJSON[struct {
		InputHistory []historyEntry `json:"history"`
	}](t, readFile(t, atB455063a, "history-act-counterparty", "given", "x-deal-v0", "repeat-in-one-deal.as-sealed.json"))
	entry := env.InputHistory[0]
	delete(entry, "counterparty_profile")
	delete(entry, "counterparty")
	block, body := strings.Repeat("ab", 32), strings.Repeat("cd", 32)
	sealAgentInput(t, entry, rawBlock(t, checkBlock{FPAlg: "hmac-sha256-deal-key", IDs: map[string]string{"payee": block}}), "x-deal-v0", "counterparty")
	sealAgentInput(t, entry, rawBlock(t, checkBlock{FPAlg: "hmac-sha256-chain-key", IDs: map[string]string{"payee": body}}), "body", "counterparty")
	for _, typed := range []string{"", typedActRecord, proposedActionType} {
		if typed != "" {
			sealAgentInput(t, entry, rawString(t, typed), "type")
		}
		if r := decodeEntry(t, entry); deref(r.Target) != "payee-fp:hmac-sha256-deal-key:"+block {
			t.Errorf("type %q: target %s", typed, deref(r.Target))
		}
	}
}

// A typed proposed-action/v0 in the history (a check carried out) is read
// from its body, as the bridge reads one.
func TestATypedCheckInTheHistoryIsReadFromItsBody(t *testing.T) {
	entry := typedRepeatEntry(t)
	delete(entry, "counterparty")
	sealed := strings.Repeat("ef", 32)
	sealAgentInput(t, entry, rawString(t, proposedActionType), "type")
	sealAgentInput(t, entry, rawBlock(t, checkBlock{FPAlg: "hmac-sha256-chain-key", IDs: map[string]string{"payee": sealed}}), "body", "counterparty")
	if r := decodeEntry(t, entry); deref(r.Target) != "payee-fp:hmac-sha256-chain-key:"+sealed {
		t.Errorf("target %s", deref(r.Target))
	}
}
