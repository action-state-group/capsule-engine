package guard

import (
	"encoding/json"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

type statedVectors struct {
	FloorMinor int64 `json:"floor_minor"`
	Bridge     map[string]struct {
		AmountMinor       *int64 `json:"amount_minor"`
		StatedAmountMinor *int64 `json:"stated_amount_minor"`
	} `json:"bridge"`
	PriceFloor map[string]struct {
		AmountMinor       *int64          `json:"amount_minor"`
		StatedAmountMinor *int64          `json:"stated_amount_minor"`
		Result            string          `json:"result"`
		Evidence          json.RawMessage `json:"evidence"`
	} `json:"price_floor"`
}

type statedCorpus struct {
	Bodies map[string]map[string]json.RawMessage `json:"bodies"`
}

// bicycleAuthority and bicycleOpening are capsule-engine
// tests/test_price_floor_committed_bounds.py AUTHORITY and OPENING at
// 8522efc: capsule-cli's first commercial-bounds vector (floor 170000).
const (
	bicycleAuthority = `{"type":"task-authority/v0","body":{"outcome_id":"household.sell_the_bicycle/1.0.0","allowed_actions":["offer","sell"],"preconditions":[],"bounds_commitment":"13563d6685d99e1bd4b5508a85a6ca679331cb3ff60e8516177ccafcfd5b4998"}}`
	bicycleOpening   = `{"document":{"type":"commercial-bounds/v0","min_total_minor":170000},"nonce":"43a3c8a2914ecd228f722ed9f47dc4b4005cc1e257bc64777c0635a41ba7ea67","bounds_commitment":"13563d6685d99e1bd4b5508a85a6ca679331cb3ff60e8516177ccafcfd5b4998"}`
)

// statedTypes fixes the amounts' shape for comparison.
type statedPair struct {
	Amount, Stated string
}

func pairOf(amount, stated *int64) statedPair {
	s := func(p *int64) string {
		if p == nil {
			return "null"
		}
		b, _ := json.Marshal(*p)
		return string(b)
	}
	return statedPair{Amount: s(amount), Stated: s(stated)}
}

// CROSS-IMPLEMENTATION, capsule-engine #129 (8522efc): the deal bridge
// carries a check body's amount_minor, taken only as an integer, as
// stated_amount_minor beside the spend, which stays what caps reads (0 on a
// sale, 0 when money moved in); price_floor compares stated_amount_minor
// when set, else the spend. Each bridge body is decoded as check --emit
// decodes a record; each floor case is checked against the engine's result
// and evidence digest, the floor read from the bicycle opening.
func TestPriceFloorReadsTheStatedAmount(t *testing.T) {
	corpus := decodeJSON[statedCorpus](t, readFile(t, "testdata", "price-floor-stated-amount-vectors", "corpus.json"))
	py := decodeJSON[statedVectors](t, readFile(t, "testdata", "price-floor-stated-amount-vectors", "python-decisions.json"))
	for name, body := range corpus.Bodies {
		full := map[string]json.RawMessage{"action": json.RawMessage(`"commit"`), "action_class": json.RawMessage(`"agreement.accept"`),
			"taxonomy_version": json.RawMessage(`"6"`)}
		for k, v := range body {
			full[k] = v
		}
		input := map[string]json.RawMessage{"x-deal-v0": json.RawMessage(`{"record_type":"check"}`), "body": mustRaw(t, full)}
		v, err := canon.FromJSON(mustRaw(t, input))
		if err != nil {
			t.Fatal(err)
		}
		digest, err := canon.Digest(v)
		if err != nil {
			t.Fatal(err)
		}
		record := `{"operator":"deal","developer":"capsulectl-deal","action_id":"deal/1","timestamp":"2026-10-08T09:00:00Z",` +
			`"model_attestation":{"compute_attestation":{"agent_input_digest":"` + digest + `"}},"agent_input":` + string(mustRaw(t, input)) + `}`
		a, err := DecodeCheckInputRecord(json.RawMessage(record))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		spend, _ := a.engineAmount()
		want := py.Bridge[name]
		if got, engine := pairOf(spend, a.StatedAmountMinor), pairOf(want.AmountMinor, want.StatedAmountMinor); got != engine {
			t.Errorf("bridge %s: Go %+v, engine %+v", name, got, engine)
		}
	}
	e, _ := sellerEngine(t)
	ref, err := canon.FromJSON([]byte(bicycleAuthority))
	if err != nil {
		t.Fatal(err)
	}
	refDigest, err := canon.Digest(ref)
	if err != nil {
		t.Fatal(err)
	}
	for name, c := range py.PriceFloor {
		a := Action{Verb: "offer", Operator: "household", Developer: "assistant@v1", ActionClass: strp("marketplace.offer"),
			AmountMinor: c.AmountMinor, StatedAmountMinor: c.StatedAmountMinor, Currency: strp("USD"), Target: strp("buyer/ref-7"),
			Timestamp: "2026-10-08T09:00:00Z", TaskAuthorityRef: &refDigest}
		got, err := e.constraintFor("price_floor", a, nil, checkInputs{taskAuthority: json.RawMessage(bicycleAuthority), opening: json.RawMessage(bicycleOpening)})
		if err != nil {
			t.Fatal(err)
		}
		ev, err := canon.FromJSON(c.Evidence)
		if err != nil {
			t.Fatal(err)
		}
		wantDigest, err := canon.Digest(ev)
		if err != nil {
			t.Fatal(err)
		}
		digest, _, err := got.EvidenceDigest()
		if err != nil || (statedPair{string(got.Result), digest} != statedPair{c.Result, wantDigest}) {
			t.Errorf("price_floor %s: Go %s %s, engine %s %s %s", name, got.Result, digest, c.Result, wantDigest, c.Evidence)
		}
	}
}

func mustRaw(t *testing.T, v map[string]json.RawMessage) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}
