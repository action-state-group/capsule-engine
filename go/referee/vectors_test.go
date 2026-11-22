// SPDX-License-Identifier: Apache-2.0

package referee

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

const vectors = "../../conformance-vectors/referee/v0/cases.json"

type party struct {
	Half   json.RawMessage `json:"half"`
	Action string          `json:"action"`
	Terms  json.RawMessage `json:"terms"`
}

type vector struct {
	Name        string          `json:"name"`
	Kind        string          `json:"kind"`
	Capsule     json.RawMessage `json:"capsule"`
	Doc         string          `json:"doc"`
	BuyerHalf   json.RawMessage `json:"buyer_half"`
	SellerHalf  json.RawMessage `json:"seller_half"`
	BuyerTerms  json.RawMessage `json:"buyer_terms"`
	SellerTerms json.RawMessage `json:"seller_terms"`
	Buyer       party           `json:"buyer"`
	Seller      party           `json:"seller"`
	Expected    struct {
		OK   bool   `json:"ok"`
		Code string `json:"code"`
	} `json:"expected"`
	// GoExpected is set where Go decides differently on purpose: the Result is
	// held to AAC's Evidence Result schema, which Python's vendored one is older than
	// (capsule-engine#318).
	GoExpected *struct {
		OK   bool   `json:"ok"`
		Code string `json:"code"`
	} `json:"go_expected"`
}

// Every case is decided as Python decides it: the same outcome or the same code,
// apart from the cases that carry a go_expected, which must really differ.
func TestSharedVectors(t *testing.T) {
	raw, err := os.ReadFile(vectors)
	if err != nil {
		t.Fatal(err)
	}
	var file struct{ Cases []vector }
	if err := json.Unmarshal(raw, &file); err != nil || len(file.Cases) == 0 {
		t.Fatalf("vectors: %v, %d cases", err, len(file.Cases))
	}
	codes := map[string]bool{}
	for _, c := range file.Cases {
		t.Run(c.Name, func(t *testing.T) {
			var err error
			switch c.Kind {
			case "bind":
				err = BindOpenings(
					Party{Half: c.Buyer.Half, Action: c.Buyer.Action, Terms: c.Buyer.Terms},
					Party{Half: c.Seller.Half, Action: c.Seller.Action, Terms: c.Seller.Terms})
			default:
				err = VerifyResult(c.Capsule, []byte(c.Doc), Inputs{
					BuyerHalf: c.BuyerHalf, SellerHalf: c.SellerHalf, BuyerTerms: c.BuyerTerms, SellerTerms: c.SellerTerms})
			}
			want := c.Expected
			if c.GoExpected != nil {
				want = *c.GoExpected
				if want == c.Expected {
					t.Fatal("go_expected equals expected: it is not a known difference")
				}
			}
			if want.OK {
				if err != nil {
					t.Fatalf("want ok, got %v", err)
				}
				return
			}
			var re *Error
			if !errors.As(err, &re) {
				t.Fatalf("want code %s, got %v", want.Code, err)
			}
			if re.Code != want.Code {
				t.Fatalf("want code %s, got %s (%s)", want.Code, re.Code, re.Detail)
			}
			codes[re.Code] = true
		})
	}
	for _, code := range []string{CodeHalfUnverified, CodeOpeningMismatch, CodeHalvesUnpaired, CodeResultUnverified,
		CodeNotNamedReferee, CodeNotAResult, CodeOtherDeal, CodeResultMismatch, CodeResultInvalid} {
		if !codes[code] {
			t.Errorf("no vector for %s", code)
		}
	}
}
